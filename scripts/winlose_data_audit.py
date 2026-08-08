"""Compare settled wins vs losses on history/feature availability (read-only)."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "football_roi.db"


def load_conn() -> sqlite3.Connection:
    if os.environ.get("USE_SERVER") == "1":
        import paramiko

        host = os.environ.get("DEPLOY_HOST", "192.168.1.106")
        user = os.environ.get("DEPLOY_USER", "miki")
        password = os.environ.get("DEPLOY_PASS", "")
        if not password:
            raise SystemExit("Set DEPLOY_PASS for USE_SERVER=1")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host,
            username=user,
            password=password,
            timeout=30,
            allow_agent=False,
            look_for_keys=False,
        )
        sftp = client.open_sftp()
        buf = io.BytesIO()
        remote = os.environ.get(
            "REMOTE_DB", "/home/miki/football-dc-bot/data/football_roi.db"
        )
        sftp.getfo(remote, buf)
        sftp.close()
        client.close()
        tmp = Path(tempfile.gettempdir()) / "winlose_audit.db"
        tmp.write_bytes(buf.getvalue())
        return sqlite3.connect(str(tmp))
    if not DB.is_file():
        raise SystemExit(f"DB not found: {DB}")
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def feat_val(features, *keys: str) -> float | None:
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


def team_ft_count(conn: sqlite3.Connection, team_id: int, before: str) -> int:
    return conn.execute(
        """
        SELECT COUNT(*) FROM fixtures
        WHERE status = 'FT' AND fixture_date < ?
          AND (home_team_id = ? OR away_team_id = ?)
        """,
        (before, team_id, team_id),
    ).fetchone()[0]


def avg(vals: list[float | None]) -> float | None:
    clean = [v for v in vals if v is not None]
    return sum(clean) / len(clean) if clean else None


def summarize(group: list[dict], label: str) -> None:
    print(f"\n=== {label} (n={len(group)}) ===")
    for key, name in [
        ("has_features", "ima feature snapshot"),
        ("has_xg", "ima oba xG"),
        ("h_ft", "home FT mecevi u bazi"),
        ("a_ft", "away FT mecevi u bazi"),
        ("min_ft", "min FT mecevi (slabija strana)"),
        ("home_xg", "home xG"),
        ("away_xg", "away xG"),
        ("home_form", "home forma"),
        ("away_form", "away forma"),
        ("h2h", "h2h gol avg"),
        ("home_inj", "home povrede"),
        ("away_inj", "away povrede"),
        ("ev", "EV"),
        ("conf", "confidence"),
        ("odds", "kvota"),
        ("clv", "CLV"),
    ]:
        if key in ("has_features", "has_xg"):
            pct = 100 * sum(1 for m in group if m[key]) / len(group) if group else 0
            print(f"  {name}: {pct:.0f}%")
        else:
            v = avg([m[key] for m in group])
            print(f"  {name}: {v:.3f}" if v is not None else f"  {name}: n/a")


def main() -> None:
    conn = load_conn()
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT p.id, p.outcome, p.odds, p.expected_value, p.confidence,
               p.clv, p.clv_raw, p.market, p.selection, p.pick_date,
               f.id AS fid, f.fixture_date, f.home_team_id, f.away_team_id,
               ht.name AS home, at.name AS away
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN teams ht ON ht.id = f.home_team_id
        LEFT JOIN teams at ON at.id = f.away_team_id
        WHERE p.outcome IN ('win', 'lose')
        ORDER BY p.pick_date
        """
    ).fetchall()

    print(f"Settled win/lose total: {len(rows)}")
    wins = [r for r in rows if r["outcome"] == "win"]
    losses = [r for r in rows if r["outcome"] == "lose"]
    print(f"Wins: {len(wins)}  Losses: {len(losses)}")

    def get_features(fid: int, as_of: str):
        r = conn.execute(
            """
            SELECT features FROM feature_vectors
            WHERE fixture_id = ? AND as_of_datetime <= ?
            ORDER BY as_of_datetime DESC LIMIT 1
            """,
            (fid, as_of),
        ).fetchone()
        if r:
            return r[0]
        r = conn.execute(
            """
            SELECT features FROM feature_vectors
            WHERE fixture_id = ? ORDER BY as_of_datetime DESC LIMIT 1
            """,
            (fid,),
        ).fetchone()
        return r[0] if r else None

    metrics: list[dict] = []
    for r in rows:
        fjson = get_features(r["fid"], r["pick_date"])
        home_xg = feat_val(fjson, "home_venue_adjusted_xg", "home_weighted_xG_last5")
        away_xg = feat_val(fjson, "away_venue_adjusted_xg", "away_weighted_xG_last5")
        h_ft = team_ft_count(conn, r["home_team_id"], r["fixture_date"])
        a_ft = team_ft_count(conn, r["away_team_id"], r["fixture_date"])
        metrics.append(
            {
                "outcome": r["outcome"],
                "id": r["id"],
                "match": f"{r['home']} vs {r['away']}",
                "market": r["market"],
                "selection": r["selection"],
                "odds": r["odds"],
                "ev": r["expected_value"],
                "conf": r["confidence"],
                "clv": r["clv_raw"] if r["clv_raw"] is not None else r["clv"],
                "home_xg": home_xg,
                "away_xg": away_xg,
                "home_form": feat_val(fjson, "home_rolling_form"),
                "away_form": feat_val(fjson, "away_rolling_form"),
                "h2h": feat_val(fjson, "h2h_goal_avg"),
                "home_inj": feat_val(fjson, "home_injury_impact_score"),
                "away_inj": feat_val(fjson, "away_injury_impact_score"),
                "h_ft": h_ft,
                "a_ft": a_ft,
                "min_ft": min(h_ft, a_ft),
                "has_xg": home_xg is not None and away_xg is not None,
                "has_features": fjson is not None,
            }
        )

    win_m = [m for m in metrics if m["outcome"] == "win"]
    lose_m = [m for m in metrics if m["outcome"] == "lose"]
    summarize(win_m, "DOBITNI")
    summarize(lose_m, "GUBITNI")

    print("\n=== Niska istorija (<10 FT meceva) ===")
    print(f"  Dobitni: {sum(1 for m in win_m if m['min_ft'] < 10)}/{len(win_m)}")
    print(f"  Gubitni: {sum(1 for m in lose_m if m['min_ft'] < 10)}/{len(lose_m)}")

    print("\n=== Bez xG ===")
    print(f"  Dobitni: {sum(1 for m in win_m if not m['has_xg'])}/{len(win_m)}")
    print(f"  Gubitni: {sum(1 for m in lose_m if not m['has_xg'])}/{len(lose_m)}")

    print("\n=== DOBITNI ===")
    for m in win_m:
        print(
            f"  #{m['id']} {m['match']} | {m['market']} {m['selection']} "
            f"@{m['odds']:.2f} | minFT={m['min_ft']} "
            f"xG={m['home_xg']}/{m['away_xg']} EV={m['ev']:.2f}"
        )

    print("\n=== GUBITNI ===")
    for m in lose_m:
        print(
            f"  #{m['id']} {m['match']} | {m['market']} {m['selection']} "
            f"@{m['odds']:.2f} | minFT={m['min_ft']} "
            f"xG={m['home_xg']}/{m['away_xg']} EV={m['ev']:.2f}"
        )

    def hist_key(m: dict) -> float:
        return float(m["min_ft"])

    def ev_key(m: dict) -> float:
        return float(m["ev"])

    def odds_key(m: dict) -> float:
        return float(m["odds"])

    for label, key_fn, bands in [
        (
            "istoriji (min FT u bazi)",
            hist_key,
            [(0, 5, "0-4 meča"), (5, 10, "5-9 meča"), (10, 9999, "10+ mečeva")],
        ),
        (
            "EV",
            ev_key,
            [(0, 0.15, "EV ≤ 0.15"), (0.15, 0.35, "EV 0.15-0.35"), (0.35, 99, "EV > 0.35")],
        ),
        (
            "kvoti",
            odds_key,
            [(0, 3, "@2-3"), (3, 5, "@3-5"), (5, 99, "@5+")],
        ),
    ]:
        print(f"\n=== Winrate po {label} ===")
        for lo, hi, name in bands:
            group = [m for m in metrics if lo <= key_fn(m) < hi]
            wins_n = sum(1 for m in group if m["outcome"] == "win")
            total = len(group)
            if total:
                print(f"  {name}: {wins_n}/{total} = {100 * wins_n / total:.0f}%")

    thin_w = sum(
        1
        for m in win_m
        if m["home_xg"] is not None
        and m["away_xg"] is not None
        and abs(m["home_xg"] - 1.08) < 0.01
        and abs(m["away_xg"] - 0.92) < 0.01
    )
    thin_l = sum(
        1
        for m in lose_m
        if m["home_xg"] is not None
        and m["away_xg"] is not None
        and abs(m["home_xg"] - 1.08) < 0.01
        and abs(m["away_xg"] - 0.92) < 0.01
    )
    print(f"\n=== 'Tanki' xG obrazac (~1 meč u bazi, 1.08/0.92) ===")
    print(f"  Dobitni: {thin_w}/{len(win_m)}")
    print(f"  Gubitni: {thin_l}/{len(lose_m)}")


if __name__ == "__main__":
    main()
