"""Calibrate Dixon-Coles parameters (MLE) from historical fixtures."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import structlog

from app.config import get_settings
from app.database.session import AsyncSessionLocal, init_db
from app.training.dc_calibrator import DixonColesCalibrator
from app.utils.model_paths import dc_params_age_days, resolve_dc_params_path


def _configure_console_encoding() -> None:
    """Windows CMD (cp1252) ne prikazuje srpske karaktere bez UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def params_ready(max_age_days: int | None = None) -> bool:
    """True ako dc_params.json postoji i nije stariji od max_age_days."""
    path = resolve_dc_params_path()
    if path is None:
        return False
    age = dc_params_age_days(path)
    limit = max_age_days if max_age_days is not None else get_settings().dc_calibration_max_age_days
    return age <= limit


async def calibrate_async(
    *,
    lookback_days: int | None = None,
    exclude_legacy: bool = True,
) -> dict:
    await init_db()
    async with AsyncSessionLocal() as session:
        calibrator = DixonColesCalibrator(exclude_legacy=exclude_legacy)
        return await calibrator.run(session, lookback_days=lookback_days)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate Dixon-Coles MLE parameters")
    parser.add_argument(
        "--if-missing",
        action="store_true",
        help="Preskoči ako dc_params.json postoji i nije stariji od max-age",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=None,
        help="Maksimalna starost dc_params.json (default iz config)",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="Koliko dana unazad uzeti FT mečeve (default iz config)",
    )
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Uključi football-data kvote u kalibraciju (nepreporučeno)",
    )
    return parser.parse_args()


def main() -> int:
    _configure_console_encoding()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    log = structlog.get_logger()
    args = parse_args()
    settings = get_settings()

    max_age = args.max_age_days if args.max_age_days is not None else settings.dc_calibration_max_age_days
    if args.if_missing and params_ready(max_age):
        path = resolve_dc_params_path()
        log.info(
            "calibration_skipped_params_fresh",
            path=str(path),
            max_age_days=max_age,
        )
        return 0

    try:
        result = asyncio.run(
            calibrate_async(
                lookback_days=args.lookback_days,
                exclude_legacy=not args.include_legacy,
            )
        )
    except ValueError as exc:
        log.error("calibration_failed", error=str(exc))
        print(
            f"\n[GRESKA] Kalibracija nije uspela: {exc}\n"
            "Proveri da ima dovoljno API FT mečeva sa feature-ima u bazi.\n"
        )
        return 1
    except Exception as exc:
        log.error("calibration_failed", error=str(exc))
        print(f"\n[GRESKA] Kalibracija nije uspela: {exc}\n")
        return 1

    log.info(
        "calibration_complete",
        path=result.get("params_path"),
        sample_size=result.get("sample_size"),
        xg_scale=result.get("xg_scale"),
        home_advantage=result.get("home_advantage"),
        leagues_calibrated=result.get("leagues_calibrated"),
    )
    print(
        f"\n[OK] DC parametri sačuvani: {result.get('params_path')}\n"
        f"     Uzorak: {result.get('sample_size')} mečeva | "
        f"xg_scale={result.get('xg_scale')} | home_adv={result.get('home_advantage')}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
