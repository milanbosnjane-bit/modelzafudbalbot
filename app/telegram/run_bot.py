"""Pokretanje Telegram interaktivnog bota (polling + meni)."""

import asyncio
import signal

import structlog

from app.database.session import init_db
from app.telegram.interactive_bot import (
    start_interactive_bot_background,
    stop_interactive_bot,
)


async def main() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    await init_db()
    app = await start_interactive_bot_background()

    stop = asyncio.Event()

    def shutdown(*_args):
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    await stop.wait()
    await stop_interactive_bot(app)


if __name__ == "__main__":
    asyncio.run(main())
