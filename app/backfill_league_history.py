"""Backfill prošle sezone za izabrane lige preko API-Football."""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from app.config import get_settings
from app.database.session import AsyncSessionLocal, init_db
from app.services.ingestion import DataIngestionService
from app.utils.helpers import last_completed_football_season

# Lige dodate u poslednjem update-u
DEFAULT_LEAGUE_IDS = [88, 144, 218, 219, 2, 3, 848]

LEAGUE_NAMES = {
    88: "Eredivisie (Holandija)",
    144: "Jupiler Pro League (Belgija)",
    218: "Bundesliga (Austrija)",
    219: "2. Liga (Austrija)",
    2: "UEFA Champions League",
    3: "UEFA Europa League",
    848: "UEFA Conference League",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill league season history via API")
    parser.add_argument(
        "--leagues",
        type=str,
        default=",".join(str(x) for x in DEFAULT_LEAGUE_IDS),
        help="Comma-separated API league IDs",
    )
    parser.add_argument(
        "--season",
        type=int,
        default=None,
        help="Season start year (default: last completed season)",
    )
    parser.add_argument(
        "--no-stats",
        action="store_true",
        help="Skip match statistics (xG) — faster, fewer API calls",
    )
    return parser.parse_args()


async def main() -> int:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    log = structlog.get_logger()
    args = parse_args()

    league_ids = [int(x.strip()) for x in args.leagues.split(",") if x.strip()]
    season = args.season if args.season is not None else last_completed_football_season()
    include_stats = not args.no_stats

    settings = get_settings()
    if not settings.api_football_key:
        print("[GRESKA] API_FOOTBALL_KEY nije postavljen u .env")
        return 1

    await init_db()

    print(f"\nBackfill sezona {season}/{season + 1} — {len(league_ids)} liga\n")

    totals = {"fixtures": 0, "stats": 0}
    async with AsyncSessionLocal() as session:
        service = DataIngestionService(session)
        for league_id in league_ids:
            name = LEAGUE_NAMES.get(league_id, f"Liga {league_id}")
            print(f"→ {name} (ID {league_id})...")
            result = await service.ingest_league_season_history(
                league_id,
                season,
                include_stats=include_stats,
            )
            totals["fixtures"] += result.get("fixtures", 0)
            totals["stats"] += result.get("stats", 0)
            print(
                f"  {result.get('fixtures', 0)} meceva, "
                f"{result.get('stats', 0)} stat redova, "
                f"API={result.get('api_items', 0)}"
            )

    print(
        f"\n[OK] Ukupno: {totals['fixtures']} meceva, {totals['stats']} stat redova "
        f"(sezona {season})\n"
    )
    log.info("backfill_complete", season=season, **totals)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
