"""Compare target normalization variants and select best OOS."""

from __future__ import annotations

import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

from app.config import get_settings
from app.training.targets import (
    TargetTransform,
    apply_target_transform,
    invert_target_transform,
    rolling_return_std,
    score_target_variant,
)
from app.training.validation import chronological_train_test_split

settings = get_settings()
SELECTOR_PATH = settings.model_dir / "target_transform.json"

ALL_TRANSFORMS = [
    TargetTransform.RAW,
    TargetTransform.LOG,
    TargetTransform.ODDS_NORM,
    TargetTransform.RISK_ADJ,
]


def _quick_evaluate(
    X: np.ndarray,
    raw_returns: np.ndarray,
    odds: np.ndarray,
    timestamps: list,
    method: TargetTransform,
) -> dict:
    split = chronological_train_test_split(
        X, raw_returns, timestamps,
        test_ratio=settings.train_test_ratio,
        embargo_days=settings.train_embargo_days,
    )
    train_idx = split.train_indices
    test_idx = split.test_indices

    train_raw = raw_returns[train_idx]
    test_raw = raw_returns[test_idx]
    train_odds = odds[train_idx]
    test_odds = odds[test_idx]

    train_y = apply_target_transform(train_raw, train_odds, method)
    test_rolling = rolling_return_std(
        np.concatenate([train_raw, test_raw])
    )[len(train_raw):]

    model = lgb.LGBMRegressor(
        n_estimators=100, max_depth=4, learning_rate=0.08,
        min_child_samples=30, verbose=-1,
    )
    model.fit(X[train_idx], train_y)
    pred_norm = model.predict(X[test_idx])
    pred_raw = invert_target_transform(pred_norm, test_odds, test_rolling, method)

    metrics = score_target_variant(test_raw, pred_raw)
    metrics["transform"] = method.value
    return metrics


def select_best_target(
    X: np.ndarray,
    raw_returns: np.ndarray,
    odds: np.ndarray,
    timestamps: list,
) -> tuple[TargetTransform, dict]:
    results = {}
    for method in ALL_TRANSFORMS:
        results[method.value] = _quick_evaluate(
            X, raw_returns, odds, timestamps, method
        )

    best = max(results.values(), key=lambda r: r["composite_score"])
    selected = TargetTransform(best["transform"])

    payload = {
        "selected": selected.value,
        "comparison": results,
    }
    SELECTOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    SELECTOR_PATH.write_text(json.dumps(payload, indent=2))

    return selected, payload


def load_selected_transform() -> TargetTransform:
    if not SELECTOR_PATH.exists():
        return TargetTransform.ODDS_NORM
    data = json.loads(SELECTOR_PATH.read_text())
    return TargetTransform(data.get("selected", TargetTransform.ODDS_NORM.value))
