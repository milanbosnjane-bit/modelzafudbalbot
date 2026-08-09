"""Database models for football ROI prediction system."""

from datetime import datetime
from enum import Enum

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class MarketType(str, Enum):
    MATCH_WINNER = "match_winner"
    DOUBLE_CHANCE = "double_chance"
    OVER_UNDER = "over_under"
    BTTS = "btts"
    ASIAN_HANDICAP = "asian_handicap"


class BetOutcome(str, Enum):
    PENDING = "pending"
    WIN = "win"
    LOSE = "lose"
    PUSH = "push"
    VOID = "void"


class League(Base):
    __tablename__ = "leagues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    country: Mapped[str | None] = mapped_column(String(100))
    season: Mapped[int | None] = mapped_column(Integer)
    strength_rating: Mapped[float | None] = mapped_column(Float, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    teams: Mapped[list["Team"]] = relationship(back_populates="league")
    fixtures: Mapped[list["Fixture"]] = relationship(back_populates="league")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    league_id: Mapped[int | None] = mapped_column(ForeignKey("leagues.id"))
    venue_lat: Mapped[float | None] = mapped_column(Float)
    venue_lon: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    league: Mapped["League | None"] = relationship(back_populates="teams")
    home_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="home_team", foreign_keys="Fixture.home_team_id"
    )
    away_fixtures: Mapped[list["Fixture"]] = relationship(
        back_populates="away_team", foreign_keys="Fixture.away_team_id"
    )


class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        Index("ix_fixtures_date", "fixture_date"),
        Index("ix_fixtures_league_date", "league_id", "fixture_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    fixture_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="NS")
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)
    home_xg: Mapped[float | None] = mapped_column(Float)
    away_xg: Mapped[float | None] = mapped_column(Float)
    venue: Mapped[str | None] = mapped_column(String(255))
    referee: Mapped[str | None] = mapped_column(String(255))
    round: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    league: Mapped["League"] = relationship(back_populates="fixtures")
    home_team: Mapped["Team"] = relationship(
        back_populates="home_fixtures", foreign_keys=[home_team_id]
    )
    away_team: Mapped["Team"] = relationship(
        back_populates="away_fixtures", foreign_keys=[away_team_id]
    )
    odds: Mapped[list["OddsSnapshot"]] = relationship(back_populates="fixture")
    stats: Mapped[list["MatchStats"]] = relationship(back_populates="fixture")
    lineups: Mapped[list["Lineup"]] = relationship(back_populates="fixture")
    injuries: Mapped[list["Injury"]] = relationship(back_populates="fixture")
    weather: Mapped["Weather | None"] = relationship(back_populates="fixture")
    features: Mapped["FeatureVector | None"] = relationship(back_populates="fixture")
    predictions: Mapped[list["Prediction"]] = relationship(back_populates="fixture")
    picks: Mapped[list["DailyPick"]] = relationship(back_populates="fixture")


class OddsSnapshot(Base):
    """Track opening, current, and closing odds for CLV analysis."""

    __tablename__ = "odds_snapshots"
    __table_args__ = (
        Index("ix_odds_fixture_market", "fixture_id", "market", "selection"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    line: Mapped[float | None] = mapped_column(Float)
    opening_odds: Mapped[float | None] = mapped_column(Float)
    current_odds: Mapped[float] = mapped_column(Float, nullable=False)
    closing_odds: Mapped[float | None] = mapped_column(Float)
    implied_prob: Mapped[float | None] = mapped_column(Float)
    fair_prob: Mapped[float | None] = mapped_column(Float)
    market_overround: Mapped[float | None] = mapped_column(Float)
    odds_change_pct: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    is_closing: Mapped[bool] = mapped_column(Boolean, default=False)

    fixture: Mapped["Fixture"] = relationship(back_populates="odds")


class MatchStats(Base):
    __tablename__ = "match_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    shots_total: Mapped[int | None] = mapped_column(Integer)
    shots_on_target: Mapped[int | None] = mapped_column(Integer)
    shots_inside_box: Mapped[int | None] = mapped_column(Integer)
    possession_pct: Mapped[float | None] = mapped_column(Float)
    corners: Mapped[int | None] = mapped_column(Integer)
    fouls: Mapped[int | None] = mapped_column(Integer)
    yellow_cards: Mapped[int | None] = mapped_column(Integer)
    red_cards: Mapped[int | None] = mapped_column(Integer)
    big_chances: Mapped[int | None] = mapped_column(Integer)
    xg: Mapped[float | None] = mapped_column(Float)
    xga: Mapped[float | None] = mapped_column(Float)
    set_pieces_conceded: Mapped[int | None] = mapped_column(Integer)

    fixture: Mapped["Fixture"] = relationship(back_populates="stats")


class Lineup(Base):
    __tablename__ = "lineups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    formation: Mapped[str | None] = mapped_column(String(20))
    starting_xi: Mapped[dict | None] = mapped_column(JSON)
    substitutes: Mapped[dict | None] = mapped_column(JSON)
    rotation_count: Mapped[int | None] = mapped_column(Integer, default=0)

    fixture: Mapped["Fixture"] = relationship(back_populates="lineups")


class Injury(Base):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int | None] = mapped_column(ForeignKey("fixtures.id"))
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    player_name: Mapped[str] = mapped_column(String(255), nullable=False)
    injury_type: Mapped[str | None] = mapped_column(String(100))
    severity: Mapped[float | None] = mapped_column(Float, default=0.5)
    is_key_player: Mapped[bool] = mapped_column(Boolean, default=False)

    fixture: Mapped["Fixture | None"] = relationship(back_populates="injuries")


class Standing(Base):
    __tablename__ = "standings"
    __table_args__ = (UniqueConstraint("league_id", "season", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    league_id: Mapped[int] = mapped_column(ForeignKey("leagues.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    played: Mapped[int] = mapped_column(Integer, default=0)
    won: Mapped[int] = mapped_column(Integer, default=0)
    draw: Mapped[int] = mapped_column(Integer, default=0)
    lost: Mapped[int] = mapped_column(Integer, default=0)
    goals_for: Mapped[int] = mapped_column(Integer, default=0)
    goals_against: Mapped[int] = mapped_column(Integer, default=0)
    form: Mapped[str | None] = mapped_column(String(20))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Weather(Base):
    __tablename__ = "weather"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), unique=True)
    temperature: Mapped[float | None] = mapped_column(Float)
    humidity: Mapped[float | None] = mapped_column(Float)
    wind_speed: Mapped[float | None] = mapped_column(Float)
    precipitation: Mapped[float | None] = mapped_column(Float)
    conditions: Mapped[str | None] = mapped_column(String(100))

    fixture: Mapped["Fixture"] = relationship(back_populates="weather")


class FeatureVector(Base):
    __tablename__ = "feature_vectors"
    __table_args__ = (
        UniqueConstraint("fixture_id", "as_of_datetime", name="uq_feature_fixture_asof"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    as_of_datetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    features: Mapped[dict] = mapped_column(JSON, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fixture: Mapped["Fixture"] = relationship(back_populates="features")


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (Index("ix_predictions_fixture_market", "fixture_id", "market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    prob_poisson: Mapped[float | None] = mapped_column(Float)
    prob_lightgbm: Mapped[float | None] = mapped_column(Float)
    prob_xgboost: Mapped[float | None] = mapped_column(Float)
    prob_neural: Mapped[float | None] = mapped_column(Float)
    prob_ensemble: Mapped[float] = mapped_column(Float, nullable=False)
    bookmaker_odds: Mapped[float] = mapped_column(Float, nullable=False)
    implied_prob: Mapped[float] = mapped_column(Float, nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    roi_score: Mapped[float] = mapped_column(Float, nullable=False)
    model_agreement: Mapped[float | None] = mapped_column(Float)
    predicted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fixture: Mapped["Fixture"] = relationship(back_populates="predictions")


class DailyPick(Base):
    __tablename__ = "daily_picks"
    __table_args__ = (Index("ix_daily_picks_date", "pick_date"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pick_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    line: Mapped[float | None] = mapped_column(Float)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    opening_odds: Mapped[float | None] = mapped_column(Float)
    closing_odds: Mapped[float | None] = mapped_column(Float)
    fair_implied_prob: Mapped[float | None] = mapped_column(Float)
    probability: Mapped[float] = mapped_column(Float, nullable=False)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    roi_score: Mapped[float] = mapped_column(Float, nullable=False)
    stake_units: Mapped[float] = mapped_column(Float, nullable=False)
    stake_method: Mapped[str] = mapped_column(String(50), default="fractional_kelly")
    reasoning: Mapped[list | None] = mapped_column(JSON)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(20), default=BetOutcome.PENDING.value)
    profit_units: Mapped[float | None] = mapped_column(Float)
    realized_return: Mapped[float | None] = mapped_column(Float)
    clv: Mapped[float | None] = mapped_column(Float)
    clv_raw: Mapped[float | None] = mapped_column(Float)
    closing_fair_edge: Mapped[float | None] = mapped_column(Float)
    closing_fair_prob: Mapped[float | None] = mapped_column(Float)
    model_edge: Mapped[float | None] = mapped_column(Float)
    closing_edge: Mapped[float | None] = mapped_column(Float)
    edge_capture: Mapped[float | None] = mapped_column(Float)
    raw_edge_capture: Mapped[float | None] = mapped_column(Float)
    adjusted_edge_capture: Mapped[float | None] = mapped_column(Float)
    market_regime: Mapped[str | None] = mapped_column(String(30))
    is_paper: Mapped[bool] = mapped_column(Boolean, default=False)
    played_manually: Mapped[bool] = mapped_column(Boolean, default=False)
    user_odds: Mapped[float | None] = mapped_column(Float)
    sent_to_telegram: Mapped[bool] = mapped_column(Boolean, default=False)
    warning_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    calibrated_confidence: Mapped[float | None] = mapped_column(Float)
    calibrated_ev: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    fixture: Mapped["Fixture"] = relationship(back_populates="picks")


class ConfidencePredictionLog(Base):
    """Pre-match snapshot for confidence calibrator training (isolated from DC)."""

    __tablename__ = "confidence_prediction_logs"
    __table_args__ = (Index("ix_confidence_logs_predicted", "predicted_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    daily_pick_id: Mapped[int | None] = mapped_column(ForeignKey("daily_picks.id"))
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    predicted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    dixon_coles_probability: Mapped[float] = mapped_column(Float, nullable=False)
    market_fair_probability: Mapped[float] = mapped_column(Float, nullable=False)
    edge: Mapped[float] = mapped_column(Float, nullable=False)
    raw_ev: Mapped[float] = mapped_column(Float, nullable=False)
    odds: Mapped[float] = mapped_column(Float, nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    league_id: Mapped[int | None] = mapped_column(Integer)
    home_ft_count: Mapped[int | None] = mapped_column(Integer)
    away_ft_count: Mapped[int | None] = mapped_column(Integer)
    used_default_lambda: Mapped[bool] = mapped_column(Boolean, default=False)
    home_lambda: Mapped[float | None] = mapped_column(Float)
    away_lambda: Mapped[float | None] = mapped_column(Float)
    feature_quality: Mapped[float | None] = mapped_column(Float)
    hours_to_kickoff: Mapped[float | None] = mapped_column(Float)
    old_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    calibrated_confidence: Mapped[float | None] = mapped_column(Float)
    calibrated_ev: Mapped[float | None] = mapped_column(Float)
    outcome: Mapped[str] = mapped_column(String(20), default=BetOutcome.PENDING.value)
    snapshot_json: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    total_bets: Mapped[int] = mapped_column(Integer, default=0)
    total_staked: Mapped[float] = mapped_column(Float, default=0.0)
    total_profit: Mapped[float] = mapped_column(Float, default=0.0)
    roi_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_clv: Mapped[float | None] = mapped_column(Float)
    avg_ev: Mapped[float | None] = mapped_column(Float)
    win_rate: Mapped[float | None] = mapped_column(Float)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float)
    config: Mapped[dict | None] = mapped_column(JSON)
    results: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ModelMetrics(Base):
    """Track model quality via CLV and ROI, not win rate."""

    __tablename__ = "model_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(50), nullable=False)
    market: Mapped[str] = mapped_column(String(50), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    avg_clv: Mapped[float | None] = mapped_column(Float)
    avg_ev: Mapped[float | None] = mapped_column(Float)
    roi_pct: Mapped[float | None] = mapped_column(Float)
    brier_score: Mapped[float | None] = mapped_column(Float)
    calibration_error: Mapped[float | None] = mapped_column(Float)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RegimeHistory(Base):
    __tablename__ = "regime_history"
    __table_args__ = (Index("ix_regime_history_date", "detected_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fixture_id: Mapped[int | None] = mapped_column(ForeignKey("fixtures.id"))
    league_id: Mapped[int | None] = mapped_column(Integer)
    cluster_id: Mapped[int] = mapped_column(Integer, nullable=False)
    regime_label: Mapped[str] = mapped_column(String(30), nullable=False)
    ev_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    features: Mapped[dict] = mapped_column(JSON, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FeatureDriftRun(Base):
    __tablename__ = "feature_drift_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    max_psi: Mapped[float] = mapped_column(Float, nullable=False)
    mean_psi: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    feature_psi: Mapped[dict] = mapped_column(JSON, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    retrain_required: Mapped[bool] = mapped_column(Boolean, default=False)
    feature_snapshot: Mapped[dict | None] = mapped_column(JSON)
    prediction_time: Mapped[datetime | None] = mapped_column(DateTime)


class TargetSelectionMetrics(Base):
    __tablename__ = "target_selection_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transform_name: Mapped[str] = mapped_column(String(30), nullable=False)
    mae: Mapped[float | None] = mapped_column(Float)
    rmse: Mapped[float | None] = mapped_column(Float)
    oos_roi_pct: Mapped[float | None] = mapped_column(Float)
    stability_score: Mapped[float | None] = mapped_column(Float)
    composite_score: Mapped[float | None] = mapped_column(Float)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RetrainEvent(Base):
    __tablename__ = "retrain_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    triggered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    executed: Mapped[bool] = mapped_column(Boolean, default=False)
    result: Mapped[dict | None] = mapped_column(JSON)
