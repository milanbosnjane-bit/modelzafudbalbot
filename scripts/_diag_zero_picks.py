"""Diagnose zero picks on server (read-only)."""
from __future__ import annotations

import io
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

    def run(cmd: str) -> str:
        _, o, e = c.exec_command(cmd, timeout=120)
        return (o.read() + e.read()).decode("utf-8", errors="replace")

    print("=== SERVER TIME ===")
    print(run("date"))

    print("=== ENV (key settings) ===")
    print(run(f"grep LEAGUE_IDS {REMOTE}/.env"))
    print(run(f"grep MIN_EV {REMOTE}/.env"))
    print(run(f"grep MIN_CONF {REMOTE}/.env"))
    print(run(f"grep MAX_DAILY {REMOTE}/.env"))

    print("=== SCHEDULER CRASH 08:00 ===")
    print(run(
        "journalctl --user -u football-dc-scheduler --since '2026-08-03 06:00:00' --until '2026-08-03 06:02:00' --no-pager 2>/dev/null"
    ))

    print("=== SCHEDULER LOG (today) ===")
    print(run(
        "journalctl --user -u football-dc-scheduler --since '2026-08-03 00:00' --no-pager 2>/dev/null | "
        "grep -E 'daily_predictions|pipeline|picks_selected|DEBUG_FUNNEL|drop_summary|fixtures|error|ERROR|Traceback|Exception' | tail -60"
    ))

    print("=== STARTUP LOG (today) ===")
    print(run(
        "journalctl --user -u football-dc-startup --since '2026-08-03 00:00' --no-pager 2>/dev/null | tail -15"
    ))

    sftp = c.open_sftp()
    buf = io.BytesIO()
    sftp.getfo(f"{REMOTE}/data/football_roi.db", buf)
    sftp.close()
    c.close()

    tmp = Path(tempfile.gettempdir()) / "diag_zero.db"
    tmp.write_bytes(buf.getvalue())
    conn = sqlite3.connect(str(tmp))
    conn.row_factory = sqlite3.Row

    print("=== DB FIXTURES TODAY ===")
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(CASE WHEN status='NS' THEN 1 ELSE 0 END) AS ns FROM fixtures WHERE date(fixture_date)=?",
        (TODAY,),
    ).fetchone()
    print(dict(row))

    print("\nTop leagues today in DB:")
    for r in conn.execute(
        """
        SELECT f.league_id, COALESCE(l.name,'?') AS name, COUNT(*) AS n,
               SUM(CASE WHEN f.status='NS' THEN 1 ELSE 0 END) AS ns
        FROM fixtures f LEFT JOIN leagues l ON l.id=f.league_id
        WHERE date(f.fixture_date)=?
        GROUP BY f.league_id ORDER BY n DESC LIMIT 20
        """,
        (TODAY,),
    ):
        print(dict(r))

    print("\nPicks today:", conn.execute(
        "SELECT COUNT(*) FROM daily_picks WHERE date(pick_date)=?", (TODAY,)
    ).fetchone()[0])

    print("Last 3 pick days:")
    for r in conn.execute(
        """
        SELECT date(pick_date) d, COUNT(*) n,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) w
        FROM daily_picks GROUP BY date(pick_date) ORDER BY d DESC LIMIT 5
        """
    ):
        print(dict(r))

    conn.close()


if __name__ == "__main__":
    main()
