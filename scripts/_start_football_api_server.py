#!/usr/bin/env python3
import os, paramiko
HOST = os.environ.get("SERVER_IP", "192.168.1.106")
USER = os.environ.get("SERVER_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = "/home/miki/football-dc-bot"
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=20, allow_agent=False, look_for_keys=False)

def run(cmd):
    _, o, e = c.exec_command(cmd, timeout=60)
    out = o.read().decode()
    err = e.read().decode().strip()
    print("===", cmd)
    print(out)
    if err:
        print("[stderr]", err[:500])
    return out

run("docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null || echo no_docker")
run("ps aux | grep -E 'uvicorn|8000' | grep -v grep")
run(f"test -x {REMOTE}/venv/bin/uvicorn && echo venv_ok || echo no_venv")
run(f"cd {REMOTE} && venv/bin/python -c \"from app.api.mobile_routes import mobile_router; print('mobile_router_ok', mobile_router)\"")

# Try start on 8001 to avoid conflict
start_cmd = f"""
cd {REMOTE}
mkdir -p logs
pkill -u miki -f 'uvicorn app.main:app.*8001' 2>/dev/null || true
sleep 1
nohup venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 >> logs/fastapi.log 2>&1 &
sleep 3
curl -sf http://127.0.0.1:8001/api/v1/health || echo health_8001_fail
curl -sf http://127.0.0.1:8001/api/v1/status || echo status_8001_fail
curl -sf 'http://127.0.0.1:8001/api/v1/odds/tracker?limit=1' || echo tracker_8001_fail
pgrep -af 'uvicorn app.main:app'
"""
run(start_cmd)
c.close()
