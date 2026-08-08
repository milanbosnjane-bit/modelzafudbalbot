#!/usr/bin/env python3
"""Assert the app /picks/today output is identical to the Telegram LIVE PICKS list. Read-only."""
from __future__ import annotations

import json
import os
import sys
import urllib.request

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")
API = f"http://{HOST}:8001/api/v1"

SCRIPT = r"""
import asyncio, json

from app.telegram.stats_service import get_telegram_live_picks_rows


async def main():
    rows = await get_telegram_live_picks_rows(max_display=None)
    out = [
        {
            "rank": r.pick.rank,
            "id": r.pick.pick_id,
            "market": r.pick.market,
            "selection": r.pick.selection,
            "status": r.status,
            "label": r.pick.match_label,
        }
        for r in rows
    ]
    print("<<<JSON>>>" + json.dumps(out))


asyncio.run(main())
"""


def telegram_rows() -> list[dict]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_parity_tg.py", "w") as fh:
        fh.write(SCRIPT)
    sftp.close()
    _, stdout, stderr = client.exec_command(
        f"cd {REMOTE} && PYTHONPATH={REMOTE} PYTHONUTF8=1 venv/bin/python /tmp/_parity_tg.py 2>&1",
        timeout=600,
    )
    raw = (stdout.read() + stderr.read()).decode("utf-8", errors="replace")
    client.close()
    marker = raw.find("<<<JSON>>>")
    if marker < 0:
        raise RuntimeError(f"Telegram pipeline nije vratio JSON:\n{raw}")
    return json.loads(raw[marker + len("<<<JSON>>>"):].strip())


def main() -> int:
    tg = telegram_rows()
    app = json.loads(urllib.request.urlopen(f"{API}/picks/today", timeout=40).read())

    print(f"Telegram LIVE PICKS : {len(tg)} pikova")
    print(f"App /picks/today    : {len(app)} pikova\n")

    ok = True
    if len(tg) != len(app):
        ok = False
        print(f"[FAIL] Razlicit broj pikova: telegram={len(tg)} app={len(app)}")

    print(f"{'#':>3}  {'TELEGRAM':<44} | {'APP':<44}  poklapa")
    print("-" * 108)
    for index in range(max(len(tg), len(app))):
        t = tg[index] if index < len(tg) else None
        a = app[index] if index < len(app) else None
        t_key = (t["id"], t["market"], t["selection"], t["rank"]) if t else None
        a_key = (a["id"], a["market"], a["selection"], a["rank"]) if a else None
        same = t_key == a_key
        ok = ok and same
        t_txt = f"#{t['rank']:2d} id={t['id']} {t['market'][:12]:12s} {t['selection'][:9]:9s} {t['status'][:4]}" if t else "-"
        a_txt = f"#{a['rank']:2d} id={a['id']} {a['market'][:12]:12s} {a['selection'][:9]:9s} {a.get('status', '?')[:4]}" if a else "-"
        print(f"{index + 1:>3}  {t_txt:<44} | {a_txt:<44}  {'OK' if same else 'RAZLIKA'}")

    app_ranks = [p["rank"] for p in app]
    print(f"\nApp rankovi: {app_ranks}")
    expected = list(range(1, len(app) + 1))
    if app_ranks != expected:
        ok = False
        print(f"[FAIL] Rankovi nisu 1..{len(app)} (duplikati ili preskakanja)")
    else:
        print(f"[OK] Rankovi su neprekidni 1..{len(app)}, bez ponavljanja")

    live_app = sum(1 for p in app if (p.get("status") or "").upper() == "LIVE")
    live_tg = sum(1 for p in tg if p["status"].upper() == "LIVE")
    print(f"[{'OK' if live_app == live_tg else 'FAIL'}] u toku meca: telegram={live_tg} app={live_app}")
    ok = ok and live_app == live_tg

    print("\n" + ("=== PARITET POTVRDJEN: app == telegram ===" if ok else "=== NEUSPEH: postoje razlike ==="))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
