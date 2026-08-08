#!/usr/bin/env python3
"""Health sweep across football-dc services: status, errors, API, picks (read-only)."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
API_PORT = int(os.environ.get("FOOTBALL_API_PORT", "8001"))

SERVICES = ("football-dc-scheduler", "football-dc-telegram", "football-dc-api")
ERROR_PATTERN = (
    "Traceback|AttributeError|TypeError|ValueError|KeyError|ImportError|OperationalError|"
    "raised an exception|Task exception|CancelledError|\\[error|ERROR|CRITICAL|"
    "timed out|SIGKILL|Failed with result"
)


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 180) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return (out + err).rstrip()


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    safe_print("=== STATUS SERVISA ===")
    safe_print(
        run(
            client,
            "for s in " + " ".join(SERVICES) + "; do "
            'printf "%-26s %s  (uptime: %s)\\n" "$s" '
            '"$(systemctl --user is-active $s.service)" '
            "\"$(systemctl --user show $s.service -p ActiveEnterTimestamp --value)\"; done",
        )
    )

    safe_print("\n=== PORTOVI ===")
    safe_print(run(client, "ss -ltnp 2>/dev/null | grep -E ':8000|:8001' || echo 'nema'"))

    safe_print("\n=== RESTARTOVI / PADOVI (danas) ===")
    for svc in SERVICES:
        out = run(
            client,
            f"journalctl --user -u {svc} --since today --no-pager 2>&1 | "
            f"grep -cE 'Started {svc}' || echo 0",
        )
        fails = run(
            client,
            f"journalctl --user -u {svc} --since today --no-pager 2>&1 | "
            "grep -cE 'Failed with result|SIGKILL|timed out' || echo 0",
        )
        safe_print(f"  {svc:26s} startova: {out.strip():4s} problema: {fails.strip()}")

    for svc in SERVICES:
        safe_print(f"\n=== GRESKE: {svc} (poslednja 2h) ===")
        out = run(
            client,
            f"journalctl --user -u {svc} --since '2 hours ago' --no-pager 2>&1 | "
            f"grep -E '{ERROR_PATTERN}' | tail -20",
        )
        safe_print(out if out.strip() else "  (nema gresaka)")

    for svc in SERVICES:
        safe_print(f"\n=== ZADNJE LINIJE: {svc} ===")
        safe_print(run(client, f"journalctl --user -u {svc} --no-pager -n 6 2>&1"))

    safe_print("\n=== API (lokalno na serveru) ===")
    safe_print(
        run(
            client,
            f"curl -sf -m 10 http://127.0.0.1:{API_PORT}/api/v1/health; echo; "
            f"curl -sf -m 10 http://127.0.0.1:{API_PORT}/api/v1/status; echo",
        )
    )

    client.close()

    safe_print("=== APP endpointi (preko Tailscale) ===")
    base = f"http://{HOST}:{API_PORT}/api/v1"
    for path in ("/picks/today", "/picks/recent?limit=5", "/odds/tracker?limit=6"):
        try:
            with urllib.request.urlopen(f"{base}{path}", timeout=20) as resp:
                data = json.loads(resp.read())
            safe_print(f"  OK {path:26s} -> {len(data)} zapisa")
        except Exception as exc:  # noqa: BLE001 - diagnostic sweep
            safe_print(f"  FAIL {path:26s} -> {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
