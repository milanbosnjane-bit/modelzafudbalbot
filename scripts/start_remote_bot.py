"""Finish remote setup and start football-dc-bot (reads creds from env)."""
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


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return stdout.channel.recv_exit_status(), out, err


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
    seen = set()
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


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

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

    local_env = ROOT / ".env"
    if local_env.is_file():
        sftp = client.open_sftp()
        with sftp.file(f"{REMOTE}/.env", "w") as f:
            f.write(patch_env_for_server(local_env.read_text(encoding="utf-8")))
        for sh in (ROOT / "scripts" / "server").glob("*.sh"):
            sftp.put(str(sh), f"{REMOTE}/scripts/server/{sh.name}")
        sftp.put(str(ROOT / "requirements_server.txt"), f"{REMOTE}/requirements_server.txt")
        sftp.close()
        print("[OK] .env + server skripte postavljene")

    steps = [
        (f"cd {REMOTE} && sed -i 's/\\r$//' scripts/server/*.sh", "dos2unix"),
        (f"cd {REMOTE} && ./venv/bin/pip install --no-cache-dir greenlet aiosqlite -q", "greenlet"),
        (f"cd {REMOTE} && ./venv/bin/pip install --no-cache-dir -r requirements_server.txt -q", "pip"),
        (f"cd {REMOTE} && ./venv/bin/python -m app.calibrate_models --if-missing", "calibrate"),
        (f"cd {REMOTE} && ./scripts/server/stopbot.sh 2>/dev/null || true", "stop_old"),
        (f"cd {REMOTE} && chmod +x scripts/server/*.sh && ./scripts/server/install_systemd.sh", "systemd"),
        (
            f"cd {REMOTE} && ./venv/bin/python -m app.run_local --full-build",
            "full_build",
        ),
    ]

    for cmd, label in steps:
        print(f"\n--- {label} ---")
        code, out, err = run(client, cmd, timeout=900)
        if out.strip():
            safe_print(out.rstrip()[-3000:])
        if err.strip():
            safe_print("[stderr] " + err.rstrip()[-1000:])
        if code != 0 and label in ("pip", "systemd", "full_build"):
            print(f"[GRESKA] Korak '{label}' nije uspeo (exit {code})")
            client.close()
            return 1
        print(f"[exit {code}]")

    _, out, _ = run(
        client,
        "systemctl --user is-active football-dc-scheduler football-dc-telegram 2>&1",
        timeout=30,
    )
    safe_print("\n=== Status servisa ===")
    safe_print(out.strip())

    client.close()
    print("\n[OK] Bot pokrenut na serveru u ~/football-dc-bot")
    print("Stari bot u ~/projekti nije diran.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
