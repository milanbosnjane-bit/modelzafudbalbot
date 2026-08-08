#!/usr/bin/env python3
"""
Re-ingest odds for every upcoming fixture so no pick-relevant key is left on pre-fix data.

Only odds are refreshed (no lineups/injuries), and only rows are added, nothing deleted.
This is the same work the 30-minute scheduler job does, pulled forward once.
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
from datetime import datetime, timedelta

from sqlalchemy import select

from app.database.models import Fixture
from app.database.session import AsyncSessionLocal
from app.services.ingestion import DataIngestionService


async def main():
    now = datetime.utcnow()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Fixture)
            .where(
                Fixture.fixture_date >= now,
                Fixture.fixture_date <= now + timedelta(hours=48),
                Fixture.status == "NS",
            )
            .order_by(Fixture.fixture_date)
        )
        fixtures = list(result.scalars().all())
        print(f"meceva za re-ingest: {len(fixtures)}")

        service = DataIngestionService(session)
        total = 0
        empty = 0
        for i, fx in enumerate(fixtures, 1):
            try:
                n = await service.ingest_odds(fx.id)
            except Exception as exc:
                print(f"  [{i}/{len(fixtures)}] {fx.id} GRESKA: {exc}")
                continue
            total += n
            if n == 0:
                empty += 1
            if i % 20 == 0 or i == len(fixtures):
                print(f"  [{i}/{len(fixtures)}] snapshotova ukupno={total} bez_kvota={empty}")

        print(f"\ngotovo: {total} snapshotova, {empty} meceva bez kvota")


asyncio.run(main())
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_reingest_odds.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 venv/bin/python -u /tmp/_reingest_odds.py 2>&1",
        timeout=3600,
    )
    for line in iter(stdout.readline, ""):
        sys.stdout.buffer.write(line.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    sys.stdout.buffer.write(stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
