"""Verify server DB and run full-build."""
import os
import sys

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = "/home/miki/football-dc-bot"


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    checks = [
        f"ls -lh {REMOTE}/data/football_roi.db",
        f"sqlite3 {REMOTE}/data/football_roi.db 'SELECT COUNT(*) FROM fixtures;'",
        f"sqlite3 {REMOTE}/data/football_roi.db 'SELECT COUNT(*) FROM daily_picks;'",
        f"sqlite3 {REMOTE}/data/football_roi.db 'SELECT COUNT(*) FROM odds_snapshots;'",
        f"cd {REMOTE} && ./venv/bin/python -m app.run_local --full-build",
    ]
    for cmd in checks:
        sys.stdout.buffer.write(f"\n=== {cmd[:70]} ===\n".encode())
        sys.stdout.buffer.flush()
        _, o, e = c.exec_command(cmd, timeout=300)
        out = o.read().decode("utf-8", errors="replace")
        err = e.read().decode("utf-8", errors="replace")
        if out:
            sys.stdout.buffer.write(out[-2500:].encode("utf-8", errors="replace"))
        if err:
            sys.stdout.buffer.write(f"\nERR: {err[-500:]}".encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
        sys.stdout.buffer.flush()

    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
