"""Telegram meni — statistika, tipovi i rezultati iz baze."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import func, or_, select

from app.config import get_settings
from app.database.models import DailyPick, Fixture, Team
from app.database.session import AsyncSessionLocal
from app.predictions.pick_selector import SelectedPick
from app.predictions.probability_layer import is_disabled_market
from app.telegram.bot import TelegramNotifier
from app.telegram.formatting import PICK_SEPARATOR, format_tip
from app.telegram.pick_output import prepare_live_picks
from app.telegram.pick_status import (
    FIXTURE_FINISHED_STATUSES,
    resolve_pick_status,
)
from app.utils.model_paths import resolve_dc_params_path

settings = get_settings()
_runtime_started_at = datetime.utcnow()
log = structlog.get_logger()


@dataclass
class PickRow:
    """Pick sa statusom za Telegram prikaz."""

    status: str
    pick: SelectedPick


def set_runtime_started_at(when: datetime | None = None) -> None:
    global _runtime_started_at
    _runtime_started_at = when or datetime.utcnow()


async def _load_fixtures_teams(session, fixture_ids: set[int]):
    fixtures = {
        f.id: f
        for f in (
            await session.execute(select(Fixture).where(Fixture.id.in_(fixture_ids)))
        ).scalars().all()
    }
    team_ids = {
        tid for f in fixtures.values() for tid in (f.home_team_id, f.away_team_id)
    }
    teams = {
        t.id: t
        for t in (
            await session.execute(select(Team).where(Team.id.in_(team_ids)))
        ).scalars().all()
    }
    return fixtures, teams


def _daily_pick_to_selected(
    pick: DailyPick,
    *,
    home: str,
    away: str,
    fixture: Fixture | None,
    status: str,
) -> PickRow:
    sp = SelectedPick(
        fixture_id=pick.fixture_id,
        match_label=f"{home} vs {away}",
        market=pick.market,
        selection=pick.selection,
        odds=pick.odds,
        opening_odds=pick.opening_odds,
        fair_implied_prob=pick.fair_implied_prob or 0.5,
        line=pick.line,
        expected_return=pick.expected_value,
        probability=pick.probability,
        expected_value=pick.expected_value,
        confidence=pick.confidence,
        pick_rank_score=pick.roi_score,
        stake_units=pick.stake_units or 0.0,
        stake_method=pick.stake_method,
        market_regime=pick.market_regime or "moderate",
        reasoning=pick.reasoning or [],
        rank=pick.rank,
        fixture_date=fixture.fixture_date if fixture else None,
        status=status,
        pick_id=pick.id,
        calibrated_confidence=pick.calibrated_confidence,
        calibrated_ev=pick.calibrated_ev,
    )
    return PickRow(status=status, pick=sp)


def _pending_outcome_filter():
    return or_(
        DailyPick.outcome.is_(None),
        DailyPick.outcome == "",
        func.lower(DailyPick.outcome) == "pending",
    )


async def get_telegram_live_picks_rows(
    *, max_display: int | None = None
) -> list[PickRow]:
    """
    Isti pipeline kao Telegram dugme LIVE PICKS:
    dedupe → pending/live filter → sort po EV → rank 1..N.
    """
    raw = await get_picks_from_db()
    rows, stats = prepare_live_picks(raw, max_display=max_display)
    log.info("telegram_live_picks_rows", render=len(rows), **stats)
    return rows


async def get_picks_from_db() -> list[PickRow]:
    """Otvoreni tipovi: outcome pending, meč još nije FT (uključuje LIVE u toku)."""
    cutoff = datetime.utcnow() - timedelta(days=7)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyPick)
            .where(
                DailyPick.pick_date >= cutoff,
                _pending_outcome_filter(),
            )
            .order_by(DailyPick.pick_date.desc(), DailyPick.rank)
        )
        rows = list(result.scalars().all())
        if not rows:
            return []

        fixture_ids = {p.fixture_id for p in rows}
        fixtures, teams = await _load_fixtures_teams(session, fixture_ids)

    picks: list[PickRow] = []
    for pick in rows:
        if is_disabled_market(pick.market):
            continue

        fixture = fixtures.get(pick.fixture_id)
        if fixture:
            fs = (fixture.status or "NS").strip().upper()
            if fs in FIXTURE_FINISHED_STATUSES:
                continue

        home = away = "?"
        if fixture:
            home_t = teams.get(fixture.home_team_id)
            away_t = teams.get(fixture.away_team_id)
            home = home_t.name if home_t else "Home"
            away = away_t.name if away_t else "Away"

        row_status = getattr(pick, "status", None)
        status = resolve_pick_status(
            pick.outcome,
            fixture.status if fixture else None,
            row_status,
        )
        if status == "SETTLED":
            continue

        picks.append(
            _daily_pick_to_selected(
                pick,
                home=home,
                away=away,
                fixture=fixture,
                status=status,
            )
        )
    return picks


async def live_picks() -> str:
    """Svi otvoreni tipovi sortirani po EV (#1 = najjači)."""
    notifier = TelegramNotifier()
    active = await get_telegram_live_picks_rows(max_display=None)
    stats = {"total_render": len(active)}

    if not active:
        return (
            "📭 Nema aktivnih tipova\n\n"
            "Nema otvorenih tipova (pre kickoff-a ili u toku meča).\n\n"
            "▶ Generiši nove tipove:\n"
            "`python -m app.run_local --full-build`"
        )

    blocks = []
    for row in active:
        block = notifier.format_pick(row.pick)
        if row.status == "LIVE":
            block = f"🔴 U TOKU MEČA\n{block}"
        blocks.append(block)
    pending_n = sum(1 for r in active if r.status == "PENDING")
    live_n = sum(1 for r in active if r.status == "LIVE")
    summary = f"📊 Ukupno: {len(active)} (sortirano po EV, #1 = najjači)"
    if live_n:
        summary += f"\n⏳ {pending_n} pre kickoff · 🔴 {live_n} u toku"
    msg = (
        "📈 SVI AKTIVNI TIPOVI\n"
        f"{summary}\n"
        f"{PICK_SEPARATOR}\n\n"
        + f"\n\n{PICK_SEPARATOR}\n\n".join(blocks)
        + f"\n\n{PICK_SEPARATOR}"
    )
    log.info("live_picks_render", blocks=len(blocks), **stats)
    return msg.strip()


def split_telegram_picks_message(text: str, *, max_len: int = 4000) -> list[str]:
    """Podeli dugačku LIVE listu na više Telegram poruka."""
    if len(text) <= max_len:
        return [text]
    sep = f"\n\n{PICK_SEPARATOR}\n\n"
    header, _, body = text.partition(sep)
    header = header.strip() + f"\n{PICK_SEPARATOR}\n\n"
    chunks = [c.strip() for c in body.split(sep) if c.strip()]
    if not chunks:
        return [text[: max_len - 1] + "…"]

    parts: list[str] = []
    current = header
    for i, chunk in enumerate(chunks):
        piece = chunk if i == 0 else sep + chunk
        if len(current) + len(piece) + len(PICK_SEPARATOR) + 2 <= max_len:
            current += piece
            continue
        if current.strip() != header.strip():
            parts.append(current.rstrip() + f"\n\n{PICK_SEPARATOR}")
        current = f"📈 SVI AKTIVNI TIPOVI (nastavak)\n{PICK_SEPARATOR}\n\n{chunk}"

    if current.strip():
        suffix = "" if current.rstrip().endswith(PICK_SEPARATOR) else f"\n\n{PICK_SEPARATOR}"
        parts.append(current.rstrip() + suffix)
    return parts or [text[: max_len - 1] + "…"]


async def latest_picks_from_db() -> str:
    """Alias — koristi live_picks filter."""
    return await live_picks()


async def roi_stats() -> str:
    """Kompletan ROI i winrate — svi setlovani tipovi + pending na čekanju."""
    from app.services.paper_trading import PaperTradingService
    from app.telegram.formatting import format_tip

    report = await asyncio.to_thread(
        PaperTradingService().evaluate,
        all_time=True,
    )
    last_match = await _last_settled_match_label()
    msg = format_evaluate_report(report, last_match=last_match)

    # Dodaj pending pikove ispod statistike
    pending_rows = await get_picks_from_db()
    if pending_rows:
        num_icons = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
        lines = ["", "─────────────────────────", f"⏳ TIPOVI NA ČEKANJU  ({len(pending_rows)})"]
        for i, row in enumerate(pending_rows):
            p = row.pick
            tip = format_tip(p.market, p.selection, p.line)
            if p.fixture_date:
                from datetime import timezone, timedelta
                srb = timezone(timedelta(hours=2))
                local_dt = p.fixture_date.replace(tzinfo=timezone.utc).astimezone(srb)
                date_str = "📅 " + local_dt.strftime("%d.%m u %H:%M")
            else:
                date_str = ""
            icon = num_icons[i] if i < len(num_icons) else "🔵"
            live_tag = " 🔴 U TOKU" if row.status == "LIVE" else ""
            lines.append("")
            lines.append(f"{icon}  {p.match_label}{live_tag}")
            if date_str:
                lines.append(f"   {date_str}")
            lines.append(f"   🎯 {tip}  ·  @{p.odds:.2f}")
        lines.append("")
        lines.append("─────────────────────────")
        msg += "\n" + "\n".join(lines)

    return msg


async def _last_settled_match_label() -> str | None:
    """Poslednja setlovana utakmica (win/lose/push) — za ROI footer."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyPick)
            .where(DailyPick.outcome.in_(("win", "lose", "push")))
            .order_by(DailyPick.pick_date.desc(), DailyPick.id.desc())
            .limit(1)
        )
        pick = result.scalar_one_or_none()
        if not pick:
            return None

        fixture = await session.get(Fixture, pick.fixture_id)
        if not fixture:
            return pick.pick_date.strftime("%Y-%m-%d %H:%M")

        teams = {
            t.id: t
            for t in (
                await session.execute(
                    select(Team).where(
                        Team.id.in_((fixture.home_team_id, fixture.away_team_id))
                    )
                )
            ).scalars().all()
        }
        home_t = teams.get(fixture.home_team_id)
        away_t = teams.get(fixture.away_team_id)
        home = home_t.name if home_t else "Home"
        away = away_t.name if away_t else "Away"
        when = pick.pick_date.strftime("%Y-%m-%d %H:%M")
        score = ""
        if fixture.home_goals is not None:
            score = f" ({fixture.home_goals}-{fixture.away_goals})"
        return f"{home} vs {away}{score} · {when}"


def format_evaluate_report(report: dict, *, last_match: str | None = None) -> str:
    if not report.get("total_bets"):
        return "📊 ROI STATISTIKA\n\nJoš nema setlovanih tipova u bazi."

    profit = report.get("profit_units", 0.0) or 0.0
    profit_sign = "+" if profit >= 0 else ""
    roi = report.get("roi_pct", 0.0) or 0.0
    roi_sign = "+" if roi >= 0 else ""
    staked = report.get("staked_units", 0.0) or 0.0

    lines = [
        "📊 ROI STATISTIKA",
        "(automatski — svi bot tipovi @ bot kvota)",
        "",
        f"💰 Profit: {profit_sign}{profit:.2f}u",
        f"📈 ROI: {roi_sign}{roi:.2f}%",
        f"🎯 Winrate: {report.get('winrate', 0.0):.1f}%",
        f"📦 Tipovi: {report.get('total_bets', 0)} završeno",
        f"💵 Uloženo: {staked:.2f}u",
        f"✅ Pobede: {report.get('wins', 0)}",
        f"❌ Gubici: {report.get('losses', 0)}",
    ]
    avg_clv = report.get("avg_clv")
    if avg_clv is not None and report.get("total_bets", 0) > 0:
        lines.append(f"📉 CLV prosečno: {avg_clv:+.2%}")
    pushes = report.get("pushes", 0)
    if pushes:
        lines.append(f"➖ Push: {pushes}")
    if last_match:
        lines.extend(["", f"🕐 Poslednja setlovana: {last_match}"])

    return "\n".join(lines)


async def settle_now() -> str:
    from app.services.paper_trading import PaperTradingService

    count = await PaperTradingService().settle_finished_picks()
    if count:
        return f"✅ Settle uspešno završen\n\nRešeno tipova: {count}"
    return "✅ Settle uspešno završen\n\nNema novih FT mečeva za rešavanje."


async def format_settled_bets(limit: int = 10) -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DailyPick)
            .where(
                DailyPick.outcome.in_(("win", "lose", "push")),
            )
            .order_by(DailyPick.pick_date.desc())
            .limit(limit * 3)
        )
        picks = [
            p for p in result.scalars().all()
            if not is_disabled_market(p.market)
        ][:limit]
        if not picks:
            return "📉 POSLEDNJI REZULTATI\n\nJoš nema rešenih tipova."

        fixture_ids = {p.fixture_id for p in picks}
        fixtures, teams = await _load_fixtures_teams(session, fixture_ids)

    lines = ["📉 POSLEDNJI REZULTATI", ""]
    for pick in picks:
        fixture = fixtures.get(pick.fixture_id)
        home = away = "?"
        score = ""
        if fixture:
            home_t = teams.get(fixture.home_team_id)
            away_t = teams.get(fixture.away_team_id)
            home = home_t.name if home_t else "Home"
            away = away_t.name if away_t else "Away"
            if fixture.home_goals is not None:
                score = f" ({fixture.home_goals}-{fixture.away_goals})"

        icon = {"win": "✅", "lose": "❌", "push": "➖"}.get(pick.outcome, "•")
        tip = format_tip(pick.market, pick.selection, pick.line)
        profit = pick.profit_units or 0.0
        profit_txt = f"{profit:+.2f}u" if pick.profit_units is not None else "—"
        odds_txt = pick.odds
        clv_txt = ""
        clv_val = pick.clv_raw if pick.clv_raw is not None else pick.clv
        if clv_val is not None:
            clv_txt = f" | CLV {clv_val:+.2%}"
        lines.append(f"{icon} {home} vs {away}{score}")
        lines.append(f"   {tip}")
        lines.append(
            f"   @{odds_txt:.2f} | {pick.outcome.upper()} | {profit_txt}{clv_txt}"
        )
        lines.append("")

    return "\n".join(lines).strip()


async def bot_status() -> str:
    from app.config import get_settings as gs

    cfg = gs()
    dc_path = resolve_dc_params_path()
    models_line = "DIXON-COLES (DC)" if dc_path else "DIXON-COLES (default params)"
    api_ok = bool(cfg.api_football_key)
    uptime = datetime.utcnow() - _runtime_started_at
    hours, rem = divmod(int(uptime.total_seconds()), 3600)
    minutes, _ = divmod(rem, 60)

    return f"""
⚙️ STATUS BOTA

🟢 Pipeline: ACTIVE
🧠 Models: {models_line}
📡 API: {"CONNECTED" if api_ok else "NO KEY"}
⏱ Runtime: {hours}h {minutes}m
""".strip()


def restart_bot() -> str:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent.parent

    if sys.platform == "win32":
        bat = root / "startbot.bat"
        if not bat.is_file():
            return "❌ startbot.bat nije pronađen."
        subprocess.Popen(
            ["cmd", "/c", "start", "", str(bat)],
            cwd=str(root),
        )
        return "🔄 RESTART RUN\n\nPokrećem startbot.bat u novom prozoru..."

    sh = root / "scripts" / "server" / "restart_bot.sh"
    if not sh.is_file():
        return "❌ restart_bot.sh nije pronađen."

    subprocess.Popen(
        ["/bin/bash", str(sh)],
        cwd=str(root),
        start_new_session=True,
    )
    return (
        "🔄 RESTART RUN\n\n"
        "Povlačim lige + pickove, pa restartujem servise.\n"
        "Poruka stiže za ~1–2 min."
    )
