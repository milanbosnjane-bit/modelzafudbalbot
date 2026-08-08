"""Provera da li je scheduler danas povukao mečeve."""
import sqlite3
import tempfile
import paramiko
import io
from pathlib import Path
from datetime import datetime, timezone

HOST = "192.168.1.106"
USER = "miki"
PASS = "miki0510"
REMOTE = "/home/miki/football-dc-bot"

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

# scheduler log + journal
for cmd in [
    "systemctl --user is-active football-dc-scheduler.service",
    "journalctl --user -u football-dc-scheduler --since today --no-pager 2>/dev/null | tail -40",
    f"tail -30 {REMOTE}/logs/scheduler.log 2>/dev/null || echo 'no scheduler.log'",
]:
    print(f"\n=== {cmd[:70]} ===")
    _, o, e = c.exec_command(cmd, timeout=60)
    out = o.read().decode("utf-8", errors="replace")
    err = e.read().decode("utf-8", errors="replace")
    if out.strip():
        print(out.rstrip()[-3500:])
    if err.strip():
        print("[stderr]", err.rstrip()[-500:])

sftp = c.open_sftp()
buf = io.BytesIO()
sftp.getfo(f"{REMOTE}/data/football_roi.db", buf)
sftp.close()
c.close()

p = Path(tempfile.gettempdir()) / "srv_check.db"
p.write_bytes(buf.getvalue())
conn = sqlite3.connect(str(p))
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

print(f"\n=== Baza (UTC danas: {today}) ===")
print("Picks danas:", conn.execute(
    "SELECT COUNT(*) FROM daily_picks WHERE date(pick_date)=?", (today,)
).fetchone()[0])

print("\nMecevi danas (fixture_date):")
for r in conn.execute(
    """
    SELECT f.id, f.fixture_date, f.status, f.league_id,
           (SELECT COUNT(*) FROM odds_snapshots o WHERE o.fixture_id=f.id) as odds_n
    FROM fixtures f
    WHERE date(f.fixture_date)=?
    ORDER BY f.fixture_date
    LIMIT 30
    """,
    (today,),
):
    print(r)

print("\nNS mecevi 7 dana:", conn.execute(
    "SELECT COUNT(*) FROM fixtures WHERE status='NS' AND fixture_date BETWEEN datetime('now') AND datetime('now','+7 days')"
).fetchone()[0])

print("Poslednji ingestovani mecevi (po datumu):")
for r in conn.execute(
    "SELECT date(fixture_date), status, COUNT(*) FROM fixtures GROUP BY date(fixture_date), status ORDER BY date(fixture_date) DESC LIMIT 12"
):
    print(r)

print("\nPoslednji odds snapshot:")
print(conn.execute(
    "SELECT MAX(captured_at), COUNT(*) FROM odds_snapshots WHERE date(captured_at)=date('now')"
).fetchone())

# re-connect for journal
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
_, o, _ = c.exec_command(
    "journalctl --user -u football-dc-scheduler --since '2026-07-28 05:50:00' --no-pager 2>/dev/null | tail -25",
    timeout=60,
)
print("\n=== Journal posle 05:50 UTC ===")
print(o.read().decode("utf-8", errors="replace")[-2500:])
c.close()
