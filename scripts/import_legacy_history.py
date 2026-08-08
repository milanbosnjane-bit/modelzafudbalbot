"""CLI: import data/history.db into football_roi.db for training."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.legacy_history_importer import (  # noqa: E402
    DEFAULT_LEGACY_HISTORY_DB,
    build_missing_features,
    import_legacy_history,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Uvezi Football-Data history.db u football_roi.db (trening/backtest)."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_LEGACY_HISTORY_DB,
        help="Putanja do legacy history.db",
    )
    parser.add_argument(
        "--no-features",
        action="store_true",
        help="Samo fixtures + odds, bez feature engineering-a",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Uvoz i ako football_roi.db vec ima fixtures",
    )
    parser.add_argument(
        "--if-empty",
        action="store_true",
        help="Preskoci ako football_roi.db vec ima fixtures",
    )
    parser.add_argument(
        "--build-missing-features",
        action="store_true",
        help="Samo feature vectors za meceve koji ih nemaju (bez ponovnog uvoza)",
    )
    parser.add_argument(
        "--feature-limit",
        type=int,
        default=12000,
        help="Koliko najnovijih meceva dobija feature vectors (default 12000)",
    )
    args = parser.parse_args()

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )

    try:
        if args.build_missing_features:
            stats = asyncio.run(
                build_missing_features(
                    feature_limit=None if args.no_features else args.feature_limit,
                )
            )
            print("=" * 48)
            print("LEGACY FEATURE BUILD")
            print("=" * 48)
            print(f"Mecevi bez feature-a:          {stats['fixtures_needing']}")
            print(f"Feature vectors napravljeno:   {stats['features_built']}")
            print("=" * 48)
            return 0

        stats = asyncio.run(
            import_legacy_history(
                legacy_db_path=args.db_path,
                build_features=not args.no_features,
                force=args.force,
                if_empty=args.if_empty,
                feature_limit=None if args.no_features else args.feature_limit,
            )
        )
    except Exception as exc:
        print(f"[GRESKA] Legacy import nije uspeo: {exc}")
        return 1

    if stats.get("skipped"):
        print("[INFO] football_roi.db vec ima fixtures — import preskocen.")
        return 0

    print("=" * 48)
    print("LEGACY HISTORY IMPORT")
    print("=" * 48)
    print(f"Legacy redova (mapirane lige): {stats['legacy_rows']}")
    print(f"Novi fixtures:               {stats['fixtures_imported']}")
    print(f"Preskoceno (duplikat):         {stats['fixtures_skipped']}")
    print(f"Odds snapshots:                {stats['odds_snapshots']}")
    print(f"Feature vectors:               {stats['features_built']}")
    print("=" * 48)
    print("Sledeci korak: python -m app.train_models --no-optimize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
