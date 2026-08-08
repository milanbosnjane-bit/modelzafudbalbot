"""Deploy backfill script and run on server."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = os.environ.get("DEPLOY_REMOTE_DIR", "/home/miki/football-dc-bot")

FILES = [
    "app/backfill_league_history.py",
    "app/services/ingestion.py",
    "app/services/api_football.py",
    "app/utils/helpers.py",
]


def main() -> int:
    if not PASS:
        print("[GRESKA] Postavi DEPLOY_PASS.")
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    for rel in FILES:
        print(f"Upload {rel}")
        sftp.put(str(ROOT / rel), f"{REMOTE}/{rel.replace(chr(92), '/')}")
    sftp.close()

    cmd = (
        f"cd {REMOTE} && "
        "nohup ./venv/bin/python -m app.backfill_league_history "
        "> logs/backfill_league_history.log 2>&1 & echo $!"
    )
    _, o, e = client.exec_command(cmd, timeout=60)
    pid = o.read().decode().strip()
    err = e.read().decode().strip()
    print(f"Backfill pokrenut (PID {pid})")
    if err:
        print("[stderr]", err)

    log_path = f"{REMOTE}/logs/backfill_league_history.log"
    for i in range(120):
        time.sleep(5)
        _, o, _ = client.exec_command(f"tail -20 {log_path} 2>/dev/null", timeout=30)
        tail = o.read().decode("utf-8", errors="replace")
        if "[OK] Ukupno" in tail or "[GRESKA]" in tail:
            _, o2, _ = client.exec_command(f"cat {log_path}", timeout=60)
            sys.stdout.buffer.write(o2.read())
            client.close()
            return 0
        if i % 6 == 5:
            print(f"... radi ({(i + 1) * 5}s)")
            sys.stdout.buffer.write(tail.encode("utf-8", errors="replace"))

    _, o3, _ = client.exec_command(f"tail -40 {log_path}", timeout=30)
    print("\n[Jos u toku] Poslednje iz loga:")
    sys.stdout.buffer.write(o3.read())
    client.close()
    print(f"\n[INFO] Backfill jos traje na serveru — log: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
