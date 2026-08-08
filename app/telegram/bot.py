"""Telegram bot for daily pick delivery."""

from datetime import datetime

import httpx
import structlog

from app.config import get_settings
from app.predictions.pick_selector import SelectedPick
from app.telegram.formatting import (
    PICK_SEPARATOR,
    fmt_team,
    format_confidence_percent,
    format_edge_pp,
    format_ev_percent,
    format_implied_from_odds,
    format_kickoff_time,
    format_probability_percent,
    format_tip,
    parse_match_label,
    tip_reason,
)

logger = structlog.get_logger()
settings = get_settings()


class TelegramNotifier:
    """Sends daily picks to Telegram."""

    BASE_URL = "https://api.telegram.org/bot{token}"

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or settings.telegram_bot_token
        if chat_id:
            self.chat_ids = [chat_id]
        else:
            self.chat_ids = settings.telegram_chat_ids

    async def send_message(
        self,
        text: str,
        parse_mode: str | None = None,
        *,
        with_menu: bool = False,
    ) -> bool:
        if not self.bot_token or not self.chat_ids:
            logger.warning("telegram_not_configured")
            return False

        url = f"{self.BASE_URL.format(token=self.bot_token)}/sendMessage"
        success = True
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for chat_id in self.chat_ids:
                    payload: dict = {
                        "chat_id": chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    }
                    if parse_mode:
                        payload["parse_mode"] = parse_mode
                    if with_menu:
                        from app.telegram.keyboard import main_menu_markup

                        payload["reply_markup"] = main_menu_markup()
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
        except Exception as e:
            logger.error("telegram_send_failed", error=str(e))
            success = False
        return success

    async def send_startup_menu(self) -> bool:
        from app.telegram.keyboard import STARTUP_MESSAGE

        return await self.send_message(STARTUP_MESSAGE, with_menu=True)

    def format_header(self, date: str | None = None) -> str:
        day = date or datetime.utcnow().strftime("%Y-%m-%d")
        return f"⚽ FOOTBALL PICKS | {day} | Dixon-Coles\n\n{PICK_SEPARATOR}"

    def format_pick(self, pick: SelectedPick) -> str:
        home_team, away_team = parse_match_label(pick.match_label)
        tip = format_tip(pick.market, pick.selection, pick.line)
        reason = tip_reason(pick.market, pick.selection, pick.line)
        implied = format_implied_from_odds(pick.odds)
        edge = format_edge_pp(pick.probability, pick.fair_implied_prob)

        lines = [
            f"#{pick.rank} {fmt_team(home_team)} vs {fmt_team(away_team)}",
        ]
        lines.extend([
            "",
            format_kickoff_time(pick.fixture_date),
            "",
            tip,
            f"💰 KVOTA (bot): {pick.odds:.2f}  (implied {implied}%)",
        ])

        if settings.use_calibrated_confidence:
            cal_label = (
                f"{format_confidence_percent(pick.calibrated_confidence)}%"
                if pick.calibrated_confidence is not None
                else "nije kalibrisan"
            )
            lines.extend([
                f"📊 Model verovatnoća: {format_probability_percent(pick.probability)}%",
                f"🎯 Kalibrisana pouzdanost: {cal_label}",
                f"📈 EV po modelu: {format_ev_percent(pick.expected_value)}%  |  Edge: {edge}",
            ])
        else:
            lines.extend([
                f"📊 DC/Fair: {format_probability_percent(pick.probability)}% / {format_probability_percent(pick.fair_implied_prob)}%",
                f"📈 EV: {format_ev_percent(pick.expected_value)}%  |  Edge: {edge}",
                f"🔒 CONF: {format_confidence_percent(pick.confidence)}%",
            ])

        lines.extend([
            f"💵 PREPORUKA: {pick.stake_units:.2f}u",
            "",
            f"ℹ️ {reason}",
        ])
        if pick.reasoning:
            lines.append("")
            lines.append("📋 ANALIZA:")
            for item in pick.reasoning[:4]:
                lines.append(f"  • {item}")
        return "\n".join(lines)

    async def send_daily_picks(self, picks: list[SelectedPick]) -> bool:
        if not picks:
            await self.send_message(
                "Danas nema tipova — nijedan meč nije prošao filter vrednosti."
            )
            return True

        today = datetime.utcnow().strftime("%Y-%m-%d")
        header = self.format_header(today)
        pick_blocks = [self.format_pick(pick) for pick in picks]
        full_message = header + "\n\n" + f"\n\n{PICK_SEPARATOR}\n\n".join(pick_blocks)
        full_message += f"\n\n{PICK_SEPARATOR}"

        if len(full_message) > 4000:
            success = await self.send_message(header, parse_mode="Markdown")
            for block in pick_blocks:
                ok = await self.send_message(
                    f"\n{block}\n\n{PICK_SEPARATOR}",
                    parse_mode="Markdown",
                )
                success = success and ok
            return success

        return await self.send_message(full_message, parse_mode="Markdown")

    async def send_clv_report(self, metrics: dict) -> bool:
        text = (
            f"📊 CLV izveštaj\n"
            f"Prosečan CLV: {metrics.get('avg_clv', 0):.2%}\n"
            f"Prosečan EV: {metrics.get('avg_ev', 0):.2%}\n"
            f"ROI: {metrics.get('roi_pct', 0):.2f}%\n"
            f"Uzorka: {metrics.get('sample_size', 0)} opklada\n"
            f"Win rate (informativno): {metrics.get('win_rate', 0):.0%}"
        )
        return await self.send_message(text)
