"""Temporal validation utilities — no random splits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
from sklearn.model_selection import TimeSeriesSplit


@dataclass
class TemporalSplit:
    X_train: np.ndarray
    y_train: np.ndarray
    X_test: np.ndarray
    y_test: np.ndarray
    train_indices: np.ndarray
    test_indices: np.ndarray


def chronological_train_test_split(
    X: np.ndarray,
    y: np.ndarray,
    timestamps: list[datetime],
    test_ratio: float = 0.2,
    embargo_days: int = 1,
) -> TemporalSplit:
    order = np.argsort([t.timestamp() for t in timestamps])
    X = X[order]
    y = y[order]
    timestamps = [timestamps[i] for i in order]

    n = len(y)
    split_at = max(1, min(n - 1, int(n * (1 - test_ratio))))

    test_start_time = timestamps[split_at]
    embargo_boundary = test_start_time - timedelta(days=embargo_days)

    train_idx = np.array([i for i in range(split_at) if timestamps[i] <= embargo_boundary])
    test_idx = np.arange(split_at, n)

    if len(train_idx) == 0:
        train_idx = np.arange(0, max(1, split_at))

    return TemporalSplit(
        X_train=X[train_idx],
        y_train=y[train_idx],
        X_test=X[test_idx],
        y_test=y[test_idx],
        train_indices=train_idx,
        test_indices=test_idx,
    )


def time_series_cv(n_splits: int = 3) -> TimeSeriesSplit:
    return TimeSeriesSplit(n_splits=n_splits)


def brier_score(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    return float(np.mean((y_prob - y_true) ** 2))


def expected_calibration_error(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> float:
    bins = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        mask = (y_prob >= bins[i]) & (y_prob < bins[i + 1])
        if not mask.any():
            continue
        acc = y_true[mask].mean()
        conf = y_prob[mask].mean()
        ece += mask.sum() / len(y_true) * abs(acc - conf)
    return float(ece)


def expected_return_mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.mean(np.abs(y_true - y_pred)))


def expected_return_rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))
