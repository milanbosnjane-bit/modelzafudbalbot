#!/usr/bin/env python3
"""
Deploy app/services/scheduler.py and restart the scheduler service.

PrelaziBot on port 8000 is never touched — only football-dc-scheduler.service.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

VERIFY_SCRIPT = r"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.scheduler import create_scheduler

BG = ZoneInfo("Europe/Belgrade")
UTC = ZoneInfo("UTC")
now = datetime.now(UTC)

scheduler = create_scheduler()
print(f"now = {now.astimezone(BG):%Y-%m-%d %H:%M} srpsko / {now:%H:%M} UTC")
for job in scheduler.get_jobs():
    fires = []
    cursor = now
    for _ in range(4):
        nxt = job.trigger.get_next_fire_time(None, cursor)
        if nxt is None:
            break
        fires.append(f"{nxt.astimezone(BG):%a %d.%m %H:%M}")
        cursor = nxt + timedelta(seconds=1)
    print(f"  {job.id:20s} {job.name}")
    print(f"    trigger: {job.trigger}")
    print(f"    sledeca paljenja (srpsko): {', '.join(fires) if fires else 'n/a'}")
"""


DROPIN_CMD = r"""
set -e
DIR="$HOME/.config/systemd/user/football-dc-scheduler.service.d"
mkdir -p "$DIR"
cat > "$DIR/timeout.conf" <<'EOF'
[Service]
KillSignal=SIGTERM
TimeoutStopSec=10
EOF
systemctl --user daemon-reload
echo "drop-in:"
cat "$DIR/timeout.conf"
systemctl --user show football-dc-scheduler.service -p TimeoutStopUSec -p KillSignal
"""

RESTART_TIMED_CMD = r"""
start=$(date +%s%3N)
systemctl --user restart football-dc-scheduler.service
end=$(date +%s%3N)
echo "restart trajao: $((end-start)) ms"
sleep 3
echo -n "is-active: "; systemctl --user is-active football-dc-scheduler.service
"""


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 180) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out + (f"\n[stderr] {err}" if err.strip() else "")


def main() -> int:
    verify_only = "--verify-only" in sys.argv
    local = ROOT / "app" / "services" / "scheduler.py"
    if not local.is_file():
        safe_print(f"[GRESKA] Nedostaje {local}")
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    safe_print(f"Scheduler -> {USER}@{HOST}  (PrelaziBot :8000 netaknut)")
    sftp = client.open_sftp()
    if not verify_only:
        sftp.put(str(local), f"{REMOTE}/app/services/scheduler.py")
        safe_print("  upload scheduler.py")
        unit_installer = ROOT / "scripts" / "server" / "install_systemd.sh"
        if unit_installer.is_file():
            sftp.put(str(unit_installer), f"{REMOTE}/scripts/server/install_systemd.sh")
            safe_print("  upload install_systemd.sh")
    with sftp.open("/tmp/_verify_scheduler.py", "w") as fh:
        fh.write(VERIFY_SCRIPT)
    sftp.close()

    env = (
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 LOCAL_MODE=true "
        f"DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db "
        f"DATABASE_URL_SYNC=sqlite:///./data/football_roi.db"
    )

    safe_print("\n=== Provera cron podesavanja (na serveru) ===")
    safe_print(run(client, f"{env} venv/bin/python /tmp/_verify_scheduler.py 2>&1"))

    if not verify_only:
        safe_print("=== systemd drop-in: TimeoutStopSec ===")
        safe_print(run(client, DROPIN_CMD))

        safe_print("=== Restart football-dc-scheduler.service (mereno) ===")
        safe_print(run(client, RESTART_TIMED_CMD, timeout=300))

        safe_print("=== Scheduler log (poslednjih 12 linija) ===")
        safe_print(run(client, "journalctl --user -u football-dc-scheduler --no-pager -n 12 2>&1"))

    client.close()
    safe_print("Scheduler provera zavrsena." if verify_only else "Scheduler deploy zavrsen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
