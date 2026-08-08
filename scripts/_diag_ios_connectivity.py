#!/usr/bin/env python3
"""Diagnose iOS app connectivity: FastAPI :8001, Tailscale, mobile endpoints."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import paramiko

HOST_LAN = os.environ.get("DEPLOY_HOST", "192.168.1.106")
TAILSCALE_IP = os.environ.get("TAILSCALE_IP", "100.122.226.3")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = "/home/miki/football-dc-bot"
API_PORT = 8001


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out + (f"\n[stderr] {err}" if err.strip() else "")


def curl_public(url: str, timeout: float = 8.0) -> tuple[int | None, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:400]
            return resp.status, body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:400]
        return exc.code, body
    except Exception as exc:
        return None, str(exc)


def main() -> int:
    if not PASS:
        safe_print("[GRESKA] Postavi DEPLOY_PASS.")
        return 1

    safe_print("=== 1. Tailscale / public reachability (from Windows) ===")
    for label, ip in [("Tailscale IP", TAILSCALE_IP), ("LAN IP", HOST_LAN)]:
        for port in (8001, 8000):
            url = f"http://{ip}:{port}/api/v1/health"
            code, body = curl_public(url)
            safe_print(f"{label} {url}")
            safe_print(f"  -> HTTP {code} | {body[:200]}")
        safe_print("")

    safe_print("=== 2. SSH server diagnostics ===")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST_LAN, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    checks = [
        ("Tailscale IP on server", "tailscale ip -4 2>/dev/null || ip -4 addr show tailscale0 2>/dev/null | grep inet || echo 'no tailscale'"),
        ("Listening ports 8000/8001", "ss -tlnp 2>/dev/null | grep -E ':8000|:8001' || netstat -tlnp 2>/dev/null | grep -E ':8000|:8001' || echo 'none'"),
        ("football uvicorn processes", "pgrep -af 'football-dc-bot.*uvicorn|uvicorn app.main:app' || echo 'no uvicorn'"),
        ("systemd football-dc-api", "systemctl --user is-active football-dc-api.service 2>/dev/null || echo inactive"),
        ("Local health :8001", f"curl -sf -m 5 http://127.0.0.1:{API_PORT}/api/v1/health || echo FAIL_8001"),
        ("Local status :8001", f"curl -sf -m 5 http://127.0.0.1:{API_PORT}/api/v1/status || echo FAIL_STATUS"),
        ("Local health :8000 (PrelaziBot)", "curl -sf -m 5 http://127.0.0.1:8000/api/v1/health || curl -sf -m 5 http://127.0.0.1:8000/ | head -c 120 || echo FAIL_8000"),
        ("mobile_routes present", f"test -f {REMOTE}/app/api/mobile_routes.py && grep -c mobile_router {REMOTE}/app/api/routes.py || echo missing"),
        ("Recent fastapi log", f"tail -n 20 {REMOTE}/logs/fastapi.log 2>/dev/null || echo 'no log'"),
        ("ufw status", "sudo ufw status 2>/dev/null || ufw status 2>/dev/null || echo 'ufw n/a'"),
    ]
    for title, cmd in checks:
        safe_print(f"--- {title} ---")
        safe_print(run(client, cmd).strip())
        safe_print("")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
