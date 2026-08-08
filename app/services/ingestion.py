"""Data ingestion service - collects and stores all football data."""

import re
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database.models import (
    Fixture,
    Injury,
    League,
    Lineup,
    MatchStats,
    OddsSnapshot,
    Standing,
    Team,
    Weather,
)
from app.services.api_football import APIFootballClient
from app.services.odds_api import OddsAPIClient
from app.services.weather_api import WeatherClient
from app.utils.helpers import implied_probability, odds_change_pct, safe_float, utc_now, football_season_candidates, last_completed_football_season, normalize_selection
from app.utils.clv_metrics import fair_prob_matches_closing_odds
from app.utils.odds import market_overround, proportional_devig

logger = structlog.get_logger()
settings = get_settings()

# Exact API-Football bet names (ids 1, 5, 8) for the three full-time markets the
# models predict. Names are matched in full: every variant carries a suffix
# ("Goals Over/Under First Half", "Both Teams Score - First Half") or a different
# subject ("Corners Over Under", "Home Team Total Goals(1st Half)"), so exact
# matching keeps them out while a substring test pulled them in.
FULL_TIME_MARKET_NAMES = {
    "match winner": "match_winner",
    "goals over/under": "over_under",
    "both teams score": "btts",
    "both teams to score": "btts",
}

MATCH_WINNER_VALUES = {
    "home": "Home",
    "draw": "Draw",
    "away": "Away",
    "1": "Home",
    "x": "Draw",
    "2": "Away",
}

BTTS_VALUES = {"yes": "Yes", "no": "No"}

ALLOWED_OU_LINES = frozenset({1.5, 2.5, 3.5})
OU_SELECTION_RE = re.compile(r"^(over|under)\s+(\d+(?:\.\d+)?)$")


class DataIngestionService:
    """Scheduled jobs collect fixtures, odds, stats, lineups, injuries, standings."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.api_football = APIFootballClient()
        self.odds_api = OddsAPIClient()
        self.weather = WeatherClient()

    async def ingest_leagues(self, season: int | None = None) -> int:
        season = season or datetime.utcnow().year
        count = 0
        for league_id in settings.league_ids:
            if league_id in settings.exclude_league_ids:
                continue
            data = await self.api_football.get_leagues(league_id, season)
            if not data:
                continue
            league_info = data.get("league", {})
            country = data.get("country", {}).get("name")
            stmt = insert(League).values(
                id=league_id,
                name=league_info.get("name", f"League {league_id}"),
                country=country,
                season=season,
            ).on_conflict_do_update(
                index_elements=["id"],
                set_={"name": league_info.get("name"), "country": country, "updated_at": utc_now()},
            )
            await self.session.execute(stmt)
            count += 1
        await self.session.commit()
        return count

    async def ingest_fixtures(self, date: str | None = None, season: int | None = None) -> int:
        """Ingest fixtures za dati datum — 3-step prioritetna logika.

        Korak 1 (1 API poziv): GET /fixtures?date=...
          → sve utakmice tog dana u svetu

        Korak 2a — PRIORITETNE EVROPSKE LIGE
          Filtrira priority_league_ids. Ako ima mečeva → koristi samo te.

        Korak 2b — TRACKED LISTE (league_ids)
          Ako nema prioritetnih → filtrira sve league_ids iz config.py.

        Korak 2c — OPEN FALLBACK
          Ako ni tracked nema ništa → uzima prvih max_open_fixtures mečeva
          sortiranih po broju bukmejkera (likviditet), bez filtriranja po ligi.
          Budžet: ~80 mečeva × 2 API poziva ≈ 160 poziva od 7500 dnevnih.

        Korak 3 — Per-league fallback (samo ako je ceo API odgovor prazan)
          Pokušava per-league+season upite za off-season edge case.
        """
        date = date or datetime.utcnow().strftime("%Y-%m-%d")
        priority_set = set(settings.priority_league_ids)
        league_set = set(settings.league_ids)
        seen_ids: set[int] = set()
        count = 0

        # ── Korak 1: jedan API poziv za ceo dan ───────────────────────────────
        all_day = await self.api_football.get_fixtures_by_date(date)

        if all_day:
            # Korak 2a: prioritetne evropske lige
            priority_items = [
                item for item in all_day
                if item.get("league", {}).get("id") in priority_set
            ]
            if priority_items:
                for item in priority_items:
                    league_id = item.get("league", {}).get("id")
                    if await self._upsert_fixture_item(item, league_id):
                        fid = item.get("fixture", {}).get("id")
                        if fid and fid not in seen_ids:
                            seen_ids.add(fid)
                            count += 1
                logger.info(
                    "ingested_fixtures_priority",
                    date=date,
                    count=count,
                    leagues=sorted({i.get("league", {}).get("id") for i in priority_items}),
                )

            # Korak 2b: tracked lige — uvek dodaj (ne samo kad je priority prazan)
            tracked_items = [
                item for item in all_day
                if item.get("league", {}).get("id") in league_set
            ]
            tracked_added = 0
            for item in tracked_items:
                league_id = item.get("league", {}).get("id")
                if await self._upsert_fixture_item(item, league_id):
                    fid = item.get("fixture", {}).get("id")
                    if fid and fid not in seen_ids:
                        seen_ids.add(fid)
                        count += 1
                        tracked_added += 1
            if tracked_added:
                logger.info(
                    "ingested_fixtures_tracked",
                    date=date,
                    count=tracked_added,
                    total=count,
                    leagues=sorted({i.get("league", {}).get("id") for i in tracked_items}),
                )

            # Korak 2c: open fallback — dopuni do max_open_fixtures likvidnim mečevima
            if count < settings.max_open_fixtures:
                def _bookie_count(item: dict) -> int:
                    return len(item.get("bookmakers") or [])

                open_pool = sorted(all_day, key=_bookie_count, reverse=True)
                open_added = 0
                for item in open_pool:
                    if count >= settings.max_open_fixtures:
                        break
                    league_id = item.get("league", {}).get("id")
                    if not league_id:
                        continue
                    fid = item.get("fixture", {}).get("id")
                    if not fid or fid in seen_ids:
                        continue
                    if await self._upsert_fixture_item(item, league_id):
                        seen_ids.add(fid)
                        count += 1
                        open_added += 1

                if open_added:
                    open_leagues = sorted({
                        item.get("league", {}).get("id") for item in open_pool
                        if item.get("fixture", {}).get("id") in seen_ids
                    })
                    logger.info(
                        "ingested_fixtures_open_fallback",
                        date=date,
                        added=open_added,
                        total=count,
                        leagues=open_leagues[:20],
                        max_cap=settings.max_open_fixtures,
                    )

        # ── Korak 3: per-league+season fallback (samo ako API vratio 0 ukupno) ─
        if count == 0:
            logger.info("ingested_fixtures_per_league_fallback", date=date)
            for league_id in settings.league_ids:
                for try_season in football_season_candidates():
                    fixtures = await self.api_football.get_fixtures(
                        league_id, try_season, date=date
                    )
                    for item in fixtures:
                        fid = item.get("fixture", {}).get("id")
                        if not fid or fid in seen_ids:
                            continue
                        if await self._upsert_fixture_item(item, league_id):
                            seen_ids.add(fid)
                            count += 1

        await self.session.commit()
        logger.info("ingested_fixtures", date=date, count=count)
        return count

    _FINISHED_STATUSES = frozenset({"FT", "AET", "PEN", "AWD", "WO"})

    async def ingest_league_season_history(
        self,
        league_id: int,
        season: int,
        *,
        include_stats: bool = True,
    ) -> dict:
        """Povuci celu prošlu sezonu za jednu ligu (fixtures + opciono xG statistika)."""
        import asyncio

        if league_id in settings.exclude_league_ids:
            return {"league_id": league_id, "season": season, "fixtures": 0, "stats": 0, "skipped": True}

        league_data = await self.api_football.get_leagues(league_id, season)
        if league_data:
            league_info = league_data.get("league", {})
            country = league_data.get("country", {}).get("name")
            stmt = insert(League).values(
                id=league_id,
                name=league_info.get("name", f"League {league_id}"),
                country=country,
                season=season,
            ).on_conflict_do_update(
                index_elements=["id"],
                set_={"name": league_info.get("name"), "country": country, "updated_at": utc_now()},
            )
            await self.session.execute(stmt)
            await self.session.commit()

        items = await self.api_football.get_fixtures_all_pages(league_id, season)
        fixture_count = 0
        stats_count = 0

        for item in items:
            if await self._upsert_fixture_item(item, league_id):
                fixture_count += 1

        await self.session.commit()

        if include_stats:
            for item in items:
                status = item.get("fixture", {}).get("status", {}).get("short", "")
                if status not in self._FINISHED_STATUSES:
                    continue
                fixture_id = item.get("fixture", {}).get("id")
                if not fixture_id:
                    continue
                existing = await self.session.execute(
                    select(MatchStats.id).where(MatchStats.fixture_id == fixture_id).limit(1)
                )
                if existing.scalar_one_or_none():
                    continue
                try:
                    stats_count += await self.ingest_match_stats(fixture_id)
                except Exception as exc:
                    logger.warning(
                        "backfill_stats_failed",
                        fixture_id=fixture_id,
                        league_id=league_id,
                        error=str(exc),
                    )
                await asyncio.sleep(0.15)

        try:
            await self.ingest_standings(league_id, season)
        except Exception as exc:
            logger.warning("backfill_standings_failed", league_id=league_id, season=season, error=str(exc))

        logger.info(
            "ingested_league_season_history",
            league_id=league_id,
            season=season,
            fixtures=fixture_count,
            stats=stats_count,
            api_items=len(items),
        )
        return {
            "league_id": league_id,
            "season": season,
            "fixtures": fixture_count,
            "stats": stats_count,
            "api_items": len(items),
        }

    async def ingest_team_recent_history(
        self,
        team_id: int,
        *,
        last: int = 10,
    ) -> dict:
        """Povuci poslednje FT mečeve tima sa API-ja (on-demand pre odbacivanja picka)."""
        import asyncio

        seen: set[int] = set()
        fixture_count = 0
        stats_count = 0

        for season in football_season_candidates():
            items = await self.api_football.get_team_fixtures_fresh(
                team_id, season, last=last
            )
            for item in items:
                status = item.get("fixture", {}).get("status", {}).get("short", "")
                if status not in self._FINISHED_STATUSES:
                    continue
                fixture_id = item.get("fixture", {}).get("id")
                if not fixture_id or fixture_id in seen:
                    continue
                league_id = item.get("league", {}).get("id")
                if not league_id or league_id in settings.exclude_league_ids:
                    continue
                if await self._upsert_fixture_item(item, league_id):
                    seen.add(fixture_id)
                    fixture_count += 1
                    existing = await self.session.execute(
                        select(MatchStats.id).where(MatchStats.fixture_id == fixture_id).limit(1)
                    )
                    if not existing.scalar_one_or_none():
                        try:
                            stats_count += await self.ingest_match_stats(fixture_id)
                        except Exception as exc:
                            logger.warning(
                                "on_demand_stats_failed",
                                team_id=team_id,
                                fixture_id=fixture_id,
                                error=str(exc),
                            )
                    await asyncio.sleep(0.1)
            if fixture_count >= last:
                break

        await self.session.commit()
        logger.info(
            "ingested_team_recent_history",
            team_id=team_id,
            fixtures=fixture_count,
            stats=stats_count,
        )
        return {"fixtures": fixture_count, "stats": stats_count}

    async def _upsert_fixture_item(self, item: dict, league_id: int) -> bool:
        if league_id in settings.exclude_league_ids:
            return False

        fixture_data = item.get("fixture", {})
        teams = item.get("teams", {})
        goals = item.get("goals", {})
        league_info = item.get("league", {})

        home_id = teams.get("home", {}).get("id")
        away_id = teams.get("away", {}).get("id")
        if not home_id or not away_id or not fixture_data.get("id"):
            return False

        await self._upsert_team(home_id, teams["home"]["name"], league_id)
        await self._upsert_team(away_id, teams["away"]["name"], league_id)

        fixture_id = fixture_data["id"]
        fixture_date = datetime.fromisoformat(
            fixture_data["date"].replace("Z", "+00:00")
        ).replace(tzinfo=None)

        stmt = insert(Fixture).values(
            id=fixture_id,
            league_id=league_id,
            season=league_info.get("season", football_season_candidates()[0]),
            home_team_id=home_id,
            away_team_id=away_id,
            fixture_date=fixture_date,
            status=fixture_data.get("status", {}).get("short", "NS"),
            home_goals=goals.get("home"),
            away_goals=goals.get("away"),
            venue=fixture_data.get("venue", {}).get("name"),
            referee=fixture_data.get("referee"),
            round=league_info.get("round"),
        ).on_conflict_do_update(
            index_elements=["id"],
            set_={
                "status": fixture_data.get("status", {}).get("short", "NS"),
                "home_goals": goals.get("home"),
                "away_goals": goals.get("away"),
                "updated_at": utc_now(),
            },
        )
        await self.session.execute(stmt)
        return True

    async def _upsert_team(self, team_id: int, name: str, league_id: int) -> None:
        stmt = insert(Team).values(id=team_id, name=name, league_id=league_id).on_conflict_do_update(
            index_elements=["id"],
            set_={"name": name, "updated_at": utc_now()},
        )
        await self.session.execute(stmt)

    async def ingest_odds(self, fixture_id: int) -> int:
        odds_data = await self.api_football.get_odds(fixture_id)
        if not odds_data:
            return 0

        captured_at = utc_now()
        supported = set(settings.supported_markets)

        # One query for all prior opening odds on this fixture
        existing_result = await self.session.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.fixture_id == fixture_id,
            ).order_by(OddsSnapshot.captured_at.asc())
        )
        opening_map: dict[tuple, float] = {}
        for snap in existing_result.scalars().all():
            key = (snap.bookmaker, snap.market, snap.selection)
            if key not in opening_map:
                opening_map[key] = snap.opening_odds or snap.current_odds

        market_groups: dict[tuple, list[tuple[str, float]]] = {}
        parsed_rows: list[dict] = []
        # One row per (bookmaker, market, selection, line): a bookmaker occasionally
        # repeats an outcome, and a duplicate would add a phantom leg to the de-vig group.
        seen: set[tuple] = set()
        skipped_markets = 0

        for entry in odds_data:
            for bm in entry.get("bookmakers", []):
                bm_name = bm.get("name", "unknown")
                for bet in bm.get("bets", []):
                    market = self._normalize_market(bet.get("name", ""))
                    if market is None or market not in supported:
                        skipped_markets += 1
                        continue
                    for value in bet.get("values", []):
                        current_odds = safe_float(value.get("odd", 0))
                        if current_odds <= 1.0:
                            continue
                        canonical = self._canonical_selection(
                            market, str(value.get("value", "")).strip()
                        )
                        if canonical is None:
                            continue
                        selection, line = canonical

                        dedupe_key = (bm_name, market, selection, line)
                        if dedupe_key in seen:
                            continue
                        seen.add(dedupe_key)

                        key = (bm_name, market, line)
                        market_groups.setdefault(key, []).append((selection, current_odds))
                        parsed_rows.append({
                            "bm_name": bm_name,
                            "market": market,
                            "selection": selection,
                            "line": line,
                            "current_odds": current_odds,
                            "key": key,
                        })

        fair_probs: dict[tuple, dict[str, float]] = {}
        group_overround: dict[tuple, float] = {}
        for key, outcomes in market_groups.items():
            odds_list = [o for _, o in outcomes]
            fair = proportional_devig(odds_list)
            group_overround[key] = market_overround(odds_list)
            fair_probs[key] = {sel: fair[i] for i, (sel, _) in enumerate(outcomes) if i < len(fair)}

        count = 0
        for row in parsed_rows:
            lookup = (row["bm_name"], row["market"], row["selection"])
            opening = opening_map.get(lookup, row["current_odds"])
            key = row["key"]
            current_odds = row["current_odds"]
            raw_implied = implied_probability(current_odds)
            fair_p = fair_probs.get(key, {}).get(row["selection"])
            if fair_p is None:
                fair_p = raw_implied
            elif not fair_prob_matches_closing_odds(fair_p, current_odds):
                logger.warning(
                    "FAIR_PROB_INVALID",
                    fixture_id=fixture_id,
                    bookmaker=row["bm_name"],
                    market=row["market"],
                    selection=row["selection"],
                    odds=current_odds,
                    fair_prob=fair_p,
                )
                fair_p = None

            self.session.add(OddsSnapshot(
                fixture_id=fixture_id,
                bookmaker=row["bm_name"],
                market=row["market"],
                selection=row["selection"],
                line=row["line"],
                opening_odds=opening,
                current_odds=current_odds,
                implied_prob=raw_implied,
                fair_prob=fair_p,
                market_overround=group_overround.get(key),
                odds_change_pct=odds_change_pct(opening, current_odds),
                captured_at=captured_at,
            ))
            count += 1

        if count:
            await self.session.commit()
        logger.info(
            "ingested_odds",
            fixture_id=fixture_id,
            snapshots=count,
            groups=len(market_groups),
            ignored_bets=skipped_markets,
        )
        return count

    def _parse_ou_line(self, selection: str, sel_norm: str) -> float | None:
        for part in str(selection).split():
            try:
                return float(part)
            except ValueError:
                continue
        return None

    def _normalize_market(self, market_name: str) -> str | None:
        """Map a bookmaker bet name to an internal market, or None to ignore it.

        Exact-name allowlist by design. Substring matching used to pull first-half
        goals, team totals, corners, cards and combo bets into these three keys,
        so several different bets landed on one
        (bookmaker, market, selection, line) row and destroyed the de-vig groups.
        An unknown name must be dropped rather than guessed.
        """
        return FULL_TIME_MARKET_NAMES.get(" ".join(market_name.lower().split()))

    def _canonical_selection(
        self, market: str, selection: str
    ) -> tuple[str, float | None] | None:
        """Canonical (selection, line) for a full-time market, or None if unusable."""
        sel_norm = normalize_selection(selection)

        if market == "match_winner":
            canonical = MATCH_WINNER_VALUES.get(sel_norm)
            return (canonical, None) if canonical else None

        if market == "btts":
            canonical = BTTS_VALUES.get(sel_norm)
            return (canonical, None) if canonical else None

        if market == "over_under":
            # Strictly "over <line>" / "under <line>". Anything with extra tokens is a
            # combo such as "Away/Over 2.5" and must not be read as a plain total.
            match = OU_SELECTION_RE.match(sel_norm)
            if not match:
                return None
            try:
                line = float(match.group(2))
            except ValueError:
                return None
            if line not in ALLOWED_OU_LINES:
                return None
            return f"{match.group(1).capitalize()} {line:g}", line

        return None

    async def ingest_match_stats(self, fixture_id: int) -> int:
        stats_data = await self.api_football.get_fixture_statistics(fixture_id)
        count = 0

        fixture = await self.session.get(Fixture, fixture_id)
        if not fixture:
            return 0

        team_xg: dict[int, float] = {}
        records: list[MatchStats] = []

        for team_stats in stats_data:
            team_info = team_stats.get("team", {})
            team_id = team_info.get("id")
            is_home = team_id == fixture.home_team_id
            stat_map = {s["type"]: s["value"] for s in team_stats.get("statistics", [])}

            def stat(key: str) -> int | None:
                val = stat_map.get(key)
                if val is None:
                    return None
                if isinstance(val, str) and "%" in val:
                    return int(val.replace("%", ""))
                return int(val) if val else None

            xg_val = safe_float(stat_map.get("expected_goals") or stat_map.get("Expected Goals"))
            team_xg[team_id] = xg_val
            poss_raw = stat_map.get("Ball Possession")

            record = MatchStats(
                fixture_id=fixture_id,
                team_id=team_id,
                is_home=is_home,
                shots_total=stat("Total Shots"),
                shots_on_target=stat("Shots on Goal"),
                shots_inside_box=stat("Shots insidebox"),
                possession_pct=safe_float(str(poss_raw or "0").replace("%", "")),
                corners=stat("Corner Kicks"),
                fouls=stat("Fouls"),
                yellow_cards=stat("Yellow Cards"),
                red_cards=stat("Red Cards"),
                big_chances=stat("Big Chances Created") or stat("Big Chances"),
                xg=xg_val or None,
            )
            records.append(record)

        for record in records:
            opp_id = fixture.away_team_id if record.team_id == fixture.home_team_id else fixture.home_team_id
            record.xga = team_xg.get(opp_id)
            self.session.add(record)
            count += 1

        await self.session.commit()
        return count

    async def ingest_lineups(self, fixture_id: int) -> int:
        from sqlalchemy import delete

        lineups = await self.api_football.get_lineups(fixture_id)
        if not lineups:
            return 0

        await self.session.execute(delete(Lineup).where(Lineup.fixture_id == fixture_id))
        count = 0
        for lineup in lineups:
            team_id = lineup.get("team", {}).get("id")
            start_xi = lineup.get("startXI", [])
            subs = lineup.get("substitutes", [])
            record = Lineup(
                fixture_id=fixture_id,
                team_id=team_id,
                formation=lineup.get("formation"),
                starting_xi={"players": start_xi},
                substitutes={"players": subs},
                rotation_count=len(subs),
            )
            self.session.add(record)
            count += 1
        await self.session.commit()
        return count

    async def ingest_injuries(self, fixture_id: int) -> int:
        from sqlalchemy import delete

        injuries = await self.api_football.get_injuries(fixture_id)
        await self.session.execute(delete(Injury).where(Injury.fixture_id == fixture_id))
        count = 0
        for injury in injuries:
            player = injury.get("player", {})
            team_id = injury.get("team", {}).get("id")
            record = Injury(
                fixture_id=fixture_id,
                team_id=team_id,
                player_name=player.get("name", "Unknown"),
                injury_type=player.get("type"),
                severity=0.7 if player.get("reason") else 0.5,
                is_key_player=False,
            )
            self.session.add(record)
            count += 1
        await self.session.commit()
        return count

    async def ingest_standings(self, league_id: int, season: int) -> int:
        standings_data = await self.api_football.get_standings(league_id, season)
        count = 0
        for entry in standings_data:
            for group in entry.get("league", {}).get("standings", [[]]):
                for row in group:
                    team_id = row.get("team", {}).get("id")
                    stmt = insert(Standing).values(
                        league_id=league_id,
                        season=season,
                        team_id=team_id,
                        rank=row.get("rank", 0),
                        points=row.get("points", 0),
                        played=row.get("all", {}).get("played", 0),
                        won=row.get("all", {}).get("win", 0),
                        draw=row.get("all", {}).get("draw", 0),
                        lost=row.get("all", {}).get("lose", 0),
                        goals_for=row.get("all", {}).get("goals", {}).get("for", 0),
                        goals_against=row.get("all", {}).get("goals", {}).get("against", 0),
                        form=row.get("form"),
                    ).on_conflict_do_update(
                        index_elements=["league_id", "season", "team_id"],
                        set_={
                            "rank": row.get("rank"),
                            "points": row.get("points"),
                            "form": row.get("form"),
                            "updated_at": utc_now(),
                        },
                    )
                    await self.session.execute(stmt)
                    count += 1
        await self.session.commit()
        return count

    async def ingest_weather(self, fixture_id: int) -> bool:
        fixture = await self.session.get(Fixture, fixture_id)
        if not fixture:
            return False
        home_team = await self.session.get(Team, fixture.home_team_id)
        if not home_team or not home_team.venue_lat:
            return False

        weather_data = await self.weather.get_weather(home_team.venue_lat, home_team.venue_lon)
        if not weather_data:
            return False

        record = Weather(fixture_id=fixture_id, **weather_data)
        self.session.add(record)
        await self.session.commit()
        return True

    async def capture_closing_odds(self) -> int:
        """Capture closing odds for fixtures starting within 60 minutes."""
        now = utc_now()
        window_start = now
        window_end = now + timedelta(minutes=60)

        result = await self.session.execute(
            select(Fixture).where(
                Fixture.fixture_date >= window_start,
                Fixture.fixture_date <= window_end,
                Fixture.status == "NS",
            )
        )
        fixtures = result.scalars().all()
        count = 0

        for fixture in fixtures:
            await self.ingest_odds(fixture.id)
            # Mark only latest pre-kickoff snapshot per (bookmaker, market, selection) as closing
            latest_odds = await self.session.execute(
                select(OddsSnapshot).where(
                    OddsSnapshot.fixture_id == fixture.id,
                    OddsSnapshot.captured_at <= fixture.fixture_date,
                ).order_by(OddsSnapshot.captured_at.desc())
            )
            seen_keys: set[tuple] = set()
            for odds in latest_odds.scalars().all():
                key = (odds.bookmaker, odds.market, odds.selection, odds.line)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                odds.closing_odds = odds.current_odds
                odds.is_closing = True
                count += 1

        await self.session.commit()
        logger.info("captured_closing_odds", count=count)
        return count

    async def full_daily_ingest(self, date: str | None = None) -> dict:
        """Run complete daily data collection pipeline."""
        date = date or datetime.utcnow().strftime("%Y-%m-%d")
        season = football_season_candidates()[0]

        results = {
            "leagues": await self.ingest_leagues(season),
            "fixtures": await self.ingest_fixtures(date, season),
        }

        fixture_result = await self.session.execute(
            select(Fixture).where(
                Fixture.fixture_date >= datetime.strptime(date, "%Y-%m-%d"),
                Fixture.fixture_date < datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1),
            )
        )
        fixtures = fixture_result.scalars().all()

        odds_count = stats_count = injury_count = 0
        for fixture in fixtures:
            try:
                odds_count += await self.ingest_odds(fixture.id)
            except Exception as exc:
                logger.warning("ingest_odds_failed", fixture_id=fixture.id, error=str(exc))
            try:
                injury_count += await self.ingest_injuries(fixture.id)
            except Exception as exc:
                logger.warning("ingest_injuries_failed", fixture_id=fixture.id, error=str(exc))
            if fixture.status == "FT":
                try:
                    stats_count += await self.ingest_match_stats(fixture.id)
                except Exception as exc:
                    logger.warning("ingest_stats_failed", fixture_id=fixture.id, error=str(exc))

        leagues_today = {f.league_id for f in fixtures}
        for league_id in leagues_today:
            for try_season in football_season_candidates():
                await self.ingest_standings(league_id, try_season)

        results.update({
            "odds_snapshots": odds_count,
            "injuries": injury_count,
            "match_stats": stats_count,
            "fixtures_processed": len(fixtures),
        })
        return results
