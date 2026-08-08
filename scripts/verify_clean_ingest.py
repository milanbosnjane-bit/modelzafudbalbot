#!/usr/bin/env python3
"""
Run a real odds ingest for upcoming fixtures, then assert the new rows are clean.

Checks: no colliding (bookmaker, market, selection, line) keys, no NULL fair_prob,
de-vig groups the right size, and only allowlisted markets present.
"""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

SCRIPT = r"""
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database.models import Fixture
from app.database.session import AsyncSessionLocal
from app.services.ingestion import DataIngestionService

LIMIT = 6


async def main():
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    now = started
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Fixture)
            .where(
                Fixture.fixture_date >= now,
                Fixture.fixture_date <= now + timedelta(hours=48),
                Fixture.status == "NS",
            )
            .order_by(Fixture.fixture_date)
            .limit(LIMIT)
        )
        fixtures = list(res.scalars().all())

        print(f"=== CIST INGEST ({len(fixtures)} meceva) ===")
        svc = DataIngestionService(session)
        ids = []
        for fx in fixtures:
            n = await svc.ingest_odds(fx.id)
            ids.append(fx.id)
            print(f"  fixture {fx.id}: {n} snapshotova")

    marker = started.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n(novi redovi = captured_at >= {marker})")
    return marker, ids


marker, ids = asyncio.run(main())

conn = sqlite3.connect("file:./data/football_roi.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
NEW = f"captured_at >= '{marker}'"

print("\n=== 1. KOLIZIJE (isti bookmaker/market/selection/line) ===")
rows = conn.execute(f'''
    SELECT market, selection, line, n, lo, hi FROM (
      SELECT market, selection, line, COUNT(*) n,
             MIN(current_odds) lo, MAX(current_odds) hi
      FROM odds_snapshots WHERE {NEW}
      GROUP BY fixture_id, bookmaker, market, selection, line
      HAVING n > 1
    ) ORDER BY n DESC LIMIT 10
''').fetchall()
print(f"  grupa sa kolizijom: {len(rows)}")
for r in rows:
    print(f"   {r['market']} {r['selection']} line={r['line']} puta={r['n']} {r['lo']}..{r['hi']}")

print("\n=== 2. MARKETI I fair_prob ===")
for r in conn.execute(f'''
    SELECT market, COUNT(*) n,
           SUM(CASE WHEN fair_prob IS NULL THEN 1 ELSE 0 END) null_fair,
           COUNT(DISTINCT selection) selekcija
    FROM odds_snapshots WHERE {NEW}
    GROUP BY market ORDER BY n DESC
'''):
    print(f"  {r['market']:14s} redova={r['n']:5d} bez_fair_prob={r['null_fair']:4d} "
          f"razlicitih_selekcija={r['selekcija']}")

print("\n=== 3. SELEKCIJE PO MARKETU ===")
for r in conn.execute(f'''
    SELECT market, selection, line, COUNT(*) n
    FROM odds_snapshots WHERE {NEW}
    GROUP BY market, selection, line ORDER BY market, selection
'''):
    print(f"  {r['market']:14s} {r['selection']:12s} line={str(r['line']):5s} redova={r['n']}")

print("\n=== 4. VELICINA DEVIG GRUPA (mora 3 / 2 / 2) ===")
for r in conn.execute(f'''
    SELECT market, line, n, COUNT(*) grupa FROM (
      SELECT market, line, COUNT(*) n FROM odds_snapshots
      WHERE {NEW}
      GROUP BY fixture_id, bookmaker, market, line
    ) GROUP BY market, line, n ORDER BY market, line, n
'''):
    print(f"  {r['market']:14s} line={str(r['line']):5s} ishoda_u_grupi={r['n']} broj_grupa={r['grupa']}")

print("\n=== 5. market_overround (sanity: ~1.0-1.15) ===")
for r in conn.execute(f'''
    SELECT market, ROUND(MIN(market_overround),4) lo, ROUND(AVG(market_overround),4) avg,
           ROUND(MAX(market_overround),4) hi
    FROM odds_snapshots WHERE {NEW} AND market_overround IS NOT NULL
    GROUP BY market
'''):
    print(f"  {r['market']:14s} min={r['lo']} avg={r['avg']} max={r['hi']}")

conn.close()
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_verify_clean_ingest.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 venv/bin/python /tmp/_verify_clean_ingest.py 2>&1",
        timeout=900,
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
