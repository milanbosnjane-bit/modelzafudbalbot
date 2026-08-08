"""Build ML training rows from finished fixtures (not only settled daily picks)."""

from __future__ import annotations

from datetime import datetime

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database.models import FeatureVector, Fixture, OddsSnapshot
from app.predictions.market_selection import is_eligible_selection
from app.predictions.probability_layer import is_disabled_market, is_supported_market
from app.training.market_encoding import augment_features
from app.training.outcomes import resolve_market_outcome
from app.training.targets import realized_return_from_outcome
from app.predictions.regime import extract_regime_features
from app.utils.helpers import decision_time
from app.utils.legacy_data import LEGACY_BOOKMAKERS, has_api_odds_exists, is_legacy_bookmaker
from app.utils.odds import median_odds

logger = structlog.get_logger()
settings = get_settings()

PICK_MARKETS = frozenset({"match_winner", "over_under", "btts"})
MIN_BOOKMAKERS = 2


def _group_odds_at_decision(
    snapshots: list[OddsSnapshot],
    *,
    exclude_legacy: bool = False,
) -> dict[str, dict[str, dict]]:
    if not snapshots:
        return {}

    supported = set(settings.supported_markets) & PICK_MARKETS
    latest: dict[tuple, OddsSnapshot] = {}
    opening: dict[tuple, float] = {}
    for snap in snapshots:
        if exclude_legacy and is_legacy_bookmaker(snap.bookmaker):
            continue
        key = (snap.bookmaker, snap.market, snap.selection, snap.line)
        if key not in opening:
            opening[key] = snap.opening_odds or snap.current_odds
        if key not in latest or snap.captured_at > latest[key].captured_at:
            latest[key] = snap

    grouped: dict[str, dict[str, dict]] = {}
    for key, snap in latest.items():
        _, market, selection, line = key
        if not is_supported_market(market, supported):
            continue
        if is_disabled_market(market):
            continue
        if not is_eligible_selection(market, selection, line, live=False):
            continue
        grouped.setdefault(market, {}).setdefault(
            selection,
            {"odds_list": [], "line": line},
        )
        grouped[market][selection]["odds_list"].append(snap.current_odds)

    result: dict[str, dict[str, dict]] = {}
    for market, selections in grouped.items():
        result[market] = {}
        for selection, data in selections.items():
            odds_list = data["odds_list"]
            if len(odds_list) < MIN_BOOKMAKERS:
                continue
            result[market][selection] = {
                "odds": median_odds(odds_list),
                "line": data["line"],
            }
    return result


def build_historical_training_records(
    session: Session,
    *,
    exclude_legacy: bool | None = None,
) -> tuple[list[dict], list[dict]]:
    """One row per eligible market line on finished fixtures with point-in-time features."""
    if exclude_legacy is None:
        exclude_legacy = settings.exclude_legacy_training

    fixture_query = select(Fixture).where(
        Fixture.status.in_(["FT", "AET", "PEN"]),
        Fixture.home_goals.isnot(None),
        Fixture.away_goals.isnot(None),
    )
    if exclude_legacy:
        fixture_query = fixture_query.where(has_api_odds_exists())

    fixtures = session.execute(fixture_query).scalars().all()

    records: list[dict] = []
    regime_rows: list[dict] = []

    for fixture in fixtures:
        as_of = decision_time(fixture.fixture_date, settings.decision_hours_before_kickoff)
        fv = session.execute(
            select(FeatureVector)
            .where(FeatureVector.fixture_id == fixture.id)
            .order_by(FeatureVector.as_of_datetime.desc())
        ).scalar_one_or_none()
        if not fv or fv.as_of_datetime >= fixture.fixture_date:
            continue

        snaps = session.execute(
            select(OddsSnapshot).where(
                OddsSnapshot.fixture_id == fixture.id,
                OddsSnapshot.market.in_(tuple(PICK_MARKETS)),
            )
        ).scalars().all()
        odds_by_market = _group_odds_at_decision(list(snaps), exclude_legacy=exclude_legacy)
        if not odds_by_market:
            continue

        for market, selections in odds_by_market.items():
            for selection, odds_info in selections.items():
                outcome = resolve_market_outcome(
                    fixture.home_goals,
                    fixture.away_goals,
                    market,
                    selection,
                    odds_info.get("line"),
                )
                if outcome == "void":
                    continue
                odds = float(odds_info["odds"])
                raw_ret = realized_return_from_outcome(outcome, odds, None, 1.0)
                feats = augment_features(fv.features, market, selection)
                regime_rows.append(extract_regime_features(fv.features))
                records.append({
                    **feats,
                    "raw_return": raw_ret,
                    "odds": odds,
                    "profit_units": raw_ret,
                    "_ts": as_of or fixture.fixture_date,
                })

    records.sort(key=lambda r: r["_ts"])
    logger.info(
        "historical_training_records",
        fixtures=len(fixtures),
        samples=len(records),
        exclude_legacy=exclude_legacy,
    )
    return records, regime_rows
