"""Deploy Telegram LIVE-picks display fix to server."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
KEY = os.environ.get("DEPLOY_KEY", str(Path.home() / ".ssh" / "id_lan_101"))
REMOTE = os.environ.get("DEPLOY_REMOTE_DIR", "/home/miki/football-dc-bot")

FILES = [
    "app/telegram/pick_output.py",
    "app/telegram/stats_service.py",
    "app/telegram/interactive_bot.py",
    "app/tests/test_pick_output.py",
    "scripts/_verify_open_picks_server.py",
]


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {
        "hostname": HOST,
        "username": USER,
        "timeout": 30,
        "allow_agent": False,
        "look_for_keys": False,
    }
    password = os.environ.get("DEPLOY_PASS", "").strip() or "miki0510"
    kwargs["password"] = password
    client.connect(**kwargs)
    return client


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
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
        ("systemctl --user restart football-dc-telegram.service", "restart"),
        ("systemctl --user is-active football-dc-telegram.service", "status"),
        (
            f"cd {REMOTE} && PYTHONPATH={REMOTE} ./venv/bin/python scripts/_verify_open_picks_server.py",
            "verify",
        ),
    ]
    for cmd, label in steps:
        print(f"\n--- {label} ---")
        code, out, err = run(client, cmd)
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("[stderr]", err.rstrip())
        if code != 0:
            print(f"[GRESKA] {label} failed (exit {code})")
            client.close()
            return 1

    client.close()
    print("\n[OK] Fix deploy-ovan, Telegram restartovan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
