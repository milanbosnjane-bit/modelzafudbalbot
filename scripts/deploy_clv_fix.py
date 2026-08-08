"""Deploy CLV fix + backup DB + backfill."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
REMOTE = os.environ.get("DEPLOY_REMOTE_DIR", "/home/miki/football-dc-bot")

FILES = [
    "app/utils/clv_metrics.py",
    "app/utils/helpers.py",
    "app/services/clv_tracker.py",
    "app/services/ingestion.py",
    "app/services/paper_trading.py",
    "app/services/retrain_manager.py",
    "app/services/manual_betting.py",
    "app/database/models.py",
    "app/database/session.py",
    "app/telegram/stats_service.py",
    "app/run_local.py",
    "scripts/server/startup_ingest.sh",
    "scripts/backfill_clv_raw.py",
    "app/tests/test_clv_metrics.py",
    "app/tests/test_roi_calculations.py",
]


def connect() -> paramiko.SSHClient:
    password = os.environ.get("DEPLOY_PASS", "").strip() or "miki0510"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST, username=USER, password=password, timeout=30,
        allow_agent=False, look_for_keys=False,
    )
    return client


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return stdout.channel.recv_exit_status(), out, err


def main() -> int:
    client = connect()
    sftp = client.open_sftp()
    for rel in FILES:
        print(f"Upload {rel}")
        sftp.put(str(ROOT / rel), f"{REMOTE}/{rel}")
    sftp.close()

    steps = [
        (
            f"cd {REMOTE} && cp data/football_roi.db "
            f"data/football_roi.db.bak_clv_$(date +%Y%m%d_%H%M%S)",
            "backup_db",
        ),
        (
            f"cd {REMOTE} && PYTHONPATH={REMOTE} ./venv/bin/python -c "
            "\"import asyncio; from app.database.session import init_db; asyncio.run(init_db())\"",
            "migrate",
        ),
        (
            f"cd {REMOTE} && PYTHONPATH={REMOTE} APP_DEBUG=false "
            f"DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db "
            f"DATABASE_URL_SYNC=sqlite:///./data/football_roi.db "
            f"./venv/bin/python scripts/backfill_clv_raw.py 2>&1 | tail -5",
            "backfill",
        ),
        (
            "systemctl --user restart football-dc-telegram.service football-dc-scheduler.service",
            "restart",
        ),
    ]
    for cmd, label in steps:
        print(f"\n--- {label} ---")
        code, out, err = run(client, cmd)
        if out.strip():
            sys.stdout.buffer.write((out.rstrip() + "\n").encode("utf-8", errors="replace"))
        if err.strip() and label != "backfill":
            print("[stderr]", err.rstrip())
        if code != 0:
            print(f"[GRESKA] {label} exit {code}")
            client.close()
            return 1

    client.close()
    print("\n[OK] CLV fix deploy-ovan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
