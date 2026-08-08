"""Definicije A/B profila za backtest — bez importa app modula."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Bazni env za sve testove (izolovan subprocess, API-only, Poisson kao u produkciji)
BASE_ENV: dict[str, str] = {
    "LOCAL_MODE": "true",
    "USE_MEMORY_CACHE": "true",
    "APP_DEBUG": "false",
    "POISSON_ONLY_MODE": "true",
    "EXCLUDE_LEGACY_TRAINING": "true",
    "EXCLUDE_LEAGUE_IDS": "[2]",
    "DATABASE_URL": "sqlite+aiosqlite:///./data/football_roi.db",
    "DATABASE_URL_SYNC": "sqlite:///./data/football_roi.db",
}

# Referenca za A/B testove — usklađeno sa pick_selector SELECTION_QUALITY_FILTERS
BASELINE_SELECTION_FILTERS: dict[tuple[str, str], dict[str, float]] = {
    ("match_winner", "home"): {
        "min_ev_by_bucket": {"mid": 0.03, "high": 0.03},
        "min_edge_pp_by_bucket": {"mid": 3.0, "high": 4.0},
        "max_odds": 7.0,
    },
    ("match_winner", "away"): {
        "min_ev_by_bucket": {"mid": 0.03, "high": 0.03},
        "min_edge_pp_by_bucket": {"mid": 4.0, "high": 5.0},
        "max_odds": 8.0,
    },
}


def _filters_with_max_odds(home_max: float, away_max: float) -> dict[tuple[str, str], dict]:
    f = deepcopy(BASELINE_SELECTION_FILTERS)
    f[("match_winner", "home")]["max_odds"] = home_max
    f[("match_winner", "away")]["max_odds"] = away_max
    return f


# Ključ: profile_id → konfiguracija
# patches.selection_quality_filters: runtime monkeypatch u worker-u (ne menja source fajl)
AB_PROFILES: dict[str, dict[str, Any]] = {
    "baseline": {
        "label": "BASELINE",
        "description": "Trenutno stanje: .env + config.py + pick_selector filteri",
        "env": {
            "CONTEXT_GATES_ENABLED": "true",
            "FATIGUE_GATE_ENABLED": "true",
            "MARKET_CONFIRMATION_GATE_ENABLED": "false",
            "LINEUP_GATE_ENABLED": "true",
            "DECISION_HOURS_BEFORE_KICKOFF": "1.0",
        },
        "backtest": {
            "decision_hours": 1.0,
            "exclude_legacy": True,
        },
        "patches": {},
    },
    "test_a_max_odds": {
        "label": "TEST A",
        "description": "Smanjene max kvote Home 3.0 / Away 3.2 (runtime patch)",
        "env": {
            "CONTEXT_GATES_ENABLED": "true",
            "FATIGUE_GATE_ENABLED": "true",
            "MARKET_CONFIRMATION_GATE_ENABLED": "false",
            "LINEUP_GATE_ENABLED": "true",
            "DECISION_HOURS_BEFORE_KICKOFF": "1.0",
        },
        "backtest": {
            "decision_hours": 1.0,
            "exclude_legacy": True,
        },
        "patches": {
            "selection_quality_filters": _filters_with_max_odds(3.0, 3.2),
        },
    },
    "test_b_decision_3h": {
        "label": "TEST B (3h)",
        "description": "Decision time 3h pre kickoff-a",
        "env": {
            "CONTEXT_GATES_ENABLED": "true",
            "FATIGUE_GATE_ENABLED": "true",
            "MARKET_CONFIRMATION_GATE_ENABLED": "false",
            "LINEUP_GATE_ENABLED": "true",
            "DECISION_HOURS_BEFORE_KICKOFF": "3.0",
        },
        "backtest": {
            "decision_hours": 3.0,
            "exclude_legacy": True,
        },
        "patches": {},
    },
    "test_b_decision_5h": {
        "label": "TEST B (5h)",
        "description": "Decision time 5h pre kickoff-a",
        "env": {
            "CONTEXT_GATES_ENABLED": "true",
            "FATIGUE_GATE_ENABLED": "true",
            "MARKET_CONFIRMATION_GATE_ENABLED": "false",
            "LINEUP_GATE_ENABLED": "true",
            "DECISION_HOURS_BEFORE_KICKOFF": "5.0",
        },
        "backtest": {
            "decision_hours": 5.0,
            "exclude_legacy": True,
        },
        "patches": {},
    },
    "test_c_no_market_gate": {
        "label": "TEST C",
        "description": "Context gates ON, market confirmation OFF",
        "env": {
            "CONTEXT_GATES_ENABLED": "true",
            "FATIGUE_GATE_ENABLED": "true",
            "MARKET_CONFIRMATION_GATE_ENABLED": "false",
            "LINEUP_GATE_ENABLED": "true",
            "DECISION_HOURS_BEFORE_KICKOFF": "1.0",
        },
        "backtest": {
            "decision_hours": 1.0,
            "exclude_legacy": True,
        },
        "patches": {},
    },
    "test_c_no_context_gates": {
        "label": "TEST C (all off)",
        "description": "Svi context gates isključeni",
        "env": {
            "CONTEXT_GATES_ENABLED": "false",
            "DECISION_HOURS_BEFORE_KICKOFF": "1.0",
        },
        "backtest": {
            "decision_hours": 1.0,
            "exclude_legacy": True,
        },
        "patches": {},
    },
    # TEST D — popuni posle što vidiš najbolje iz A/B/C (placeholder kombinacija)
    "test_d_combo": {
        "label": "TEST D",
        "description": "Kombinacija: max_odds 3.0/3.2 + decision 3h + market gate OFF",
        "env": {
            "CONTEXT_GATES_ENABLED": "true",
            "FATIGUE_GATE_ENABLED": "true",
            "MARKET_CONFIRMATION_GATE_ENABLED": "false",
            "LINEUP_GATE_ENABLED": "true",
            "DECISION_HOURS_BEFORE_KICKOFF": "3.0",
        },
        "backtest": {
            "decision_hours": 3.0,
            "exclude_legacy": True,
        },
        "patches": {
            "selection_quality_filters": _filters_with_max_odds(3.0, 3.2),
        },
    },
}

DEFAULT_PROFILE_ORDER = [
    "baseline",
    "test_a_max_odds",
    "test_b_decision_3h",
    "test_b_decision_5h",
    "test_c_no_market_gate",
    "test_c_no_context_gates",
    "test_d_combo",
]

# A/B: minimalna kvota + opcioni globalni EV prag (runtime patch u worker-u)
_ODDS_FLOOR_ENV = {
    "CONTEXT_GATES_ENABLED": "true",
    "FATIGUE_GATE_ENABLED": "true",
    "MARKET_CONFIRMATION_GATE_ENABLED": "false",
    "LINEUP_GATE_ENABLED": "true",
    "DECISION_HOURS_BEFORE_KICKOFF": "1.0",
}

AB_PROFILES.update({
    "min_odds_2_00": {
        "label": "MIN ODDS 2.00",
        "description": "Globalni floor kvote 2.00 (sve selekcije)",
        "env": dict(_ODDS_FLOOR_ENV),
        "backtest": {"decision_hours": 1.0, "exclude_legacy": True},
        "patches": {"global_min_odds": 2.0},
    },
    "min_odds_2_20": {
        "label": "MIN ODDS 2.20",
        "description": "Globalni floor kvote 2.20 (sve selekcije)",
        "env": dict(_ODDS_FLOOR_ENV),
        "backtest": {"decision_hours": 1.0, "exclude_legacy": True},
        "patches": {"global_min_odds": 2.2},
    },
    "min_odds_2_00_ev5": {
        "label": "MIN ODDS 2.00 + EV 5%",
        "description": "Floor kvote 2.00 + globalni min EV 5%",
        "env": dict(_ODDS_FLOOR_ENV),
        "backtest": {"decision_hours": 1.0, "exclude_legacy": True},
        "patches": {"global_min_odds": 2.0, "global_min_ev": 0.05},
    },
    "min_odds_2_20_ev5": {
        "label": "MIN ODDS 2.20 + EV 5%",
        "description": "Floor kvote 2.20 + globalni min EV 5%",
        "env": dict(_ODDS_FLOOR_ENV),
        "backtest": {"decision_hours": 1.0, "exclude_legacy": True},
        "patches": {"global_min_odds": 2.2, "global_min_ev": 0.05},
    },
})

ODDS_FLOOR_PROFILE_ORDER = [
    "baseline",
    "min_odds_2_00",
    "min_odds_2_20",
    "min_odds_2_00_ev5",
    "min_odds_2_20_ev5",
]
