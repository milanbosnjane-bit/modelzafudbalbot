"""Report today's picks only — read-only."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "miki0510")
REMOTE = "/home/miki/football-dc-bot"
TODAY = "2026-08-03"


def main() -> None:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)
    sftp = c.open_sftp()
    buf = io.BytesIO()
    sftp.getfo(f"{REMOTE}/data/football_roi.db", buf)
    sftp.close()
    c.close()

    tmp = Path(tempfile.gettempdir()) / "report_picks.db"
    tmp.write_bytes(buf.getvalue())
    conn = sqlite3.connect(str(tmp))
    conn.row_factory = sqlite3.Row

    picks = conn.execute(
        """
        SELECT p.*, f.fixture_date, f.league_id, f.status AS fstatus,
               ht.name AS home, at.name AS away
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        LEFT JOIN teams ht ON ht.id = f.home_team_id
        LEFT JOIN teams at ON at.id = f.away_team_id
        WHERE date(p.pick_date) = ?
        ORDER BY p.rank
        """,
        (TODAY,),
    ).fetchall()

    print(f"=== PICKOVI {TODAY} (n={len(picks)}) ===\n")
    for p in picks:
        league = conn.execute("SELECT name FROM leagues WHERE id=?", (p["league_id"],)).fetchone()
        lname = league[0] if league else f"L{p['league_id']}"
        ev_pct = p["expected_value"] * 100
        print(f"#{p['rank']} id={p['id']} | {p['home']} vs {p['away']}")
        print(f"   Liga: {lname}")
        print(f"   Tip: {p['market']} / {p['selection']}  line={p['line']}")
        print(f"   Kvota: {p['odds']:.2f} | EV: {ev_pct:+.1f}% | Conf: {p['confidence']*100:.0f}%")
        print(f"   Model prob: {p['probability']*100:.1f}% | Fair implied: {(p['fair_implied_prob'] or 0)*100:.1f}%")
        print(f"   Ulog: {p['stake_units']:.2f}u | Kickoff: {p['fixture_date']}")
        if p["reasoning"]:
            try:
                r = json.loads(p["reasoning"]) if isinstance(p["reasoning"], str) else p["reasoning"]
                if r:
                    print(f"   Razlog: {r[0][:120]}")
            except (json.JSONDecodeError, TypeError):
                pass
        # min FT history
        h_ft = conn.execute(
            "SELECT COUNT(*) FROM fixtures WHERE status='FT' AND fixture_date < ? AND (home_team_id=(SELECT home_team_id FROM fixtures WHERE id=?) OR away_team_id=(SELECT home_team_id FROM fixtures WHERE id=?))",
            (p["fixture_date"], p["fixture_id"], p["fixture_id"]),
        ).fetchone()[0]
        print(f"   Istorija home u bazi: ~{h_ft} FT meceva")
        print()

    print("=== SUMARNO ===")
    if picks:
        evs = [p["expected_value"] for p in picks]
        odds = [p["odds"] for p in picks]
        print(f"Prosek EV: {sum(evs)/len(evs)*100:+.1f}%")
        print(f"Prosek kvota: {sum(odds)/len(odds):.2f}")
        print(f"EV > 50%: {sum(1 for e in evs if e > 0.5)}/{len(evs)}")
        print(f"Kvota > 5: {sum(1 for o in odds if o > 5)}/{len(odds)}")
        mk = {}
        for p in picks:
            mk[p["market"]] = mk.get(p["market"], 0) + 1
        print("Po trzistu:", mk)

    print("\n=== FIXTURES DANAS U BAZI ===")
    row = conn.execute(
        "SELECT COUNT(*) t, SUM(CASE WHEN status='NS' THEN 1 ELSE 0 END) ns FROM fixtures WHERE date(fixture_date)=?",
        (TODAY,),
    ).fetchone()
    print(f"Ukupno: {row[0]}, NS: {row[1]}")

    print("\nTop lige danas:")
    for r in conn.execute(
        """
        SELECT COALESCE(l.name,'?') name, COUNT(*) n
        FROM fixtures f LEFT JOIN leagues l ON l.id=f.league_id
        WHERE date(f.fixture_date)=? GROUP BY f.league_id ORDER BY n DESC LIMIT 12
        """,
        (TODAY,),
    ):
        print(f"  {r[0]}: {r[1]}")

    conn.close()


if __name__ == "__main__":
    main()
