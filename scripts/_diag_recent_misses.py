#!/usr/bin/env python3
"""READ-ONLY diagnostic: recent win/loss trend + config + service logs on server."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import paramiko

HOST = os.environ.get("DEPLOY_HOST", "192.168.1.106")
USER = os.environ.get("DEPLOY_USER", "miki")
PASS = os.environ.get("DEPLOY_PASS", "")
REMOTE = "/home/miki/football-dc-bot"


def safe_print(text: str) -> None:
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> str:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    return out + (f"\n[stderr] {err}" if err.strip() else "")


def main() -> int:
    if not PASS:
        safe_print("[GRESKA] Postavi DEPLOY_PASS.")
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=PASS, timeout=30, allow_agent=False, look_for_keys=False)

    safe_print("=== .env on server (redacted secrets) ===")
    env_out = run(client, f"grep -Ev 'KEY|TOKEN|PASS' {REMOTE}/.env 2>/dev/null || echo 'no .env'")
    safe_print(env_out)

    safe_print("=== reboot history (last 10) ===")
    safe_print(run(client, "last -x reboot | head -10"))

    safe_print("=== scheduler service log (last 60 lines) ===")
    safe_print(run(client, "journalctl --user -u football-dc-scheduler --no-pager -n 60 2>&1"))

    safe_print("=== telegram service log (last 40 lines) ===")
    safe_print(run(client, "journalctl --user -u football-dc-telegram --no-pager -n 40 2>&1"))

    safe_print("=== errors across both services, last 3 days ===")
    safe_print(
        run(
            client,
            "journalctl --user -u football-dc-scheduler -u football-dc-telegram "
            "--since '3 days ago' --no-pager 2>&1 | grep -iE 'error|exception|traceback|critical|failed' | tail -80",
        )
    )

    safe_print("=== copying DB via SFTP (read-only) ===")
    sftp = client.open_sftp()
    buf = io.BytesIO()
    sftp.getfo(f"{REMOTE}/data/football_roi.db", buf)
    sftp.close()
    client.close()

    tmp = Path(tempfile.gettempdir()) / "diag_recent_misses.db"
    tmp.write_bytes(buf.getvalue())

    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    safe_print("\n=== Daily win rate, last 20 days with settled picks ===")
    rows = cur.execute(
        """
        SELECT date(pick_date) d,
               COUNT(*) n,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) w,
               SUM(CASE WHEN outcome='lose' THEN 1 ELSE 0 END) l,
               SUM(CASE WHEN outcome='void' THEN 1 ELSE 0 END) v,
               SUM(CASE WHEN outcome='pending' THEN 1 ELSE 0 END) p,
               ROUND(AVG(expected_value), 3) avg_ev,
               ROUND(AVG(odds), 2) avg_odds,
               ROUND(AVG(confidence), 3) avg_conf
        FROM daily_picks
        GROUP BY date(pick_date)
        ORDER BY d DESC
        LIMIT 20
        """
    ).fetchall()
    for r in rows:
        wl = r["w"] + r["l"]
        wr = f"{(r['w']/wl*100):.0f}%" if wl else "n/a"
        safe_print(
            f"{r['d']}: n={r['n']} W={r['w']} L={r['l']} void={r['v']} pending={r['p']} "
            f"WR={wr} avgEV={r['avg_ev']} avgOdds={r['avg_odds']} avgConf={r['avg_conf']}"
        )

    safe_print("\n=== Last 20 settled picks (raw + reasoning) ===")
    rows = cur.execute(
        """
        SELECT id, pick_date, fixture_id, market, selection, odds, probability,
               fair_implied_prob, expected_value, confidence, outcome, reasoning
        FROM daily_picks
        WHERE outcome IN ('win','lose')
        ORDER BY pick_date DESC
        LIMIT 20
        """
    ).fetchall()
    for r in rows:
        d = dict(r)
        safe_print(
            f"{d['pick_date']} fx={d['fixture_id']} {d['market']}/{d['selection']} "
            f"odds={d['odds']} prob={d['probability']:.3f} fair={d['fair_implied_prob']} "
            f"ev={d['expected_value']:.3f} conf={d['confidence']:.3f} outcome={d['outcome']}"
        )
        safe_print(f"   reasoning: {d['reasoning']}")

    safe_print("\n=== Leagues of losing picks since 2026-07-28 ===")
    rows = cur.execute(
        """
        SELECT f.league_id, COUNT(*) n,
               SUM(CASE WHEN p.outcome='win' THEN 1 ELSE 0 END) w,
               SUM(CASE WHEN p.outcome='lose' THEN 1 ELSE 0 END) l
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date >= '2026-07-28'
        GROUP BY f.league_id
        ORDER BY n DESC
        """
    ).fetchall()
    for r in rows:
        safe_print(dict(r).__repr__())

    safe_print("\n=== Overall last 30 days (settled only) ===")
    row = cur.execute(
        """
        SELECT COUNT(*) n,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) w,
               SUM(CASE WHEN outcome='lose' THEN 1 ELSE 0 END) l,
               ROUND(AVG(expected_value),3) avg_ev,
               ROUND(SUM(profit_units),2) profit,
               ROUND(SUM(stake_units),2) staked
        FROM daily_picks
        WHERE outcome IN ('win','lose') AND pick_date >= date('now','-30 days')
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== Default-lambda share per day (reasoning contains '1.00 \u2014 gost 1.00') ===")
    rows = cur.execute(
        """
        SELECT date(pick_date) d,
               COUNT(*) n,
               SUM(CASE WHEN reasoning LIKE '%1.00%gost 1.00%' THEN 1 ELSE 0 END) default_lambda,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) w,
               SUM(CASE WHEN outcome='lose' THEN 1 ELSE 0 END) l
        FROM daily_picks
        WHERE pick_date >= '2026-07-15'
        GROUP BY date(pick_date)
        ORDER BY d
        """
    ).fetchall()
    for r in rows:
        safe_print(dict(r).__repr__())

    safe_print("\n=== WR: default-lambda picks vs real-lambda picks (settled, since 2026-07-15) ===")
    row = cur.execute(
        """
        SELECT
          SUM(CASE WHEN reasoning LIKE '%1.00%gost 1.00%' AND outcome='win' THEN 1 ELSE 0 END) dl_w,
          SUM(CASE WHEN reasoning LIKE '%1.00%gost 1.00%' AND outcome='lose' THEN 1 ELSE 0 END) dl_l,
          SUM(CASE WHEN reasoning NOT LIKE '%1.00%gost 1.00%' AND outcome='win' THEN 1 ELSE 0 END) rl_w,
          SUM(CASE WHEN reasoning NOT LIKE '%1.00%gost 1.00%' AND outcome='lose' THEN 1 ELSE 0 END) rl_l
        FROM daily_picks
        WHERE outcome IN ('win','lose') AND pick_date >= '2026-07-15'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== Picks by tracked vs open-fallback league, since 2026-07-15 ===")
    tracked = "1,2,3,39,61,71,76,78,88,94,103,128,132,135,140,144,218,219,848"
    row = cur.execute(
        f"""
        SELECT
          SUM(CASE WHEN f.league_id IN ({tracked}) AND p.outcome='win' THEN 1 ELSE 0 END) tr_w,
          SUM(CASE WHEN f.league_id IN ({tracked}) AND p.outcome='lose' THEN 1 ELSE 0 END) tr_l,
          SUM(CASE WHEN f.league_id NOT IN ({tracked}) AND p.outcome='win' THEN 1 ELSE 0 END) of_w,
          SUM(CASE WHEN f.league_id NOT IN ({tracked}) AND p.outcome='lose' THEN 1 ELSE 0 END) of_l
        FROM daily_picks p JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date >= '2026-07-15'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== PROFIT: tracked vs open-fallback leagues, since 2026-07-15 ===")
    row = cur.execute(
        f"""
        SELECT
          ROUND(SUM(CASE WHEN f.league_id IN ({tracked}) THEN p.profit_units ELSE 0 END),2) tr_profit,
          ROUND(SUM(CASE WHEN f.league_id IN ({tracked}) THEN p.stake_units ELSE 0 END),2) tr_staked,
          ROUND(SUM(CASE WHEN f.league_id NOT IN ({tracked}) THEN p.profit_units ELSE 0 END),2) of_profit,
          ROUND(SUM(CASE WHEN f.league_id NOT IN ({tracked}) THEN p.stake_units ELSE 0 END),2) of_staked
        FROM daily_picks p JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date >= '2026-07-15'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== PROFIT: before vs after 2026-07-28 (hotfix relax date) ===")
    row = cur.execute(
        """
        SELECT
          ROUND(SUM(CASE WHEN pick_date < '2026-07-28' THEN profit_units ELSE 0 END),2) before_profit,
          ROUND(SUM(CASE WHEN pick_date < '2026-07-28' THEN stake_units ELSE 0 END),2) before_staked,
          SUM(CASE WHEN pick_date < '2026-07-28' AND outcome='win' THEN 1 ELSE 0 END) before_w,
          SUM(CASE WHEN pick_date < '2026-07-28' AND outcome='lose' THEN 1 ELSE 0 END) before_l,
          ROUND(SUM(CASE WHEN pick_date >= '2026-07-28' THEN profit_units ELSE 0 END),2) after_profit,
          ROUND(SUM(CASE WHEN pick_date >= '2026-07-28' THEN stake_units ELSE 0 END),2) after_staked,
          SUM(CASE WHEN pick_date >= '2026-07-28' AND outcome='win' THEN 1 ELSE 0 END) after_w,
          SUM(CASE WHEN pick_date >= '2026-07-28' AND outcome='lose' THEN 1 ELSE 0 END) after_l
        FROM daily_picks
        WHERE outcome IN ('win','lose') AND pick_date >= '2026-07-15'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== PROFIT: default-lambda vs real-lambda picks, since 2026-07-15 ===")
    row = cur.execute(
        """
        SELECT
          ROUND(SUM(CASE WHEN reasoning LIKE '%1.00%gost 1.00%' THEN profit_units ELSE 0 END),2) dl_profit,
          ROUND(SUM(CASE WHEN reasoning LIKE '%1.00%gost 1.00%' THEN stake_units ELSE 0 END),2) dl_staked,
          ROUND(SUM(CASE WHEN reasoning NOT LIKE '%1.00%gost 1.00%' THEN profit_units ELSE 0 END),2) rl_profit,
          ROUND(SUM(CASE WHEN reasoning NOT LIKE '%1.00%gost 1.00%' THEN stake_units ELSE 0 END),2) rl_staked
        FROM daily_picks
        WHERE outcome IN ('win','lose') AND pick_date >= '2026-07-15'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== PROFIT: default-lambda picks split before/after 2026-07-28 ===")
    row = cur.execute(
        """
        SELECT
          SUM(CASE WHEN pick_date < '2026-07-28' AND reasoning LIKE '%1.00%gost 1.00%' THEN 1 ELSE 0 END) before_dl_n,
          ROUND(SUM(CASE WHEN pick_date < '2026-07-28' AND reasoning LIKE '%1.00%gost 1.00%' THEN profit_units ELSE 0 END),2) before_dl_profit,
          SUM(CASE WHEN pick_date >= '2026-07-28' AND reasoning LIKE '%1.00%gost 1.00%' THEN 1 ELSE 0 END) after_dl_n,
          ROUND(SUM(CASE WHEN pick_date >= '2026-07-28' AND reasoning LIKE '%1.00%gost 1.00%' THEN profit_units ELSE 0 END),2) after_dl_profit,
          SUM(CASE WHEN pick_date >= '2026-07-28' AND reasoning NOT LIKE '%1.00%gost 1.00%' THEN 1 ELSE 0 END) after_rl_n,
          ROUND(SUM(CASE WHEN pick_date >= '2026-07-28' AND reasoning NOT LIKE '%1.00%gost 1.00%' THEN profit_units ELSE 0 END),2) after_rl_profit
        FROM daily_picks
        WHERE outcome IN ('win','lose') AND pick_date >= '2026-07-15'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== Daily picks that would REMAIN if default-lambda ones were rejected (since 2026-07-28) ===")
    rows = cur.execute(
        """
        SELECT date(pick_date) d,
               COUNT(*) total_settled,
               SUM(CASE WHEN reasoning NOT LIKE '%1.00%gost 1.00%' THEN 1 ELSE 0 END) would_remain
        FROM daily_picks
        WHERE outcome IN ('win','lose') AND pick_date >= '2026-07-28'
        GROUP BY date(pick_date)
        ORDER BY d
        """
    ).fetchall()
    for r in rows:
        safe_print(dict(r).__repr__())

    safe_print("\n=== PROFIT since 2026-07-28 ONLY: tracked vs open-fallback ===")
    row = cur.execute(
        f"""
        SELECT
          SUM(CASE WHEN f.league_id IN ({tracked}) THEN 1 ELSE 0 END) tr_n,
          ROUND(SUM(CASE WHEN f.league_id IN ({tracked}) THEN p.profit_units ELSE 0 END),2) tr_profit,
          ROUND(SUM(CASE WHEN f.league_id IN ({tracked}) THEN p.stake_units ELSE 0 END),2) tr_staked,
          SUM(CASE WHEN f.league_id NOT IN ({tracked}) THEN 1 ELSE 0 END) of_n,
          ROUND(SUM(CASE WHEN f.league_id NOT IN ({tracked}) THEN p.profit_units ELSE 0 END),2) of_profit,
          ROUND(SUM(CASE WHEN f.league_id NOT IN ({tracked}) THEN p.stake_units ELSE 0 END),2) of_staked
        FROM daily_picks p JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date >= '2026-07-28'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== PROFIT since 2026-07-28: tracked-league picks split by lambda type ===")
    row = cur.execute(
        f"""
        SELECT
          SUM(CASE WHEN f.league_id IN ({tracked}) AND p.reasoning LIKE '%1.00%gost 1.00%' THEN 1 ELSE 0 END) tr_dl_n,
          ROUND(SUM(CASE WHEN f.league_id IN ({tracked}) AND p.reasoning LIKE '%1.00%gost 1.00%' THEN p.profit_units ELSE 0 END),2) tr_dl_profit,
          SUM(CASE WHEN f.league_id IN ({tracked}) AND p.reasoning NOT LIKE '%1.00%gost 1.00%' THEN 1 ELSE 0 END) tr_rl_n,
          ROUND(SUM(CASE WHEN f.league_id IN ({tracked}) AND p.reasoning NOT LIKE '%1.00%gost 1.00%' THEN p.profit_units ELSE 0 END),2) tr_rl_profit
        FROM daily_picks p JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date >= '2026-07-28'
        """
    ).fetchone()
    safe_print(dict(row).__repr__())

    safe_print("\n=== EV distribution since 2026-07-28 (bucketed) ===")
    rows = cur.execute(
        """
        SELECT
          CASE
            WHEN expected_value < 0.15 THEN '0-15%'
            WHEN expected_value < 0.35 THEN '15-35%'
            WHEN expected_value < 0.6 THEN '35-60%'
            ELSE '60%+'
          END bucket,
          COUNT(*) n,
          SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) w,
          ROUND(SUM(profit_units),2) profit,
          ROUND(SUM(stake_units),2) staked
        FROM daily_picks
        WHERE outcome IN ('win','lose') AND pick_date >= '2026-07-28'
        GROUP BY bucket
        ORDER BY bucket
        """
    ).fetchall()
    for r in rows:
        safe_print(dict(r).__repr__())

    safe_print("\n=== USE_CALIBRATED_CONFIDENCE column present? sample calibrated_confidence ===")
    cols = [r[1] for r in cur.execute("PRAGMA table_info(daily_picks)").fetchall()]
    safe_print("calibrated_confidence in columns: " + str("calibrated_confidence" in cols))
    if "calibrated_confidence" in cols:
        n_filled = cur.execute(
            "SELECT COUNT(*) FROM daily_picks WHERE calibrated_confidence IS NOT NULL"
        ).fetchone()[0]
        safe_print(f"rows with calibrated_confidence filled: {n_filled}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
