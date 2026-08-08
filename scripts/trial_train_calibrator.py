#!/usr/bin/env python3
"""
Dry trial: upload the calibrator module and train it on the server's real picks.

Touches nothing the bot imports — no pipeline change, no schema change, no service
restart. Writes only the model file, which no deployed code loads yet. The point is
to see the honest validation metrics before deciding whether to activate anything.
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

FILES = [
    ("app/model/__init__.py", f"{REMOTE}/app/model/__init__.py"),
    ("app/model/confidence_calibrator.py", f"{REMOTE}/app/model/confidence_calibrator.py"),
    ("app/model/confidence_context.py", f"{REMOTE}/app/model/confidence_context.py"),
    ("app/model/confidence_training_data.py", f"{REMOTE}/app/model/confidence_training_data.py"),
    ("scripts/train_confidence_calibrator.py", f"{REMOTE}/scripts/train_confidence_calibrator.py"),
]


def main() -> int:
    for local, _ in FILES:
        if not (ROOT / local).is_file():
            print(f"[GRESKA] nedostaje {local}")
            return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    client.exec_command(f"mkdir -p {REMOTE}/app/model {REMOTE}/scripts")[1].channel.recv_exit_status()

    sftp = client.open_sftp()
    for local, remote in FILES:
        sftp.put(str(ROOT / local), remote)
        print(f"  upload {local}")
    sftp.close()

    # confidence_service.py is deliberately NOT uploaded: it is the only file the
    # prediction pipeline would import, and the pipeline stays untouched for now.
    print()
    print("=== TRENING (samo cita bazu, pise model fajl) ===")
    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 "
        f"venv/bin/python scripts/train_confidence_calibrator.py 2>&1",
        timeout=1800,
    )
    sys.stdout.buffer.write(stdout.read() + stderr.read())
    sys.stdout.buffer.flush()

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
