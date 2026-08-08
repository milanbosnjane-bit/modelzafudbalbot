import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "football_roi.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

settled = conn.execute(
    "SELECT COUNT(*) c FROM daily_picks WHERE outcome IN ('win','lose','push')"
).fetchone()["c"]
with_clv = conn.execute(
    "SELECT COUNT(*) c FROM daily_picks WHERE outcome IN ('win','lose','push') AND clv IS NOT NULL"
).fetchone()["c"]
no_clv = settled - with_clv

rows = conn.execute(
    "SELECT outcome, stake_units, odds, user_odds, closing_odds, profit_units FROM daily_picks "
    "WHERE outcome IN ('win','lose','push')"
).fetchall()
st = pe = 0.0
for r in rows:
    stake = r["stake_units"] or 0.0
    entry = r["user_odds"] or r["odds"]
    st += stake
    if r["outcome"] == "win":
        pe += stake * (entry - 1)
    elif r["outcome"] == "lose":
        pe -= stake

st2 = pe2 = 0.0
n_close = 0
for r in rows:
    if not r["closing_odds"]:
        continue
    n_close += 1
    stake = r["stake_units"] or 0.0
    close = r["closing_odds"]
    st2 += stake
    if r["outcome"] == "win":
        pe2 += stake * (close - 1)
    elif r["outcome"] == "lose":
        pe2 -= stake

print(f"settled={settled} with_clv={with_clv} no_clv={no_clv}")
print(f"ROI all @ entry: {pe/st*100:+.2f}% (staked {st:.2f}u)")
print(f"ROI all @ closing (only {n_close} with closing_odds): {pe2/st2*100:+.2f}%")

print("\n--- Jul 30 pick batches (duplicate run check) ---")
for r in conn.execute(
    "SELECT id, fixture_id, created_at, pick_date, market, selection, odds, outcome, clv "
    "FROM daily_picks WHERE date(pick_date)='2026-07-30' OR date(created_at)='2026-07-30' "
    "ORDER BY created_at"
):
    print(
        f"id={r['id']} fix={r['fixture_id']} created={r['created_at']} "
        f"pick_date={r['pick_date']} {r['market']}/{r['selection']} @{r['odds']} "
        f"out={r['outcome']} clv={r['clv']}"
    )

print("\n--- Settled WITHOUT clv (first 15) ---")
for r in conn.execute(
    "SELECT id, fixture_id, outcome, odds, closing_odds, pick_date, created_at "
    "FROM daily_picks WHERE outcome IN ('win','lose','push') AND clv IS NULL "
    "ORDER BY id DESC LIMIT 15"
):
    print(dict(r))

clvs = [
    r[0]
    for r in conn.execute(
        "SELECT clv FROM daily_picks WHERE outcome IN ('win','lose','push') AND clv IS NOT NULL"
    )
]
print(f"\navg stored CLV (settled with clv): {sum(clvs)/len(clvs)*100:+.2f}%")

# Raw CLV recalc for all with closing
print("\n--- RAW vs STORED summary ---")
raws = []
for r in conn.execute(
    """
    SELECT p.id, p.odds, p.closing_odds, p.clv, o.fair_prob
    FROM daily_picks p
    LEFT JOIN odds_snapshots o ON o.fixture_id=p.fixture_id AND o.market=p.market
      AND o.selection=p.selection AND o.is_closing=1
    WHERE p.outcome IN ('win','lose','push') AND p.clv IS NOT NULL
    GROUP BY p.id
    """
):
    entry = r["odds"]
    close = r["closing_odds"]
    if close:
        raw = (entry / close) - 1
        raws.append(raw)
print(f"Mean RAW CLV (17 picks): {sum(raws)/len(raws)*100:+.2f}%")
print(f"Positive RAW: {sum(1 for x in raws if x>0)} / {len(raws)}")

conn.close()
