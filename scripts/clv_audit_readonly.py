"""READ-ONLY CLV audit — no DB writes."""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "football_roi.db"


def clv_raw(entry: float, closing: float) -> float:
    if closing <= 0:
        return 0.0
    return (entry / closing) - 1.0


def clv_fair(entry: float, closing_fair_prob: float) -> float:
    return (entry * closing_fair_prob) - 1.0


def clv_code(entry: float, closing: float, closing_fair_prob: float | None) -> float:
    if closing_fair_prob is not None:
        return clv_fair(entry, closing_fair_prob)
    return clv_raw(entry, closing)


def main() -> None:
    if not DB.is_file():
        print(f"DB not found: {DB}")
        return

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    print("=" * 72)
    print("1. CLV FORMULA (code: app/utils/helpers.py closing_line_value)")
    print("   RAW:    (entry_odds / closing_odds) - 1")
    print("   FAIR:   (entry_odds * closing_fair_prob) - 1  [used when fair_prob set]")
    print("   Stored: daily_picks.clv, daily_picks.closing_odds")
    print()

    tests = [
        (7.00, 5.80, None, 0.2069),
        (5.80, 7.00, None, -0.1714),
        (5.80, 5.70, None, 0.0175),
    ]
    print("   Expected RAW tests:")
    for entry, closing, fair, exp in tests:
        got = clv_raw(entry, closing)
        ok = abs(got - exp) < 0.001
        print(f"   entry={entry} close={closing} -> {got*100:+.2f}%  {'OK' if ok else 'FAIL'}")

    picks_sql = """
    SELECT
        p.id AS pick_id,
        p.fixture_id,
        p.pick_date,
        p.created_at,
        p.market,
        p.selection,
        p.line,
        p.odds AS entry_odds,
        p.opening_odds,
        p.user_odds,
        p.closing_odds AS pick_closing_odds,
        p.clv AS stored_clv,
        p.fair_implied_prob,
        p.closing_fair_prob,
        p.outcome,
        p.profit_units,
        p.stake_units,
        p.probability,
        f.fixture_date,
        f.status AS fixture_status,
        f.home_goals,
        f.away_goals,
        ht.name AS home,
        at.name AS away
    FROM daily_picks p
    JOIN fixtures f ON f.id = p.fixture_id
    LEFT JOIN teams ht ON ht.id = f.home_team_id
    LEFT JOIN teams at ON at.id = f.away_team_id
    WHERE p.outcome IN ('win', 'lose', 'push')
    ORDER BY p.pick_date DESC, p.id DESC
    """
    settled = conn.execute(picks_sql).fetchall()
    with_clv = [r for r in settled if r["stored_clv"] is not None]

    rows_out = []
    anomalies = []
    mismatch_flags = []

    for r in with_clv:
        entry = r["user_odds"] or r["entry_odds"]
        closing_snap = conn.execute(
            """
            SELECT bookmaker, market, selection, line, closing_odds, current_odds,
                   fair_prob, implied_prob, captured_at, is_closing
            FROM odds_snapshots
            WHERE fixture_id = ? AND market = ? AND selection = ?
              AND is_closing = 1
            ORDER BY captured_at DESC
            LIMIT 1
            """,
            (r["fixture_id"], r["market"], r["selection"]),
        ).fetchone()

        closing_odds = r["pick_closing_odds"]
        closing_ts = None
        bookmaker = source = "—"
        fair_used = r["closing_fair_prob"]
        if closing_snap:
            closing_odds = closing_odds or closing_snap["closing_odds"] or closing_snap["current_odds"]
            closing_ts = closing_snap["captured_at"]
            bookmaker = closing_snap["bookmaker"]
            source = "odds_snapshots.is_closing=1"
            if fair_used is None and closing_snap["fair_prob"]:
                fair_used = closing_snap["fair_prob"]

        recalc_raw = clv_raw(entry, closing_odds) if closing_odds else None
        recalc_code = (
            clv_code(entry, closing_odds, fair_used)
            if closing_odds
            else None
        )

        match_ok = True
        issues = []
        if closing_snap:
            if closing_snap["market"] != r["market"]:
                match_ok = False
                issues.append("market_mismatch")
            if closing_snap["selection"] != r["selection"]:
                match_ok = False
                issues.append("selection_mismatch")
            if r["line"] is not None and closing_snap["line"] is not None:
                if abs(float(r["line"]) - float(closing_snap["line"])) > 0.01:
                    match_ok = False
                    issues.append("line_mismatch")
            if closing_ts and r["fixture_date"]:
                fts = datetime.fromisoformat(str(r["fixture_date"]).replace("Z", ""))
                cts = datetime.fromisoformat(str(closing_ts).replace("Z", ""))
                if cts > fts:
                    match_ok = False
                    issues.append("closing_after_kickoff")
        else:
            issues.append("no_closing_snapshot")

        if r["stored_clv"] is not None and recalc_code is not None:
            if abs(r["stored_clv"] - recalc_code) > 0.02:
                issues.append(f"stored_vs_recalc delta={r['stored_clv']-recalc_code:+.3f}")

        clv_pct = r["stored_clv"] * 100
        if abs(clv_pct) > 30:
            anomalies.append(
                {
                    "pick_id": r["pick_id"],
                    "match": f"{r['home']} vs {r['away']}",
                    "clv_pct": clv_pct,
                    "entry": entry,
                    "closing": closing_odds,
                    "fair_prob": fair_used,
                    "raw_clv_pct": (recalc_raw * 100) if recalc_raw is not None else None,
                    "issues": issues,
                }
            )

        if issues:
            mismatch_flags.append({"pick_id": r["pick_id"], "issues": issues})

        rows_out.append(
            {
                "pick_id": r["pick_id"],
                "fixture_id": r["fixture_id"],
                "match": f"{r['home']} vs {r['away']}",
                "pick_date": str(r["pick_date"]),
                "market": r["market"],
                "selection": r["selection"],
                "entry_odds": entry,
                "entry_timestamp": str(r["created_at"] or r["pick_date"]),
                "closing_odds": closing_odds,
                "closing_timestamp": str(closing_ts) if closing_ts else None,
                "bookmaker": bookmaker,
                "stored_clv_pct": clv_pct,
                "raw_clv_pct": (recalc_raw * 100) if recalc_raw is not None else None,
                "fair_prob_used": fair_used,
                "outcome": r["outcome"],
                "match_ok": match_ok,
                "issues": issues,
            }
        )

    clvs = [r["stored_clv"] for r in with_clv]
    clv_pcts = [c * 100 for c in clvs]

    def trimmed_mean(vals: list[float], trim_pct: float = 0.10) -> float | None:
        if not vals:
            return None
        s = sorted(vals)
        k = int(len(s) * trim_pct)
        if k * 2 >= len(s):
            return statistics.mean(s)
        return statistics.mean(s[k : len(s) - k])

    pos = sum(1 for c in clvs if c > 0)
    neg = sum(1 for c in clvs if c < 0)

    # ROI entry vs closing (settled with CLV only)
    profit_entry = profit_close = staked = 0.0
    for r in with_clv:
        stake = r["stake_units"] or 0.0
        entry = r["user_odds"] or r["entry_odds"]
        close = r["pick_closing_odds"] or entry
        staked += stake
        if r["outcome"] == "win":
            profit_entry += stake * (entry - 1)
            profit_close += stake * (close - 1)
        elif r["outcome"] == "lose":
            profit_entry -= stake
            profit_close -= stake

    # Duplicate runs: same fixture_id, same UTC day multiple picks
    dupes = conn.execute(
        """
        SELECT fixture_id, date(pick_date) AS d, COUNT(*) AS n,
               GROUP_CONCAT(id) AS pick_ids
        FROM daily_picks
        GROUP BY fixture_id, date(pick_date)
        HAVING n > 1
        ORDER BY d DESC
        """
    ).fetchall()

    same_fixture_any = conn.execute(
        """
        SELECT fixture_id, COUNT(*) AS n, GROUP_CONCAT(id) AS pick_ids,
               MIN(date(pick_date)) AS first_d, MAX(date(pick_date)) AS last_d
        FROM daily_picks
        GROUP BY fixture_id
        HAVING n > 1
        """
    ).fetchall()

    coverage = len(with_clv) / len(settled) * 100 if settled else 0

    print()
    print("=" * 72)
    print("4. AGGREGATE STATS (settled picks)")
    print(f"   Settled total:        {len(settled)}")
    print(f"   With CLV:             {len(with_clv)}")
    print(f"   CLV coverage:         {coverage:.1f}%")
    if clv_pcts:
        print(f"   Mean CLV:             {statistics.mean(clv_pcts):+.2f}%")
        print(f"   Median CLV:           {statistics.median(clv_pcts):+.2f}%")
        tm = trimmed_mean(clv_pcts)
        print(f"   Trimmed mean (10%):   {tm:+.2f}%" if tm is not None else "")
        print(f"   Positive CLV:         {pos}")
        print(f"   Negative CLV:         {neg}")
    print()
    print("7. ROI (picks with CLV only)")
    if staked:
        print(f"   ROI @ entry odds:     {profit_entry/staked*100:+.2f}%")
        print(f"   ROI @ closing odds:   {profit_close/staked*100:+.2f}%")
    print()
    print("8. DUPLICATE PICKS")
    print(f"   Same fixture + same UTC day: {len(dupes)} cases")
    for d in dupes[:15]:
        print(f"      fixture={d['fixture_id']} day={d['d']} count={d['n']} ids={d['pick_ids']}")
    print(f"   Same fixture any day:        {len(same_fixture_any)} fixtures")
    for d in same_fixture_any[:10]:
        print(
            f"      fixture={d['fixture_id']} count={d['n']} "
            f"days={d['first_d']}..{d['last_d']} ids={d['pick_ids']}"
        )

    print()
    print("=" * 72)
    print("2. PER-PICK TABLE (settled with CLV)")
    print(
        f"{'id':>4} {'match':<35} {'mkt':<12} {'sel':<8} "
        f"{'entry':>6} {'close':>6} {'CLV%':>8} {'raw%':>8} {'fair':>6} issues"
    )
    for row in rows_out:
        iss = ",".join(row["issues"]) if row["issues"] else "OK"
        raw = f"{row['raw_clv_pct']:+.1f}" if row["raw_clv_pct"] is not None else "—"
        fair = f"{row['fair_prob_used']:.3f}" if row["fair_prob_used"] else "—"
        match_short = row["match"][:34]
        print(
            f"{row['pick_id']:>4} {match_short:<35} {row['market'][:12]:<12} "
            f"{row['selection'][:8]:<8} {row['entry_odds']:>6.2f} "
            f"{(row['closing_odds'] or 0):>6.2f} {row['stored_clv_pct']:>+7.1f} "
            f"{raw:>8} {fair:>6} {iss}"
        )

    print()
    print("=" * 72)
    print("5. |CLV| > 30% anomalies")
    if not anomalies:
        print("   (none)")
    for a in anomalies:
        print(
            f"   #{a['pick_id']} {a['match']} CLV={a['clv_pct']:+.1f}% "
            f"entry={a['entry']} close={a['closing']} raw={a['raw_clv_pct']} "
            f"fair_p={a['fair_prob']} issues={a['issues']}"
        )

    print()
    print("=" * 72)
    print("6. DATA QUALITY FLAGS")
    print(f"   Picks with any issue: {len(mismatch_flags)}")
    for m in mismatch_flags[:20]:
        print(f"      pick #{m['pick_id']}: {m['issues']}")

    # Check if avg uses FAIR formula predominantly
    fair_dominated = sum(
        1
        for row in rows_out
        if row["fair_prob_used"] and row["raw_clv_pct"] is not None
        and abs(row["stored_clv_pct"] - row["raw_clv_pct"]) > 5
    )

    print()
    print("=" * 72)
    print("CONCLUSION INPUT")
    print(f"   Picks where stored CLV != raw CLV by >5pp (fair formula): {fair_dominated}")
    print(f"   No closing snapshot: {sum(1 for r in rows_out if 'no_closing_snapshot' in r['issues'])}")
    print(f"   Closing after kickoff: {sum(1 for r in rows_out if 'closing_after_kickoff' in r['issues'])}")

    out = Path(__file__).resolve().parent / "clv_audit_output.json"
    out.write_text(json.dumps({"rows": rows_out, "anomalies": anomalies}, indent=2), encoding="utf-8")
    print(f"\nFull JSON: {out}")
    conn.close()


if __name__ == "__main__":
    main()
