#!/usr/bin/env python3
"""Print exactly what the Telegram LIVE PICKS pipeline returns. Read-only, sends nothing."""
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
from datetime import datetime

from app.telegram.stats_service import get_telegram_live_picks_rows, get_picks_from_db


async def main():
    raw = await get_picks_from_db()
    print(f"get_picks_from_db (7 dana, pending, nije FT): {len(raw)}")
    from collections import Counter
    print("  po statusu:", dict(Counter(r.status for r in raw)))
    print("  po danu:", dict(Counter(
        (r.pick.fixture_date.date().isoformat() if r.pick.fixture_date else "?") for r in raw
    )))

    rows = await get_telegram_live_picks_rows(max_display=None)
    print(f"\nLIVE PICKS render: {len(rows)}")
    for r in rows:
        p = r.pick
        ko = p.fixture_date.strftime("%m-%d %H:%M") if p.fixture_date else "?"
        print(f"  #{p.rank:2d} [{r.status:7s}] id={p.pick_id} {p.market:14s} {p.selection:10s} "
              f"kvota={p.odds:.2f} EV={p.expected_value:+.4f} ko={ko}  {p.match_label[:34]}")

    print(f"\n  rankovi: {[r.pick.rank for r in rows]}")
    print(f"  danas (pick_date danas): ", end="")
    print(sum(1 for r in rows if r.pick.pick_id))


asyncio.run(main())
"""


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_diag_tg_live.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 venv/bin/python /tmp/_diag_tg_live.py 2>&1",
        timeout=600,
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
