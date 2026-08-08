#!/usr/bin/env python3
"""Verify no double_chance activity in scheduler logs since a given time (read-only)."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 180) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    return (
        stdout.read().decode("utf-8", errors="replace")
        + stderr.read().decode("utf-8", errors="replace")
    ).strip()


def main() -> int:
    since = sys.argv[1] if len(sys.argv) > 1 else "10 min ago"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    base = f"journalctl --user -u football-dc-scheduler --since '{since}' --no-pager 2>&1"

    safe_print(f"=== Provera od: {since} ===")
    ingest_cycles = run(client, f"{base} | grep -c 'ingested_odds' || echo 0")
    dc_lines = run(client, f"{base} | grep -ci 'double_chance' || echo 0")
    fair_warn = run(client, f"{base} | grep -c 'FAIR_PROB_INVALID' || echo 0")

    safe_print(f"  ingest ciklusa (ingested_odds):       {ingest_cycles}")
    safe_print(f"  linija sa 'double_chance':            {dc_lines}")
    safe_print(f"  FAIR_PROB_INVALID warninga (ukupno):  {fair_warn}")

    if dc_lines.strip() != "0":
        safe_print("\n[PAZI] jos ima double_chance linija:")
        safe_print(run(client, f"{base} | grep -i 'double_chance' | tail -10"))

    if fair_warn.strip() != "0":
        safe_print("\nPreostali FAIR_PROB_INVALID (drugi marketi):")
        safe_print(run(client, f"{base} | grep 'FAIR_PROB_INVALID' | tail -10"))

    safe_print("\n=== Zadnji ingest rezultat ===")
    safe_print(run(client, f"{base} | grep -E 'ingested_odds|captured_closing_odds|job_closing_odds_complete' | tail -5"))

    client.close()

    ok = dc_lines.strip() == "0" and ingest_cycles.strip() != "0"
    safe_print("\nZAKLJUCAK: " + ("double chance je iskljucen" if ok else "vidi gore"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
