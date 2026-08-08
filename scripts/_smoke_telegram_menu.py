#!/usr/bin/env python3
"""Smoke-test Telegram menu renderers on the server without sending messages."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

SCRIPT = r"""
import asyncio, traceback

async def main():
    from app.telegram.stats_service import (
        bot_status,
        get_picks_from_db,
        live_picks,
        roi_stats,
        split_telegram_picks_message,
    )

    try:
        rows = await get_picks_from_db()
        print("OK get_picks_from_db:", len(rows), "otvorenih tipova")
    except Exception:
        print("FAIL get_picks_from_db")
        traceback.print_exc()
        return

    try:
        msg = await live_picks()
        parts = split_telegram_picks_message(msg)
        blocks = msg.count("Tip:") or msg.count("#")
        print(f"OK live_picks: {len(msg)} znakova, {len(parts)} Telegram poruka")
        print("--- prve 12 linija LIVE PICKS ---")
        for line in msg.splitlines()[:12]:
            print("   ", line)
    except Exception:
        print("FAIL live_picks")
        traceback.print_exc()

    try:
        msg = await roi_stats()
        print(f"OK roi_stats: {len(msg)} znakova")
    except Exception:
        print("FAIL roi_stats")
        traceback.print_exc()

    try:
        msg = await bot_status()
        print(f"OK bot_status: {len(msg)} znakova")
    except Exception:
        print("FAIL bot_status")
        traceback.print_exc()

asyncio.run(main())
"""


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_smoke_telegram.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()

    cmd = (
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 LOCAL_MODE=true "
        f"DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db "
        f"DATABASE_URL_SYNC=sqlite:///./data/football_roi.db "
        f"venv/bin/python /tmp/_smoke_telegram.py 2>&1"
    )
    _, stdout, _ = client.exec_command(cmd, timeout=300)
    safe_print(stdout.read().decode("utf-8", errors="replace"))
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
