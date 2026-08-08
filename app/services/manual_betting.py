"""Ručno klađenje — označi odigrane tipove i prati CLV/ROI."""

from __future__ import annotations

import structlog
from sqlalchemy import func, select

from app.database.models import DailyPick, Fixture, OddsSnapshot, Team
from app.database.session import AsyncSessionLocal, SyncSessionLocal
from app.utils.helpers import closing_line_value

logger = structlog.get_logger()


def effective_odds(pick: DailyPick) -> float:
    return float(pick.user_odds or pick.odds)


class ManualBettingService:
    async def mark_played(self, pick_id: int, user_odds: float) -> dict:
        if user_odds < 1.01:
            raise ValueError("Kvota mora biti >= 1.01")

        async with AsyncSessionLocal() as session:
            pick = await session.get(DailyPick, pick_id)
            if not pick:
                raise ValueError(f"Tip #{pick_id} nije pronađen")

            pick.played_manually = True
            pick.user_odds = user_odds
            pick.is_paper = False
            await session.commit()

            fixture = await session.get(Fixture, pick.fixture_id)
            label = await self._match_label(session, fixture)
            logger.info(
                "manual_bet_marked",
                pick_id=pick_id,
                user_odds=user_odds,
                bot_odds=pick.odds,
            )
            return {
                "pick_id": pick_id,
                "match": label,
                "market": pick.market,
                "selection": pick.selection,
                "bot_odds": pick.odds,
                "user_odds": user_odds,
            }

    async def _match_label(self, session, fixture: Fixture | None) -> str:
        if not fixture:
            return "?"
        teams = (
            await session.execute(
                select(Team).where(Team.id.in_((fixture.home_team_id, fixture.away_team_id)))
            )
        ).scalars().all()
        by_id = {t.id: t.name for t in teams}
        home = by_id.get(fixture.home_team_id, "Home")
        away = by_id.get(fixture.away_team_id, "Away")
        return f"{home} vs {away}"

    def stats_report(self) -> dict:
        session = SyncSessionLocal()
        try:
            picks = session.execute(
                select(DailyPick).where(
                    DailyPick.played_manually == True,
                    DailyPick.outcome.in_(("win", "lose", "push")),
                )
            ).scalars().all()

            pending = session.execute(
                select(func.count(DailyPick.id)).where(
                    DailyPick.played_manually == True,
                    DailyPick.outcome == "pending",
                )
            ).scalar() or 0

            if not picks:
                return {
                    "total_bets": 0,
                    "pending": pending,
                    "wins": 0,
                    "losses": 0,
                    "profit_units": 0.0,
                    "staked_units": 0.0,
                    "roi_pct": 0.0,
                    "avg_clv": 0.0,
                    "clv_coverage": 0.0,
                    "winrate": 0.0,
                }

            profits: list[float] = []
            staked = 0.0
            wins = losses = 0
            clvs: list[float] = []
            for pick in picks:
                stake = pick.stake_units or 1.0
                staked += stake
                odds = effective_odds(pick)
                if pick.outcome == "win":
                    wins += 1
                    profits.append(stake * (odds - 1))
                elif pick.outcome == "lose":
                    losses += 1
                    profits.append(-stake)
                else:
                    profits.append(0.0)
                clv_val = pick.clv_raw if pick.clv_raw is not None else pick.clv
                if clv_val is not None:
                    clvs.append(clv_val)

            profit_total = sum(profits)
            decisive = wins + losses
            return {
                "total_bets": len(picks),
                "pending": pending,
                "wins": wins,
                "losses": losses,
                "profit_units": profit_total,
                "staked_units": staked,
                "roi_pct": (profit_total / staked * 100) if staked else 0.0,
                "avg_clv": sum(clvs) / len(clvs) if clvs else 0.0,
                "clv_coverage": len(clvs) / len(picks) if picks else 0.0,
                "winrate": (wins / decisive * 100) if decisive else 0.0,
            }
        finally:
            session.close()


def format_manual_stats(report: dict) -> str:
    if not report.get("total_bets") and not report.get("pending"):
        return (
            "📝 MOJI TIPOVI\n\n"
            "Još nema ručno odigranih tipova.\n\n"
            "Kad odigraš tip u kladionici, pošalji:\n"
            "`/odigrao ID KVOTA`\n"
            "npr. `/odigrao 42 3.25`\n\n"
            "ID vidiš u LIVE PICKS poruci (🆔 #ID)."
        )

    profit = report.get("profit_units", 0.0)
    roi = report.get("roi_pct", 0.0)
    return "\n".join([
        "📝 MOJI TIPOVI (ručno odigrano)",
        "",
        f"💰 Profit: {profit:+.2f}u",
        f"📈 ROI: {roi:+.2f}%",
        f"🎯 Winrate: {report.get('winrate', 0):.1f}%",
        f"📦 Završeno: {report.get('total_bets', 0)}",
        f"⏳ Na čekanju: {report.get('pending', 0)}",
        f"📉 CLV prosečno: {report.get('avg_clv', 0):+.2%}",
        f"📊 CLV pokrivenost: {report.get('clv_coverage', 0):.0%}",
        "",
        "Unos: `/odigrao ID KVOTA`",
    ])
