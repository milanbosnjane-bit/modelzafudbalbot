"""Application configuration loaded from environment."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    app_debug: bool = True
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://football:football@localhost:5432/football_roi"
    database_url_sync: str = "postgresql://football:football@localhost:5432/football_roi"

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 3600
    use_memory_cache: bool = False
    local_mode: bool = False

    api_football_key: str = ""
    api_football_base_url: str = "https://v3.football.api-sports.io"

    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"

    weather_api_key: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""  # jedan ID ili više odvojenih zarezom

    @property
    def telegram_chat_ids(self) -> list[str]:
        raw = (self.telegram_chat_id or "").strip()
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    min_ev_threshold: float = 0.015
    # Hard ceiling — reject longshot EV mirages (backtest avg EV was +52%).
    max_ev_threshold: float = 0.25
    # Absolute tip odds floor (enforced in pick_selector.GLOBAL_MIN_ODDS).
    min_pick_odds: float = 2.0
    # Home / Away / BTTS Yes odds cap (Draw uses tighter 3.60 in selector).
    max_pick_odds: float = 4.50
    min_confidence_threshold: float = 0.55
    max_daily_picks: int = 6
    kelly_fraction: float = 0.25
    default_stake_method: str = "fractional_kelly"

    # Temporal integrity
    decision_hours_before_kickoff: float = 1.0
    min_training_samples: int = 100
    train_test_ratio: float = 0.2
    train_embargo_days: int = 1
    allow_synthetic_training: bool = False
    exclude_legacy_training: bool = True

    # Realistic execution
    backtest_slippage_pct: float = 0.015
    probability_shrink_weight: float = 0.55
    max_stake_pct_bankroll: float = 0.02
    default_bankroll: float = 100.0
    paper_trading_enabled: bool = True
    poisson_only_mode: bool = True  # deprecated alias — uvek DC engine

    # Dixon-Coles kalibracija
    dc_params_file: str = "dc_params.json"
    dc_calibration_lookback_days: int = 365
    dc_calibration_max_age_days: int = 14
    dc_time_decay_xi: float = 0.0018

    # Context gates (fatigue, market, lineup) — API-Football only
    context_gates_enabled: bool = True
    fatigue_gate_enabled: bool = True
    market_confirmation_gate_enabled: bool = False
    lineup_gate_enabled: bool = True
    fatigue_block_under_threshold: float = 0.20
    fatigue_block_side_threshold: float = 0.70
    fatigue_support_under_threshold: float = 0.30
    motivation_high_threshold: float = 0.62
    market_adverse_move_pct: float = 0.025
    # Pre-kickoff Telegram warning when decision odds jump this much (~T-30).
    pre_kickoff_adverse_jump_pct: float = 0.03
    market_confirm_shortening_pct: float = 0.01
    lineup_injury_block_threshold: float = 0.50
    lineup_rotation_block_threshold: float = 0.65
    lineup_window_hours: float = 2.0
    min_team_xg_threshold: float = 0.15

    walk_forward_min_train_days: int = 90
    walk_forward_test_days: int = 7

    model_dir: Path = Path("./data/models")
    feature_dir: Path = Path("./data/features")

    # Ingested odds markets (features / fair probs). Live tip selection is
    # narrower: see PickSelectionEngine.PICK_MARKETS (over_under paused).
    supported_markets: list[str] = Field(
        default=[
            "match_winner",
            "over_under",
            "btts",
        ]
    )

    # Sve praćene lige — koriste se kao fallback kada nema prioritetnih.
    league_ids: list[int] = Field(
        default=[
            # ── Prioritetne evropske (top 5 + kupi) ─────────────────────────
            39,   # Premier League (England)
            140,  # La Liga (Spain)
            135,  # Serie A (Italy)
            78,   # Bundesliga (Germany)
            61,   # Ligue 1 (France)
            3,    # UEFA Europa League
            848,  # UEFA Conference League
            # ── Ostale jake evropske ─────────────────────────────────────────
            88,   # Eredivisie (Netherlands)
            144,  # Jupiler Pro League (Belgium)
            218,  # Bundesliga (Austria)
            219,  # 2. Liga (Austria)
            94,   # Primeira Liga (Portugal)
            203,  # Süper Lig (Turkey)
            # ── UEFA (kvalifikacije uključene u isti league ID) ───────────────
            2,    # UEFA Champions League (+ Q)
            # ── Međunarodne (letnji period) ───────────────────────────────────
            1,    # FIFA World Cup
            # ── Van-Evropa (aktuelne tokom leta) ─────────────────────────────
            71,   # Brazil Serie A
            76,   # Brazil Serie D (4. liga)
            128,  # Argentina Liga Profesional
            132,  # Argentina Primera C (4. nivo)
            103,  # Norway Eliteserien
        ]
    )

    # Prioritetne lige — uvek se biraju prve ako ima mečeva.
    # Ostatak league_ids se koristi samo kada nema ni jednog prioritetnog meča.
    priority_league_ids: list[int] = Field(
        default=[
            39,   # Premier League
            140,  # La Liga
            135,  # Serie A
            78,   # Bundesliga
            61,   # Ligue 1
            3,    # UEFA Europa League (+ Q)
            848,  # UEFA Conference League (+ Q)
            2,    # UEFA Champions League (+ Q)
            88,   # Eredivisie (Netherlands)
            144,  # Jupiler Pro League (Belgium)
            218,  # Bundesliga (Austria)
            219,  # 2. Liga (Austria)
            94,   # Primeira Liga (Portugal)
            203,  # Süper Lig (Turkey)
        ]
    )

    # Crna lista liga — nikad se ne ingestuju niti generišu pickovi.
    exclude_league_ids: list[int] = Field(default=[])

    # Open fallback — max broj mečeva iz kompletne dnevne ponude
    # kada ni prioritetne ni tracked liste nemaju ništa.
    # ~80 mečeva × ~2 odds poziva = ~160 API poziva (od 7500 dnevnih).
    max_open_fixtures: int = 80

    # Isolated confidence calibrator (display/stats only — does not filter picks)
    use_calibrated_confidence: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
