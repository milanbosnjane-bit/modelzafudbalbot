"""Dublja analiza: winrate vs ROI po periodu, kvoti, tipu."""
import sqlite3
from collections import defaultdict

conn = sqlite3.connect("data/football_roi.db")
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT date(pick_date) as d, selection, market, odds, outcome,
           stake_units, profit_units, expected_value
    FROM daily_picks
    WHERE outcome IN ('win','lose','push')
    ORDER BY pick_date ASC
""").fetchall()

def stats(group):
    wins = sum(1 for r in group if r["outcome"] == "win")
    losses = sum(1 for r in group if r["outcome"] == "lose")
    staked = sum(r["stake_units"] or 0 for r in group)
    profit = sum(r["profit_units"] or 0 for r in group)
    wr = wins / (wins + losses) * 100 if wins + losses else 0
    roi = profit / staked * 100 if staked else 0
    avg_odds = sum(r["odds"] for r in group) / len(group) if group else 0
    return len(group), wins, losses, wr, staked, profit, roi, avg_odds

print("=== PO DANU (hronološki) ===")
print(f"{'Datum':<12} {'N':>3} {'W':>3} {'L':>3} {'WR%':>6} {'Profit':>8} {'ROI%':>8} {'Kum.profit':>10}")
print("-" * 60)
cum = 0.0
by_day = defaultdict(list)
for r in rows:
    by_day[r["d"]].append(r)
for d in sorted(by_day):
    n, w, l, wr, st, pr, roi, _ = stats(by_day[d])
    cum += pr
    print(f"{d:<12} {n:>3} {w:>3} {l:>3} {wr:>6.1f} {pr:>+8.2f} {roi:>+8.1f} {cum:>+10.2f}")

print("\n=== PO VRSTI TIPA ===")
by_sel = defaultdict(list)
for r in rows:
    key = f"{r['market']}/{r['selection']}"
    by_sel[key].append(r)
for key in sorted(by_sel, key=lambda k: stats(by_sel[k])[5]):
    n, w, l, wr, st, pr, roi, avg = stats(by_sel[key])
    print(f"  {key:<28} n={n:>2} W/L={w}/{l} WR={wr:>5.1f}% profit={pr:>+7.2f}u ROI={roi:>+7.1f}% avg_kv={avg:.2f}")

print("\n=== PO OPSEGU KVOTE ===")
def bucket(o):
    if o < 2.0: return "1.50-1.99 (niske)"
    if o < 3.0: return "2.00-2.99 (srednje)"
    if o < 5.0: return "3.00-4.99 (visoke)"
    return "5.00+ (longshot)"
by_odds = defaultdict(list)
for r in rows:
    by_odds[bucket(r["odds"])].append(r)
for b in ["1.50-1.99 (niske)", "2.00-2.99 (srednje)", "3.00-4.99 (visoke)", "5.00+ (longshot)"]:
    if b not in by_odds: continue
    n, w, l, wr, st, pr, roi, avg = stats(by_odds[b])
    print(f"  {b:<22} n={n:>2} W/L={w}/{l} WR={wr:>5.1f}% profit={pr:>+7.2f}u ROI={roi:>+7.1f}%")

print("\n=== PERIOD: JUN (stara logika) vs JUL (novija) ===")
jun = [r for r in rows if r["d"] < "2026-07-01"]
jul = [r for r in rows if r["d"] >= "2026-07-01"]
for label, grp in [("Jun 27-29", jun), ("Jul 07-09", jul)]:
    n, w, l, wr, st, pr, roi, avg = stats(grp)
    print(f"  {label}: n={n} W/L={w}/{l} WR={wr:.1f}% profit={pr:+.2f}u ROI={roi:+.1f}% avg_kv={avg:.2f}")

print("\n=== UNDER 2.5 SAMO — Jul trend ===")
u25_jul = [r for r in rows if r["selection"] == "Under 2.5" and r["d"] >= "2026-07-01"]
for r in u25_jul:
    icon = "W" if r["outcome"]=="win" else "L"
    print(f"  {r['d']} @{r['odds']:.2f} {icon} {r['profit_units']:+.2f}u")

print("\n=== BTTS No — svi ===")
no = [r for r in rows if r["selection"] == "No"]
n, w, l, wr, st, pr, roi, avg = stats(no)
print(f"  n={n} W/L={w}/{l} WR={wr:.1f}% profit={pr:+.2f}u ROI={roi:.1f}%")

print("\n=== NIZOVI (win/lose pattern) ===")
seq = "".join("W" if r["outcome"]=="win" else "L" for r in rows)
print(f"  Sekvenca ({len(seq)} tipova): {seq}")
# count alternating
alt = sum(1 for i in range(1, len(seq)) if seq[i] != seq[i-1])
print(f"  Promena ishoda: {alt}/{len(seq)-1} ({alt/(len(seq)-1)*100:.0f}% alternira)" if len(seq)>1 else "")

conn.close()
