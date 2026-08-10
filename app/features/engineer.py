"""Advanced feature engineering with strict point-in-time discipline."""

from datetime import datetime, timedelta
from math import exp

import structlog
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import (
    FeatureVector,
    Fixture,
    Injury,
    League,
    MatchStats,
    OddsSnapshot,
    Team,
    Weather,
)
from app.utils.helpers import decision_time, safe_float, utc_now
from app.utils.legacy_data import has_api_odds_exists
from app.utils.odds import implied_probability, proportional_devig

logger = structlog.get_logger()
settings = get_settings()

TEAM_FEATURE_KEYS = [
    "venue_adjusted_xg",
    "weighted_xG_last5",
    "big_chances_created",
    "shots_inside_box_ratio",
    "shot_quality_index",
    "weighted_xGA",
    "defensive_pressure_index",
    "set_piece_conceded",
    "clean_sheet_probability",
    "fatigue_score",
    "rotation_score",
    "injury_impact_score",
    "motivation_score",
    "momentum_score",
    "rolling_form",
]


class FeatureEngineer:
    """Build ROI features using only data available at decision_time."""

    def __init__(
        self,
        session: AsyncSession,
        historical_mode: bool = False,
        exclude_legacy_fixtures: bool = False,
    ):
        self.session = session
        # Backtests must not call live API (lookahead via "last N")
        self.historical_mode = historical_mode
        self.exclude_legacy_fixtures = exclude_legacy_fixtures

    async def build_features(
        self,
        fixture_id: int,
        as_of: datetime | None = None,
        persist: bool = True,
    ) -> dict[str, float]:
        fixture = await self.session.get(Fixture, fixture_id)
        if not fixture:
            raise ValueError(f"Fixture {fixture_id} not found")

        as_of = as_of or decision_time(fixture.fixture_date, settings.decision_hours_before_kickoff)
        if as_of >= fixture.fixture_date:
            as_of = decision_time(fixture.fixture_date, settings.decision_hours_before_kickoff)

        home_feats = await self._team_features(fixture.home_team_id, fixture, is_home=True, as_of=as_of)
        away_feats = await self._team_features(fixture.away_team_id, fixture, is_home=False, as_of=as_of)
        market_feats = await self._market_features(fixture_id, as_of=as_of)
        context_feats = await self._context_features(fixture, as_of=as_of)
        h2h_feats = await self._h2h_features(fixture.home_team_id, fixture.away_team_id, as_of=as_of)

        combined = self._combine_features(fixture, home_feats, away_feats, market_feats, context_feats, h2h_feats)

        if persist:
            await self._persist_features(fixture_id, as_of, combined)
        return combined

    def _combine_features(
        self,
        fixture: Fixture,
        home: dict,
        away: dict,
        market: dict,
        context: dict,
        h2h: dict,
    ) -> dict[str, float]:
        league_strength = context.pop("league_strength", 1.0)
        return {
            **{f"home_{k}": v for k, v in home.items()},
            **{f"away_{k}": v for k, v in away.items()},
            **market,
            **context,
            **h2h,
            "league_strength": league_strength,
            "fixture_id": float(fixture.id),
            "league_id": float(fixture.league_id),
        }

    async def _team_features(
        self,
        team_id: int,
        fixture: Fixture,
        is_home: bool,
        as_of: datetime,
    ) -> dict[str, float]:
        recent_fixtures = await self._get_team_fixtures_before(team_id, as_of, limit=5)

        xg_values: list[float] = []
        xga_values: list[float] = []
        big_chances: list[float] = []
        shots_in_box: list[float] = []
        shots_total: list[float] = []
        goals_scored: list[float] = []
        goals_conceded: list[float] = []
        clean_sheets: list[float] = []
        set_pieces: list[float] = []

        for past in recent_fixtures:
            is_team_home = past.home_team_id == team_id
            gf = past.home_goals if is_team_home else past.away_goals
            ga = past.away_goals if is_team_home else past.home_goals
            goals_scored.append(safe_float(gf, 0))
            goals_conceded.append(safe_float(ga, 0))
            clean_sheets.append(1.0 if ga == 0 else 0.0)

            stats = await self._get_team_match_stats(past.id, team_id, as_of=as_of)
            opp_stats = await self._get_opponent_match_stats(past, team_id, as_of=as_of)
            xg_values.append(stats.get("xg", goals_scored[-1] * 0.9))
            xga_values.append(opp_stats.get("xg", goals_conceded[-1] * 0.9))
            big_chances.append(stats.get("big_chances", 0))
            shots_in_box.append(stats.get("shots_inside_box", 0))
            shots_total.append(max(stats.get("shots_total", 1), 1))
            set_pieces.append(stats.get("set_pieces_conceded", 0))

        weights = [exp(-0.3 * i) for i in range(len(xg_values))]

        def weighted_avg(values: list[float]) -> float:
            if not values:
                return 0.0
            w = weights[: len(values)]
            return sum(v * wt for v, wt in zip(values, w)) / sum(w)

        shots_in_box_ratio = weighted_avg(
            [s / t for s, t in zip(shots_in_box, shots_total)]
        ) if shots_total else 0.5
        base_xg = weighted_avg(xg_values)
        shot_quality = base_xg / max(weighted_avg([float(t) for t in shots_total]), 0.1)

        motivation = await self._motivation_from_results(team_id, fixture.league_id, fixture.season, as_of)
        injuries = await self._injury_impact(team_id, fixture.id, as_of)
        fatigue = await self._fatigue_score(team_id, as_of)
        rotation = await self._rotation_score(fixture.id, team_id, as_of)

        venue_multiplier = 1.08 if is_home else 0.92

        return {
            "venue_adjusted_xg": base_xg * venue_multiplier,
            "weighted_xG_last5": base_xg,
            "big_chances_created": weighted_avg(big_chances),
            "shots_inside_box_ratio": min(1.0, shots_in_box_ratio),
            "shot_quality_index": min(1.0, shot_quality),
            "weighted_xGA": weighted_avg(xga_values),
            "defensive_pressure_index": weighted_avg(xga_values) / max(base_xg, 0.1),
            "set_piece_conceded": weighted_avg(set_pieces),
            "clean_sheet_probability": weighted_avg(clean_sheets),
            "fatigue_score": fatigue,
            "rotation_score": rotation,
            "injury_impact_score": injuries,
            "motivation_score": motivation,
            "momentum_score": self._momentum(goals_scored, goals_conceded),
            "rolling_form": self._rolling_form(goals_scored, goals_conceded),
        }

    async def _get_team_fixtures_before(
        self, team_id: int, as_of: datetime, limit: int = 5
    ) -> list[Fixture]:
        filters = [
            or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
            Fixture.fixture_date < as_of,
            Fixture.status == "FT",
            Fixture.home_goals.isnot(None),
        ]
        if self.exclude_legacy_fixtures:
            filters.append(has_api_odds_exists())

        result = await self.session.execute(
            select(Fixture).where(*filters).order_by(Fixture.fixture_date.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def _get_team_match_stats(
        self, fixture_id: int, team_id: int, as_of: datetime
    ) -> dict:
        fixture = await self.session.get(Fixture, fixture_id)
        if not fixture or fixture.fixture_date >= as_of:
            return {"xg": 1.0, "big_chances": 1, "shots_inside_box": 3, "shots_total": 10, "set_pieces_conceded": 0}

        result = await self.session.execute(
            select(MatchStats).where(
                MatchStats.fixture_id == fixture_id,
                MatchStats.team_id == team_id,
            ).limit(1)
        )
        stat = result.scalars().first()
        if not stat:
            return {"xg": 1.0, "big_chances": 1, "shots_inside_box": 3, "shots_total": 10, "set_pieces_conceded": 0}
        return {
            "xg": stat.xg or 1.0,
            "big_chances": float(stat.big_chances or 0),
            "shots_inside_box": float(stat.shots_inside_box or 0),
            "shots_total": float(stat.shots_total or 1),
            "set_pieces_conceded": float(stat.set_pieces_conceded or 0),
        }

    async def _get_opponent_match_stats(
        self, fixture: Fixture, team_id: int, as_of: datetime
    ) -> dict:
        opp_id = fixture.away_team_id if fixture.home_team_id == team_id else fixture.home_team_id
        return await self._get_team_match_stats(fixture.id, opp_id, as_of=as_of)

    async def _market_features(self, fixture_id: int, as_of: datetime) -> dict[str, float]:
        result = await self.session.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.fixture_id == fixture_id,
                OddsSnapshot.captured_at <= as_of,
            ).order_by(OddsSnapshot.captured_at.asc())
        )
        snapshots = list(result.scalars().all())
        base = {
            "market_overround_1x2": 0.05,
            "odds_change_pct_home": 0.0,
            "sharp_money_signal_home": 0.0,
        }
        if not snapshots:
            return base

        # Latest snapshot per (bookmaker, market, selection) before as_of
        latest: dict[tuple, OddsSnapshot] = {}
        opening: dict[tuple, float] = {}
        for snap in snapshots:
            key = (snap.bookmaker, snap.market, snap.selection, snap.line)
            if key not in opening:
                opening[key] = snap.opening_odds or snap.current_odds
            latest[key] = snap

        # Devig match_winner per bookmaker
        home_fair, draw_fair, away_fair = [], [], []
        over_fair, btts_fair = [], []
        overrounds = []
        home_changes = []

        by_book_market: dict[tuple, list[OddsSnapshot]] = {}
        for key, snap in latest.items():
            bm, market, selection, line = key
            by_book_market.setdefault((bm, market, line), []).append(snap)

        for (bm, market, line), snaps in by_book_market.items():
            valid = [s for s in snaps if s.current_odds and s.current_odds > 1.0]
            if len(valid) < 2:
                continue
            odds_list = [s.current_odds for s in valid]
            fair = proportional_devig(odds_list)
            overrounds.append(sum(implied_probability(o) for o in odds_list) - 1.0)

            if market == "match_winner" and len(fair) >= 2:
                for s, fp in zip(valid, fair):
                    sel = (s.selection or "").lower().strip()
                    if sel in ("home", "1"):
                        home_fair.append(fp)
                    elif sel in ("draw", "x"):
                        draw_fair.append(fp)
                    elif sel in ("away", "2"):
                        away_fair.append(fp)
                for s in valid:
                    sel = (s.selection or "").lower().strip()
                    if sel in ("home", "1"):
                        open_o = opening.get((bm, market, s.selection, line), s.current_odds)
                        if open_o:
                            home_changes.append((s.current_odds - open_o) / open_o)
            elif market == "over_under" and line == 2.5 and len(fair) >= 2:
                for s, fp in zip(valid, fair):
                    sel = (s.selection or "").lower()
                    if "over" in sel:
                        over_fair.append(fp)
            elif market == "btts" and len(fair) >= 2:
                for s, fp in zip(valid, fair):
                    sel = (s.selection or "").lower()
                    if sel in ("yes", "btts yes") or sel == "yes":
                        btts_fair.append(fp)

        def avg(lst: list[float]) -> float | None:
            return sum(lst) / len(lst) if lst else None

        change = avg(home_changes) or 0.0
        out = {
            **base,
            "market_overround_1x2": avg(overrounds) or 0.05,
            "odds_change_pct_home": change,
            "sharp_money_signal_home": -change if change < 0 else 0.0,
        }
        if (v := avg(home_fair)) is not None:
            out["fair_prob_home"] = v
        if (v := avg(draw_fair)) is not None:
            out["fair_prob_draw"] = v
        if (v := avg(away_fair)) is not None:
            out["fair_prob_away"] = v
        if (v := avg(over_fair)) is not None:
            out["fair_prob_over_2_5"] = v
        if (v := avg(btts_fair)) is not None:
            out["fair_prob_btts_yes"] = v
        return out

    async def _context_features(self, fixture: Fixture, as_of: datetime) -> dict[str, float]:
        league = await self.session.get(League, fixture.league_id)
        league_strength = league.strength_rating if league and league.strength_rating else 1.0

        weather_impact = 0.0
        weather = await self.session.execute(
            select(Weather).where(Weather.fixture_id == fixture.id)
        )
        w = weather.scalar_one_or_none()
        if w and w.precipitation and w.precipitation > 5:
            weather_impact = -0.05

        return {
            "league_strength": league_strength,
            "weather_impact": weather_impact,
        }

    async def _h2h_features(
        self, home_id: int, away_id: int, as_of: datetime
    ) -> dict[str, float]:
        result = await self.session.execute(
            select(Fixture).where(
                or_(
                    and_(Fixture.home_team_id == home_id, Fixture.away_team_id == away_id),
                    and_(Fixture.home_team_id == away_id, Fixture.away_team_id == home_id),
                ),
                Fixture.fixture_date < as_of,
                Fixture.status == "FT",
                Fixture.home_goals.isnot(None),
            ).order_by(Fixture.fixture_date.desc()).limit(5)
        )
        matches = result.scalars().all()
        total_goals = sum((m.home_goals or 0) + (m.away_goals or 0) for m in matches)
        return {"h2h_goal_avg": total_goals / max(len(matches), 1)}

    async def _motivation_from_results(
        self, team_id: int, league_id: int, season: int, as_of: datetime
    ) -> float:
        played = await self.session.execute(
            select(Fixture).where(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.league_id == league_id,
                Fixture.season == season,
                Fixture.fixture_date < as_of,
                Fixture.status == "FT",
            )
        )
        fixtures = played.scalars().all()
        if not fixtures:
            return 0.5

        points = rank_score = 0
        for f in fixtures:
            is_home = f.home_team_id == team_id
            gf = f.home_goals if is_home else f.away_goals
            ga = f.away_goals if is_home else f.home_goals
            if gf > ga:
                points += 3
            elif gf == ga:
                points += 1

        avg_points = points / max(len(fixtures), 1)
        rank_score = min(1.0, avg_points / 2.0)
        return 0.3 + rank_score * 0.5

    async def _injury_impact(self, team_id: int, fixture_id: int, as_of: datetime) -> float:
        result = await self.session.execute(
            select(Injury).where(
                Injury.team_id == team_id,
                Injury.fixture_id == fixture_id,
            )
        )
        injuries = result.scalars().all()
        if not injuries:
            return 0.0
        impact = sum(i.severity or 0.5 for i in injuries) / len(injuries)
        key_bonus = sum(0.3 for i in injuries if i.is_key_player)
        return min(1.0, impact + key_bonus)

    async def _rest_days(self, team_id: int, as_of: datetime) -> float:
        result = await self.session.execute(
            select(Fixture).where(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.fixture_date < as_of,
                Fixture.status == "FT",
            ).order_by(Fixture.fixture_date.desc()).limit(1)
        )
        last = result.scalar_one_or_none()
        if not last:
            return 7.0
        return max(1.0, (as_of - last.fixture_date).days)

    async def _schedule_density(self, team_id: int, as_of: datetime) -> float:
        window_start = as_of - timedelta(days=14)
        result = await self.session.execute(
            select(Fixture).where(
                or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
                Fixture.fixture_date >= window_start,
                Fixture.fixture_date < as_of,
            )
        )
        matches = len(result.scalars().all())
        return min(matches / 5.0, 1.0)

    async def _fatigue_score(self, team_id: int, as_of: datetime) -> float:
        rest = await self._rest_days(team_id, as_of)
        density = await self._schedule_density(team_id, as_of)
        return min(1.0, max(0.0, 1.0 - rest / 7.0) * 0.6 + density * 0.4)

    async def _rotation_score(
        self, fixture_id: int, team_id: int, as_of: datetime
    ) -> float:
        from app.database.models import Lineup

        fixture = await self.session.get(Fixture, fixture_id)
        if not fixture:
            return 0.0
        # Lineups typically available ~1h before kickoff
        lineup_cutoff = fixture.fixture_date - timedelta(hours=1)
        if as_of < lineup_cutoff:
            return 0.0

        result = await self.session.execute(
            select(Lineup).where(Lineup.fixture_id == fixture_id, Lineup.team_id == team_id)
        )
        lineup = result.scalar_one_or_none()
        if not lineup:
            return 0.0
        return min(1.0, (lineup.rotation_count or 0) / 5.0)

    def _momentum(self, scored: list[float], conceded: list[float]) -> float:
        if not scored:
            return 0.5
        recent = [(s - c) for s, c in zip(scored[:3], conceded[:3])]
        return 0.5 + sum(recent) / (len(recent) * 6)

    def _rolling_form(self, scored: list[float], conceded: list[float]) -> float:
        if not scored:
            return 0.5
        points = []
        for s, c in zip(scored, conceded):
            if s > c:
                points.append(3)
            elif s == c:
                points.append(1)
            else:
                points.append(0)
        return sum(points) / (len(points) * 3)

    async def _persist_features(
        self, fixture_id: int, as_of: datetime, features: dict
    ) -> None:
        existing = await self.session.execute(
            select(FeatureVector).where(
                FeatureVector.fixture_id == fixture_id,
                FeatureVector.as_of_datetime == as_of,
            )
        )
        rows = existing.scalars().all()
        if len(rows) > 1:
            fv = rows[0]
            for dup in rows[1:]:
                await self.session.delete(dup)
        elif rows:
            fv = rows[0]
        else:
            fv = None
        if fv:
            fv.features = features
            fv.computed_at = utc_now()
        else:
            self.session.add(
                FeatureVector(fixture_id=fixture_id, as_of_datetime=as_of, features=features)
            )
        await self.session.commit()

    async def build_batch(
        self,
        fixture_ids: list[int],
        as_of_map: dict[int, datetime] | None = None,
        persist: bool = True,
    ) -> dict[int, dict]:
        results = {}
        for fid in fixture_ids:
            try:
                as_of = as_of_map.get(fid) if as_of_map else None
                results[fid] = await self.build_features(fid, as_of=as_of, persist=persist)
            except Exception as e:
                logger.warning("feature_build_failed", fixture_id=fid, error=str(e))
        return results

    async def load_batch(
        self,
        fixture_ids: list[int],
        as_of_map: dict[int, datetime] | None = None,
    ) -> dict[int, dict]:
        """Load precomputed features from DB (phase 2 / live mode)."""
        results: dict[int, dict] = {}
        for fid in fixture_ids:
            as_of = as_of_map.get(fid) if as_of_map else None
            if as_of is None:
                continue
            exact = await self.session.execute(
                select(FeatureVector).where(
                    FeatureVector.fixture_id == fid,
                    FeatureVector.as_of_datetime == as_of,
                ).order_by(FeatureVector.computed_at.desc()).limit(1)
            )
            fv = exact.scalar_one_or_none()
            if fv is None:
                fallback = await self.session.execute(
                    select(FeatureVector)
                    .where(
                        FeatureVector.fixture_id == fid,
                        FeatureVector.as_of_datetime <= as_of,
                    )
                    .order_by(FeatureVector.as_of_datetime.desc())
                    .limit(1)
                )
                fv = fallback.scalar_one_or_none()
            if fv:
                results[fid] = fv.features
            else:
                logger.warning("feature_cache_miss", fixture_id=fid, as_of=as_of.isoformat())
        return results
