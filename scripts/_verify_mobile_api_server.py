#!/usr/bin/env python3
import os, paramiko
HOST = os.environ.get("SERVER_IP", "192.168.1.106")
USER = os.environ.get("SERVER_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=20, allow_agent=False, look_for_keys=False)
cmds = [
    "readlink /proc/2029/cwd 2>/dev/null || echo no_cwd",
    "ls -la /home/miki/football-dc-bot/app/api/",
    "grep -c mobile_router /home/miki/football-dc-bot/app/api/routes.py || echo 0",
    "ss -tlnp 2>/dev/null | grep 8000 || netstat -tlnp 2>/dev/null | grep 8000 || echo no_ss",
    "systemctl --user list-units --type=service 2>/dev/null | grep football || echo no_football_units",
    "curl -s http://127.0.0.1:8000/ | head -c 200",
]
for cmd in cmds:
    _, o, e = c.exec_command(cmd, timeout=30)
    print("===", cmd)
    print(o.read().decode())
    err = e.read().decode().strip()
    if err:
        print("[stderr]", err)
c.close()
