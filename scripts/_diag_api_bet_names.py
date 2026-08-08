#!/usr/bin/env python3
"""
Dump the real API-Football bet catalog and per-fixture bet names.

Read-only. Used to build the ingestion market allowlist from real data
instead of guessing names.
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
import json
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.database.models import Fixture
from app.database.session import AsyncSessionLocal

s = get_settings()
BASE = s.api_football_base_url
HEAD = {"x-apisports-key": s.api_football_key, "Accept": "application/json"}
print("api key set:", bool(s.api_football_key), "len:", len(s.api_football_key or ""))
print("base:", BASE)


async def get(client, ep, params):
    r = await client.get(f"{BASE}/{ep}", headers=HEAD, params=params)
    print(f"  GET {ep} {params} -> HTTP {r.status_code}")
    if r.status_code != 200:
        print("   body:", r.text[:300])
        return {}
    d = r.json()
    if d.get("errors"):
        print("   errors:", d["errors"])
    print("   results:", d.get("results"), "paging:", d.get("paging"))
    return d


async def main():
    async with httpx.AsyncClient(timeout=40.0) as client:
        print("\n=== 1. KATALOG SVIH BET TIPOVA (/odds/bets) ===")
        d = await get(client, "odds/bets", {})
        catalog = {str(b["id"]): b["name"] for b in d.get("response", [])}
        for bid, nm in sorted(catalog.items(), key=lambda x: int(x[0])):
            print(f"   id={bid:>4s}  {nm}")

        print("\n=== 2. KANDIDAT FIXTURE (NS, 6-48h u buducnosti) ===")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(Fixture)
                .where(
                    Fixture.fixture_date >= now + timedelta(hours=6),
                    Fixture.fixture_date <= now + timedelta(hours=48),
                    Fixture.status == "NS",
                )
                .order_by(Fixture.fixture_date)
                .limit(6)
            )
            fixtures = list(res.scalars().all())
        print("  kandidata:", [f.id for f in fixtures])

        chosen = None
        for fx in fixtures:
            print(f"\n  -- probam fixture {fx.id} ({fx.fixture_date}) --")
            d = await get(client, "odds", {"fixture": fx.id})
            resp = d.get("response", [])
            if resp and resp[0].get("bookmakers"):
                chosen = (fx, d)
                break

        if not chosen:
            print("\n  [!] nijedan fixture nije vratio kvote")
            return

        fx, d = chosen
        print(f"\n=== 3. BET IMENA za fixture {fx.id} ===")
        names = {}
        for entry in d.get("response", []):
            for bm in entry.get("bookmakers", []):
                for bet in bm.get("bets", []):
                    key = (str(bet.get("id")), bet.get("name", ""))
                    names.setdefault(key, set())
                    for v in bet.get("values", [])[:4]:
                        names[key].add(str(v.get("value", "")))

        print(f"  razlicitih bet imena: {len(names)}")
        for (bid, nm), vals in sorted(names.items(), key=lambda x: int(x[0][0]) if x[0][0].isdigit() else 999):
            print(f"   id={bid:>4s}  {nm!r}")
            print(f"          vrednosti: {sorted(vals)[:6]}")

        with open("/tmp/bet_names.json", "w") as fh:
            json.dump(
                {"fixture_id": fx.id, "catalog": catalog,
                 "names": [{"id": b, "name": n, "values": sorted(v)} for (b, n), v in names.items()]},
                fh, indent=2,
            )
        print("\n  snimljeno u /tmp/bet_names.json")


asyncio.run(main())
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_api_bets.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 venv/bin/python /tmp/_diag_api_bets.py 2>&1",
        timeout=600,
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
