"""Jednoklik lokalno pokretanje — bez Docker-a."""

import argparse
import asyncio
import sys

import structlog


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Football ROI Bot — local runner")
    parser.add_argument(
        "--full-build",
        action="store_true",
        help="Phase 1 + 2: ingest, rebuild features, then select picks",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Samo Phase 1 ingest (bez pickova i Telegram poruka)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Phase 2 only: load cached DB data (default when --full-build omitted)",
    )
    return parser.parse_args()


async def main() -> int:
    _configure_console_encoding()
    args = parse_args()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    log = structlog.get_logger()

    from app.config import get_settings
    from app.database.session import init_db
    from app.predictions.pipeline import PipelineDataCorruptionError, PipelineMode, PredictionPipeline
    from app.telegram.bot import TelegramNotifier

    settings = get_settings()
    settings.model_dir.mkdir(parents=True, exist_ok=True)
    settings.feature_dir.mkdir(parents=True, exist_ok=True)

    mode = PipelineMode.FULL_BUILD if args.full_build else PipelineMode.LIVE
    if mode == PipelineMode.LIVE:
        log.info("[MODE] live: using cached data only")
    else:
        log.info("[MODE] full-build: rebuilding dataset")

    log.info("init_database")
    await init_db()

    if args.ingest_only:
        log.info("ingest_only_start")
        await PredictionPipeline().run_phase1_build()
        print("\n[OK] Ingest zavrsen — pickovi se ne generisu (ceka scheduler).\n")
        return 0

    log.info("generating_picks", mode=mode.value)
    try:
        result = await PredictionPipeline().run_daily_detailed(mode=mode)
    except PipelineDataCorruptionError as e:
        log.error("pipeline_data_corruption", error=str(e))
        print(f"\n[GRESKA] Pipeline odbijen — korumpirani EV podaci: {e}\n")
        return 1

    notifier = TelegramNotifier()
    if not settings.telegram_bot_token or not settings.telegram_chat_ids:
        log.error("telegram_not_configured")
        print("\n[GRESKA] TELEGRAM_BOT_TOKEN ili TELEGRAM_CHAT_ID nisu u .env fajlu.\n")
        return 1

    if result.fixture_count == 0:
        msg = (
            "Nema meceva u pracenim ligama u narednih 7 dana.\n"
            "Verovatno je pauza izmedju sezona — bot ce automatski slati pickove kad sezona krene."
        )
        await notifier.send_message(msg)
        print("\n[INFO] Van sezone — nema meceva u narednih 7 dana.\n")
    elif result.all_already_picked:
        print(
            "\n[INFO] Tipovi za danas su vec upisani — preskacem duplikate. "
            "Ne pokreci startbot ponovo istog dana.\n"
        )
    elif result.picks:
        ok = await notifier.send_daily_picks(result.picks)
        if ok:
            when = f" (datum: {result.target_date})" if result.lookahead_used else ""
            print(f"\n[OK] Poslato {len(result.picks)} pickova na Telegram{when}.\n")
        else:
            print("\n[GRESKA] Slanje na Telegram nije uspelo. Proveri token i chat ID.\n")
            return 1
    else:
        await notifier.send_message(
            f"Pronadjeno {result.fixture_count} meceva za {result.target_date}, "
            "ali nijedan nije prosao EV/confidence filter.\n\n"
            "Koristi /start za meni (ROI, status, rezultati).",
            with_menu=True,
        )
        print(
            f"\n[INFO] {result.fixture_count} meceva, ali 0 pickova (filter previsok).\n"
        )

    await notifier.send_startup_menu()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as e:
        print(f"\n[GRESKA] {e}\n")
        sys.exit(1)
