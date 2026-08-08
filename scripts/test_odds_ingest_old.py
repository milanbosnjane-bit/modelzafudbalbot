import asyncio
import sqlite3

from app.database.session import AsyncSessionLocal, init_db
from app.services.ingestion import DataIngestionService

c = sqlite3.connect("data/football_roi.db")
rows = c.execute(
    "select id, fixture_date from fixtures where status='FT' order by fixture_date asc limit 3"
).fetchall()
print("oldest FT:", rows)

fid = rows[0][0]


async def main():
    await init_db()
    async with AsyncSessionLocal() as session:
        ing = DataIngestionService(session)
        n = await ing.ingest_odds(fid)
        await session.commit()
        print("odds ingested for", fid, n)


asyncio.run(main())
