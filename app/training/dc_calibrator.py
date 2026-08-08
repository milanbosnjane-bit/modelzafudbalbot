"""Maximum Likelihood Estimation for Dixon-Coles parameters."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import structlog
from scipy.optimize import minimize, minimize_scalar
from sqlalchemy import select

from app.config import get_settings
from app.database.models import Fixture
from app.features.engineer import FeatureEngineer
from app.models.dixon_coles_model import DixonColesModel
from app.utils.feature_values import first_present, numeric_feature
from app.utils.helpers import decision_time
from app.utils.legacy_data import has_api_odds_exists

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class CalibrationRow:
    fixture_id: int
    league_id: int
    home_goals: int
    away_goals: int
    base_home_xg: float
    base_away_xg: float
    match_date: datetime
    weight: float = 1.0


def base_xg_from_features(features: dict) -> tuple[float, float] | None:
    """Raw venue-adjusted xG sa injury korekcijom — pre globalnog scale/home_adv."""
    home_xg = first_present(features, "home_venue_adjusted_xg", "home_weighted_xG_last5")
    away_xg = first_present(features, "away_venue_adjusted_xg", "away_weighted_xG_last5")
    if home_xg is None or away_xg is None:
        return None

    home_inj = numeric_feature(features, "home_injury_impact_score", default=0.0)
    away_inj = numeric_feature(features, "away_injury_impact_score", default=0.0)
    home_xg = home_xg * (1.0 - home_inj * 0.15)
    away_xg = away_xg * (1.0 - away_inj * 0.15)
    if home_xg <= 0 or away_xg <= 0:
        return None
    return float(home_xg), float(away_xg)


class DixonColesCalibrator:
    """
    MLE kalibracija:
    - globalni xg_scale i home_advantage
    - rho po ligi (DC korelacija niskih rezultata)
    - opcioni time decay (Dixon-Coles xi)
    """

    DEFAULT_RHO = -0.13
    DEFAULT_XG_SCALE = 1.0
    DEFAULT_HOME_ADVANTAGE = 1.08
    MIN_GLOBAL_SAMPLES = 50
    MIN_LEAGUE_SAMPLES = 30
    MAX_GOALS_CAP = 10

    def __init__(
        self,
        *,
        xi: float | None = None,
        exclude_legacy: bool = True,
        params_path: Path | None = None,
    ):
        self.xi = xi if xi is not None else settings.dc_time_decay_xi
        self.exclude_legacy = exclude_legacy
        self.params_path = params_path or (settings.model_dir / settings.dc_params_file)
        self._score_model = DixonColesModel()

    def time_decay_weight(self, match_date: datetime, reference: datetime) -> float:
        days = max((reference - match_date).total_seconds() / 86400.0, 0.0)
        return math.exp(-self.xi * days)

    def score_probability(
        self,
        home_goals: int,
        away_goals: int,
        lambda_h: float,
        lambda_a: float,
        rho: float,
    ) -> float:
        h = min(int(home_goals), self.MAX_GOALS_CAP)
        a = min(int(away_goals), self.MAX_GOALS_CAP)
        matrix = self._score_model.build_score_matrix(lambda_h, lambda_a, rho)
        return max(float(matrix[h, a]), 1e-12)

    def log_likelihood_rows(
        self,
        rows: list[CalibrationRow],
        *,
        xg_scale: float,
        home_advantage: float,
        rho_by_league: dict[int, float],
        default_rho: float,
    ) -> float:
        if not rows:
            return float("-inf")

        total = 0.0
        for row in rows:
            rho = rho_by_league.get(row.league_id, default_rho)
            lambda_h = max(0.05, row.base_home_xg * xg_scale * home_advantage)
            lambda_a = max(0.05, row.base_away_xg * xg_scale)
            prob = self.score_probability(
                row.home_goals, row.away_goals, lambda_h, lambda_a, rho
            )
            total += row.weight * math.log(prob)
        return total

    async def build_dataset(
        self,
        session,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        lookback_days: int | None = None,
    ) -> list[CalibrationRow]:
        """FT mečevi + point-in-time feature-i na T-1h."""
        end_date = end_date or datetime.utcnow()
        if start_date is None:
            days = lookback_days or settings.dc_calibration_lookback_days
            start_date = end_date - timedelta(days=days)

        filters = [
            Fixture.fixture_date >= start_date,
            Fixture.fixture_date <= end_date,
            Fixture.status.in_(["FT", "AET", "PEN"]),
            Fixture.home_goals.isnot(None),
            Fixture.away_goals.isnot(None),
        ]
        if self.exclude_legacy:
            filters.append(has_api_odds_exists())

        result = await session.execute(select(Fixture).where(*filters))
        fixtures = list(result.scalars().all())
        if not fixtures:
            logger.warning("dc_calibration_no_fixtures", start=start_date, end=end_date)
            return []

        engineer = FeatureEngineer(session, historical_mode=True, exclude_legacy_fixtures=self.exclude_legacy)
        as_of_map = {
            f.id: decision_time(f.fixture_date, settings.decision_hours_before_kickoff)
            for f in fixtures
        }

        reference = max(f.fixture_date for f in fixtures)
        rows: list[CalibrationRow] = []

        batch_size = 100
        fixture_ids = [f.id for f in fixtures]
        for offset in range(0, len(fixture_ids), batch_size):
            chunk_ids = fixture_ids[offset : offset + batch_size]
            chunk_as_of = {fid: as_of_map[fid] for fid in chunk_ids}

            features_map = await engineer.load_batch(chunk_ids, as_of_map=chunk_as_of)
            missing = [fid for fid in chunk_ids if fid not in features_map]
            if missing:
                built = await engineer.build_batch(missing, as_of_map=chunk_as_of, persist=True)
                features_map.update(built)

            fixtures_by_id = {f.id: f for f in fixtures if f.id in chunk_ids}
            for fid in chunk_ids:
                fixture = fixtures_by_id.get(fid)
                features = features_map.get(fid)
                if not fixture or not features:
                    continue

                base = base_xg_from_features(features)
                if base is None:
                    continue

                base_h, base_a = base
                weight = self.time_decay_weight(fixture.fixture_date, reference)
                rows.append(
                    CalibrationRow(
                        fixture_id=fid,
                        league_id=int(fixture.league_id),
                        home_goals=int(fixture.home_goals),
                        away_goals=int(fixture.away_goals),
                        base_home_xg=base_h,
                        base_away_xg=base_a,
                        match_date=fixture.fixture_date,
                        weight=weight,
                    )
                )

        logger.info(
            "dc_calibration_dataset_built",
            fixtures=len(fixtures),
            usable_rows=len(rows),
            start=start_date.isoformat(),
            end=end_date.isoformat(),
        )
        return rows

    def fit(self, rows: list[CalibrationRow]) -> dict:
        if len(rows) < self.MIN_GLOBAL_SAMPLES:
            raise ValueError(
                f"Premalo uzoraka za MLE kalibraciju: {len(rows)} "
                f"(minimum {self.MIN_GLOBAL_SAMPLES})"
            )

        default_rho = self.DEFAULT_RHO

        def neg_ll_global(params: np.ndarray) -> float:
            xg_scale, home_adv = float(params[0]), float(params[1])
            if xg_scale <= 0 or home_adv <= 0:
                return 1e12
            ll = self.log_likelihood_rows(
                rows,
                xg_scale=xg_scale,
                home_advantage=home_adv,
                rho_by_league={},
                default_rho=default_rho,
            )
            return -ll

        global_result = minimize(
            neg_ll_global,
            x0=np.array([self.DEFAULT_XG_SCALE, self.DEFAULT_HOME_ADVANTAGE]),
            bounds=[(0.60, 1.50), (0.95, 1.30)],
            method="L-BFGS-B",
        )
        if not global_result.success:
            logger.warning("dc_global_fit_warning", message=global_result.message)

        xg_scale = float(global_result.x[0])
        home_advantage = float(global_result.x[1])

        rho_by_league: dict[int, float] = {}
        league_counts: dict[int, int] = {}
        for row in rows:
            league_counts[row.league_id] = league_counts.get(row.league_id, 0) + 1

        for league_id, count in league_counts.items():
            if count < self.MIN_LEAGUE_SAMPLES:
                continue
            league_rows = [r for r in rows if r.league_id == league_id]

            def neg_ll_rho(rho_val: float) -> float:
                ll = self.log_likelihood_rows(
                    league_rows,
                    xg_scale=xg_scale,
                    home_advantage=home_advantage,
                    rho_by_league={league_id: float(rho_val)},
                    default_rho=default_rho,
                )
                return -ll

            rho_result = minimize_scalar(
                neg_ll_rho,
                bounds=(-0.25, 0.05),
                method="bounded",
            )
            rho_by_league[league_id] = float(rho_result.x)

        final_ll = self.log_likelihood_rows(
            rows,
            xg_scale=xg_scale,
            home_advantage=home_advantage,
            rho_by_league=rho_by_league,
            default_rho=default_rho,
        )

        return {
            "default_rho": default_rho,
            "xg_scale": round(xg_scale, 6),
            "home_advantage": round(home_advantage, 6),
            "rho_by_league": {str(k): round(v, 6) for k, v in rho_by_league.items()},
            "calibrated_at": datetime.utcnow().isoformat(),
            "sample_size": len(rows),
            "league_count": len(league_counts),
            "leagues_calibrated": len(rho_by_league),
            "xi": self.xi,
            "exclude_legacy": self.exclude_legacy,
            "log_likelihood": round(final_ll, 4),
            "lookback_days": settings.dc_calibration_lookback_days,
        }

    def save_params(self, params: dict, path: Path | None = None) -> Path:
        out = path or self.params_path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(params, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("dc_params_saved", path=str(out), sample_size=params.get("sample_size"))
        return out

    async def run(
        self,
        session,
        *,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        lookback_days: int | None = None,
    ) -> dict:
        rows = await self.build_dataset(
            session,
            start_date=start_date,
            end_date=end_date,
            lookback_days=lookback_days,
        )
        params = self.fit(rows)
        path = self.save_params(params)
        params["params_path"] = str(path)
        return params
