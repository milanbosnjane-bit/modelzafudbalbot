"""Server health check: CPU, RAM, disk, bot processes."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = "/home/miki/football-dc-bot"


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out + (f"\n[stderr] {err}" if err.strip() else "")


def main() -> int:
    if not PASS:
        safe_print("[GRESKA] Postavi DEPLOY_PASS.")
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    checks = [
        ("OS / uptime", "uname -a && uptime"),
        ("CPU info", "nproc && lscpu 2>/dev/null | grep -E 'Model name|CPU\\(s\\)|Thread|MHz' || true"),
        ("Load / CPU usage", "top -bn1 | head -5"),
        ("RAM", "free -h"),
        ("Swap", "swapon --show 2>/dev/null || echo 'no swap'"),
        ("Disk", "df -h / /home"),
        ("Top processes by RAM", "ps aux --sort=-%mem | head -12"),
        ("Top processes by CPU", "ps aux --sort=-%cpu | head -8"),
        ("DC bot servisi", "systemctl --user is-active football-dc-scheduler football-dc-telegram 2>&1; systemctl --user status football-dc-scheduler --no-pager -l 2>&1 | head -8; echo '---'; systemctl --user status football-dc-telegram --no-pager -l 2>&1 | head -8"),
        ("DC bot procesi", f"pgrep -af 'football-dc-bot|app.services.scheduler|app.telegram.run_bot' || true"),
        ("DC bot RAM", f"ps -o pid,rss,vsz,cmd -C python3 2>/dev/null | grep -E 'scheduler|run_bot|football-dc' || ps aux | grep '{REMOTE}' | grep -v grep || true"),
        ("Stari bot (projekti)", "pgrep -af 'projekti' | head -5 || echo 'nema aktivnih projekti procesa'"),
        ("Mreza", "ip -br addr 2>/dev/null | head -5; ping -c 1 -W 2 8.8.8.8 >/dev/null 2>&1 && echo 'internet: OK' || echo 'internet: FAIL'"),
        ("DC bot baza", f"ls -lh {REMOTE}/data/football_roi.db 2>/dev/null"),
    ]

    for title, cmd in checks:
        safe_print(f"\n{'='*50}\n{title}\n{'='*50}")
        safe_print(run(client, cmd).rstrip())

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
