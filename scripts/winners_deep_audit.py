"""Deep read-only audit: winning picks vs bot baseline (no code changes)."""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "football_roi.db"


def load_features(conn: sqlite3.Connection, fixture_id: int, as_of: str):
    r = conn.execute(
        """
        SELECT features FROM feature_vectors
        WHERE fixture_id = ? AND as_of_datetime <= ?
        ORDER BY as_of_datetime DESC LIMIT 1
        """,
        (fixture_id, as_of),
    ).fetchone()
    if r:
        return r[0]
    r = conn.execute(
        """
        SELECT features FROM feature_vectors
        WHERE fixture_id = ? ORDER BY as_of_datetime DESC LIMIT 1
        """,
        (fixture_id,),
    ).fetchone()
    return r[0] if r else None


def feat(features, *keys: str) -> float | None:
    if not features:
        return None
    try:
        d = json.loads(features) if isinstance(features, str) else features
    except (json.JSONDecodeError, TypeError):
        return None
    for k in keys:
        v = d.get(k)
        if v is not None:
            return float(v)
    return None


def team_ft(conn, team_id: int, before: str) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM fixtures
        WHERE status = 'FT' AND fixture_date < ?
          AND (home_team_id = ? OR away_team_id = ?)
        """,
        (before, team_id, team_id),
    ).fetchone()[0]


def league_name(conn, league_id: int | None) -> str:
    if league_id is None:
        return "?"
    r = conn.execute("SELECT name FROM leagues WHERE id = ?", (league_id,)).fetchone()
    return r[0] if r else f"L{league_id}"


def market_label(market: str, selection: str, line) -> str:
    if market == "match_winner":
        return f"1X2 {selection}"
    if market == "over_under":
        return f"O/U {line} {selection}"
    if market == "btts":
        return f"BTTS {selection}"
    return f"{market} {selection}"


def odds_band(o: float) -> str:
    if o < 2.5:
        return "2.0-2.5"
    if o < 3.5:
        return "2.5-3.5"
    if o < 5.0:
        return "3.5-5.0"
    return "5.0+"


def ev_band(ev: float) -> str:
    pct = ev * 100
    if pct <= 15:
        return "EV +0-15%"
    if pct <= 35:
        return "EV +15-35%"
    return "EV +35%+"


def thin_xg(h, a) -> bool:
    return h is not None and a is not None and abs(h - 1.08) < 0.02 and abs(a - 0.92) < 0.02


def avg(vals: list[float | None]) -> float | None:
    clean = [v for v in vals if v is not None]
    return statistics.mean(clean) if clean else None


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def summarize_group(rows: list[dict], label: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {label}  (n={len(rows)})")
    print("=" * 60)
    if not rows:
        return
    print(f"  Prosečna kvota:     {avg([r['odds'] for r in rows]):.2f}")
    print(f"  Prosečan EV:        {avg([r['ev'] for r in rows]) * 100:+.1f}%")
    print(f"  Prosečan confidence:{avg([r['conf'] for r in rows]) * 100:.0f}%")
    print(f"  Prosečan CLV:       {(avg([r['clv'] for r in rows]) or 0) * 100:+.1f}%")
    print(f"  Prosečan ulog:      {avg([r['stake'] for r in rows]):.2f}u")
    print(f"  Prosečan profit:    {avg([r['profit'] for r in rows]):+.2f}u")
    print(f"  min FT istorija:    {avg([r['min_ft'] for r in rows]):.1f} mečeva")
    print(f"  Tanki xG (1.08/0.92): {sum(1 for r in rows if r['thin'])} ({pct(sum(1 for r in rows if r['thin']), len(rows))})")


def bucket_winrate(all_rows: list[dict], key_fn, bands: list[tuple]) -> None:
    print(f"\n  {'Segment':<22} {'W':>3} {'L':>3} {'WR':>6}  {'Avg EV':>8}")
    print("  " + "-" * 50)
    for lo, hi, name in bands:
        group = [r for r in all_rows if lo <= key_fn(r) < hi]
        w = sum(1 for r in group if r["outcome"] == "win")
        l = sum(1 for r in group if r["outcome"] == "lose")
        t = w + l
        ev = avg([r["ev"] for r in group])
        ev_s = f"{ev * 100:+.0f}%" if ev is not None else "—"
        print(f"  {name:<22} {w:>3} {l:>3} {pct(w, t):>6}  {ev_s:>8}")


def main() -> None:
    if not DB.is_file():
        print(f"DB not found: {DB}")
        return

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    raw = conn.execute(
        """
        SELECT p.id, p.outcome, p.odds, p.expected_value, p.confidence,
               p.probability, p.fair_implied_prob, p.clv, p.clv_raw,
               p.market, p.selection, p.line, p.stake_units, p.profit_units,
               p.rank, p.pick_date, p.reasoning,
               f.id AS fid, f.fixture_date, f.league_id,
               f.home_team_id, f.away_team_id, f.home_goals, f.away_goals,
               ht.name AS home, at.name AS away
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN teams ht ON ht.id = f.home_team_id
        LEFT JOIN teams at ON ht.id = f.away_team_id
        WHERE p.outcome IN ('win', 'lose')
        ORDER BY p.pick_date, p.id
        """
    ).fetchall()

    # fix join typo - away team
    raw = conn.execute(
        """
        SELECT p.id, p.outcome, p.odds, p.expected_value, p.confidence,
               p.probability, p.fair_implied_prob, p.clv, p.clv_raw,
               p.market, p.selection, p.line, p.stake_units, p.profit_units,
               p.rank, p.pick_date, p.reasoning,
               f.id AS fid, f.fixture_date, f.league_id,
               f.home_team_id, f.away_team_id, f.home_goals, f.away_goals,
               ht.name AS home, at.name AS away
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN teams ht ON ht.id = f.home_team_id
        LEFT JOIN teams at ON at.id = f.away_team_id
        WHERE p.outcome IN ('win', 'lose')
        ORDER BY p.pick_date, p.id
        """
    ).fetchall()

    rows: list[dict] = []
    for r in raw:
        fjson = load_features(conn, r["fid"], r["pick_date"])
        hx = feat(fjson, "home_venue_adjusted_xg", "home_weighted_xG_last5")
        ax = feat(fjson, "away_venue_adjusted_xg", "away_weighted_xG_last5")
        h_ft = team_ft(conn, r["home_team_id"], r["fixture_date"])
        a_ft = team_ft(conn, r["away_team_id"], r["fixture_date"])
        edge = (r["probability"] - r["fair_implied_prob"]) if r["fair_implied_prob"] else None
        rows.append(
            {
                "id": r["id"],
                "outcome": r["outcome"],
                "match": f"{r['home']} vs {r['away']}",
                "score": f"{r['home_goals']}-{r['away_goals']}",
                "league": league_name(conn, r["league_id"]),
                "market_lbl": market_label(r["market"], r["selection"], r["line"]),
                "market": r["market"],
                "selection": r["selection"],
                "odds": r["odds"],
                "ev": r["expected_value"],
                "conf": r["confidence"],
                "prob": r["probability"],
                "fair": r["fair_implied_prob"],
                "edge": edge,
                "clv": r["clv_raw"] if r["clv_raw"] is not None else r["clv"],
                "stake": r["stake_units"],
                "profit": r["profit_units"],
                "rank": r["rank"],
                "min_ft": min(h_ft, a_ft),
                "h_ft": h_ft,
                "a_ft": a_ft,
                "home_xg": hx,
                "away_xg": ax,
                "thin": thin_xg(hx, ax),
                "pick_date": r["pick_date"][:10],
            }
        )

    wins = [r for r in rows if r["outcome"] == "win"]
    losses = [r for r in rows if r["outcome"] == "lose"]
    all_settled = rows

    print("=" * 60)
    print("  ANALIZA DOBITNIH TIPOVA vs BOT (read-only)")
    print("=" * 60)
    print(f"\nUkupno settled: {len(all_settled)}  |  Wins: {len(wins)}  |  Losses: {len(losses)}")
    print(f"Winrate: {pct(len(wins), len(all_settled))}")
    total_profit = sum(r["profit"] or 0 for r in all_settled)
    win_profit = sum(r["profit"] or 0 for r in wins)
    lose_profit = sum(r["profit"] or 0 for r in losses)
    print(f"Profit: {total_profit:+.2f}u  (dobici {win_profit:+.2f}u, gubici {lose_profit:+.2f}u)")

    summarize_group(wins, "DOBITNI")
    summarize_group(losses, "GUBITNI")
    summarize_group(all_settled, "SVE (baseline bota)")

    print("\n" + "=" * 60)
    print("  WINRATE PO SEGMENTU (wins vs losses)")
    print("=" * 60)

    bucket_winrate(
        all_settled,
        lambda r: r["odds"],
        [(0, 2.5, "Kvota 2.0-2.5"), (2.5, 3.5, "Kvota 2.5-3.5"), (3.5, 5.0, "Kvota 3.5-5.0"), (5.0, 99, "Kvota 5.0+")],
    )
    bucket_winrate(
        all_settled,
        lambda r: r["ev"],
        [(0, 0.15, "EV +0-15%"), (0.15, 0.35, "EV +15-35%"), (0.35, 99, "EV +35%+")],
    )
    bucket_winrate(
        all_settled,
        lambda r: float(r["min_ft"]),
        [(0, 5, "Istorija 0-4 FT"), (5, 10, "Istorija 5-9 FT"), (10, 9999, "Istorija 10+ FT")],
    )
    bucket_winrate(
        all_settled,
        lambda r: 1 if r["thin"] else 0,
        [(0, 0.5, "Puna xG istorija"), (0.5, 2, "Tanki xG 1.08/0.92")],
    )
    bucket_winrate(
        all_settled,
        lambda r: r["rank"],
        [(0, 2, "Rank #1"), (2, 4, "Rank #2-3"), (4, 99, "Rank #4+")],
    )

    print("\n" + "=" * 60)
    print("  PO TRZISTU")
    print("=" * 60)
    by_market: dict[str, list] = defaultdict(list)
    for r in all_settled:
        by_market[r["market_lbl"].split()[0] if r["market"] == "over_under" else r["market_lbl"]].append(r)
    # simpler: group by market+selection pattern
    mk: dict[str, list] = defaultdict(list)
    for r in all_settled:
        key = r["market_lbl"]
        mk[key].append(r)
    print(f"\n  {'Trziste':<28} {'W':>3} {'L':>3} {'WR':>6}")
    print("  " + "-" * 44)
    for key in sorted(mk, key=lambda k: -len(mk[k])):
        g = mk[key]
        w = sum(1 for r in g if r["outcome"] == "win")
        l = len(g) - w
        print(f"  {key:<28} {w:>3} {l:>3} {pct(w, len(g)):>6}")

    print("\n" + "=" * 60)
    print("  PROFIL DOBITNIH — sta imaju ZAJEDNICKO")
    print("=" * 60)

    win_markets = Counter(r["market_lbl"] for r in wins)
    win_odds = Counter(odds_band(r["odds"]) for r in wins)
    win_ev = Counter(ev_band(r["ev"]) for r in wins)
    win_sel = Counter(r["selection"] for r in wins if r["market"] == "match_winner")

    print("\n  Najcesca trzista (dobitni):")
    for k, v in win_markets.most_common(5):
        print(f"    {k}: {v}x")

    print("\n  Kvota band (dobitni):")
    for k, v in win_odds.most_common():
        print(f"    {k}: {v}x")

    print("\n  EV band (dobitni):")
    for k, v in win_ev.most_common():
        print(f"    {k}: {v}x")

    print("\n  1X2 selekcija (dobitni):")
    for k, v in win_sel.most_common():
        print(f"    {k}: {v}x")

    # winner profile vs loser profile deltas
    print("\n" + "=" * 60)
    print("  RAZLIKE DOBITNI vs GUBITNI (kljucno)")
    print("=" * 60)
    diffs = [
        ("Prosečna kvota", avg([r["odds"] for r in wins]), avg([r["odds"] for r in losses])),
        ("Prosečan EV", avg([r["ev"] for r in wins]), avg([r["ev"] for r in losses])),
        ("Prosečan rank (#)", avg([r["rank"] for r in wins]), avg([r["rank"] for r in losses])),
        ("Prosečan ulog", avg([r["stake"] for r in wins]), avg([r["stake"] for r in losses])),
        ("min FT istorija", avg([r["min_ft"] for r in wins]), avg([r["min_ft"] for r in losses])),
        ("CLV", avg([r["clv"] for r in wins]), avg([r["clv"] for r in losses])),
    ]
    print(f"\n  {'Metrika':<22} {'Dobitni':>10} {'Gubitni':>10}")
    print("  " + "-" * 44)
    for name, wv, lv in diffs:
        if wv is None or lv is None:
            continue
        if "EV" in name or name == "CLV":
            print(f"  {name:<22} {wv * 100:>+9.1f}% {lv * 100:>+9.1f}%")
        else:
            print(f"  {name:<22} {wv:>10.2f} {lv:>10.2f}")

    print("\n" + "=" * 60)
    print("  SVI DOBITNI TIPOVI (detalj)")
    print("=" * 60)
    for r in wins:
        ev_pct = r["ev"] * 100
        clv_s = f"{(r['clv'] or 0) * 100:+.0f}%" if r["clv"] is not None else "—"
        thin = " [TANKI xG]" if r["thin"] else ""
        print(
            f"\n  #{r['id']} #{r['rank']} {r['pick_date']} | {r['match']} ({r['score']})"
        )
        print(f"     {r['league']}")
        print(f"     {r['market_lbl']} @{r['odds']:.2f} | EV {ev_pct:+.0f}% | CLV {clv_s}{thin}")
        print(f"     ulog {r['stake']:.2f}u → profit {r['profit']:+.2f}u | minFT={r['min_ft']}")

    print("\n" + "=" * 60)
    print("  SMART UOCENJA (bez menjanja bota)")
    print("=" * 60)
    # compute best segments
    segments = []
    for name, filt in [
        ("Under 2.5", lambda r: r["market"] == "over_under" and r["selection"] == "Under"),
        ("O/U bilo", lambda r: r["market"] == "over_under"),
        ("1X2 Away", lambda r: r["market"] == "match_winner" and r["selection"] == "Away"),
        ("1X2 Draw", lambda r: r["market"] == "match_winner" and r["selection"] == "Draw"),
        ("1X2 Home", lambda r: r["market"] == "match_winner" and r["selection"] == "Home"),
        ("Kvota 2.5-3.5", lambda r: 2.5 <= r["odds"] < 3.5),
        ("Kvota 3.5-5.0", lambda r: 3.5 <= r["odds"] < 5.0),
        ("EV +0-15%", lambda r: r["ev"] <= 0.15),
        ("EV +15-35%", lambda r: 0.15 < r["ev"] <= 0.35),
        ("Rank #1", lambda r: r["rank"] == 1),
        ("Rank #1-2", lambda r: r["rank"] <= 2),
        ("Nije tanki xG", lambda r: not r["thin"]),
        ("Istorija 10+ FT", lambda r: r["min_ft"] >= 10),
    ]:
        g = [r for r in all_settled if filt(r)]
        w = sum(1 for r in g if r["outcome"] == "win")
        t = len(g)
        if t >= 3:
            p = sum(r["profit"] or 0 for r in g)
            segments.append((name, w, t, w / t, p))

    segments.sort(key=lambda x: (-x[3], -x[2]))
    print(f"\n  {'Segment':<22} {'W/L':>7} {'WR':>6} {'Profit':>8}")
    print("  " + "-" * 48)
    for name, w, t, wr, p in segments:
        print(f"  {name:<22} {w}/{t - w:<4} {pct(w, t):>6} {p:>+8.2f}u")

    conn.close()


if __name__ == "__main__":
    main()
