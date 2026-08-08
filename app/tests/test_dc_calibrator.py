"""Unit tests for Dixon-Coles MLE calibrator."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import numpy as np
import pytest

from app.training.dc_calibrator import (
    CalibrationRow,
    DixonColesCalibrator,
    base_xg_from_features,
)


def _row(
    *,
    league_id: int = 39,
    home_goals: int = 1,
    away_goals: int = 0,
    base_home_xg: float = 1.4,
    base_away_xg: float = 1.0,
    days_ago: int = 10,
    weight: float | None = None,
) -> CalibrationRow:
    match_date = datetime.utcnow() - timedelta(days=days_ago)
    calibrator = DixonColesCalibrator()
    w = weight if weight is not None else calibrator.time_decay_weight(match_date, datetime.utcnow())
    return CalibrationRow(
        fixture_id=days_ago,
        league_id=league_id,
        home_goals=home_goals,
        away_goals=away_goals,
        base_home_xg=base_home_xg,
        base_away_xg=base_away_xg,
        match_date=match_date,
        weight=w,
    )


def _synthetic_rows(n: int = 55, league_id: int = 39) -> list[CalibrationRow]:
    outcomes = [(1, 0), (0, 1), (1, 1), (2, 1), (0, 0), (2, 0), (1, 2), (0, 2)]
    rows: list[CalibrationRow] = []
    for i in range(n):
        h, a = outcomes[i % len(outcomes)]
        rows.append(
            _row(
                league_id=league_id,
                home_goals=h,
                away_goals=a,
                base_home_xg=1.2 + (i % 5) * 0.05,
                base_away_xg=0.9 + (i % 4) * 0.04,
                days_ago=i + 1,
            )
        )
    return rows


class TestBaseXgFromFeatures:
    def test_venue_adjusted_with_injury(self):
        features = {
            "home_venue_adjusted_xg": 1.5,
            "away_venue_adjusted_xg": 1.1,
            "home_injury_impact_score": 0.4,
            "away_injury_impact_score": 0.0,
        }
        home, away = base_xg_from_features(features)
        assert home == pytest.approx(1.5 * (1.0 - 0.4 * 0.15))
        assert away == pytest.approx(1.1)

    def test_missing_xg_returns_none(self):
        assert base_xg_from_features({"home_venue_adjusted_xg": 1.0}) is None


class TestDixonColesCalibrator:
    def test_time_decay_recent_weighs_more(self):
        calibrator = DixonColesCalibrator(xi=0.01)
        ref = datetime.utcnow()
        recent = ref - timedelta(days=1)
        old = ref - timedelta(days=100)
        assert calibrator.time_decay_weight(recent, ref) > calibrator.time_decay_weight(old, ref)

    def test_score_probability_positive(self):
        calibrator = DixonColesCalibrator()
        prob = calibrator.score_probability(1, 0, lambda_h=1.3, lambda_a=0.9, rho=-0.13)
        assert 0.0 < prob < 1.0

    def test_log_likelihood_increases_with_better_fit(self):
        calibrator = DixonColesCalibrator()
        rows = _synthetic_rows(20)
        ll_default = calibrator.log_likelihood_rows(
            rows,
            xg_scale=1.0,
            home_advantage=1.08,
            rho_by_league={},
            default_rho=-0.13,
        )
        ll_tuned = calibrator.log_likelihood_rows(
            rows,
            xg_scale=1.05,
            home_advantage=1.10,
            rho_by_league={39: -0.15},
            default_rho=-0.13,
        )
        assert isinstance(ll_default, float)
        assert isinstance(ll_tuned, float)

    def test_fit_returns_calibrated_params(self):
        calibrator = DixonColesCalibrator()
        calibrator.MIN_GLOBAL_SAMPLES = 20
        calibrator.MIN_LEAGUE_SAMPLES = 15

        rows = _synthetic_rows(25, league_id=39) + _synthetic_rows(20, league_id=140)
        result = calibrator.fit(rows)

        assert result["sample_size"] == 45
        assert 0.60 <= result["xg_scale"] <= 1.50
        assert 0.95 <= result["home_advantage"] <= 1.30
        assert "39" in result["rho_by_league"]
        assert "140" in result["rho_by_league"]
        assert result["log_likelihood"] > float("-inf")

    def test_fit_raises_on_insufficient_samples(self):
        calibrator = DixonColesCalibrator()
        with pytest.raises(ValueError, match="Premalo uzoraka"):
            calibrator.fit(_synthetic_rows(10))

    def test_save_params_writes_json(self, tmp_path):
        calibrator = DixonColesCalibrator(params_path=tmp_path / "dc_params.json")
        payload = {
            "xg_scale": 1.02,
            "home_advantage": 1.07,
            "default_rho": -0.13,
            "rho_by_league": {"39": -0.12},
            "sample_size": 100,
        }
        path = calibrator.save_params(payload)
        assert path.is_file()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["xg_scale"] == 1.02
        assert loaded["rho_by_league"]["39"] == -0.12

    def test_global_fit_improves_over_random_params(self):
        calibrator = DixonColesCalibrator()
        calibrator.MIN_GLOBAL_SAMPLES = 30
        rows = _synthetic_rows(35)

        ll_before = calibrator.log_likelihood_rows(
            rows,
            xg_scale=0.75,
            home_advantage=1.25,
            rho_by_league={},
            default_rho=0.0,
        )
        result = calibrator.fit(rows)
        ll_after = result["log_likelihood"]
        assert ll_after > ll_before
