"""Training pipeline with target normalization selection."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
from sqlalchemy import select

from app.config import get_settings
from app.database.models import DailyPick, FeatureVector, Fixture, TargetSelectionMetrics
from app.database.session import SyncSessionLocal
from app.models.lightgbm_model import LightGBMModel
from app.models.neural_model import NeuralNetworkModel
from app.models.xgboost_model import XGBoostModel
from app.predictions.regime import RegimeDetector, extract_regime_features
from app.training.market_encoding import augment_features
from app.training.target_selector import load_selected_transform, select_best_target
from app.training.targets import (
    TargetTransform,
    apply_target_transform,
    invert_target_transform,
    realized_return_from_outcome,
    rolling_return_std,
    score_target_variant,
)
from app.training.historical_data import build_historical_training_records
from app.training.validation import (
    chronological_train_test_split,
    expected_return_mae,
    expected_return_rmse,
)
from app.utils.legacy_data import fixture_has_api_odds

logger = structlog.get_logger()
settings = get_settings()


class TrainingPipeline:
    MIN_SAMPLES = settings.min_training_samples

    def __init__(self):
        self.lightgbm = LightGBMModel()
        self.xgboost = XGBoostModel()
        self.neural = NeuralNetworkModel()
        self.target_transform: TargetTransform = load_selected_transform()

    def build_training_data(
        self,
        *,
        exclude_legacy: bool | None = None,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str], list, dict, list[dict]]:
        if exclude_legacy is None:
            exclude_legacy = settings.exclude_legacy_training

        session = SyncSessionLocal()
        try:
            records = []
            regime_rows = []
            picks = session.execute(
                select(DailyPick).where(DailyPick.outcome.in_(["win", "lose", "push"]))
            ).scalars().all()

            for pick in picks:
                fixture = session.get(Fixture, pick.fixture_id)
                if not fixture:
                    continue
                if exclude_legacy and not fixture_has_api_odds(session, fixture.id):
                    continue

                fv = session.execute(
                    select(FeatureVector).where(
                        FeatureVector.fixture_id == pick.fixture_id,
                    ).order_by(FeatureVector.as_of_datetime.desc())
                ).scalar_one_or_none()
                if not fv or fv.as_of_datetime >= fixture.fixture_date:
                    continue

                raw_ret = realized_return_from_outcome(
                    pick.outcome, pick.odds, pick.profit_units, pick.stake_units
                )
                feats = augment_features(fv.features, pick.market, pick.selection)
                regime_rows.append(extract_regime_features(fv.features))
                records.append({
                    **feats,
                    "raw_return": raw_ret,
                    "odds": pick.odds,
                    "profit_units": pick.profit_units or 0.0,
                    "_ts": pick.pick_date,
                })

            if len(records) < self.MIN_SAMPLES:
                hist_records, hist_regime = build_historical_training_records(
                    session, exclude_legacy=exclude_legacy
                )
                records.extend(hist_records)
                regime_rows.extend(hist_regime)

            if len(records) < self.MIN_SAMPLES:
                raise ValueError(
                    f"Insufficient training samples ({len(records)} < {self.MIN_SAMPLES}). "
                    "Run: python -m app.train_models --bootstrap-days 90"
                    + (" (legacy football-data is excluded from training)" if exclude_legacy else "")
                )

            records.sort(key=lambda r: r["_ts"])
            timestamps = [r.pop("_ts") for r in records]
            df = pd.DataFrame(records)
            feature_cols = [c for c in df.columns if c not in ("raw_return", "odds", "profit_units")]
            X = df[feature_cols].values.astype(float)
            raw_returns = df["raw_return"].values.astype(float)
            odds = df["odds"].values.astype(float)

            meta = {
                "sample_size": len(records),
                "mean_return": float(raw_returns.mean()),
                "std_return": float(raw_returns.std()),
                "exclude_legacy": exclude_legacy,
            }
            return X, raw_returns, odds, feature_cols, timestamps, meta, regime_rows
        finally:
            session.close()

    def train_all(self, optimize: bool = True, exclude_legacy: bool | None = None) -> dict:
        X, raw_returns, odds, feature_names, timestamps, meta, regime_rows = self.build_training_data(
            exclude_legacy=exclude_legacy
        )

        regime_detector = RegimeDetector()
        regime_detector.fit(regime_rows)

        selected, comparison = select_best_target(X, raw_returns, odds, timestamps)
        self.target_transform = selected

        y = apply_target_transform(raw_returns, odds, selected)
        split = chronological_train_test_split(
            X, y, timestamps,
            test_ratio=settings.train_test_ratio,
            embargo_days=settings.train_embargo_days,
        )

        logger.info(
            "training_start",
            target_transform=selected.value,
            train_samples=len(split.y_train),
            test_samples=len(split.y_test),
        )

        results = {
            "target_selection": comparison,
            "selected_transform": selected.value,
            "lightgbm": self.lightgbm.train(
                split.X_train, split.y_train, feature_names,
                optimize=optimize, target_transform=selected,
            ),
            "xgboost": self.xgboost.train(
                split.X_train, split.y_train, feature_names,
                optimize=optimize, target_transform=selected,
            ),
        }

        test_odds = odds[split.test_indices]
        test_rolling = rolling_return_std(
            np.concatenate([raw_returns[split.train_indices], raw_returns[split.test_indices]])
        )[len(split.train_indices):]
        test_raw = raw_returns[split.test_indices]

        preds_norm = np.array([
            self.lightgbm.predict_norm(dict(zip(feature_names, row)))
            for row in split.X_test
        ])
        preds_raw = invert_target_transform(preds_norm, test_odds, test_rolling, selected)

        oos = score_target_variant(test_raw, preds_raw)
        results["oos_metrics"] = {
            **oos,
            "mae_norm_space": expected_return_mae(split.y_test, preds_norm),
            "rmse_norm_space": expected_return_rmse(split.y_test, preds_norm),
            **meta,
        }

        self._persist_target_metrics(selected.value, comparison, results["oos_metrics"])

        if len(split.y_train) >= 200:
            try:
                results["neural"] = self.neural.train(
                    split.X_train, split.y_train, feature_names,
                    X_val=split.X_test, y_val=split.y_test,
                    target_transform=selected.value,
                )
            except Exception as e:
                logger.warning("neural_training_skipped", error=str(e))

        logger.info("training_complete", oos=results["oos_metrics"])
        return results

    def _persist_target_metrics(self, selected: str, comparison: dict, oos: dict) -> None:
        session = SyncSessionLocal()
        try:
            for name, metrics in comparison.get("comparison", {}).items():
                if not isinstance(metrics, dict):
                    continue
                session.add(TargetSelectionMetrics(
                    transform_name=name,
                    mae=metrics.get("mae"),
                    rmse=metrics.get("rmse"),
                    oos_roi_pct=metrics.get("oos_roi_pct"),
                    stability_score=metrics.get("stability"),
                    composite_score=metrics.get("composite_score"),
                    is_selected=(name == selected),
                    details=metrics,
                ))
            session.commit()
        finally:
            session.close()

    def evaluate_clv_metrics(self) -> dict:
        session = SyncSessionLocal()
        try:
            all_picks = session.execute(select(DailyPick)).scalars().all()
            settled = [p for p in all_picks if p.outcome in ("win", "lose", "push")]
            with_clv = [p for p in settled if p.clv is not None]
            if not settled:
                return {"sample_size": 0, "clv_coverage_pct": 0}
            clvs = [p.clv for p in with_clv]
            profits = [p.profit_units or 0 for p in settled]
            staked = sum(p.stake_units for p in settled)
            return {
                "avg_clv": sum(clvs) / len(clvs) if clvs else 0,
                "roi_pct": (sum(profits) / staked * 100) if staked else 0,
                "sample_size": len(settled),
                "clv_coverage_pct": len(with_clv) / len(settled),
            }
        finally:
            session.close()


def run_training():
    return TrainingPipeline().train_all()
