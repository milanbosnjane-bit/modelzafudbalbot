"""Brisanje/pregled pikova od danas."""
import sqlite3

conn = sqlite3.connect("data/football_roi.db")
c = conn.cursor()

print("=== SVI PIKOVI ZA DANAS (2026-07-06) ===")
c.execute("""
    SELECT id, pick_date, fixture_id, selection, odds, expected_value, outcome
    FROM daily_picks
    WHERE pick_date LIKE '2026-07-06%'
""")
rows = c.fetchall()
for r in rows:
    print(f"  id={r[0]} | {r[1]} | fixture:{r[2]} | {r[3]} @ {r[4]} | EV:{r[5]:.1%} | {r[6] or 'pending'}")

if not rows:
    print("  (nema pikova)")
    conn.close()
    raise SystemExit(0)

pending = [r for r in rows if not r[6]]
print(f"\nPending (bez ishoda): {len(pending)}")

if pending:
    c.execute("DELETE FROM daily_picks WHERE pick_date LIKE '2026-07-06%' AND (outcome IS NULL OR outcome = '')")
    print(f"Obrisano {c.rowcount} pending pikova.")
    conn.commit()

conn.close()
