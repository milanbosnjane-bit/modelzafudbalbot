"""Hotfix deploy: ingest supplement + feature dup fix + env thresholds."""
from __future__ import annotations

import os
import re
import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = "/home/miki/football-dc-bot"
ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

FILES = [
    "app/features/engineer.py",
    "app/services/ingestion.py",
    "app/predictions/pick_selector.py",
]

ENV_FIXES = {
    "MIN_EV_THRESHOLD=0.07": "MIN_EV_THRESHOLD=0.015",
    "MIN_CONFIDENCE_THRESHOLD=0.72": "MIN_CONFIDENCE_THRESHOLD=0.55",
}


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    for rel in FILES:
        print("Upload", rel)
        sftp.put(str(ROOT / rel), f"{REMOTE}/{rel}")
    with sftp.open(f"{REMOTE}/.env", "r") as f:
        env = f.read().decode("utf-8")
    for old, new in ENV_FIXES.items():
        if old in env:
            env = env.replace(old, new)
            print("ENV fix:", old, "->", new)
        elif new.split("=")[0] not in env:
            env += f"\n{new}\n"
            print("ENV add:", new)
    with sftp.open(f"{REMOTE}/.env", "w") as f:
        f.write(env.encode("utf-8"))
    sftp.close()

    cmd = (
        f"cd {REMOTE} && source venv/bin/activate && "
        "export LOCAL_MODE=true USE_MEMORY_CACHE=true APP_DEBUG=false PYTHONUTF8=1 "
        "POISSON_ONLY_MODE=true PAPER_TRADING_ENABLED=true "
        "DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db "
        "DATABASE_URL_SYNC=sqlite:///./data/football_roi.db && "
        "python -m app.run_local --full-build 2>&1 | tail -30"
    )

    def run(c: str) -> str:
        _, o, e = client.exec_command(c, timeout=600)
        return (o.read() + e.read()).decode("utf-8", errors="replace")

    print("\n=== FULL BUILD + PICKS ===")
    print(run(cmd))
    print("\n=== RESTART ===")
    print(run(f"{REMOTE}/scripts/server/restart_bot.sh"))
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
