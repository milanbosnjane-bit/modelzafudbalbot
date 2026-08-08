#!/usr/bin/env python3
"""
Check the odds the selector actually decides on (read-only, no picks persisted).

A missing fair_prob here is what makes the ensemble reject a candidate with
"missing_fair_implied", so this is the check that the fix reaches the pipeline.
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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database.models import Fixture, Team
from app.database.session import AsyncSessionLocal
from app.predictions.pick_selector import PickSelectionEngine
from app.utils.helpers import utc_now


async def main():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Fixture)
            .where(
                Fixture.fixture_date >= now,
                Fixture.fixture_date <= now + timedelta(hours=48),
                Fixture.status == "NS",
            )
            .order_by(Fixture.fixture_date)
            .limit(6)
        )
        fixtures = list(res.scalars().all())
        ids = [f.id for f in fixtures]
        teams = {}
        for f in fixtures:
            for tid in (f.home_team_id, f.away_team_id):
                t = await session.get(Team, tid)
                teams[tid] = t.name if t else "?"

        selector = PickSelectionEngine(session)
        as_of_map = {fid: utc_now() for fid in ids}
        odds_map = await selector._load_all_decision_odds(ids, as_of_map)

        total = 0
        missing = 0
        for f in fixtures:
            grouped = odds_map.get(f.id) or {}
            label = f"{teams.get(f.home_team_id)} vs {teams.get(f.away_team_id)}"
            print(f"\n=== {f.id}  {label} ===")
            if not grouped:
                print("   (nema kvota)")
                continue
            for market in sorted(grouped):
                for selection in sorted(grouped[market]):
                    info = grouped[market][selection]
                    fp = info.get("fair_prob")
                    total += 1
                    if fp is None:
                        missing += 1
                    fp_txt = f"{fp:.4f}" if fp is not None else "NEMA  <-- missing_fair_implied"
                    odds = info.get("odds")
                    odds_txt = f"{odds:.2f}" if odds else "None"
                    print(f"   {market:14s} {selection:12s} kvota={odds_txt:>6s} fair_prob={fp_txt}")

        print(f"\n=== ZBIR ===")
        print(f"  market/selection kombinacija: {total}")
        print(f"  bez fair_prob (odbacile bi se): {missing}")
        print(f"  ISPRAVNO" if missing == 0 else "  IMA PROBLEMA")


asyncio.run(main())
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_verify_decision_odds.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 venv/bin/python /tmp/_verify_decision_odds.py 2>&1",
        timeout=900,
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
