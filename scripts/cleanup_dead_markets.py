#!/usr/bin/env python3
"""
Delete odds snapshots for markets outside the allowlist, then VACUUM the database.

Only match_winner, over_under and btts can ever become a pick, and ingestion no
longer stores anything else. The historical rows are dead weight, and they also
skew market_overround_1x2 in _market_features, which averages the overround
across every market present.

Dry run by default; pass --apply. Takes a backup and stops the services that
hold the database open, since VACUUM needs exclusive access.
"""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")

SERVICES = ("football-dc-scheduler", "football-dc-api", "football-dc-telegram")

SCRIPT_TMPL = r'''
import os
import sqlite3
import time

APPLY = {apply!r}
DB = "data/football_roi.db"
KEEP = ("match_winner", "over_under", "btts")

size_mb = os.path.getsize(DB) / 1024 / 1024
st = os.statvfs(".")
free_mb = st.f_bavail * st.f_frsize / 1024 / 1024
print(f"fajl pre: {{size_mb:.0f}} MB   slobodno: {{free_mb:.0f}} MB")
# VACUUM writes a full second copy alongside the backup.
if APPLY and free_mb < size_mb * 2.5:
    raise SystemExit("[STOP] nema dovoljno prostora za backup + VACUUM")

if APPLY:
    backup = f"{{DB}}.bak-{{time.strftime('%Y%m%d-%H%M%S')}}"
    src = sqlite3.connect(DB)
    dest = sqlite3.connect(backup)
    with dest:
        src.backup(dest)
    dest.close()
    src.close()
    print(f"backup: {{backup}}  ({{os.path.getsize(backup)/1024/1024:.0f}} MB)")

conn = sqlite3.connect(DB, timeout=300, isolation_level=None)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=300000")

before = conn.execute("SELECT COUNT(*) n FROM odds_snapshots").fetchone()["n"]
doomed = conn.execute(
    "SELECT COUNT(*) n FROM odds_snapshots WHERE market NOT IN ('match_winner','over_under','btts')"
).fetchone()["n"]
print(f"\nredova: {{before}}   za brisanje: {{doomed}} ({{doomed*100.0/before:.1f}}%)")

if not APPLY:
    print("\n[DRY RUN] nista nije obrisano. Pokreni sa --apply.")
    raise SystemExit(0)

deleted = 0
while True:
    cur = conn.execute(
        "DELETE FROM odds_snapshots WHERE id IN ("
        "  SELECT id FROM odds_snapshots"
        "  WHERE market NOT IN ('match_winner','over_under','btts') LIMIT 100000)"
    )
    if cur.rowcount <= 0:
        break
    deleted += cur.rowcount
    print(f"  obrisano {{deleted}}/{{doomed}}...", flush=True)

after = conn.execute("SELECT COUNT(*) n FROM odds_snapshots").fetchone()["n"]
print(f"\nobrisano: {{deleted}}   redova posle: {{after}}")

print("\n=== VACUUM (moze potrajati) ===")
t0 = time.time()
conn.execute("VACUUM")
print(f"  VACUUM zavrsen za {{time.time()-t0:.0f}}s")
try:
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
except Exception as exc:
    print(f"  (wal_checkpoint: {{exc}})")

print("\n=== Kontrola ===")
for r in conn.execute(
    "SELECT market, COUNT(*) n FROM odds_snapshots GROUP BY market ORDER BY n DESC"
):
    print(f"  {{r['market']:16s}} {{r['n']}}")
r = conn.execute(
    "SELECT COUNT(*) n FROM odds_snapshots WHERE market NOT IN ('match_winner','over_under','btts')"
).fetchone()
print(f"  van allowlist-a: {{r['n']}}")
print(f"  daily_picks: {{conn.execute('SELECT COUNT(*) n FROM daily_picks').fetchone()['n']}}")
print(f"  fixtures: {{conn.execute('SELECT COUNT(*) n FROM fixtures').fetchone()['n']}}")
print(f"  feature_vectors: {{conn.execute('SELECT COUNT(*) n FROM feature_vectors').fetchone()['n']}}")
conn.close()

new_mb = os.path.getsize(DB) / 1024 / 1024
print(f"\nfajl posle: {{new_mb:.0f}} MB  (bilo {{size_mb:.0f}} MB, oslobodjeno {{size_mb-new_mb:.0f}} MB)")
'''


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 7200) -> None:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    for line in iter(stdout.readline, ""):
        sys.stdout.buffer.write(line.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    err = stderr.read()
    if err:
        sys.stdout.buffer.write(err)
        sys.stdout.buffer.flush()


def main() -> int:
    apply = "--apply" in sys.argv

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    sftp = client.open_sftp()
    with sftp.open("/tmp/_cleanup_dead.py", "w") as fh:
        fh.write(SCRIPT_TMPL.format(apply=apply))
    sftp.close()

    units = " ".join(f"{s}.service" for s in SERVICES)
    if apply:
        print("=== Zaustavljam servise (VACUUM traži ekskluzivan pristup) ===")
        run(client, f"systemctl --user stop {units}; sleep 3; systemctl --user is-active {units}; true",
            timeout=300)

    try:
        run(client, f"cd {REMOTE} && PYTHONUTF8=1 venv/bin/python -u /tmp/_cleanup_dead.py 2>&1")
    finally:
        if apply:
            print("\n=== Vracam servise ===")
            run(client, f"systemctl --user start {units}; sleep 8; systemctl --user is-active {units}",
                timeout=300)
            print("\n=== API health ===")
            run(client, "curl -sf -m 15 http://127.0.0.1:8001/api/v1/health; echo", timeout=120)

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
