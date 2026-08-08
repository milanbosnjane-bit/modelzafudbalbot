#!/usr/bin/env python3
"""
Delete odds snapshot rows produced by the old leaky market normalisation.

Two sets:
  A) any selection containing "/" — combo bets such as "Away/Over 2.5" that were
     stored under a real market key. No pick has ever used such a selection.
  B) pre-fix over_under rows for upcoming fixtures, so no stale phantom key can
     still win the "latest row per key" race in _group_odds_snapshots.

Dry run by default. Pass --apply to delete. Always takes a database backup first
and stops the scheduler while writing.
"""
from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("SERVER_IP") or os.environ.get("DEPLOY_HOST", "100.122.226.3")
USER = os.environ.get("SERVER_USER") or os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = os.environ.get("REMOTE_PATH", "/home/miki/football-dc-bot")
CUTOFF = os.environ.get("FIX_CUTOFF", "2026-08-08 15:29:00")

DB = "data/football_roi.db"

SCRIPT_TMPL = r'''
import os
import shutil
import sqlite3
import time

CUTOFF = {cutoff!r}
APPLY = {apply!r}
DB = "data/football_roi.db"

size_mb = os.path.getsize(DB) / 1024 / 1024
st = os.statvfs(".")
free_mb = st.f_bavail * st.f_frsize / 1024 / 1024
print(f"baza: {{size_mb:.0f}} MB, slobodno na disku: {{free_mb:.0f}} MB")
if APPLY and free_mb < size_mb * 1.2:
    raise SystemExit("[STOP] nema dovoljno prostora za backup")

if APPLY:
    backup = f"{{DB}}.bak-{{time.strftime('%Y%m%d-%H%M%S')}}"
    print(f"backup -> {{backup}}")
    conn = sqlite3.connect(DB)
    dest = sqlite3.connect(backup)
    with dest:
        conn.backup(dest)
    dest.close()
    conn.close()
    print(f"backup napravljen: {{os.path.getsize(backup) / 1024 / 1024:.0f}} MB")

conn = sqlite3.connect(DB, timeout=120)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=120000")

COMBO = "SELECT id FROM odds_snapshots WHERE selection LIKE '%/%'"
STALE = f"""
    SELECT o.id FROM odds_snapshots o
    JOIN fixtures f ON f.id = o.fixture_id
    WHERE f.status='NS' AND f.fixture_date >= datetime('now')
      AND o.market='over_under' AND o.captured_at < '{{CUTOFF}}'
      AND o.selection NOT LIKE '%/%'
"""

before = conn.execute("SELECT COUNT(*) n FROM odds_snapshots").fetchone()["n"]
n_combo = conn.execute(f"SELECT COUNT(*) n FROM ({{COMBO}})").fetchone()["n"]
n_stale = conn.execute(f"SELECT COUNT(*) n FROM ({{STALE}})").fetchone()["n"]
print(f"\nredova sada: {{before}}")
print(f"  A) kombinacije sa '/': {{n_combo}}")
print(f"  B) stari over_under za predstojece: {{n_stale}}")
print(f"  za brisanje: {{n_combo + n_stale}}")

if not APPLY:
    print("\n[DRY RUN] nista nije obrisano. Pokreni sa --apply.")
    raise SystemExit(0)

deleted = 0
for label, sql in (("A", COMBO), ("B", STALE)):
    while True:
        cur = conn.execute(
            f"DELETE FROM odds_snapshots WHERE id IN "
            f"(SELECT id FROM ({{sql}}) LIMIT 50000)"
        )
        conn.commit()
        if cur.rowcount <= 0:
            break
        deleted += cur.rowcount
        print(f"  {{label}}: obrisano {{deleted}}...", flush=True)

after = conn.execute("SELECT COUNT(*) n FROM odds_snapshots").fetchone()["n"]
print(f"\nobrisano ukupno: {{deleted}}")
print(f"redova posle: {{after}}  (bilo {{before}})")

print("\n=== Kontrola ===")
print("  kombinacija sa '/':", conn.execute(f"SELECT COUNT(*) n FROM ({{COMBO}})").fetchone()["n"])
print("  starih over_under za predstojece:", conn.execute(f"SELECT COUNT(*) n FROM ({{STALE}})").fetchone()["n"])
print("  daily_picks netaknuti:", conn.execute("SELECT COUNT(*) n FROM daily_picks").fetchone()["n"])
conn.close()
'''


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 3600) -> None:
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

    script = SCRIPT_TMPL.format(cutoff=CUTOFF, apply=apply)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_cleanup_odds.py", "w") as fh:
        fh.write(script)
    sftp.close()

    if apply:
        print("=== Zaustavljam scheduler (da baza ne bude zakljucana) ===")
        run(client, "systemctl --user stop football-dc-scheduler.service; sleep 2; "
                    "systemctl --user is-active football-dc-scheduler.service; true", timeout=180)

    try:
        run(client, f"cd {REMOTE} && PYTHONUTF8=1 venv/bin/python -u /tmp/_cleanup_odds.py 2>&1")
    finally:
        if apply:
            print("\n=== Vracam scheduler ===")
            run(client, "systemctl --user start football-dc-scheduler.service; sleep 5; "
                        "systemctl --user is-active football-dc-scheduler.service", timeout=180)

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
