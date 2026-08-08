"""Telegram interaktivni bot — glavni meni i dugmad."""

from __future__ import annotations

import structlog
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import get_settings
from app.telegram.keyboard import (
    BTN_LIVE,
    BTN_RESTART,
    BTN_RESULTS,
    BTN_ROI,
    BTN_SETTLE,
    BTN_STATUS,
    MAIN_MENU,
    STARTUP_MESSAGE,
)
from app.telegram.stats_service import (
    bot_status,
    format_settled_bets,
    live_picks,
    restart_bot,
    roi_stats,
    settle_now,
    set_runtime_started_at,
    split_telegram_picks_message,
)

logger = structlog.get_logger()
settings = get_settings()


def _authorized(update: Update) -> bool:
    allowed = settings.telegram_chat_ids
    if not allowed:
        return False
    chat = update.effective_chat
    if not chat:
        return False
    return str(chat.id) in allowed


async def _reply(update: Update, text: str, *, markdown: bool = False) -> None:
    if not update.message:
        return
    kwargs = {"reply_markup": MAIN_MENU}
    if markdown:
        kwargs["parse_mode"] = "Markdown"
    try:
        await update.message.reply_text(text, **kwargs)
    except BadRequest:
        if markdown:
            await update.message.reply_text(text, reply_markup=MAIN_MENU)
        else:
            raise


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    chat_id = update.effective_chat.id if update.effective_chat else "?"
    if not _authorized(update):
        logger.warning(
            "telegram_unauthorized",
            chat_id=chat_id,
            expected=",".join(settings.telegram_chat_ids),
        )
        await update.message.reply_text(
            f"⛔ Neautorizovan chat.\n"
            f"Tvoj chat_id: `{chat_id}`\n"
            f"U .env postavi: TELEGRAM_CHAT_ID={chat_id}",
            parse_mode="Markdown",
        )
        return
    await _reply(update, STARTUP_MESSAGE)


async def handle_menu_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if not _authorized(update):
        chat_id = update.effective_chat.id if update.effective_chat else "?"
        logger.warning("telegram_unauthorized", chat_id=chat_id)
        await update.message.reply_text(f"⛔ Neautorizovan. chat_id={chat_id}")
        return

    text = update.message.text.strip()

    if text == BTN_ROI:
        await _reply(update, await roi_stats())
    elif text == BTN_LIVE:
        msg = await live_picks()
        for part in split_telegram_picks_message(msg):
            await _reply(update, part, markdown=True)
    elif text == BTN_RESULTS:
        await _reply(update, await format_settled_bets(limit=10))
    elif text == BTN_STATUS:
        await _reply(update, await bot_status())
    elif text == BTN_RESTART:
        await _reply(update, restart_bot())
    elif text == BTN_SETTLE:
        await _reply(update, await settle_now())
    elif text.lower() in ("start", "meni", "menu"):
        await _reply(update, STARTUP_MESSAGE)
    else:
        await _reply(
            update,
            "Koristi dugmad ispod 👇\n\n" + STARTUP_MESSAGE,
        )


async def send_startup_message(app: Application) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_ids:
        logger.warning("telegram_startup_skipped_not_configured")
        return
    for chat_id in settings.telegram_chat_ids:
        await app.bot.send_message(
            chat_id=chat_id,
            text=STARTUP_MESSAGE,
            reply_markup=MAIN_MENU,
        )
    logger.info("telegram_startup_message_sent", chat_ids=settings.telegram_chat_ids)


def build_application() -> Application:
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN nije podešen")

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(send_startup_message)
        .build()
    )
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CommandHandler("menu", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_message))
    return app


async def start_interactive_bot_background() -> Application:
    """Pokreni polling u pozadini."""
    set_runtime_started_at()
    app = build_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=False)
    logger.info("telegram_interactive_bot_background_started")
    return app


async def stop_interactive_bot(app: Application) -> None:
    await app.updater.stop()
    await app.stop()
    await app.shutdown()
    logger.info("telegram_interactive_bot_stopped")
