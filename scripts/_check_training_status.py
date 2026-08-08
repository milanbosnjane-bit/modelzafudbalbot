"""One-off: training status + latest metrics from DB."""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "football_roi.db"
MODELS = ROOT / "data" / "models"

print("=== MODEL FILES ===")
for p in sorted(MODELS.glob("*")):
    if p.is_file() and p.name != ".gitkeep":
        print(f"  {p.name}: {p.stat().st_size:,} bytes, modified {p.stat().st_mtime}")

tt = MODELS / "target_transform.json"
if tt.exists():
    data = json.loads(tt.read_text(encoding="utf-8"))
    print("\n=== TARGET TRANSFORM (last train) ===")
    print(f"  selected: {data.get('selected')}")
    comp = data.get("comparison", {})
    for name, m in comp.items():
        print(
            f"  {name}: OOS ROI {m.get('oos_roi_pct', 0):.2f}%, "
            f"MAE {m.get('mae', 0):.3f}, stability {m.get('stability', 0):.3f}"
        )

if not DB.exists():
    print("\nNo football_roi.db")
    raise SystemExit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("\n=== DATABASE ===")
for t in ["daily_picks", "fixtures", "feature_vectors", "odds_snapshots"]:
    try:
        n = cur.execute(f"SELECT COUNT(1) FROM {t}").fetchone()[0]
        print(f"  {t}: {n:,}")
    except Exception as exc:
        print(f"  {t}: {exc}")

try:
    rows = cur.execute(
        "SELECT outcome, COUNT(1) c FROM daily_picks GROUP BY outcome ORDER BY c DESC"
    ).fetchall()
    print("\n  daily_picks outcomes:", {r["outcome"]: r["c"] for r in rows})
except Exception as exc:
    print("  outcomes:", exc)

for table in ["retrain_events", "backtest_runs", "model_metrics", "target_selection_metrics"]:
    try:
        n = cur.execute(f"SELECT COUNT(1) FROM {table}").fetchone()[0]
        print(f"  {table}: {n} rows")
        if n and table == "backtest_runs":
            print("\n=== ALL BACKTEST RUNS ===")
            for r in cur.execute(
                "SELECT id,name,start_date,end_date,total_bets,roi_pct,win_rate,avg_clv,created_at "
                "FROM backtest_runs ORDER BY id"
            ):
                print(f"  #{r['id']} {r['name']}: {r['start_date'][:10]} → {r['end_date'][:10]}, "
                      f"bets={r['total_bets']}, ROI={r['roi_pct']:+.1f}%, WR={r['win_rate']:.0%}, "
                      f"CLV={r['avg_clv']:+.2%}, at {r['created_at']}")
        elif n and table == "target_selection_metrics":
            print("\n=== TARGET SELECTION METRICS (recent) ===")
            for r in cur.execute(
                "SELECT id,transform_name,oos_roi_pct,mae,stability_score,is_selected,created_at "
                "FROM target_selection_metrics ORDER BY id DESC LIMIT 8"
            ):
                sel = " *SELECTED*" if r["is_selected"] else ""
                print(f"  #{r['id']} {r['transform_name']}: OOS ROI {r['oos_roi_pct']:+.2f}%, "
                      f"MAE {r['mae']:.3f}, stability {r['stability_score']:.3f}{sel} @ {r['created_at']}")
        elif n and table != "backtest_runs" and table != "target_selection_metrics":
            row = cur.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
            print(f"    latest: {dict(row)}")
    except Exception as exc:
        print(f"  {table}: {exc}")

try:
    row = cur.execute(
        """
        SELECT COUNT(1) n,
          SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) wins,
          SUM(CASE WHEN outcome='lose' THEN 1 ELSE 0 END) losses,
          SUM(profit_units) profit,
          SUM(stake_units) staked
        FROM daily_picks WHERE outcome IN ('win','lose','push')
        """
    ).fetchone()
    print("\n=== LIVE BOT ROI (settled picks) ===")
    print(f"  Bets: {row['n']}, W/L: {row['wins']}/{row['losses']}")
    print(f"  Profit: {row['profit']:+.2f}u, Staked: {row['staked']:.2f}u")
    if row["staked"]:
        print(f"  ROI: {row['profit']/row['staked']*100:+.2f}%")
except Exception as exc:
    print("  settled ROI:", exc)

conn.close()
