#!/usr/bin/env python3
"""
Deploy Football DC bot (v3) na remote Linux server u POSEBNOM folderu.

Postojeći bot na serveru se NE dira — samo se kreira novi direktorijum.

Upotreba (PowerShell):
  $env:DEPLOY_HOST="100.122.226.3"
  $env:DEPLOY_USER="oristupi"
  $env:DEPLOY_PASS="miki0510"
  python scripts/deploy_to_server.py --explore
  python scripts/deploy_to_server.py --deploy
  python scripts/deploy_to_server.py --deploy --include-env

Opciono:
  $env:DEPLOY_REMOTE_DIR="/home/oristupi/football-dc-bot"
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import zipfile
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REMOTE_DIR = "~/football-dc-bot"

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    "htmlcov",
    "dist",
    "build",
    ".cursor",
    "node_modules",
}

SKIP_FILES = {
    ".env",
    "bot_deploy.zip",
}

SKIP_PREFIXES = (
    "data/ab_tests/",
    "data/backtest",
)

SKIP_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".log",
    ".pkl",
)


def connect() -> paramiko.SSHClient:
    host = os.environ.get("DEPLOY_HOST", "").strip()
    user = os.environ.get("DEPLOY_USER", "").strip()
    password = os.environ.get("DEPLOY_PASS", "").strip()
    key_file = os.environ.get("DEPLOY_KEY", "").strip()

    if not host or not user:
        print("[GRESKA] Postavi DEPLOY_HOST i DEPLOY_USER u okruzenju.")
        sys.exit(1)
    if not password and not key_file:
        print("[GRESKA] Postavi DEPLOY_PASS ili DEPLOY_KEY (putanja do SSH kljuca).")
        sys.exit(1)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {"hostname": host, "username": user, "timeout": 30, "allow_agent": False, "look_for_keys": False}
    if key_file:
        kwargs["key_filename"] = key_file
    else:
        kwargs["password"] = password
    client.connect(**kwargs)
    return client


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def print_block(title: str, text: str) -> None:
    print(f"\n=== {title} ===")
    if text.strip():
        print(text.rstrip())
    else:
        print("(prazno)")


def explore(client: paramiko.SSHClient) -> None:
    remote_dir = os.environ.get("DEPLOY_REMOTE_DIR", DEFAULT_REMOTE_DIR)
    commands = [
        "whoami && hostname && pwd",
        "uname -a",
        "python3 --version 2>&1 || python --version 2>&1",
        "df -h ~",
        "ls -la ~",
        "find ~ -maxdepth 2 -type d \\( -name '*bot*' -o -name '*football*' -o -name '*fudbal*' \\) 2>/dev/null | head -30",
        f"test -d {remote_dir} && ls -la {remote_dir} || echo 'NOVA LOKACIJA ({remote_dir}) — jos ne postoji'",
    ]
    for cmd in commands:
        code, out, err = run(client, cmd, timeout=60)
        print_block(cmd, out)
        if err.strip():
            print(f"[stderr] {err.strip()}")
        if code != 0 and "NOVA LOKACIJA" not in out:
            print(f"[exit {code}]")


def should_include(rel: str) -> bool:
    parts = rel.replace("\\", "/").split("/")
    if parts[0] in SKIP_DIRS or any(p in SKIP_DIRS for p in parts):
        return False
    if parts[-1] in SKIP_FILES:
        return False
    norm = rel.replace("\\", "/")
    if norm.startswith("data/football_roi.db"):
        return True  # ukljuci punu bazu u deploy
    if any(norm.startswith(p) for p in SKIP_PREFIXES):
        return False
    if any(norm.endswith(s) for s in SKIP_SUFFIXES):
        return False
    return True


def build_zip(include_env: bool) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(ROOT))
            if rel == ".env" and not include_env:
                continue
            if not should_include(rel):
                continue
            zf.write(path, rel)
    return buf.getvalue()


def upload_bytes(sftp: paramiko.SFTPClient, data: bytes, remote_path: str) -> None:
    with sftp.file(remote_path, "wb") as f:
        f.write(data)


def deploy(client: paramiko.SSHClient, include_env: bool) -> None:
    remote_dir = os.environ.get("DEPLOY_REMOTE_DIR", DEFAULT_REMOTE_DIR)
    zip_bytes = build_zip(include_env=include_env)
    print(f"Paket: {len(zip_bytes) // 1024} KB")

    run(client, f"mkdir -p {remote_dir}")
    sftp = client.open_sftp()
    remote_zip = f"{remote_dir}/deploy_package.zip"
    print(f"Upload -> {remote_zip}")
    upload_bytes(sftp, zip_bytes, remote_zip)
    sftp.close()

    setup_cmds = f"""
set -e
cd {remote_dir}
unzip -o deploy_package.zip
rm -f deploy_package.zip
mkdir -p data/models data/features logs
if [ ! -d venv ]; then python3 -m venv venv; fi
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install --no-cache-dir -r requirements_server.txt -q
chmod +x scripts/server/*.sh
echo "SETUP_OK"
ls -la
"""
    code, out, err = run(client, setup_cmds, timeout=900)
    print_block("Setup", out)
    if err.strip():
        print_block("Setup stderr", err)
    if code != 0 or "SETUP_OK" not in out:
        print(f"[GRESKA] Setup nije uspeo (exit {code})")
        sys.exit(1)

    print(
        f"""
Deploy zavrsen u: {remote_dir}
Postojeci bot NIJE diran.

Sledeci koraci na serveru:
  1. SSH na server
  2. cd {remote_dir}
  3. cp .env.example .env   (ako nisi poslao --include-env)
  4. nano .env              (API_FOOTBALL_KEY, TELEGRAM_*)
  5. ./scripts/server/startbot.sh

Za pozadinu (systemd):
  ./scripts/server/install_systemd.sh
"""
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deploy football-dc-bot na remote server")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--explore", action="store_true", help="Samo pregled servera (read-only)")
    g.add_argument("--deploy", action="store_true", help="Deploy u novi folder")
    p.add_argument("--include-env", action="store_true", help="Ukljuci lokalni .env u paket")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        client = connect()
    except paramiko.AuthenticationException:
        print(
            "[GRESKA] SSH autentifikacija nije uspela.\n"
            "Proveri DEPLOY_HOST, DEPLOY_USER i DEPLOY_PASS.\n"
            "Ako server koristi kljuc: set DEPLOY_KEY=C:\\Users\\Miki\\.ssh\\id_rsa"
        )
        return 1
    except Exception as exc:
        print(f"[GRESKA] Ne mogu se povezati na server: {exc}")
        return 1

    try:
        if args.explore:
            explore(client)
        else:
            deploy(client, include_env=args.include_env)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
