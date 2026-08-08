"""Enable auto-start on boot: systemd user linger + verify services."""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = os.environ.get("DEPLOY_REMOTE_DIR", "/home/miki/football-dc-bot")


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 120) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return stdout.channel.recv_exit_status(), out, err


def main() -> int:
    if not PASS:
        safe_print("[GRESKA] Postavi DEPLOY_PASS.")
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    # Upload latest install script
    sftp = client.open_sftp()
    local_sh = __file__.replace("_enable_autostart.py", "server/install_systemd.sh").replace("\\", "/")
    from pathlib import Path
    sh = Path(__file__).resolve().parent / "server" / "install_systemd.sh"
    sftp.put(str(sh), f"{REMOTE}/scripts/server/install_systemd.sh")
    sftp.close()

    steps = [
        # linger = user systemd servisi rade i posle reboota bez login-a
        f"sudo loginctl enable-linger {USER}",
        f"cd {REMOTE} && sed -i 's/\\r$//' scripts/server/*.sh && chmod +x scripts/server/*.sh",
        f"cd {REMOTE} && ./scripts/server/install_systemd.sh",
        "systemctl --user is-enabled football-dc-scheduler football-dc-telegram",
        "systemctl --user is-active football-dc-scheduler football-dc-telegram",
        f"loginctl show-user {USER} -p Linger",
    ]

    for cmd in steps:
        safe_print(f"\n--- {cmd} ---")
        code, out, err = run(client, cmd)
        if out.strip():
            safe_print(out.rstrip())
        if err.strip():
            safe_print("[stderr] " + err.rstrip())
        safe_print(f"[exit {code}]")
        if code != 0 and "sudo loginctl" in cmd:
            safe_print("[WARN] sudo za linger nije uspelo — probaj rucno na serveru")

    client.close()
    safe_print("\n[OK] Bot ce se automatski pokrenuti posle restarta servera.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
