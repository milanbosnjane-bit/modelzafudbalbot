#!/usr/bin/env python3
"""List raw bookmaker bet names and how _normalize_market maps them (read-only, 1 API call)."""
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
from app.services.api_football import APIFootballClient
from app.services.ingestion import DataIngestionService


async def main():
    now = datetime.utcnow()
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Fixture)
            .where(Fixture.fixture_date >= now, Fixture.fixture_date <= now + timedelta(hours=36))
            .order_by(Fixture.fixture_date)
            .limit(1)
        )
        fx = res.scalars().first()
        if not fx:
            print("nema fixtura")
            return
        print("fixture_id:", fx.id, "kickoff:", fx.fixture_date)

        svc = DataIngestionService(session)
        client = APIFootballClient()
        try:
            odds = await client.get_odds(fx.id)
        finally:
            close = getattr(client, "close", None)
            if close:
                maybe = close()
                if asyncio.iscoroutine(maybe):
                    await maybe

    names = {}
    for entry in odds:
        for bm in entry.get("bookmakers", []):
            for bet in bm.get("bets", []):
                raw = bet.get("name", "")
                names.setdefault(raw, set())
                for v in bet.get("values", [])[:3]:
                    names[raw].add(str(v.get("value", "")))

    buckets = {}
    for raw in sorted(names):
        mapped = svc._normalize_market(raw)
        buckets.setdefault(mapped, []).append(raw)

    print(f"\nrazlicitih bet imena: {len(names)}")
    for mapped in sorted(buckets, key=lambda x: (x is None, str(x))):
        label = mapped if mapped else "IGNORISANO (None)"
        print(f"\n=== {label}  ({len(buckets[mapped])} imena) ===")
        for raw in buckets[mapped]:
            sample = ", ".join(sorted(names[raw])[:3])
            print(f"   {raw!r:52s} primeri: {sample}")


asyncio.run(main())
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_bet_names.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    cmd = (
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 LOCAL_MODE=true USE_MEMORY_CACHE=true "
        f"DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db "
        f"DATABASE_URL_SYNC=sqlite:///./data/football_roi.db "
        f"venv/bin/python /tmp/_diag_bet_names.py 2>&1"
    )
    _, stdout, stderr = client.exec_command(cmd, timeout=300)
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
