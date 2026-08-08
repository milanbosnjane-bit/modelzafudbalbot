#!/usr/bin/env python3
"""Verify /picks/today matches persisted daily_picks rows 1:1 (read-only, server-side query)."""
from __future__ import annotations

import json
import os
import urllib.request

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")
API_PORT = int(os.environ.get("FOOTBALL_API_PORT", "8001"))

FINISHED = ("FT", "AET", "PEN", "AWD", "WO")
LIVE = ("1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT", "SUSP", "BREAK")

REMOTE_QUERY = r"""
import json, sqlite3
from datetime import datetime

conn = sqlite3.connect("file:{db}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
today = datetime.utcnow().strftime("%Y-%m-%d")
now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

batches = [
    dict(r)
    for r in conn.execute(
        "SELECT pick_date, COUNT(*) n FROM daily_picks WHERE date(pick_date)=? "
        "GROUP BY pick_date ORDER BY pick_date",
        (today,),
    )
]

rows = [
    dict(r)
    for r in conn.execute(
        '''
        SELECT p.id, p.pick_date, p.rank, p.fixture_id, p.market, p.selection,
               p.odds, p.probability, p.expected_value, p.confidence, p.roi_score,
               p.stake_units, p.outcome, p.is_paper,
               f.status AS fstatus, f.fixture_date,
               ht.name AS home, at.name AS away
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN teams ht ON ht.id = f.home_team_id
        LEFT JOIN teams at ON at.id = f.away_team_id
        WHERE date(p.pick_date) = ?
          AND (p.outcome IS NULL OR p.outcome = '' OR lower(p.outcome) = 'pending')
        ORDER BY p.pick_date, p.rank
        ''',
        (today,),
    )
]
conn.close()
print(json.dumps({{"today": today, "now": now, "batches": batches, "rows": rows}}, default=str))
""".format(db=f"{REMOTE}/data/football_roi.db")


def remote_json() -> dict:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_verify_picks_query.py", "w") as fh:
        fh.write(REMOTE_QUERY)
    sftp.close()
    _, stdout, stderr = client.exec_command("python3 /tmp/_verify_picks_query.py", timeout=120)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    client.close()
    if not out.strip():
        raise SystemExit(f"Remote query failed: {err}")
    return json.loads(out)


def api_json() -> list[dict]:
    url = f"http://{HOST}:{API_PORT}/api/v1/picks/today"
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read())


def main() -> None:
    data = remote_json()
    api = api_json()
    now = data["now"]

    print(f"=== daily_picks za {data['today']} (UTC), now={now} ===")
    print("Batch-evi:")
    for b in data["batches"]:
        print(f"  {b['pick_date']}: {b['n']} redova")

    print(f"\nPending redova u bazi: {len(data['rows'])}")
    expected = []
    for r in data["rows"]:
        fs = (r["fstatus"] or "NS").strip().upper()
        kickoff = str(r["fixture_date"])[:19]
        pre = kickoff > now and fs not in FINISHED and fs not in LIVE
        tag = "PRE-KICKOFF" if pre else ("LIVE" if fs in LIVE else "ZAVRSEN")
        print(
            f"  id={r['id']} #{r['rank']} [{tag}] {r['home']} vs {r['away']} | "
            f"{r['market']}/{r['selection']} @ {r['odds']:.2f} | fixture={fs} | ko={kickoff}"
        )
        if pre:
            expected.append(r)

    print(f"\n=== OCEKIVANO (pre-kickoff) = {len(expected)} ===")
    print(f"=== API /picks/today = {len(api)} ===")

    exp_keys = {(r["id"], r["fixture_id"], r["market"], r["selection"]) for r in expected}
    api_keys = {(p["id"], None, p["market"], p["selection"]) for p in api}
    exp_ids = {r["id"] for r in expected}
    api_ids = {p["id"] for p in api}

    ok = True
    if exp_ids != api_ids:
        ok = False
        print(f"\n[MISMATCH] samo u bazi: {sorted(exp_ids - api_ids)}")
        print(f"[MISMATCH] samo u API: {sorted(api_ids - exp_ids)}")

    by_id = {r["id"]: r for r in expected}
    for p in api:
        r = by_id.get(p["id"])
        if not r:
            continue
        for field, db_field in (
            ("market", "market"),
            ("selection", "selection"),
            ("odds", "odds"),
            ("probability", "probability"),
            ("expected_value", "expected_value"),
            ("confidence", "confidence"),
            ("roi_score", "roi_score"),
        ):
            db_val = r[db_field]
            api_val = p[field]
            if isinstance(db_val, float) or isinstance(api_val, float):
                same = abs(float(db_val) - float(api_val)) < 1e-9
            else:
                same = db_val == api_val
            if not same:
                ok = False
                print(f"[MISMATCH] id={p['id']} {field}: db={db_val} api={api_val}")

    print("\nRezultat:", "1:1 POKLAPANJE" if ok and len(expected) == len(api) else "NE POKLAPA SE")
    _ = exp_keys, api_keys


if __name__ == "__main__":
    main()
