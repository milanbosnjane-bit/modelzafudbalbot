import sqlite3, tempfile, paramiko, io
from pathlib import Path
from datetime import datetime, timezone

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("192.168.1.106", username="miki", password="miki0510", timeout=30, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()
buf = io.BytesIO()
sftp.getfo("/home/miki/football-dc-bot/data/football_roi.db", buf)
sftp.close()
c.close()
p = Path(tempfile.gettempdir()) / "srv2.db"
p.write_bytes(buf.getvalue())
conn = sqlite3.connect(str(p))
today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
print("UTC today:", today)
print("Picks today:", conn.execute("SELECT id, selection, odds, outcome FROM daily_picks WHERE date(pick_date)=?", (today,)).fetchall())
print("All pending:", conn.execute("SELECT COUNT(*) FROM daily_picks WHERE outcome='pending'").fetchone()[0])
rows = conn.execute(
    "SELECT dp.id, dp.selection, dp.odds, f.fixture_date, f.status FROM daily_picks dp "
    "JOIN fixtures f ON f.id=dp.fixture_id WHERE dp.outcome='pending' ORDER BY f.fixture_date"
).fetchall()
print("Pending detail:", rows)
print("NS fixtures 48h:", conn.execute(
    "SELECT COUNT(*) FROM fixtures WHERE status='NS' AND fixture_date BETWEEN datetime('now') AND datetime('now','+2 days')"
).fetchone()[0])
print("NS fixtures 7d:", conn.execute(
    "SELECT COUNT(*) FROM fixtures WHERE status='NS' AND fixture_date BETWEEN datetime('now') AND datetime('now','+7 days')"
).fetchone()[0])
print("Next NS fixtures:", conn.execute(
    "SELECT fixture_date, status FROM fixtures WHERE status='NS' AND fixture_date > datetime('now') ORDER BY fixture_date LIMIT 5"
).fetchall())
print("Last daily_predictions log - picks 27:", conn.execute(
    "SELECT id, pick_date, outcome FROM daily_picks ORDER BY pick_date DESC LIMIT 5"
).fetchall())
