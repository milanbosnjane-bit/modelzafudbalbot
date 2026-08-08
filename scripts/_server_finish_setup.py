"""Verify remote deploy and report status (no secret upload)."""
import os
import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = os.environ.get("DEPLOY_REMOTE_DIR", "/home/miki/football-dc-bot")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    checks = [
        f"cd {REMOTE} && ls -la",
        f"cd {REMOTE} && ./venv/bin/pip install --no-cache-dir -r requirements_server.txt -q",
        f"cd {REMOTE} && ./venv/bin/python -c 'import fastapi, telegram, scipy, sqlalchemy; print(\"deps_ok\")'",
        f"test -f {REMOTE}/.env && echo env_exists || echo env_missing",
        f"test -f {REMOTE}/data/models/dc_params.json && echo dc_params_ok || echo dc_params_missing",
        "ls -la ~/projekti/main.py 2>/dev/null && echo old_bot_untouched || true",
    ]
    for cmd in checks:
        print(f"\n=== {cmd} ===")
        _, stdout, stderr = c.exec_command(cmd, timeout=600)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        code = stdout.channel.recv_exit_status()
        if out.strip():
            print(out.rstrip()[-1500:])
        if err.strip():
            print("[stderr]", err.rstrip()[-500:])
        print(f"[exit {code}]")

    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
