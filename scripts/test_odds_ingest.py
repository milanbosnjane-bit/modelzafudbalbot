import asyncio
import sqlite3

from app.database.session import AsyncSessionLocal, init_db
from app.services.ingestion import DataIngestionService

c = sqlite3.connect("data/football_roi.db")
fid = c.execute(
    "select id from fixtures where status='FT' order by fixture_date desc limit 1"
).fetchone()[0]
print("fixture", fid)


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        ing = DataIngestionService(session)
        n = await ing.ingest_odds(fid)
        await session.commit()
        print("odds ingested", n)


asyncio.run(main())
