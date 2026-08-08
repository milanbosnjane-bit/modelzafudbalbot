"""Ručno osvezi rezultate meceva sa tipovima i pokreni settle."""
import asyncio
import sqlite3
from datetime import datetime

from sqlalchemy import select

from app.database.models import DailyPick, Fixture, Team
from app.database.session import AsyncSessionLocal
from app.services.api_football import APIFootballClient
from app.services.ingestion import DataIngestionService
from app.services.paper_trading import PaperTradingService
from app.utils.cache import cache


async def refresh_pick_fixtures() -> dict[int, dict]:
    """Povuci sve unique fixture_id iz daily_picks sa API-ja."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyPick.fixture_id).distinct()
        )
        fixture_ids = [row[0] for row in result.all()]

    api = APIFootballClient()
    updated: dict[int, dict] = {}

    for fid in fixture_ids:
        # Preskoci kes — zelimo sveze podatke
        data = await api._request("fixtures", {"id": fid})
        items = data.get("response", [])
        if not items:
            updated[fid] = {"error": "not found in API"}
            continue
        item = items[0]
        league_id = item.get("league", {}).get("id")
        async with AsyncSessionLocal() as session:
            svc = DataIngestionService(session)
            await svc._upsert_fixture_item(item, league_id)
            await session.commit()

        fixture = item.get("fixture", {})
        goals = item.get("goals", {})
        teams = item.get("teams", {})
        updated[fid] = {
            "home": teams.get("home", {}).get("name"),
            "away": teams.get("away", {}).get("name"),
            "status": fixture.get("status", {}).get("short"),
            "score": f"{goals.get('home')}-{goals.get('away')}",
            "date": fixture.get("date"),
        }

    return updated


async def ingest_today() -> int:
    async with AsyncSessionLocal() as session:
        svc = DataIngestionService(session)
        return await svc.ingest_fixtures(date=datetime.utcnow().strftime("%Y-%m-%d"))


async def settle() -> int:
    svc = PaperTradingService()
    return await svc.settle_finished_picks()


def print_stats():
    c = sqlite3.connect("data/football_roi.db")
    print("\n=== OUTCOMES ===")
    for r in c.execute("SELECT outcome, COUNT(*) FROM daily_picks GROUP BY outcome"):
        print(f"  {r[0]}: {r[1]}")

    w = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='win'").fetchone()[0]
    l = c.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='lose'").fetchone()[0]
    settled = w + l
    if settled:
        stake = c.execute(
            "SELECT COALESCE(SUM(stake_units),0) FROM daily_picks WHERE outcome IN ('win','lose','push')"
        ).fetchone()[0]
        profit = c.execute(
            "SELECT COALESCE(SUM(profit_units),0) FROM daily_picks WHERE outcome IN ('win','lose','push')"
        ).fetchone()[0]
        wr = w / settled * 100
        roi = profit / stake * 100 if stake else 0
        print(f"\nWinrate: {wr:.1f}%  ({w}W / {l}L)")
        print(f"Profit: {profit:+.2f}u  ROI: {roi:+.2f}%")

    print("\n=== SETTLED PICKS ===")
    rows = c.execute("""
        SELECT th.name, ta.name, dp.market, dp.selection, dp.odds,
               dp.outcome, dp.profit_units, f.home_goals, f.away_goals
        FROM daily_picks dp
        JOIN fixtures f ON f.id = dp.fixture_id
        JOIN teams th ON th.id = f.home_team_id
        JOIN teams ta ON ta.id = f.away_team_id
        WHERE dp.outcome IN ('win','lose','push')
        ORDER BY dp.pick_date DESC
    """).fetchall()
    if not rows:
        print("  (nema)")
    for r in rows:
        home, away, market, sel, odds, outcome, profit, hg, ag = r
        print(f"  {home} vs {away} ({hg}-{ag}) | {market} {sel} @{odds} -> {outcome} ({profit:+.2f}u)")

    print("\n=== PENDING (latest batch) ===")
    for r in c.execute("""
        SELECT th.name, ta.name, dp.market, dp.selection, dp.odds,
               f.status, f.home_goals, f.away_goals, f.fixture_date
        FROM daily_picks dp
        JOIN fixtures f ON f.id = dp.fixture_id
        JOIN teams th ON th.id = f.home_team_id
        JOIN teams ta ON ta.id = f.away_team_id
        WHERE dp.pick_date = (SELECT MAX(pick_date) FROM daily_picks)
        ORDER BY dp.rank
    """):
        home, away, market, sel, odds, status, hg, ag, fdate = r
        score = f"{hg}-{ag}" if hg is not None else "?"
        print(f"  {home} vs {away} | {market} {sel} @{odds} | {status} {score} | {fdate}")


async def main():
    print(f"Manual refresh + settle @ {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n")

    print("[1/3] Refreshing pick fixtures from API...")
    updated = await refresh_pick_fixtures()
    for fid, info in sorted(updated.items()):
        if "error" in info:
            print(f"  {fid}: ERROR - {info['error']}")
        else:
            print(
                f"  {fid}: {info['home']} vs {info['away']} | "
                f"{info['status']} {info['score']} | {info['date']}"
            )

    print("\n[2/3] Ingesting all fixtures for today...")
    n = await ingest_today()
    print(f"  Updated/inserted: {n} fixtures")

    print("\n[3/3] Settling picks...")
    count = await settle()
    print(f"  Settled: {count} picks")

    print_stats()


if __name__ == "__main__":
    asyncio.run(main())
