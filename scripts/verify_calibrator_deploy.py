#!/usr/bin/env python3
"""Post-training verification — calibrator load + Telegram format sample."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

LEGACY_DB = Path(r"C:\Users\Miki\Desktop\botposlednji1\data\football_roi.db")

from app.config import get_settings
from app.model.confidence_calibrator import CalibratorInput, ConfidenceCalibrator
from app.predictions.pick_selector import SelectedPick
from app.telegram.bot import TelegramNotifier

get_settings.cache_clear()
settings = get_settings()


def legacy_hash() -> str:
    return hashlib.sha256(LEGACY_DB.read_bytes()).hexdigest()


def main() -> None:
    print("USE_CALIBRATED_CONFIDENCE:", settings.use_calibrated_confidence)
    model_path = settings.model_dir / "confidence_calibrator.joblib"
    print("Model path:", model_path, "exists:", model_path.is_file())

    cal = ConfidenceCalibrator()
    ok = cal.load(model_path)
    print("Calibrator loaded:", ok, "ready:", cal.is_ready)

    sample = CalibratorInput(
        dixon_coles_probability=0.44,
        market_fair_probability=0.24,
        edge=0.20,
        raw_ev=0.84,
        odds=5.0,
        market="match_winner",
        selection="home",
        league_id=103,
        home_ft_count=10,
        away_ft_count=8,
        used_default_lambda=True,
        home_lambda=1.0,
        away_lambda=1.0,
        feature_quality=0.5,
        hours_to_kickoff=6.0,
        old_confidence=0.95,
        predicted_at=datetime.utcnow(),
    )
    cal_prob = cal.predict_proba(sample)
    print(f"Example: DC=44% old_CONF=95% -> calibrated={cal_prob*100:.0f}%")

    pick = SelectedPick(
        fixture_id=1,
        match_label="Example Home vs Example Away",
        market="match_winner",
        selection="home",
        odds=5.0,
        opening_odds=5.0,
        fair_implied_prob=0.24,
        line=None,
        expected_return=0.84,
        probability=0.44,
        expected_value=0.84,
        confidence=0.95,
        pick_rank_score=0.8,
        stake_units=1.0,
        stake_method="fractional_kelly",
        market_regime="moderate",
        reasoning=["Dixon-Coles λ: domaćin 1.00 — gost 1.00"],
        rank=1,
        fixture_date=datetime(2026, 8, 4, 18, 0),
        calibrated_confidence=cal_prob,
    )
    print("\n--- Telegram primer ---")
    print(TelegramNotifier().format_pick(pick))
    print("\nbotposlednji1 DB sha256:", legacy_hash())


if __name__ == "__main__":
    main()
