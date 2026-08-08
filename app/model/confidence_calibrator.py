"""
Isolated confidence calibrator — maps pre-match context to P(win).

Dixon-Coles raw probability is an input only; this module does not modify DC.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.config import get_settings

settings = get_settings()

DEFAULT_MODEL_PATH = Path("data/models/confidence_calibrator.joblib")
DEFAULT_META_PATH = Path("data/models/confidence_calibrator_meta.json")

MARKET_INDEX = {"match_winner": 0, "over_under": 1, "btts": 2}
MIN_TRAIN_SAMPLES = 40
MIN_VAL_SAMPLES = 10
DEFAULT_LAMBDA = 1.0
DEFAULT_LAMBDA_TOL = 0.03


@dataclass
class CalibratorInput:
    """Pre-match snapshot at prediction time (DC output is read-only input)."""

    dixon_coles_probability: float
    market_fair_probability: float
    edge: float
    raw_ev: float
    odds: float
    market: str
    selection: str
    league_id: int | None = None
    home_ft_count: int | None = None
    away_ft_count: int | None = None
    used_default_lambda: bool = False
    home_lambda: float | None = None
    away_lambda: float | None = None
    feature_quality: float = 0.0
    hours_to_kickoff: float | None = None
    old_confidence: float | None = None
    predicted_at: datetime | None = None

    def calibrated_ev(self, calibrated_confidence: float) -> float:
        """Display-only EV from calibrated win probability (does not replace raw EV)."""
        if self.odds <= 1.0:
            return 0.0
        return (calibrated_confidence * self.odds) - 1.0


@dataclass
class CalibrationMetrics:
    brier_score: float
    log_loss: float
    calibration_error: float
    n_samples: int
    bucket_table: list[dict[str, Any]] = field(default_factory=list)
    winrate_by_bucket: list[dict[str, Any]] = field(default_factory=list)
    by_market: dict[str, dict[str, float]] = field(default_factory=dict)
    ev_gt_35: dict[str, float] | None = None


@dataclass
class TrainingReport:
    train_samples: int
    val_samples: int
    train_start: str | None
    train_end: str | None
    val_start: str | None
    val_end: str | None
    sufficient_data: bool
    message: str
    old_metrics: CalibrationMetrics | None = None
    new_metrics: CalibrationMetrics | None = None
    dc_metrics: CalibrationMetrics | None = None
    feature_names: list[str] = field(default_factory=list)


def _market_one_hot(market: str) -> list[float]:
    idx = MARKET_INDEX.get((market or "").lower().replace("-", "_"), -1)
    vec = [0.0, 0.0, 0.0]
    if 0 <= idx < 3:
        vec[idx] = 1.0
    return vec


def feature_names() -> list[str]:
    base = [
        "dc_prob",
        "fair_prob",
        "edge",
        "raw_ev",
        "log_odds",
        "home_ft_log",
        "away_ft_log",
        "min_ft_log",
        "default_lambda_flag",
        "home_lambda",
        "away_lambda",
        "feature_quality",
        "hours_to_kickoff",
    ]
    return base + ["market_mw", "market_ou", "market_btts"]


def vectorize_input(sample: CalibratorInput) -> np.ndarray:
    """Numeric feature vector for sklearn (no target leakage)."""
    home_ft = sample.home_ft_count if sample.home_ft_count is not None else 0
    away_ft = sample.away_ft_count if sample.away_ft_count is not None else 0
    min_ft = min(home_ft, away_ft)
    hl = sample.home_lambda if sample.home_lambda is not None else 0.0
    al = sample.away_lambda if sample.away_lambda is not None else 0.0
    htk = sample.hours_to_kickoff if sample.hours_to_kickoff is not None else 0.0
    odds = max(sample.odds, 1.01)
    row = [
        float(sample.dixon_coles_probability),
        float(sample.market_fair_probability),
        float(sample.edge),
        float(sample.raw_ev),
        math.log(odds),
        math.log1p(home_ft),
        math.log1p(away_ft),
        math.log1p(min_ft),
        1.0 if sample.used_default_lambda else 0.0,
        float(hl),
        float(al),
        float(max(0.0, min(1.0, sample.feature_quality))),
        float(max(0.0, htk)),
    ]
    row.extend(_market_one_hot(sample.market))
    return np.asarray(row, dtype=np.float64)


def vectorize_batch(samples: list[CalibratorInput]) -> np.ndarray:
    if not samples:
        return np.zeros((0, len(feature_names())), dtype=np.float64)
    return np.vstack([vectorize_input(s) for s in samples])


def brier_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    if len(probs) == 0:
        return float("nan")
    return float(np.mean((probs - outcomes) ** 2))


def log_loss_score(probs: np.ndarray, outcomes: np.ndarray) -> float:
    if len(probs) == 0:
        return float("nan")
    eps = 1e-15
    p = np.clip(probs, eps, 1 - eps)
    return float(-np.mean(outcomes * np.log(p) + (1 - outcomes) * np.log(1 - p)))


def expected_calibration_error(probs: np.ndarray, outcomes: np.ndarray, n_bins: int = 10) -> float:
    if len(probs) == 0:
        return float("nan")
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    n = len(probs)
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (probs >= lo) & (probs < hi if i < n_bins - 1 else probs <= hi)
        if not np.any(mask):
            continue
        acc = float(np.mean(outcomes[mask]))
        conf = float(np.mean(probs[mask]))
        ece += (np.sum(mask) / n) * abs(acc - conf)
    return float(ece)


def calibration_bucket_table(
    probs: np.ndarray,
    outcomes: np.ndarray,
    n_bins: int = 10,
) -> list[dict[str, Any]]:
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    rows: list[dict[str, Any]] = []
    for i in range(n_bins):
        lo, hi = bins[i], bins[i + 1]
        mask = (probs >= lo) & (probs < hi if i < n_bins - 1 else probs <= hi)
        count = int(np.sum(mask))
        if count == 0:
            rows.append(
                {
                    "bucket": f"{int(lo * 100)}-{int(hi * 100)}%",
                    "n": 0,
                    "mean_pred": None,
                    "winrate": None,
                }
            )
            continue
        rows.append(
            {
                "bucket": f"{int(lo * 100)}-{int(hi * 100)}%",
                "n": count,
                "mean_pred": round(float(np.mean(probs[mask])), 4),
                "winrate": round(float(np.mean(outcomes[mask])), 4),
            }
        )
    return rows


def compute_metrics(
    probs: np.ndarray,
    outcomes: np.ndarray,
    *,
    markets: list[str] | None = None,
    raw_evs: np.ndarray | None = None,
    label: str = "model",
) -> CalibrationMetrics:
    markets = markets or [""] * len(probs)
    by_market: dict[str, dict[str, float]] = {}
    for mk in sorted(set(markets)):
        if not mk:
            continue
        mask = np.array([m == mk for m in markets])
        if not np.any(mask):
            continue
        by_market[mk] = {
            "brier": brier_score(probs[mask], outcomes[mask]),
            "log_loss": log_loss_score(probs[mask], outcomes[mask]),
            "n": int(np.sum(mask)),
        }

    ev_gt_35 = None
    if raw_evs is not None and len(raw_evs) == len(probs):
        mask = raw_evs > 0.35
        if np.any(mask):
            ev_gt_35 = {
                "n": int(np.sum(mask)),
                "brier": brier_score(probs[mask], outcomes[mask]),
                "log_loss": log_loss_score(probs[mask], outcomes[mask]),
                "mean_pred": float(np.mean(probs[mask])),
                "winrate": float(np.mean(outcomes[mask])),
            }

    return CalibrationMetrics(
        brier_score=brier_score(probs, outcomes),
        log_loss=log_loss_score(probs, outcomes),
        calibration_error=expected_calibration_error(probs, outcomes),
        n_samples=len(probs),
        bucket_table=calibration_bucket_table(probs, outcomes),
        by_market=by_market,
        ev_gt_35=ev_gt_35,
    )


def detect_default_lambda(home_lambda: float | None, away_lambda: float | None) -> bool:
    if home_lambda is None or away_lambda is None:
        return False
    return (
        abs(home_lambda - DEFAULT_LAMBDA) <= DEFAULT_LAMBDA_TOL
        and abs(away_lambda - DEFAULT_LAMBDA) <= DEFAULT_LAMBDA_TOL
    )


def parse_lambdas_from_reasoning(reasoning: list[str] | None) -> tuple[float | None, float | None]:
    if not reasoning:
        return None, None
    for line in reasoning:
        if "λ:" not in line and "lambda" not in line.lower():
            continue
        # "Dixon-Coles λ: domaćin 1.00 — gost 1.00"
        parts = line.replace(",", ".").split()
        nums: list[float] = []
        for token in parts:
            try:
                val = float(token)
                if 0.0 <= val <= 8.0:
                    nums.append(val)
            except ValueError:
                continue
        if len(nums) >= 2:
            return nums[0], nums[1]
    return None, None


class ConfidenceCalibrator:
    """Logistic regression calibrator for P(win) — independent from Dixon-Coles."""

    def __init__(self) -> None:
        self.pipeline: Pipeline | None = None
        self.feature_names: list[str] = feature_names()
        self.trained_at: str | None = None
        self.train_samples: int = 0

    @property
    def is_ready(self) -> bool:
        return self.pipeline is not None

    def predict_proba(self, sample: CalibratorInput) -> float | None:
        if not self.is_ready:
            return None
        x = vectorize_input(sample).reshape(1, -1)
        assert self.pipeline is not None
        prob = float(self.pipeline.predict_proba(x)[0, 1])
        return max(0.01, min(0.99, prob))

    def fit(
        self,
        samples: list[CalibratorInput],
        outcomes: list[int],
        *,
        val_fraction: float = 0.25,
    ) -> TrainingReport:
        if len(samples) != len(outcomes):
            raise ValueError("samples and outcomes length mismatch")

        dated = [
            (s.predicted_at or datetime.min, s, o)
            for s, o in zip(samples, outcomes)
        ]
        dated.sort(key=lambda row: row[0])

        n = len(dated)
        if n < MIN_TRAIN_SAMPLES + MIN_VAL_SAMPLES:
            return TrainingReport(
                train_samples=n,
                val_samples=0,
                train_start=None,
                train_end=None,
                val_start=None,
                val_end=None,
                sufficient_data=False,
                message=(
                    f"Nedovoljno validnih primera ({n}). "
                    f"Potrebno >= {MIN_TRAIN_SAMPLES + MIN_VAL_SAMPLES} "
                    f"(hronološki settled win/lose sa pre-match poljima)."
                ),
            )

        split_idx = max(MIN_TRAIN_SAMPLES, int(n * (1.0 - val_fraction)))
        split_idx = min(split_idx, n - MIN_VAL_SAMPLES)
        train_rows = dated[:split_idx]
        val_rows = dated[split_idx:]

        x_train = vectorize_batch([r[1] for r in train_rows])
        y_train = np.asarray([r[2] for r in train_rows], dtype=np.int32)
        x_val = vectorize_batch([r[1] for r in val_rows])
        y_val = np.asarray([r[2] for r in val_rows], dtype=np.int32)

        pipeline = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=1.0,
                        max_iter=2000,
                        class_weight="balanced",
                        random_state=42,
                    ),
                ),
            ]
        )
        pipeline.fit(x_train, y_train)
        self.pipeline = pipeline
        self.trained_at = datetime.utcnow().isoformat()
        self.train_samples = len(train_rows)

        val_probs = pipeline.predict_proba(x_val)[:, 1]
        old_probs = np.asarray(
            [
                r[1].old_confidence if r[1].old_confidence is not None else 0.5
                for r in val_rows
            ],
            dtype=np.float64,
        )
        val_markets = [r[1].market for r in val_rows]
        val_evs = np.asarray([r[1].raw_ev for r in val_rows], dtype=np.float64)
        dc_probs = np.asarray(
            [r[1].dixon_coles_probability for r in val_rows], dtype=np.float64
        )

        report = TrainingReport(
            train_samples=len(train_rows),
            val_samples=len(val_rows),
            train_start=train_rows[0][0].isoformat() if train_rows[0][0] != datetime.min else None,
            train_end=train_rows[-1][0].isoformat() if train_rows[-1][0] != datetime.min else None,
            val_start=val_rows[0][0].isoformat() if val_rows else None,
            val_end=val_rows[-1][0].isoformat() if val_rows else None,
            sufficient_data=True,
            message="Trening završen (hronološka podela).",
            old_metrics=compute_metrics(
                old_probs, y_val, markets=val_markets, raw_evs=val_evs, label="old_conf"
            ),
            new_metrics=compute_metrics(
                val_probs, y_val, markets=val_markets, raw_evs=val_evs, label="calibrated"
            ),
            dc_metrics=compute_metrics(
                dc_probs, y_val, markets=val_markets, raw_evs=val_evs, label="dc_raw"
            ),
            feature_names=self.feature_names,
        )
        return report

    def save(self, path: Path | None = None, meta_path: Path | None = None) -> None:
        path = path or DEFAULT_MODEL_PATH
        meta_path = meta_path or DEFAULT_META_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        if not self.is_ready:
            raise RuntimeError("Cannot save untrained calibrator")
        joblib.dump(self.pipeline, path)
        meta = {
            "trained_at": self.trained_at,
            "train_samples": self.train_samples,
            "feature_names": self.feature_names,
            "model_type": "logistic_regression",
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def load(self, path: Path | None = None) -> bool:
        path = path or DEFAULT_MODEL_PATH
        if not path.is_file():
            return False
        try:
            self.pipeline = joblib.load(path)
            meta_path = DEFAULT_META_PATH
            if meta_path.is_file():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                self.trained_at = meta.get("trained_at")
                self.train_samples = int(meta.get("train_samples", 0))
            return True
        except Exception:
            self.pipeline = None
            return False


_calibrator_instance: ConfidenceCalibrator | None = None


def get_confidence_calibrator() -> ConfidenceCalibrator:
    global _calibrator_instance
    if _calibrator_instance is None:
        cal = ConfidenceCalibrator()
        cal.load()
        _calibrator_instance = cal
    return _calibrator_instance


def input_to_dict(sample: CalibratorInput) -> dict[str, Any]:
    data = asdict(sample)
    if data.get("predicted_at"):
        data["predicted_at"] = sample.predicted_at.isoformat() if sample.predicted_at else None
    return data
