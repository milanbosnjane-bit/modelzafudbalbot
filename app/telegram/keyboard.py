"""Telegram reply keyboard — glavni meni."""

from telegram import ReplyKeyboardMarkup

BTN_ROI = "📊 ROI STATISTIKA"
BTN_LIVE = "📈 LIVE PICKS"
BTN_RESTART = "🔄 RESTART RUN"
BTN_STATUS = "⚙️ STATUS BOTA"
BTN_RESULTS = "📉 POSLEDNJI REZULTATI"
BTN_SETTLE = "✅ SETTLE NOW"

MAIN_MENU = ReplyKeyboardMarkup(
    [
        [BTN_ROI, BTN_LIVE],
        [BTN_RESULTS, BTN_SETTLE],
        [BTN_RESTART, BTN_STATUS],
    ],
    resize_keyboard=True,
)

STARTUP_MESSAGE = (
    "⚽ FOOTBALL ROI BOT JE POKRENUT\n\n"
    "Dixon-Coles (DC) model — signal + analiza, fokus kvote ≥ 2.0.\n"
    "Svi tipovi koje bot pošalje automatski ulaze u ROI statistiku.\n"
    "Ti biraš šta i kad igraš u kladionici.\n\n"
    "Izaberi opciju ispod 👇"
)


def main_menu_markup() -> dict:
    """JSON reply_markup za Telegram HTTP API."""
    return MAIN_MENU.to_dict()
