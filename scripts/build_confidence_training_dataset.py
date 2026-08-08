#!/usr/bin/env python3
"""
Build merged confidence calibrator training dataset (read-only from botposlednji1).
Writes only under data/confidence_training/ in the current project.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "data" / "confidence_training"
CSV_PATH = OUT_DIR / "history_merged.csv"
META_PATH = OUT_DIR / "history_merged_meta.json"

LEGACY_DB = Path(r"C:\Users\Miki\Desktop\botposlednji1\data\football_roi.db")
CURRENT_DB = ROOT / "data" / "football_roi.db"

READ_ONLY_PATHS = [
    LEGACY_DB,
    LEGACY_DB.parent / "features" / "drift_baseline.json",
    LEGACY_DB.parent / "models" / "target_transform.json",
]


def fingerprint(path: Path) -> dict:
    if not path.is_file():
        return {"path": str(path), "exists": False}
    data = path.read_bytes()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "modified_utc": datetime.utcfromtimestamp(path.stat().st_mtime).isoformat() + "Z",
        "size_bytes": path.stat().st_size,
    }


def main() -> None:
    from app.model.confidence_training_data import (
        load_legacy_rows,
        load_rows_from_db,
        merge_deduplicate,
    )

    before = [fingerprint(p) for p in READ_ONLY_PATHS]

    legacy_rows = load_legacy_rows()
    current_rows = load_rows_from_db(CURRENT_DB, "current_bot")
    merged = merge_deduplicate(legacy_rows, current_rows)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(merged[0]).keys()) if merged else []
    with CSV_PATH.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in merged:
            writer.writerow(asdict(row))

    after = [fingerprint(p) for p in READ_ONLY_PATHS]
    unchanged = all(
        b.get("sha256") == a.get("sha256") for b, a in zip(before, after) if b.get("exists")
    )

    wins = sum(1 for r in merged if r.outcome == "win")
    losses = sum(1 for r in merged if r.outcome == "lose")
    meta = {
        "built_at_utc": datetime.utcnow().isoformat() + "Z",
        "legacy_rows": len(legacy_rows),
        "current_rows": len(current_rows),
        "merged_rows": len(merged),
        "wins": wins,
        "losses": losses,
        "sources": {
            "botposlednji1": sum(1 for r in merged if r.source == "botposlednji1"),
            "current_bot": sum(1 for r in merged if r.source == "current_bot"),
        },
        "period": {
            "prediction_min": min((r.prediction_timestamp for r in merged), default=None),
            "prediction_max": max((r.prediction_timestamp for r in merged), default=None),
            "kickoff_min": min((r.kickoff_timestamp for r in merged), default=None),
            "kickoff_max": max((r.kickoff_timestamp for r in merged), default=None),
        },
        "legacy_fingerprints_unchanged": unchanged,
        "fingerprints_before": before,
        "fingerprints_after": after,
        "csv_path": str(CSV_PATH),
    }
    META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(json.dumps(meta, indent=2))
    print(f"\nDataset: {CSV_PATH}")


if __name__ == "__main__":
    main()
