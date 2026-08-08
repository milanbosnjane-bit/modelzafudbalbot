"""Brza provera statusa bota - danasnji pikovi i poslednji logovi."""
import sys
import sqlite3
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

conn = sqlite3.connect("data/football_roi.db")
c = conn.cursor()

today = date.today().isoformat()

# Danasnji pikovi
c.execute("""
    SELECT pick_date, fixture_id, market, selection, odds, confidence, expected_value, outcome
    FROM daily_picks WHERE pick_date >= ? ORDER BY pick_date DESC LIMIT 20
""", (today,))
rows = c.fetchall()
print(f"=== DANASNJI PIKOVI ({len(rows)}) ===")
for r in rows:
    outcome = r[7] or "pending"
    print(f"  {r[0]} | fixture:{r[1]} | {r[2]}/{r[3]} | kvota:{r[4]} | conf:{r[5]:.0%} | EV:{r[6]:.1%} | {outcome}")

if not rows:
    print("  (nema pikova za danas)")

# Poslednji 5 pikova u bazi
c.execute("""
    SELECT pick_date, fixture_id, selection, odds, outcome, profit_units
    FROM daily_picks ORDER BY created_at DESC LIMIT 5
""")
rows2 = c.fetchall()
print(f"\n=== POSLEDNJI 5 PIKOVA U BAZI ===")
for r in rows2:
    print(f"  {r[0]} | fixture:{r[1]} | {r[2]} @ {r[3]} | {r[4] or 'pending'} | profit:{r[5]}")

# Ukupna statistika
c.execute("SELECT COUNT(*), SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END), SUM(profit_units) FROM daily_picks WHERE outcome IS NOT NULL")
total, wins, profit = c.fetchone()
print(f"\n=== UKUPNA STATISTIKA ===")
print(f"  Ukupno setlovano: {total}")
print(f"  Pobede: {wins}")
if total and total > 0:
    print(f"  Winrate: {wins/total:.1%}")
    print(f"  Ukupan profit: {profit:.2f} jedinica")

conn.close()
