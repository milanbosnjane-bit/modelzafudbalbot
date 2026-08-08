"""Walk-forward backtest with point-in-time features and realistic execution."""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import structlog
from sqlalchemy import select

from app.config import get_settings
from app.database.models import BacktestRun, OddsSnapshot
from app.database.session import AsyncSessionLocal
from app.features.engineer import FeatureEngineer
from app.predictions.pick_selector import PickSelectionEngine
from app.utils.helpers import closing_line_value, decision_time
from app.utils.legacy_data import LEGACY_BOOKMAKERS, has_api_odds_exists

logger = structlog.get_logger()
settings = get_settings()


@dataclass
class BacktestResult:
    total_bets: int
    total_staked: float
    total_profit: float
    roi_pct: float
    avg_clv: float
    avg_ev: float
    win_rate: float
    sharpe_ratio: float
    clv_coverage_pct: float
    picks: list[dict] = field(default_factory=list)


class BacktestEngine:
    """
    Walk-forward simulation:
    - historical_mode features (DB only, no live API)
    - odds at T-1h (median, not max)
    - slippage applied
    - dynamic O/U line from pick metadata
    """

    def __init__(
        self,
        slippage_pct: float | None = None,
        decision_hours: float | None = None,
        exclude_legacy: bool = True,
    ):
        self.slippage_pct = slippage_pct if slippage_pct is not None else settings.backtest_slippage_pct
        self.decision_hours = decision_hours or settings.decision_hours_before_kickoff
        self.exclude_legacy = exclude_legacy

    async def run(
        self,
        start_date: datetime,
        end_date: datetime,
        name: str = "backtest",
    ) -> BacktestResult:
        async with AsyncSessionLocal() as session:
            from app.database.models import Fixture

            fixture_filters = [
                Fixture.fixture_date >= start_date,
                Fixture.fixture_date <= end_date,
                Fixture.status.in_(["FT", "AET", "PEN"]),
                Fixture.home_goals.isnot(None),
            ]
            if self.exclude_legacy:
                fixture_filters.append(has_api_odds_exists())
            if settings.exclude_league_ids:
                fixture_filters.append(
                    ~Fixture.league_id.in_(settings.exclude_league_ids)
                )

            result = await session.execute(
                select(Fixture)
                .join(OddsSnapshot, OddsSnapshot.fixture_id == Fixture.id)
                .where(*fixture_filters)
                .distinct()
            )
            fixtures = result.scalars().all()

            if self.exclude_legacy:
                logger.info(
                    "backtest_legacy_excluded",
                    api_fixtures=len(fixtures),
                    bookmakers_excluded=sorted(LEGACY_BOOKMAKERS),
                )
            elif not fixtures:
                logger.warning("backtest_no_fixtures_in_range")

            engineer = FeatureEngineer(
                session, historical_mode=True, exclude_legacy_fixtures=self.exclude_legacy
            )
            selector = PickSelectionEngine(session, exclude_legacy_bookmakers=self.exclude_legacy)

            daily_groups: dict[str, list] = defaultdict(list)
            as_of_map: dict[int, datetime] = {}

            for fixture in fixtures:
                date_key = fixture.fixture_date.strftime("%Y-%m-%d")
                daily_groups[date_key].append(fixture.id)
                as_of_map[fixture.id] = decision_time(fixture.fixture_date, self.decision_hours)

            all_picks = []
            total_days = len(daily_groups)

            for day_idx, (date_key, fixture_ids) in enumerate(sorted(daily_groups.items()), start=1):
                features_map = await engineer.load_batch(fixture_ids, as_of_map=as_of_map)
                missing = [fid for fid in fixture_ids if fid not in features_map]
                if missing:
                    built = await engineer.build_batch(
                        missing, as_of_map=as_of_map, persist=True
                    )
                    features_map.update(built)
                if day_idx % 30 == 0 or day_idx == total_days:
                    logger.info(
                        "backtest_progress",
                        day=day_idx,
                        total_days=total_days,
                        date=date_key,
                        picks_so_far=len(all_picks),
                    )
                candidates = await selector.generate_candidates(
                    fixture_ids, features_map, as_of_map=as_of_map
                )
                picks = await selector.select_top_picks(candidates)

                for pick in picks:
                    effective_odds = pick.odds * (1.0 - self.slippage_pct)
                    outcome = await self._resolve_outcome(session, pick)
                    clv, clv_found = await self._calculate_clv(session, pick)
                    profit = self._calculate_profit(pick, outcome, effective_odds)

                    all_picks.append({
                        "date": date_key,
                        "fixture_id": pick.fixture_id,
                        "market": pick.market,
                        "selection": pick.selection,
                        "line": pick.line,
                        "odds": pick.odds,
                        "effective_odds": effective_odds,
                        "probability": pick.probability,
                        "ev": pick.expected_value,
                        "confidence": pick.confidence,
                        "stake": pick.stake_units,
                        "outcome": outcome,
                        "profit": profit,
                        "clv": clv,
                        "clv_found": clv_found,
                    })

            total_staked = sum(p["stake"] for p in all_picks)
            total_profit = sum(p["profit"] for p in all_picks)
            clvs = [p["clv"] for p in all_picks if p["clv"] is not None]
            clv_found_count = sum(1 for p in all_picks if p["clv_found"])
            evs = [p["ev"] for p in all_picks]
            wins = sum(1 for p in all_picks if p["outcome"] == "win")

            roi = (total_profit / total_staked * 100) if total_staked else 0
            avg_clv = sum(clvs) / len(clvs) if clvs else 0
            avg_ev = sum(evs) / len(evs) if evs else 0
            win_rate = wins / len(all_picks) if all_picks else 0
            sharpe = self._daily_sharpe(all_picks)

            bt_result = BacktestResult(
                total_bets=len(all_picks),
                total_staked=total_staked,
                total_profit=total_profit,
                roi_pct=roi,
                avg_clv=avg_clv,
                avg_ev=avg_ev,
                win_rate=win_rate,
                sharpe_ratio=sharpe,
                clv_coverage_pct=(clv_found_count / len(all_picks)) if all_picks else 0,
                picks=all_picks,
            )

            record = BacktestRun(
                name=name,
                start_date=start_date,
                end_date=end_date,
                total_bets=bt_result.total_bets,
                total_staked=bt_result.total_staked,
                total_profit=bt_result.total_profit,
                roi_pct=bt_result.roi_pct,
                avg_clv=bt_result.avg_clv,
                avg_ev=bt_result.avg_ev,
                win_rate=bt_result.win_rate,
                sharpe_ratio=bt_result.sharpe_ratio,
                config={
                    "slippage_pct": self.slippage_pct,
                    "decision_hours": self.decision_hours,
                    "clv_coverage_pct": bt_result.clv_coverage_pct,
                    "exclude_legacy": self.exclude_legacy,
                },
                results={"picks": all_picks[:100]},
            )
            session.add(record)
            await session.commit()

            logger.info(
                "backtest_complete",
                roi=f"{roi:.2f}%",
                avg_clv=f"{avg_clv:.4f}",
                bets=len(all_picks),
                clv_coverage=f"{bt_result.clv_coverage_pct:.0%}",
            )
            return bt_result

    async def _resolve_outcome(self, session, pick) -> str:
        from app.database.models import Fixture

        fixture = await session.get(Fixture, pick.fixture_id)
        if not fixture or fixture.home_goals is None:
            return "void"

        hg, ag = fixture.home_goals, fixture.away_goals

        if pick.market == "btts":
            if "yes" in pick.selection.lower():
                return "win" if hg > 0 and ag > 0 else "lose"
            return "win" if hg == 0 or ag == 0 else "lose"

        if pick.market == "over_under":
            total = hg + ag
            line = pick.line if pick.line is not None else 2.5
            if "over" in pick.selection.lower():
                if total == line:
                    return "push"
                return "win" if total > line else "lose"
            if total == line:
                return "push"
            return "win" if total < line else "lose"

        if pick.market == "match_winner":
            sel = pick.selection.lower()
            if sel == "home" and hg > ag:
                return "win"
            if sel == "away" and ag > hg:
                return "win"
            if sel == "draw" and hg == ag:
                return "win"
            return "lose"

        return "void"

    async def _calculate_clv(self, session, pick) -> tuple[float | None, bool]:
        filters = [
            OddsSnapshot.fixture_id == pick.fixture_id,
            OddsSnapshot.market == pick.market,
            OddsSnapshot.selection == pick.selection,
            OddsSnapshot.is_closing == True,
        ]
        if self.exclude_legacy:
            filters.append(OddsSnapshot.bookmaker.not_in(tuple(LEGACY_BOOKMAKERS)))

        result = await session.execute(
            select(OddsSnapshot).where(*filters).order_by(OddsSnapshot.captured_at.desc())
        )
        closing = result.scalars().first()
        if not closing or not closing.closing_odds:
            return None, False
        clv = closing_line_value(
            pick.odds,
            closing.closing_odds,
            closing_fair_prob=closing.fair_prob,
        )
        return clv, True

    def _calculate_profit(self, pick, outcome: str, effective_odds: float) -> float:
        if outcome == "win":
            return pick.stake_units * (effective_odds - 1)
        if outcome == "lose":
            return -pick.stake_units
        return 0.0

    def _daily_sharpe(self, picks: list[dict]) -> float:
        daily_pnl: dict[str, float] = defaultdict(float)
        for p in picks:
            daily_pnl[p["date"]] += p["profit"]
        returns = list(daily_pnl.values())
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns)
        std = arr.std()
        if std == 0:
            return 0.0
        return float(arr.mean() / std * np.sqrt(252))
