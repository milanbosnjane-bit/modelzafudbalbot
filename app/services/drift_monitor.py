"""Population Stability Index feature drift monitoring."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import structlog
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import get_settings
from app.database.models import FeatureDriftRun, FeatureVector
from app.database.session import AsyncSessionLocal, SyncSessionLocal

logger = structlog.get_logger()
settings = get_settings()

BASELINE_PATH = settings.feature_dir / "drift_baseline.json"
PSI_STABLE = 0.10
PSI_WARNING = 0.25


def compute_psi(expected: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    """PSI between two distributions using quantile bins from expected."""
    expected = expected[~np.isnan(expected)]
    actual = actual[~np.isnan(actual)]
    if len(expected) < bins or len(actual) < 5:
        return 0.0

    breakpoints = np.quantile(expected, np.linspace(0, 1, bins + 1))
    breakpoints = np.unique(breakpoints)
    if len(breakpoints) < 2:
        return 0.0

    expected_pct = np.histogram(expected, bins=breakpoints)[0] / len(expected)
    actual_pct = np.histogram(actual, bins=breakpoints)[0] / len(actual)

    psi = 0.0
    for e, a in zip(expected_pct, actual_pct):
        e = max(e, 1e-6)
        a = max(a, 1e-6)
        psi += (a - e) * np.log(a / e)
    return float(psi)


def jensen_shannon_divergence(p: np.ndarray, q: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1.0)
    q = np.clip(q, 1e-6, 1.0)
    p = p / p.sum()
    q = q / q.sum()
    m = 0.5 * (p + q)
    kl_pm = np.sum(p * np.log(p / m))
    kl_qm = np.sum(q * np.log(q / m))
    return float(0.5 * (kl_pm + kl_qm))


def psi_status(max_psi: float) -> str:
    if max_psi < PSI_STABLE:
        return "stable"
    if max_psi < PSI_WARNING:
        return "warning"
    return "drift_detected"


class DriftMonitor:
    def __init__(self):
        self.baseline: dict[str, list[float]] = {}
        self._load_baseline()

    def _load_baseline(self) -> None:
        if BASELINE_PATH.exists():
            self.baseline = json.loads(BASELINE_PATH.read_text())
            return
        try:
            self._build_baseline_from_db()
        except Exception as e:
            logger.warning("drift_baseline_unavailable", error=str(e))
            self.baseline = {}

    def _build_baseline_from_db(self) -> None:
        session = SyncSessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=90)
            vectors = session.execute(
                select(FeatureVector).where(FeatureVector.computed_at >= cutoff)
            ).scalars().all()
            if not vectors:
                return
            keys: set[str] = set()
            for v in vectors:
                keys.update(k for k in v.features if isinstance(v.features[k], (int, float)))
            for key in keys:
                vals = [float(v.features[key]) for v in vectors if key in v.features]
                if vals:
                    self.baseline[key] = vals
            self._save_baseline()
        finally:
            session.close()

    def _save_baseline(self) -> None:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(json.dumps(self.baseline))

    def update_baseline(self, feature_snapshots: list[dict]) -> None:
        for snap in feature_snapshots:
            for k, v in snap.items():
                if isinstance(v, (int, float)):
                    self.baseline.setdefault(k, []).append(float(v))
        self._save_baseline()

    def run_psi_check(self, current_features: list[dict]) -> dict:
        if not self.baseline or not current_features:
            return {"status": "no_baseline", "max_psi": 0.0, "retrain_required": False}

        feature_psi = {}
        for key, baseline_vals in self.baseline.items():
            current_vals = [float(s[key]) for s in current_features if key in s]
            if len(current_vals) < 5 or len(baseline_vals) < 20:
                continue
            feature_psi[key] = compute_psi(
                np.array(baseline_vals), np.array(current_vals)
            )

        if not feature_psi:
            return {"status": "insufficient_data", "max_psi": 0.0, "retrain_required": False}

        max_psi = max(feature_psi.values())
        mean_psi = float(np.mean(list(feature_psi.values())))
        status = psi_status(max_psi)
        retrain_required = status == "drift_detected"

        return {
            "status": status,
            "max_psi": max_psi,
            "mean_psi": mean_psi,
            "feature_psi": feature_psi,
            "sample_size": len(current_features),
            "retrain_required": retrain_required,
        }

    async def run_and_persist(
        self,
        current_features: list[dict],
        prediction_time: datetime | None = None,
    ) -> dict:
        result = self.run_psi_check(current_features)
        snapshot_summary = self._summarize_snapshot(current_features)
        pred_time = prediction_time or datetime.utcnow()
        for attempt in range(5):
            try:
                async with AsyncSessionLocal() as session:
                    record = FeatureDriftRun(
                        max_psi=result.get("max_psi", 0.0),
                        mean_psi=result.get("mean_psi", 0.0),
                        status=result.get("status", "unknown"),
                        feature_psi=result.get("feature_psi", {}),
                        sample_size=result.get("sample_size", 0),
                        retrain_required=result.get("retrain_required", False),
                        feature_snapshot=snapshot_summary,
                        prediction_time=pred_time,
                    )
                    session.add(record)
                    await session.commit()
                    result["run_id"] = record.id
                break
            except OperationalError as e:
                if "locked" in str(e).lower() and attempt < 4:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                logger.warning("drift_persist_failed", error=str(e))
                break
            result["prediction_time"] = pred_time.isoformat()
            result["feature_snapshot"] = snapshot_summary
        return result

    def _summarize_snapshot(self, features: list[dict]) -> dict:
        if not features:
            return {}
        keys = set()
        for f in features:
            keys.update(k for k, v in f.items() if isinstance(v, (int, float)))
        summary = {"fixture_count": len(features), "features": {}}
        for key in sorted(keys):
            vals = [float(f[key]) for f in features if key in f]
            if vals:
                summary["features"][key] = {
                    "mean": float(np.mean(vals)),
                    "min": float(np.min(vals)),
                    "max": float(np.max(vals)),
                }
        return summary

    def get_latest_status(self) -> dict:
        session = SyncSessionLocal()
        try:
            run = session.execute(
                select(FeatureDriftRun).order_by(FeatureDriftRun.run_at.desc()).limit(1)
            ).scalar_one_or_none()
            if not run:
                return {"status": "no_runs", "retrain_required": False}
            return {
                "run_id": run.id,
                "run_at": run.run_at.isoformat(),
                "status": run.status,
                "max_psi": run.max_psi,
                "mean_psi": run.mean_psi,
                "retrain_required": run.retrain_required,
                "sample_size": run.sample_size,
            }
        finally:
            session.close()

    async def collect_current_cycle_features(self) -> list[dict]:
        """Latest feature vectors from recent prediction cycle."""
        async with AsyncSessionLocal() as session:
            cutoff = datetime.utcnow() - timedelta(hours=24)
            vectors = await session.execute(
                select(FeatureVector).where(FeatureVector.computed_at >= cutoff)
            )
            return [v.features for v in vectors.scalars().all()]
