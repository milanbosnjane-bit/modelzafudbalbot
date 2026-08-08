"""Prebaci kompletnu lokalnu bazu (football_roi.db) na server."""
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
LOCAL_DB = ROOT / "data" / "football_roi.db"


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main() -> int:
    if not PASS:
        safe_print("[GRESKA] Postavi DEPLOY_PASS.")
        return 1
    if not LOCAL_DB.is_file():
        safe_print(f"[GRESKA] Nema baze: {LOCAL_DB}")
        return 1

    size_mb = LOCAL_DB.stat().st_size / (1024 * 1024)
    safe_print(f"Lokalna baza: {LOCAL_DB} ({size_mb:.1f} MB)")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    remote_db = f"{REMOTE}/data/football_roi.db"
    remote_tmp = f"{remote_db}.upload"

    # Zaustavi servise pre zamene baze
    for cmd in [
        f"cd {REMOTE} && ./scripts/server/stopbot.sh 2>/dev/null || true",
        "systemctl --user stop football-dc-scheduler.service football-dc-telegram.service 2>/dev/null || true",
        f"mkdir -p {REMOTE}/data",
    ]:
        client.exec_command(cmd)

    safe_print("Upload baze (moze potrajati nekoliko minuta)...")
    sftp = client.open_sftp()

    def progress(transferred: int, total: int) -> None:
        pct = (transferred / total * 100) if total else 0
        if transferred == total or transferred % (10 * 1024 * 1024) < 65536:
            safe_print(f"  {transferred // (1024*1024)} / {total // (1024*1024)} MB ({pct:.0f}%)")

    sftp.put(str(LOCAL_DB), remote_tmp, callback=progress)
    sftp.close()

    _, stdout, stderr = client.exec_command(
        f"mv {remote_tmp} {remote_db} && ls -lh {remote_db}",
        timeout=60,
    )
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    safe_print(out.strip())
    if err.strip():
        safe_print("[stderr] " + err.strip())

    # Restart servisa
    _, stdout, _ = client.exec_command(
        f"cd {REMOTE} && sed -i 's/\\r$//' scripts/server/*.sh && "
        f"chmod +x scripts/server/*.sh && ./scripts/server/install_systemd.sh",
        timeout=120,
    )
    safe_print(stdout.read().decode("utf-8", errors="replace")[-2000:])

    client.close()
    safe_print("[OK] Baza prebacena i servisi restartovani.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
