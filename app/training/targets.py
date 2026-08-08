"""Training target definitions with normalization to reduce high-odds bias."""

from enum import Enum
import math

import numpy as np


class TargetTransform(str, Enum):
    RAW = "raw"
    LOG = "log"              # Option A
    ODDS_NORM = "odds_norm"  # Option B
    RISK_ADJ = "risk_adj"    # Option C


def realized_return_from_outcome(
    outcome: str, odds: float, profit_units: float | None, stake: float
) -> float:
    stake = max(stake, 0.01)
    if outcome == "win":
        return (profit_units / stake) if profit_units is not None else (odds - 1.0)
    if outcome == "lose":
        return -1.0
    if outcome == "push":
        return 0.0
    return 0.0


def expected_return_from_probability(probability: float, odds: float) -> float:
    return (probability * odds) - 1.0


def probability_from_expected_return(expected_return: float, odds: float) -> float:
    if odds <= 1.0:
        return 0.5
    return max(0.01, min(0.99, (expected_return + 1.0) / odds))


def normalize_target(
    raw_return: float,
    odds: float,
    rolling_std: float,
    method: TargetTransform,
) -> float:
    if method == TargetTransform.RAW:
        return raw_return
    if method == TargetTransform.LOG:
        return math.log1p(max(raw_return, -0.99))
    if method == TargetTransform.ODDS_NORM:
        denom = max(abs(odds - 1.0), 0.5)
        return raw_return / denom
    # RISK_ADJ
    std = max(rolling_std, 0.05)
    return raw_return / std


def denormalize_target(
    normalized: float,
    odds: float,
    rolling_std: float,
    method: TargetTransform,
) -> float:
    if method == TargetTransform.RAW:
        return normalized
    if method == TargetTransform.LOG:
        return math.expm1(normalized)
    if method == TargetTransform.ODDS_NORM:
        return normalized * max(abs(odds - 1.0), 0.5)
    return normalized * max(rolling_std, 0.05)


def rolling_return_std(returns: np.ndarray, window: int = 30) -> np.ndarray:
    """Expanding then rolling std — no future leakage within array order."""
    out = np.zeros(len(returns))
    for i in range(len(returns)):
        start = max(0, i - window)
        chunk = returns[start:i] if i > 0 else returns[:1]
        out[i] = float(np.std(chunk)) if len(chunk) > 1 else 0.15
        if out[i] < 0.05:
            out[i] = 0.15
    return out


def apply_target_transform(
    raw_returns: np.ndarray,
    odds: np.ndarray,
    method: TargetTransform,
) -> np.ndarray:
    rolling = rolling_return_std(raw_returns)
    return np.array([
        normalize_target(r, o, s, method)
        for r, o, s in zip(raw_returns, odds, rolling)
    ])


def invert_target_transform(
    normalized: np.ndarray,
    odds: np.ndarray,
    rolling_std: np.ndarray,
    method: TargetTransform,
) -> np.ndarray:
    return np.array([
        denormalize_target(n, o, s, method)
        for n, o, s in zip(normalized, odds, rolling_std)
    ])


def stability_score(y_true: np.ndarray, y_pred: np.ndarray, n_buckets: int = 5) -> float:
    """Lower variance of bucket MAE = higher stability (0-1)."""
    if len(y_true) < n_buckets * 2:
        return 0.5
    bucket_size = len(y_true) // n_buckets
    maes = []
    for i in range(n_buckets):
        start = i * bucket_size
        end = start + bucket_size if i < n_buckets - 1 else len(y_true)
        chunk_true = y_true[start:end]
        chunk_pred = y_pred[start:end]
        if len(chunk_true) == 0:
            continue
        maes.append(float(np.mean(np.abs(chunk_true - chunk_pred))))
    if len(maes) < 2:
        return 0.5
    std = float(np.std(maes))
    mean_mae = float(np.mean(maes)) or 1.0
    return max(0.0, min(1.0, 1.0 - std / (mean_mae + 1e-6)))


def simulated_oos_roi(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Bet when predicted denormalized return > 0; ROI on test set."""
    mask = y_pred > 0
    if not mask.any():
        return 0.0
    staked = float(mask.sum())
    profit = float(y_true[mask].sum())
    return (profit / staked) * 100.0


def score_target_variant(
    y_true_raw: np.ndarray,
    y_pred_raw: np.ndarray,
) -> dict:
    mae = float(np.mean(np.abs(y_true_raw - y_pred_raw)))
    rmse = float(np.sqrt(np.mean((y_true_raw - y_pred_raw) ** 2)))
    roi = simulated_oos_roi(y_true_raw, y_pred_raw)
    stability = stability_score(y_true_raw, y_pred_raw)
    # Lower MAE/RMSE better; higher ROI/stability better
    composite = (
        (1.0 / (1.0 + mae)) * 0.25
        + (1.0 / (1.0 + rmse)) * 0.25
        + max(0.0, roi / 100.0) * 0.25
        + stability * 0.25
    )
    return {
        "mae": mae,
        "rmse": rmse,
        "oos_roi_pct": roi,
        "stability": stability,
        "composite_score": composite,
    }
