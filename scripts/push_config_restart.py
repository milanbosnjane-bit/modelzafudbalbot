"""Push league config to server and restart bot services."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = os.environ.get("DEPLOY_REMOTE_DIR", "/home/miki/football-dc-bot")

FILES = [
    "app/services/ingestion.py",
    "app/services/api_football.py",
    "app/predictions/pick_selector.py",
]


def patch_env_for_server(text: str) -> str:
    overrides = {
        "DATABASE_URL": "sqlite+aiosqlite:///./data/football_roi.db",
        "DATABASE_URL_SYNC": "sqlite:///./data/football_roi.db",
        "APP_DEBUG": "false",
        "LOCAL_MODE": "true",
        "USE_MEMORY_CACHE": "true",
        "POISSON_ONLY_MODE": "true",
        "PAPER_TRADING_ENABLED": "true",
        "MARKET_CONFIRMATION_GATE_ENABLED": "false",
    }
    seen: set[str] = set()
    lines: list[str] = []
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line and not line.strip().startswith("#") else ""
        if key in overrides:
            lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            lines.append(line)
    for key, val in overrides.items():
        if key not in seen:
            lines.append(f"{key}={val}")
    return "\n".join(lines).rstrip() + "\n"


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 900) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return stdout.channel.recv_exit_status(), out, err


def main() -> int:
    if not PASS:
        print("[GRESKA] Postavi DEPLOY_PASS u okruzenju.")
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    except Exception as exc:
        print(f"[GRESKA] SSH: {exc}")
        return 1

    sftp = client.open_sftp()
    for rel in FILES:
        print(f"Upload {rel}")
        sftp.put(str(ROOT / rel), f"{REMOTE}/{rel.replace(chr(92), '/')}")

    local_env = ROOT / ".env"
    if local_env.is_file():
        print("Upload .env")
        with sftp.file(f"{REMOTE}/.env", "w") as f:
            f.write(patch_env_for_server(local_env.read_text(encoding="utf-8")))
    sftp.close()

    steps = [
        (f"cd {REMOTE} && sed -i 's/\\r$//' scripts/server/*.sh && chmod +x scripts/server/*.sh", "dos2unix"),
        (f"cd {REMOTE} && ./scripts/server/install_systemd.sh", "install_systemd"),
    ]
    for cmd, label in steps:
        print(f"\n--- {label} ---")
        code, out, err = run(client, cmd)
        if out.strip():
            sys.stdout.buffer.write((out.rstrip() + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        if err.strip():
            sys.stdout.buffer.write(("[stderr] " + err.rstrip() + "\n").encode("utf-8", errors="replace"))
            sys.stdout.buffer.flush()
        if code != 0:
            print(f"[GRESKA] {label} failed (exit {code})")
            client.close()
            return 1

    client.close()
    print("\n[OK] Config poslat, bot restartovan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
