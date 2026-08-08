import asyncio

from app.telegram.stats_service import get_picks_from_db


async def main() -> None:
    rows = await get_picks_from_db()
    print("open_picks", len(rows))
    for row in rows:
        p = row.pick
        print(row.status, p.match_label, p.selection, f"@{p.odds}")


asyncio.run(main())
