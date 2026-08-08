import sqlite3
conn = sqlite3.connect("data/football_roi.db")
c = conn.cursor()
c.execute("""
    SELECT MIN(date(pick_date)), MAX(date(pick_date)), COUNT(*),
           SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END),
           SUM(CASE WHEN outcome='lose' THEN 1 ELSE 0 END),
           ROUND(SUM(profit_units),2), ROUND(SUM(stake_units),2)
    FROM daily_picks WHERE outcome IN ('win','lose','push')
""")
print("ROI statistika (win/lose/push):", c.fetchone())
c.execute("""
    SELECT date(pick_date), COUNT(*), GROUP_CONCAT(selection||'@'||ROUND(odds,2))
    FROM daily_picks WHERE outcome IN ('win','lose','push')
    GROUP BY date(pick_date) ORDER BY 1
""")
print("\nDani kad su tipovi STVARNO generisani i setlovani:")
for r in c.fetchall():
    print(f"  {r[0]}: {r[1]} tipova — {r[2][:80]}...")
conn.close()
