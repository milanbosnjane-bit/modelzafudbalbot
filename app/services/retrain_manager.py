"""Automatic retrain trigger based on drift, CLV, edge capture, ROI."""

from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, select

from app.config import get_settings
from app.database.models import DailyPick, RetrainEvent
from app.database.session import AsyncSessionLocal, SyncSessionLocal
from app.services.drift_monitor import DriftMonitor
from app.training.dc_calibrator import DixonColesCalibrator

logger = structlog.get_logger()
settings = get_settings()


class RetrainManager:
    ROI_DETERIORATION_PCT = 30.0

    def evaluate_triggers(self) -> dict:
        drift = DriftMonitor().get_latest_status()
        clv_edge = self._clv_and_edge_metrics()
        roi = self._roi_deterioration()

        reasons = []
        if drift.get("retrain_required"):
            reasons.append(f"PSI drift max={drift.get('max_psi', 0):.3f}")
        if clv_edge["avg_clv"] < 0:
            reasons.append(f"avg CLV {clv_edge['avg_clv']:.3f} < 0")
        if clv_edge["avg_edge_capture"] < 0.5:
            reasons.append(f"edge_capture {clv_edge['avg_edge_capture']:.2f} < 0.5")
        if roi["deteriorated"]:
            reasons.append(f"ROI dropped {roi['drop_pct']:.1f}% vs prior period")

        return {
            "retrain_required": len(reasons) > 0,
            "reasons": reasons,
            "drift": drift,
            "clv_edge": clv_edge,
            "roi": roi,
        }

    def _clv_and_edge_metrics(self) -> dict:
        session = SyncSessionLocal()
        try:
            picks = session.execute(
                select(DailyPick).where(
                    DailyPick.outcome.in_(["win", "lose", "push"]),
                    DailyPick.is_paper == True,
                    DailyPick.pick_date >= datetime.utcnow() - timedelta(days=30),
                )
            ).scalars().all()
            if not picks:
                return {"avg_clv": 0, "avg_edge_capture": 0, "sample_size": 0}

            clvs = [
                p.clv_raw if p.clv_raw is not None else p.clv
                for p in picks
                if (p.clv_raw is not None or p.clv is not None)
            ]
            captures = [
                p.adjusted_edge_capture or p.edge_capture
                for p in picks
                if (p.adjusted_edge_capture or p.edge_capture) is not None
            ]
            return {
                "avg_clv": sum(clvs) / len(clvs) if clvs else 0,
                "avg_edge_capture": sum(captures) / len(captures) if captures else 0,
                "sample_size": len(picks),
            }
        finally:
            session.close()

    def _roi_deterioration(self) -> dict:
        session = SyncSessionLocal()
        try:
            now = datetime.utcnow()
            recent = session.execute(
                select(DailyPick).where(
                    DailyPick.outcome.in_(["win", "lose", "push"]),
                    DailyPick.pick_date >= now - timedelta(days=30),
                    DailyPick.is_paper == True,
                )
            ).scalars().all()
            prior = session.execute(
                select(DailyPick).where(
                    DailyPick.outcome.in_(["win", "lose", "push"]),
                    DailyPick.pick_date >= now - timedelta(days=60),
                    DailyPick.pick_date < now - timedelta(days=30),
                    DailyPick.is_paper == True,
                )
            ).scalars().all()

            def roi(picks):
                if not picks:
                    return 0.0
                profit = sum(p.profit_units or 0 for p in picks)
                staked = sum(p.stake_units for p in picks)
                return (profit / staked * 100) if staked else 0.0

            recent_roi = roi(recent)
            prior_roi = roi(prior)
            drop = 0.0
            deteriorated = False
            if prior_roi > 0:
                drop = ((prior_roi - recent_roi) / prior_roi) * 100
                deteriorated = drop > self.ROI_DETERIORATION_PCT

            return {
                "recent_roi_pct": recent_roi,
                "prior_roi_pct": prior_roi,
                "drop_pct": drop,
                "deteriorated": deteriorated,
            }
        finally:
            session.close()

    async def check_and_retrain(self, execute: bool = False) -> dict:
        evaluation = self.evaluate_triggers()

        async with AsyncSessionLocal() as session:
            event = RetrainEvent(
                reason="; ".join(evaluation["reasons"]) or "none",
                metrics=evaluation,
                executed=False,
            )
            session.add(event)
            await session.commit()
            event_id = event.id

        if evaluation["retrain_required"] and execute:
            try:
                async with AsyncSessionLocal() as session:
                    calibrator = DixonColesCalibrator()
                    result = await calibrator.run(session)
                async with AsyncSessionLocal() as session:
                    event = await session.get(RetrainEvent, event_id)
                    if event:
                        event.executed = True
                        event.result = {"status": "complete", "calibration": result}
                        await session.commit()
                evaluation["retrain_executed"] = True
                evaluation["calibration_result"] = result
            except Exception as e:
                evaluation["retrain_executed"] = False
                evaluation["calibration_error"] = str(e)
        else:
            evaluation["retrain_executed"] = False

        return evaluation

    async def post_prediction_cycle(
        self,
        feature_snapshots: list[dict],
        prediction_time: datetime | None = None,
    ) -> dict:
        monitor = DriftMonitor()
        if feature_snapshots:
            monitor.update_baseline(feature_snapshots)
        drift_result = await monitor.run_and_persist(
            feature_snapshots, prediction_time=prediction_time
        )
        evaluation = self.evaluate_triggers()
        evaluation["drift_run"] = drift_result
        if drift_result.get("retrain_required"):
            psi_reason = f"PSI drift max={drift_result.get('max_psi', 0):.3f}"
            if psi_reason not in evaluation.get("reasons", []):
                evaluation.setdefault("reasons", []).append(psi_reason)
            evaluation["retrain_required"] = True

        async with AsyncSessionLocal() as session:
            event = RetrainEvent(
                reason="; ".join(evaluation.get("reasons", [])) or "monitoring_cycle",
                metrics=evaluation,
                executed=False,
            )
            session.add(event)
            await session.commit()

        return evaluation
