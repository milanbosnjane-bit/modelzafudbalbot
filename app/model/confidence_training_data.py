"""
Read-only adapter: map botposlednji1 daily_picks -> calibrator training rows.
Never writes to the legacy folder.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.model.confidence_calibrator import (
    CalibratorInput,
    detect_default_lambda,
    parse_lambdas_from_reasoning,
)

LEGACY_DB = Path(r"C:\Users\Miki\Desktop\botposlednji1\data\football_roi.db")

EXCLUDED_OUTCOMES = {"void", "pending", "push", "cancelled", "postponed"}


@dataclass
class TrainingRow:
    source: str
    fixture_id: int
    market: str
    selection: str
    prediction_timestamp: str
    kickoff_timestamp: str
    dixon_coles_probability: float
    market_fair_probability: float
    edge: float
    raw_ev: float
    odds: float
    league_id: int | None
    home_ft_count: int | None
    away_ft_count: int | None
    used_default_lambda: bool
    home_lambda: float | None
    away_lambda: float | None
    feature_quality: float
    hours_to_kickoff: float
    old_confidence: float
    outcome: str  # win | lose
    daily_pick_id: int | None = None

    @property
    def dedupe_key(self) -> tuple:
        return (self.fixture_id, self.market, self.selection, self.prediction_timestamp)

    @property
    def target(self) -> int:
        return 1 if self.outcome == "win" else 0

    def to_calibrator_input(self) -> CalibratorInput:
        ts = datetime.fromisoformat(self.prediction_timestamp.replace("Z", ""))
        return CalibratorInput(
            dixon_coles_probability=self.dixon_coles_probability,
            market_fair_probability=self.market_fair_probability,
            edge=self.edge,
            raw_ev=self.raw_ev,
            odds=self.odds,
            market=self.market,
            selection=self.selection,
            league_id=self.league_id,
            home_ft_count=self.home_ft_count,
            away_ft_count=self.away_ft_count,
            used_default_lambda=self.used_default_lambda,
            home_lambda=self.home_lambda,
            away_lambda=self.away_lambda,
            feature_quality=self.feature_quality,
            hours_to_kickoff=self.hours_to_kickoff,
            old_confidence=self.old_confidence,
            predicted_at=ts,
        )


def ro_connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", ""))


def team_ft_count(conn: sqlite3.Connection, team_id: int, before: datetime) -> int:
    return conn.execute(
        """
        SELECT COUNT(1) FROM fixtures
        WHERE status = 'FT' AND fixture_date < ?
          AND (home_team_id = ? OR away_team_id = ?)
        """,
        (before.isoformat(sep=" "), team_id, team_id),
    ).fetchone()[0]


def feature_quality_from_reasoning(reasoning: list | None) -> float:
    if not reasoning:
        return 0.35
    return 0.55 if any("Dixon-Coles" in str(x) for x in reasoning) else 0.35


def load_rows_from_db(db: Path, source: str) -> list[TrainingRow]:
    if not db.is_file():
        return []

    conn = ro_connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
          p.id AS daily_pick_id,
          p.fixture_id,
          p.market,
          p.selection,
          p.pick_date,
          p.probability,
          p.fair_implied_prob,
          p.expected_value,
          p.odds,
          p.confidence,
          p.reasoning,
          p.outcome,
          f.fixture_date,
          f.league_id,
          f.home_team_id,
          f.away_team_id
        FROM daily_picks p
        JOIN fixtures f ON f.id = p.fixture_id
        WHERE p.outcome IN ('win', 'lose')
          AND p.pick_date < f.fixture_date
          AND p.probability IS NOT NULL
          AND p.odds > 1
          AND p.market IS NOT NULL AND p.market != ''
          AND p.selection IS NOT NULL AND p.selection != ''
        ORDER BY p.pick_date
        """
    ).fetchall()

    out: list[TrainingRow] = []
    for r in rows:
        pick_dt = _parse_ts(r["pick_date"])
        kickoff = _parse_ts(r["fixture_date"])
        reasoning = r["reasoning"]
        if isinstance(reasoning, str):
            try:
                reasoning = json.loads(reasoning)
            except json.JSONDecodeError:
                reasoning = []
        home_l, away_l = parse_lambdas_from_reasoning(reasoning)
        fair = float(r["fair_implied_prob"] or (1.0 / r["odds"]))
        prob = float(r["probability"])
        h_ft = team_ft_count(conn, r["home_team_id"], kickoff)
        a_ft = team_ft_count(conn, r["away_team_id"], kickoff)
        hours = max(0.0, (kickoff - pick_dt).total_seconds() / 3600.0)
        out.append(
            TrainingRow(
                source=source,
                fixture_id=int(r["fixture_id"]),
                market=str(r["market"]),
                selection=str(r["selection"]),
                prediction_timestamp=pick_dt.isoformat(sep=" "),
                kickoff_timestamp=kickoff.isoformat(sep=" "),
                dixon_coles_probability=prob,
                market_fair_probability=fair,
                edge=prob - fair,
                raw_ev=float(r["expected_value"]),
                odds=float(r["odds"]),
                league_id=int(r["league_id"]) if r["league_id"] is not None else None,
                home_ft_count=h_ft,
                away_ft_count=a_ft,
                used_default_lambda=detect_default_lambda(home_l, away_l),
                home_lambda=home_l,
                away_lambda=away_l,
                feature_quality=feature_quality_from_reasoning(reasoning),
                hours_to_kickoff=hours,
                old_confidence=float(r["confidence"]),
                outcome=str(r["outcome"]),
                daily_pick_id=int(r["daily_pick_id"]),
            )
        )
    conn.close()
    return out


def load_legacy_rows() -> list[TrainingRow]:
    return load_rows_from_db(LEGACY_DB, "botposlednji1")


def merge_deduplicate(*groups: list[TrainingRow]) -> list[TrainingRow]:
    merged: dict[tuple, TrainingRow] = {}
    for group in groups:
        for row in group:
            key = row.dedupe_key
            if key not in merged:
                merged[key] = row
            elif row.source == "current_bot":
                merged[key] = row
    return sorted(merged.values(), key=lambda r: r.prediction_timestamp)
