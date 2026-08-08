#!/usr/bin/env python3
"""
READ-ONLY audit of Desktop/botposlednji1 for confidence calibrator training data.
Does NOT write to botposlednji1.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY = Path(r"C:\Users\Miki\Desktop\botposlednji1")
LEGACY_DB = LEGACY / "data" / "football_roi.db"
CURRENT_DB = ROOT / "data" / "football_roi.db"

READ_FILES = [
    LEGACY_DB,
    LEGACY / "data" / "features" / "drift_baseline.json",
    LEGACY / "data" / "models" / "target_transform.json",
    LEGACY / ".env",
    LEGACY / "app" / "database" / "models.py",
]


def file_fingerprint(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    data = path.read_bytes()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "modified_utc": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z",
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def ro_connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def audit_db(db: Path, label: str) -> dict:
    if not db.is_file():
        return {"label": label, "exists": False}

    conn = ro_connect(db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = [
        r[0]
        for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]
    table_counts = {}
    for t in tables:
        try:
            table_counts[t] = cur.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
        except sqlite3.Error as exc:
            table_counts[t] = f"error: {exc}"

    dp_cols = [r[1] for r in cur.execute("PRAGMA table_info(daily_picks)").fetchall()]

    outcomes = {
        r[0]: r[1]
        for r in cur.execute(
            "SELECT outcome, COUNT(1) c FROM daily_picks GROUP BY outcome ORDER BY c DESC"
        ).fetchall()
    }

    settled = cur.execute(
        "SELECT COUNT(1) FROM daily_picks WHERE outcome IN ('win','lose')"
    ).fetchone()[0]

    prematch = cur.execute(
        """
        SELECT COUNT(1) FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date < f.fixture_date
        """
    ).fetchone()[0]

    completeness = dict(
        cur.execute(
            """
            SELECT
              COUNT(1) total,
              SUM(CASE WHEN p.probability IS NOT NULL THEN 1 ELSE 0 END) has_prob,
              SUM(CASE WHEN p.odds IS NOT NULL AND p.odds > 1 THEN 1 ELSE 0 END) has_odds,
              SUM(CASE WHEN p.market IS NOT NULL AND p.market != '' THEN 1 ELSE 0 END) has_market,
              SUM(CASE WHEN p.selection IS NOT NULL AND p.selection != '' THEN 1 ELSE 0 END) has_sel,
              SUM(CASE WHEN p.pick_date IS NOT NULL THEN 1 ELSE 0 END) has_pick_ts,
              SUM(CASE WHEN f.fixture_date IS NOT NULL THEN 1 ELSE 0 END) has_kickoff,
              SUM(CASE WHEN p.fair_implied_prob IS NOT NULL THEN 1 ELSE 0 END) has_fair,
              SUM(CASE WHEN p.confidence IS NOT NULL THEN 1 ELSE 0 END) has_conf,
              SUM(CASE WHEN p.expected_value IS NOT NULL THEN 1 ELSE 0 END) has_ev
            FROM daily_picks p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE p.outcome IN ('win','lose') AND p.pick_date < f.fixture_date
            """
        ).fetchone()
    )

    period = cur.execute(
        """
        SELECT MIN(p.pick_date), MAX(p.pick_date), MIN(f.fixture_date), MAX(f.fixture_date)
        FROM daily_picks p JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date < f.fixture_date
        """
    ).fetchone()

    wins = cur.execute(
        """
        SELECT COUNT(1) FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome = 'win' AND p.pick_date < f.fixture_date
        """
    ).fetchone()[0]
    losses = cur.execute(
        """
        SELECT COUNT(1) FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome = 'lose' AND p.pick_date < f.fixture_date
        """
    ).fetchone()[0]

    # exportable training rows key fields
    sample_keys = cur.execute(
        """
        SELECT p.fixture_id, p.market, p.selection, p.pick_date
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win','lose') AND p.pick_date < f.fixture_date
          AND p.probability IS NOT NULL AND p.odds > 1
          AND p.market IS NOT NULL AND p.selection IS NOT NULL
        ORDER BY p.pick_date
        LIMIT 3
        """
    ).fetchall()

    conn.close()
    return {
        "label": label,
        "path": str(db),
        "exists": True,
        "size_bytes": db.stat().st_size,
        "tables": table_counts,
        "daily_picks_columns": dp_cols,
        "outcomes": outcomes,
        "settled_win_lose": settled,
        "prematch_win_lose": prematch,
        "prematch_wins": wins,
        "prematch_losses": losses,
        "field_completeness": completeness,
        "period": {
            "pick_date_min": period[0],
            "pick_date_max": period[1],
            "fixture_date_min": period[2],
            "fixture_date_max": period[3],
        },
        "has_confidence_prediction_logs": "confidence_prediction_logs" in tables,
        "sample_keys": [
            {"fixture_id": r[0], "market": r[1], "selection": r[2], "pick_date": r[3]}
            for r in sample_keys
        ],
    }


def duplicate_check(legacy_db: Path, current_db: Path) -> dict:
    if not legacy_db.is_file() or not current_db.is_file():
        return {"skipped": True, "reason": "one or both DBs missing"}

    leg = ro_connect(legacy_db)
    cur = ro_connect(current_db)
    leg_keys = {
        (r[0], r[1], r[2], r[3])
        for r in leg.execute(
            """
            SELECT p.fixture_id, p.market, p.selection, p.pick_date
            FROM daily_picks p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE p.outcome IN ('win','lose') AND p.pick_date < f.fixture_date
              AND p.probability IS NOT NULL AND p.odds > 1
            """
        ).fetchall()
    }
    cur_keys = {
        (r[0], r[1], r[2], r[3])
        for r in cur.execute(
            """
            SELECT p.fixture_id, p.market, p.selection, p.pick_date
            FROM daily_picks p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE p.outcome IN ('win','lose') AND p.pick_date < f.fixture_date
              AND p.probability IS NOT NULL AND p.odds > 1
            """
        ).fetchall()
    }
    overlap = leg_keys & cur_keys
    leg.close()
    cur.close()
    return {
        "legacy_valid_keys": len(leg_keys),
        "current_valid_keys": len(cur_keys),
        "overlap_keys": len(overlap),
        "legacy_only_keys": len(leg_keys - cur_keys),
        "current_only_keys": len(cur_keys - leg_keys),
    }


def main() -> None:
    report = {
        "audit_time_utc": datetime.utcnow().isoformat() + "Z",
        "legacy_folder": str(LEGACY),
        "read_only_files_planned": [str(p) for p in READ_FILES],
        "fingerprints_before": [file_fingerprint(p) for p in READ_FILES],
        "legacy_db_audit": audit_db(LEGACY_DB, "botposlednji1"),
        "current_db_audit": audit_db(CURRENT_DB, "current_bot"),
        "duplicate_check": duplicate_check(LEGACY_DB, CURRENT_DB),
    }

    out = ROOT / "data" / "confidence_training" / "audit_botposlednji1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    print(json.dumps(report, indent=2, default=str))
    print(f"\nAudit saved: {out}")


if __name__ == "__main__":
    main()
