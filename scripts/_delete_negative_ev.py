"""Obriši 3 pika sa negativnim EV od 2026-07-06."""
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

conn = sqlite3.connect("data/football_roi.db")
c = conn.cursor()

c.execute("""
    SELECT id, pick_date, fixture_id, selection, odds, expected_value, outcome
    FROM daily_picks
    WHERE pick_date LIKE '2026-07-06%'
    ORDER BY id
""")
rows = c.fetchall()

print("=== PIKOVI 2026-07-06 ===")
for r in rows:
    print(f"  id={r[0]} | fixture:{r[2]} | {r[3]} @ {r[4]} | EV:{r[5]:.1%} | outcome={r[6]!r}")

# Obriši one sa negativnim EV (pending)
to_delete = [r for r in rows if r[5] is not None and r[5] < 0]
if not to_delete:
    print("\nNema pikova sa negativnim EV za brisanje.")
    conn.close()
    raise SystemExit(0)

ids = [r[0] for r in to_delete]
placeholders = ",".join("?" * len(ids))

c.execute(f"DELETE FROM daily_picks WHERE id IN ({placeholders})", ids)
deleted = c.rowcount

conn.commit()
print(f"\nObrisano {deleted} pikova (ids: {ids})")

c.execute("SELECT COUNT(*) FROM daily_picks WHERE pick_date LIKE '2026-07-06%'")
print(f"Preostalo za 2026-07-06: {c.fetchone()[0]}")

conn.close()
