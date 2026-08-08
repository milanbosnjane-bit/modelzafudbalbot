import json
import sqlite3
import math

c = sqlite3.connect("data/football_roi.db")

def analyze_run(name):
    row = c.execute(
        "SELECT results, total_bets, roi_pct, win_rate FROM backtest_runs WHERE name=? ORDER BY id DESC LIMIT 1",
        (name,),
    ).fetchone()
    picks = json.loads(row[0]).get("picks", [])
    print(f"\n=== {name} (stored {len(picks)} of {row[1]} total bets) ===")
    for mkt_key in ["match_winner/draw", "over_under/under 2.5", "over_under/Under 2.5"]:
        sub = [p for p in picks if f"{p['market']}/{p['selection']}".lower() == mkt_key.lower()]
        if not sub:
            continue
        wins = sum(1 for p in sub if p["outcome"] == "win")
        profit = sum(p["profit"] for p in sub)
        staked = sum(p["stake"] for p in sub)
        avg_odds = sum(p["odds"] for p in sub) / len(sub)
        avg_ev = sum(p["ev"] for p in sub) / len(sub)
        print(f"  {mkt_key}: n={len(sub)} WR={wins/len(sub):.1%} ROI={100*profit/staked:+.1f}% avg_odds={avg_odds:.2f} avg_EV={avg_ev:.1%}")

# Binomial: draw WR 63% on 89 picks vs base rate 27%
n, k, p = 89, 57, 0.267  # ~57 wins for 63%
# approximate p-value
from math import comb
prob = sum(comb(n, i) * p**i * (1-p)**(n-i) for i in range(k, n+1))
print(f"\n=== STATISTIKA: 63% draw WR na 89 tipova vs 26.7% baza ===")
print(f"  P(sreća, bez edge-a): ~{prob:.2e}  (praktično nemoguće)")

print("\n=== ZAKLJUČAK: BLIND vs BOT ===")
print("  2025 blind draw ROI:  -7.6%  | bot draw: -9.8%  → bot ≈ market")
print("  2026 blind draw ROI:  -0.4%  | bot draw: +146%  → bot >> market  ⚠️")
print("  2025 blind under ROI: -6.2%  | bot under: -2.0% → bot malo bolji")
print("  2026 blind under ROI: -7.8%  | bot under: +60%  → bot >> market  ⚠️")

analyze_run("oos_2025")
analyze_run("oos_2026_q2")
