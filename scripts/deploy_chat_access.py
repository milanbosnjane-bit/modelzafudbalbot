"""Deploy multi-chat Telegram access to server."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
REMOTE = os.environ.get("DEPLOY_REMOTE_DIR", "/home/miki/football-dc-bot")
CHAT_IDS = os.environ.get("TELEGRAM_CHAT_IDS", "1545745366,7542623445")

FILES = [
    "app/config.py",
    "app/telegram/interactive_bot.py",
    "app/telegram/bot.py",
]


def connect() -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    password = os.environ.get("DEPLOY_PASS", "").strip() or "miki0510"
    client.connect(
        HOST,
        username=USER,
        password=password,
        timeout=30,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return stdout.channel.recv_exit_status(), out, err


def patch_env(text: str, chat_ids: str) -> str:
    line = f"TELEGRAM_CHAT_ID={chat_ids}"
    if re.search(r"^TELEGRAM_CHAT_ID=", text, flags=re.MULTILINE):
        return re.sub(r"^TELEGRAM_CHAT_ID=.*$", line, text, count=1, flags=re.MULTILINE)
    return text.rstrip() + "\n" + line + "\n"


def main() -> int:
    client = connect()
    sftp = client.open_sftp()

    for rel in FILES:
        print(f"Upload {rel}")
        sftp.put(str(ROOT / rel), f"{REMOTE}/{rel}")

    env_path = f"{REMOTE}/.env"
    with sftp.file(env_path, "r") as f:
        env_text = f.read().decode("utf-8", errors="replace")
    new_env = patch_env(env_text, CHAT_IDS)
    with sftp.file(env_path, "w") as f:
        f.write(new_env)
    print(f"Patched {env_path}: TELEGRAM_CHAT_ID={CHAT_IDS}")
    sftp.close()

    for cmd, label in [
        ("systemctl --user restart football-dc-telegram.service football-dc-scheduler.service", "restart"),
        ("systemctl --user is-active football-dc-telegram.service football-dc-scheduler.service", "status"),
        (
            f"grep '^TELEGRAM_CHAT_ID=' {REMOTE}/.env",
            "verify_env",
        ),
    ]:
        print(f"\n--- {label} ---")
        code, out, err = run(client, cmd)
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("[stderr]", err.rstrip())
        if code != 0:
            print(f"[GRESKA] {label} failed (exit {code})")
            client.close()
            return 1

    client.close()
    print("\n[OK] Oba chat ID-a aktivna na serveru.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
