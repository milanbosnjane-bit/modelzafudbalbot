# Football ROI Prediction Bot

Produkcijski Python sistem za fudbalsko klađenje optimizovan za **dugoročni ROI** i **Closing Line Value (CLV)** — ne za win rate.

Generiše do **6 dnevnih pickova** sa **Dixon-Coles (DC)** signalom, context gate filterima (umornost, sastav), izolovanom **Confidence Calibrator** kalibracijom za prikaz, strogu temporalnu disciplinu (nema lookahead-a) i interaktivni Telegram meni na srpskom.

**Arhitektura v3.1:** čista **Dixon-Coles** verovatnoća za izbor tipova — **bez ML ensemble-a u live pipeline-u**. Parametri (`xg_scale`, `home_advantage`, `rho` po ligi) uče se MLE kalibracijom iz istorijskih FT mečeva. Pored toga postoji **odvojeni Confidence Calibrator** (logistic regression) koji ne menja DC verovatnoće, EV, ranking ni filtere — koristi se samo za Telegram prikaz i statistiku.

Bot je dizajniran isključivo za **kvote ≥ 2.0** (edge-first selekcija).

---

## Sadržaj

1. [Šta bot radi](#1-šta-bot-radi)
2. [Cilj i metrike](#2-cilj-i-metrike)
3. [Arhitektura](#3-arhitektura)
4. [Struktura projekta](#4-struktura-projekta)
5. [Pipeline (Phase 1 / Phase 2)](#5-pipeline-phase-1--phase-2)
6. [Dixon-Coles model i kalibracija](#6-dixon-coles-model-i-kalibracija)
7. [Confidence Calibrator (izolovan sloj)](#7-confidence-calibrator-izolovan-sloj)
8. [Context gates (umornost, sastav)](#8-context-gates-umornost-sastav)
9. [Probability layer i EV](#9-probability-layer-i-ev)
10. [Pick selection](#10-pick-selection)
11. [Feature engineering](#11-feature-engineering)
12. [Data ingestion i praćene lige](#12-data-ingestion-i-praćene-lige)
13. [Legacy podaci i backtest](#13-legacy-podaci-i-backtest)
14. [A/B testiranje profila](#14-ab-testiranje-profila)
15. [ROI lifecycle i statistika](#15-roi-lifecycle-i-statistika)
16. [Kalibracija i re-kalibracija](#16-kalibracija-i-re-kalibracija)
17. [Scheduler (automatski poslovi)](#17-scheduler-automatski-poslovi)
18. [Baza podataka](#18-baza-podataka)
19. [REST API](#19-rest-api)
20. [Telegram](#20-telegram)
21. [Konfiguracija](#21-konfiguracija)
22. [Pokretanje (Windows)](#22-pokretanje-windows)
23. [Produkcija na Linux serveru](#23-produkcija-na-linux-serveru)
24. [Testovi](#24-testovi)
25. [Operativni checklist](#25-operativni-checklist)
26. [Troubleshooting](#26-troubleshooting)
27. [Poznata ograničenja](#27-poznata-ograničenja)

---

## 1. Šta bot radi

Bot je **signal generator + ROI tracker** — ne kladionica bot i ne postavlja opklade automatski.

| Komponenta | Uloga |
|------------|-------|
| **Ingestion** | Povlači mečeve, kvote, povrede, sastave i statistike iz API-Football |
| **Feature Engineer** | Računa xG, formu, umor, motivaciju, market signale (point-in-time) |
| **Dixon-Coles** | Računa verovatnoće za 1X2, O/U 2.5, BTTS Yes |
| **Context gates** | Blokira tipove na osnovu umora, sastava i povreda |
| **Pick selector** | Bira do 6 tipova sa pozitivnim EV, diversity pravilima i quality filterima |
| **Confidence Calibrator** | Kalibriše prikaz pouzdanosti (ne utiče na izbor tipova) |
| **Paper trading** | Automatski prati win/lose, profit, CLV i ROI |
| **Telegram** | Šalje dnevne tipove + interaktivni meni (statistika, settle, status) |
| **Scheduler** | Automatski ingest (07:00), pickovi (08:00), settle (2h), kvote (30 min) |

### Produkciona pravila (A/B 2026 + operativne izmene)

| Pravilo | Vrednost | Razlog |
|---------|----------|--------|
| `MARKET_CONFIRMATION_GATE_ENABLED` | **false** | A/B: gate smanjivao ROI |
| `MAX_OPEN_FIXTURES` | **0** (isključen od 2026-08-07) | Open fallback lige: −43% ROI od 28.7. |
| `USE_CALIBRATED_CONFIDENCE` | **true** (lokalno) | Realniji prikaz pouzdanosti u Telegramu |
| Min kvota | **≥ 2.0** | Edge-first strategija |
| Live marketi | 1X2, O/U 2.5, BTTS Yes | BTTS No blokiran |

### Korisnički workflow

1. Scheduler u 07:00 srpsko povlači mečeve iz **praćenih liga**
2. U 08:00 srpsko generiše tipove i šalje Telegram push
3. **Ti** biraš šta i kad igraš u kladionici
4. Svi bot tipovi automatski ulaze u **ROI statistiku** (@ bot kvota)
5. Posle meča: scheduler ili **SETTLE NOW** → win/lose, profit, CLV
6. **ROI STATISTIKA** / **POSLEDNJI REZULTATI** za pregled performansi

---

## 2. Cilj i metrike

### Primarni cilj

Maksimizovati **očekivani povrat (EV)** po opkladi, ne procenat pogodaka.

```
EV = (P × odds) - 1
CLV = (bet_odds / closing_odds) - 1   (ili fair-prob varijanta)
ROI% = (sum(profit_units) / sum(stake_units)) × 100
```

### Šta sistem NE optimizuje

- Win rate kao primarni KPI
- Maksimalan broj opklada po danu (cap je 6, ali manje je validno)
- „Sigurne" niske kvote bez edge-a
- Exact Score, HT/FT i visoko-varijantne markete
- Automatsko klađenje u kladionici

### Ključne garancije dizajna

| Garancija | Implementacija |
|-----------|----------------|
| Nema fallback verovatnoća (0.5) | `probability_layer.py` — `None` umesto defaulta |
| EV nije clamp-ovan | `compute_ev()` vraća sirovi EV |
| Point-in-time features | `decision_time = kickoff - 1h`; odds `captured_at <= as_of` |
| Dixon-Coles live | `ProbabilityEngine` — analitička DC matrica |
| Min kvota | `GLOBAL_MIN_ODDS=2.0` |
| Blok xG bez podataka | `min_team_xg_threshold=0.15` |
| Context gates pre picka | fatigue + lineup (market gate OFF) |
| Samo praćene lige | `LEAGUE_IDS` + `MAX_OPEN_FIXTURES=0` |
| DC netaknut od kalibratora | Confidence Calibrator samo display layer |
| Temporal train/test split | hronološki split + embargo days |
| Bez duplikata istog dana | `get_fixture_ids_picked_today()` + skip u `persist_picks()` |
| LIVE lista samo pre kickoff-a | `is_fixture_pre_kickoff()` |

---

## 3. Arhitektura

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL DATA SOURCES                            │
│   API-Football (fixtures, odds, stats, injuries, lineups)                │
│   Odds API (opciono) · OpenWeather (opciono)                             │
└────────────────────────────┬─────────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  INGESTION LAYER          app/services/ingestion.py                      │
│  Korak 2a: priority_league_ids → 2b: league_ids → 2c: OFF (max=0)       │
│  Korak 3: per-league fallback samo unutar league_ids (prazan dan)       │
└────────────────────────────┬─────────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PERSISTENCE              SQLite (local/server) / PostgreSQL (Docker)    │
│  fixtures · odds_snapshots · feature_vectors · daily_picks               │
└────────────────────────────┬─────────────────────────────────────────────┘
                             ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  FEATURE ENGINEER         app/features/engineer.py                       │
│  xG · form · fatigue · motivation · injuries · market movement         │
└────────────────────────────┬─────────────────────────────────────────────┘
                             ▼
            ┌─────────────────────────────────┐
            │ DixonColesModel (DC matrica)      │  ← live default
            │ MLE kalibracija (dc_params.json)  │
            └────────────────┬────────────────┘
                             ▼
            ┌─────────────────────────────────┐
            │ Context gates                     │
            │ fatigue · lineup (market OFF)     │
            └────────────────┬────────────────┘
                             ▼
            ┌─────────────────────────────────┐
            │ PickSelectionEngine               │
            │ EV >= 0 · diversity · quality     │
            └────────────────┬────────────────┘
                             ▼
            ┌─────────────────────────────────┐
            │ Confidence Calibrator (display)   │  ← NE menja pickove
            │ logistic regression · joblib      │
            └────────────────┬────────────────┘
                             ▼
       ┌─────────────┬─────────────────┬──────────────────────┐
       ▼             ▼                 ▼                      ▼
  Telegram push   daily_picks (DB)   settle → CLV        Interactive meni
  (format_pick)   prediction logs    edge capture        ROI / PENDING / STATUS
```

### Runtime komponente

| Proces | Windows | Linux server | Uloga |
|--------|---------|--------------|-------|
| Pipeline + push | `startbot.bat` | `startup_ingest.sh` (oneshot) | Ingest + pickovi + Telegram |
| Telegram meni | `telegram.bat` | `football-dc-telegram.service` | Polling, dugmad, `/start` |
| Scheduler | `scheduler.bat` | `football-dc-scheduler.service` | Ingest, kvote, settle, 08:00 picks |
| FastAPI (opciono) | `uvicorn app.main:app` | — | REST API |

---

## 4. Struktura projekta

```
modelzafudbalbot/
├── app/
│   ├── main.py                         # FastAPI entry
│   ├── run_local.py                    # Windows one-click runner
│   ├── calibrate_models.py             # CLI: MLE kalibracija DC parametara
│   ├── train_models.py                 # Deprecated alias → calibrate_models
│   ├── config.py                       # Pydantic settings (.env)
│   ├── api/routes.py                   # REST endpoints
│   ├── database/
│   │   ├── models.py                   # SQLAlchemy ORM (+ ConfidencePredictionLog)
│   │   └── session.py                  # async + sync + SQLite migracije
│   ├── model/                          # Izolovan confidence sloj (NE dira DC)
│   │   ├── confidence_calibrator.py    # Logistic regression pipeline
│   │   ├── confidence_context.py       # CalibratorInput builder
│   │   ├── confidence_service.py       # apply + log snapshots
│   │   └── confidence_training_data.py # Legacy dataset adapter
│   ├── services/
│   │   ├── ingestion.py                # API-Football ingest (3-step prioritet)
│   │   ├── scheduler.py                # APScheduler jobs
│   │   ├── paper_trading.py            # settle + evaluate (auto ROI)
│   │   ├── clv_tracker.py              # closing line value
│   │   ├── retrain_manager.py          # auto-retrain triggers
│   │   ├── api_football.py             # API-Football klijent
│   │   └── manual_betting.py           # legacy modul
│   ├── features/engineer.py            # feature batch build/load
│   ├── models/
│   │   └── dixon_coles_model.py        # LIVE model
│   ├── predictions/
│   │   ├── pipeline.py                 # daily orchestration
│   │   ├── ensemble.py                 # ProbabilityEngine wrapper (DC only)
│   │   ├── pick_selector.py            # selection + persist
│   │   ├── context_gates.py            # fatigue / market / lineup gates
│   │   ├── probability_layer.py
│   │   ├── market_selection.py
│   │   ├── regime.py
│   │   └── staking.py
│   ├── training/
│   │   ├── dc_calibrator.py            # MLE Dixon-Coles kalibracija
│   │   ├── backtest.py
│   │   └── ...
│   ├── telegram/
│   │   ├── bot.py                      # HTTP send + format_pick
│   │   ├── formatting.py               # SR betting UI
│   │   ├── interactive_bot.py          # Polling + dugmad
│   │   ├── keyboard.py                 # Reply keyboard
│   │   ├── stats_service.py            # ROI, PENDING PICKS, status
│   │   ├── pick_output.py              # top-6 EV, dedupe, rank
│   │   ├── pick_status.py              # PENDING / pre-kickoff filter
│   │   └── run_bot.py
│   ├── utils/
│   │   ├── feature_values.py           # xG validacija
│   │   ├── clv_metrics.py
│   │   ├── model_paths.py
│   │   └── ...
│   └── tests/                          # pytest (236 testova)
├── data/
│   ├── models/
│   │   ├── dc_params.json              # MLE Dixon-Coles parametri
│   │   ├── confidence_calibrator.joblib
│   │   └── confidence_calibrator_meta.json
│   ├── features/                       # drift baseline
│   ├── confidence_training/            # merged training dataset
│   │   ├── history_merged.csv
│   │   └── training_report.txt
│   └── football_roi.db                 # SQLite (local/server mode)
├── scripts/
│   ├── train_confidence_calibrator.py  # Trening confidence kalibratora
│   ├── build_confidence_training_dataset.py
│   ├── audit_botposlednji1_readonly.py # READ-ONLY legacy audit
│   ├── run_backtest.py
│   ├── run_automated_tests.py          # A/B backtest orchestrator
│   ├── deploy_to_server.py             # Deploy na Linux server
│   ├── server/                         # systemd + shell skripte
│   │   ├── install_systemd.sh
│   │   ├── scheduler.sh
│   │   ├── telegram.sh
│   │   ├── startup_ingest.sh
│   │   └── restart_bot.sh
│   └── ...
├── startbot.bat                        # glavni Windows launcher (CRLF!)
├── telegram.bat
├── scheduler.bat
├── stopbot.bat
├── docker-compose.yml
├── requirements.txt
├── requirements_server.txt
├── .env.example
└── README.md
```

---

## 5. Pipeline (Phase 1 / Phase 2)

### Modovi (`PipelineMode`)

| Mod | CLI | Ponašanje |
|-----|-----|-----------|
| `LIVE` | `python -m app.run_local --live` | Učitava keš iz DB; **bez** API ingest-a |
| `FULL_BUILD` | `python -m app.run_local --full-build` | Ingest + context refresh + features + picks |

`startbot.bat` koristi **`--full-build`**. Scheduler u 08:00 koristi **`LIVE`** (podaci već ingestovani u 07:00).

### Tok izvršavanja

```
run_daily_detailed()
  └─ for date in [today .. today+7]:
       └─ _run_for_date()
            ├─ [FULL_BUILD] ingestion.full_daily_ingest()
            ├─ load NS fixtures + decision_time map
            ├─ skip fixtures already picked today
            ├─ _refresh_context_data()          # injuries + lineups (≤8h)
            ├─ [FULL_BUILD] engineer.build_batch(persist=True)
            ├─ [LIVE]       engineer.load_batch()
            ├─ generate_candidates()
            │    ├─ preflight (xG >= min threshold)
            │    ├─ Dixon-Coles predict
            │    ├─ selection quality filters
            │    └─ context gates (fatigue/lineup; market OFF)
            ├─ validate_candidate_ev_distribution()
            ├─ select_candidates()              # samo EV >= 0
            ├─ select_top_picks()
            ├─ persist_picks()                # daily_picks + predictions
            └─ enrich_persisted_picks()         # confidence calibrator (display)
```

### Decision time

```python
as_of = fixture_date - DECISION_HOURS_BEFORE_KICKOFF  # default 1.0h
```

Svi feature-i i odds snapshot-i moraju biti `<= as_of` (point-in-time).

### Guard: korumpirani EV

`PipelineDataCorruptionError` ako su svi kandidati identični EV ili legacy clamp ±50%.

---

## 6. Dixon-Coles model i kalibracija

### Live model (`DixonColesModel`)

- Analitička 11×11 score matrica sa **τ korekcijom** za niske rezultate (0-0, 0-1, 1-0, 1-1)
- λ iz venue-adjusted xG + injury korekcija
- Globalni `xg_scale` i `home_advantage` + **ρ po ligi**
- Parametri iz `data/models/dc_params.json` (MLE kalibracija)

| Aspekt | Ponašanje |
|--------|-----------|
| Live pipeline | `ProbabilityEngine` — samo Dixon-Coles |
| Warmup | Učitava `dc_params.json` (fallback: default parametri) |
| Telegram header | `FOOTBALL PICKS \| datum \| Dixon-Coles` |
| Min kvota | **≥ 2.0** — hard filter u `pick_selector.py` |
| Default lambda | λ=1.0/1.0 kad nema dovoljno FT istorije — **slab signal** |

### MLE kalibracija (`DixonColesCalibrator`)

Fajl: `app/training/dc_calibrator.py`

1. Učitava FT mečeve iz SQLite/PostgreSQL (`fixtures`)
2. Point-in-time feature-i na **T−1h** (`FeatureEngineer`, `decision_time`)
3. Maksimizuje log-likelihood stvarnih rezultata preko DC matrice
4. Time decay: `weight = exp(-ξ × days)` (`dc_time_decay_xi`)
5. Snima u `data/models/dc_params.json`

```powershell
python -m app.calibrate_models
python -m app.calibrate_models --if-missing
python -m app.calibrate_models --lookback-days 180
```

**Minimum uzoraka:** 50 globalno, 30 po ligi za ρ kalibraciju.

### Probability engine

```
model_prob = DC_matrix_prob(selection, λ_h, λ_a, ρ_league)
calibrated = shrink(model_prob, fair_implied, PROBABILITY_SHRINK_WEIGHT)
EV         = (calibrated × odds) - 1
confidence = edge-driven formula (caps ~95%) — koristi se za filtere
reasoning  = _build_dc_reasoning() + context gate notes
```

ML modeli (LightGBM/XGB/Neural) su **uklonjeni iz live pipeline-a**. Legacy fajlovi mogu ostati u repou za arhivu.

---

## 7. Confidence Calibrator (izolovan sloj)

**Ključno:** Confidence Calibrator je potpuno odvojen od Dixon-Coles modela. Ne menja:

- DC verovatnoće
- EV, edge, ranking
- Pick selector filtere
- Uloge, ROI, CLV

Koristi se **samo** za Telegram prikaz i statistiku kada je `USE_CALIBRATED_CONFIDENCE=true`.

### Problem koji rešava

Stari CONF formula skoro direktno prati edge prema tržištu — veliki edge + visoka kvota automatski guraju CONF na 94–95%, iako stvarna verovatnoća pogotka nije tolika. Posebno problematično kod mečeva sa **default lambda** (λ=1.0/1.0).

### Arhitektura

```
Dixon-Coles raw probability (netaknut)
        ↓
Confidence Calibrator (logistic regression + StandardScaler)
        ↓
calibrated_confidence  →  Telegram prikaz / statistika
calibrated_ev          →  display-only EV
```

### Ulazni feature-i (pre kickoff-a)

| Feature | Opis |
|---------|------|
| `dixon_coles_probability` | Raw DC verovatnoća |
| `market_fair_probability` | Fair implied iz kvota |
| `edge`, `raw_ev`, `odds` | Market metrike |
| `market`, `selection`, `league_id` | Kontekst |
| `home_ft_count`, `away_ft_count` | Broj FT mečeva u bazi |
| `used_default_lambda` | Da li je DC koristio λ=1.0/1.0 |
| `home_lambda`, `away_lambda` | Iz reasoning JSON-a |
| `feature_quality` | Potpunost feature podataka |
| `hours_to_kickoff` | Vreme do početka |

**Target za trening:** WIN=1, LOSE=0 (VOID/PENDING isključeni).

### Fajlovi

| Fajl | Uloga |
|------|-------|
| `app/model/confidence_calibrator.py` | Model, metrike, save/load |
| `app/model/confidence_context.py` | `build_calibrator_input()` |
| `app/model/confidence_service.py` | `apply_calibration_to_pick()`, logging |
| `scripts/train_confidence_calibrator.py` | CLI trening |
| `scripts/build_confidence_training_dataset.py` | Merge current + legacy dataset |
| `data/models/confidence_calibrator.joblib` | Sačuvan model |
| `data/models/confidence_calibrator_meta.json` | Meta (train/val period, metrike) |

### Trening

Hronološka podela (stariji → train, noviji ~25% → validation). Nema random split-a.

```powershell
# 1. Build merged dataset (READ-ONLY iz botposlednji1)
python scripts/build_confidence_training_dataset.py

# 2. Treniraj kalibrator
python scripts/train_confidence_calibrator.py
```

**Referentni rezultati** (123 validna pre-match primera, validation n=31):

| Metrika | Stari CONF | DC raw prob | Kalibrisani CONF |
|---------|------------|-------------|------------------|
| Brier | 0.397 | 0.299 | **0.294** |
| Log loss | 1.281 | 0.805 | **0.806** |
| ECE | 0.437 | 0.264 | **0.258** |
| EV>35% Brier | 0.733 | 0.157 | **0.153** |

Za EV>35%: stari CONF predviđa ~94% winrate, stvarni ~17%; kalibrator ~10%.

### Ponašanje kad model nije spreman

- Bot nastavlja normalno
- Dixon-Coles ostaje aktivan
- Telegram prikazuje „nije kalibrisan"
- Nema rušenja pipeline-a

### Feature flag

```env
USE_CALIBRATED_CONFIDENCE=true   # Telegram prikaz kalibrisanog CONF-a
USE_CALIBRATED_CONFIDENCE=false  # vraća stari DC/Fair + CONF prikaz
```

---

## 8. Context gates (umornost, sastav)

Fajl: `app/predictions/context_gates.py`

Tri filtera se primenjuju **posle** DC validacije, **pre** ulaska u finalni pool.

> **Napomena (A/B 2026):** Market Confirmation Gate je **isključen u produkciji** (`MARKET_CONFIRMATION_GATE_ENABLED=false`).

### Gate 1 — Fatigue & motivation

| Pravilo | Akcija |
|---------|--------|
| Home/Away pick + picked side fatigue ≥ 70% | **BLOK** |
| Under/Draw + oba tima sveži + visoka motivacija | **BLOK** |
| Prosečan umor ≥ 30% | napomena u analizi |

### Gate 2 — Market confirmation (**isključen u live**)

Koristi `opening_odds` vs trenutnu kvotu. Default OFF.

### Gate 3 — Lineup & injury

| Pravilo | Akcija |
|---------|--------|
| Home/Away + injury ≥ 50% na picked strani | **BLOK** |
| Home/Away + rotation ≥ 65% | **BLOK** |
| ≤ 2h do meča, nema lineup podataka | **BLOK** (1X2) |

### Isključivanje

```env
CONTEXT_GATES_ENABLED=false
FATIGUE_GATE_ENABLED=false
MARKET_CONFIRMATION_GATE_ENABLED=false
LINEUP_GATE_ENABLED=false
```

---

## 9. Probability layer i EV

Fajl: `app/predictions/probability_layer.py`

| Funkcija | Opis |
|----------|------|
| `compute_ev(p, odds)` | `(p × odds) - 1`, bez clamp-a |
| `is_valid_probability(p)` | `0.05 ≤ p ≤ 0.95` |
| `is_legacy_clamped_ev()` | Detekcija ±0.50 fallback EV |
| `is_disabled_market()` | Blokira exact score, HT/FT |

Blokirani marketi: `exact_score`, `correct_score`, `ht_ft`, `half_time_full_time`, `final_score`.

BTTS **No** je globalno blokiran u live pipeline-u.

---

## 10. Pick selection

Fajl: `app/predictions/pick_selector.py`

### Live marketi

```python
PICK_MARKETS = {"match_winner", "over_under", "btts"}
```

Live O/U: samo **linija 2.5**.

### EV selection

```
1. Kandidati sa EV >= 0.0
2. Diversity: max 1 po meču, max 2 Draw
3. Ako nema pozitivnog EV → 0 pickova (nema hard fallback-a)
```

### xG preflight

Oba tima moraju imati xG ≥ `MIN_TEAM_XG_THRESHOLD` (default 0.15). Inače: `insufficient_xg`.

### Per-selekcijski filter

**Home/Away** (`SELECTION_QUALITY_FILTERS`) — bucket pragovi po kvoti:

| Selekcija | Bucket | min EV | min edge (pp) | max odds |
|-----------|--------|--------|---------------|----------|
| Home | mid (2.0–3.0) | 3% | 3.0 | 7.0 |
| Home | high (>3.0) | 3% | 4.0 | 7.0 |
| Away | mid (2.0–3.0) | 3% | 4.0 | 8.0 |
| Away | high (>3.0) | 3% | 5.0 | 8.0 |

**Draw i Under 2.5:** min EV 3%, min edge 3pp.

### Globalni floor

```python
GLOBAL_MIN_ODDS = 2.0
MIN_PICK_STAKE_UNITS = 1.0
MAX_DAILY_PICKS = 6
```

### Duplikat zaštita

Isti `fixture_id` ne može biti pickovan dva puta istog UTC dana.

---

## 11. Feature engineering

Fajl: `app/features/engineer.py`

### Ključni feature-i (po timu, prefix `home_` / `away_`)

| Feature | Opis |
|---------|------|
| `weighted_xG_last5`, `venue_adjusted_xg` | xG signal za DC |
| `weighted_xGA`, `defensive_pressure_index` | defanziva |
| `fatigue_score` | umor |
| `motivation_score` | forma u sezoni |
| `injury_impact_score` | povrede/suspenzije |
| `rotation_score` | rotacija sastava |
| `rolling_form`, `momentum_score` | forma |

### Market feature-i

- `market_overround_1x2`
- `odds_change_pct_home`, `sharp_money_signal_home`
- fair probs iz odds snapshot-a

Feature-i se persistiraju u `feature_vectors` sa `as_of_datetime`.

---

## 12. Data ingestion i praćene lige

Fajl: `app/services/ingestion.py`

### Prioritet ingest-a (3 koraka + fallback)

```
Korak 1: GET /fixtures?date=...  →  sve utakmice dana (1 API poziv)

Korak 2a: priority_league_ids  →  top evropske lige (ako ima mečeva)
Korak 2b: league_ids           →  ostale praćene lige
Korak 2c: open fallback        →  ISKLJUČEN (MAX_OPEN_FIXTURES=0)

Korak 3: per-league+season      →  samo ako je ceo dan prazan (0 mečeva)
                                   i SAMO unutar league_ids
```

> **Zašto je open fallback isključen (2026-08-07):** Mečevi van `LEAGUE_IDS` liste su generisali **−43% ROI** (−14.53u) od 28.7. uzrok: nedostatak FT istorije → default λ=1.0/1.0 → lažno visok EV i CONF 95%. Praćene lige su u istom periodu ostale profitabilne (+17.6% ROI).

### Praćene lige (`LEAGUE_IDS`)

Trenutna produkciona lista (19 liga):

| ID | Liga | Region |
|----|------|--------|
| 39 | Premier League | Engleska |
| 140 | La Liga | Španija |
| 135 | Serie A | Italija |
| 78 | Bundesliga | Nemačka |
| 61 | Ligue 1 | Francuska |
| 3 | UEFA Europa League | Evropa |
| 848 | UEFA Conference League | Evropa |
| 2 | UEFA Champions League | Evropa |
| 88 | Eredivisie | Holandija |
| 144 | Jupiler Pro League | Belgija |
| 94 | Primeira Liga | Portugal |
| 218 | Bundesliga | Austrija |
| 219 | 2. Liga | Austrija |
| 1 | FIFA World Cup | Međunarodno |
| 71 | Serie A | Brazil |
| 76 | Serie D | Brazil |
| 128 | Liga Profesional | Argentina |
| 132 | Primera C | Argentina |
| 103 | Eliteserien | Norveška |

Prioritetne lige (`priority_league_ids`) se biraju prve kad postoje mečevi tog dana.

### Crna lista liga (`exclude_league_ids`)

Lige sa negativnim ROI u A/B testovima mogu se dodati:

```env
EXCLUDE_LEAGUE_IDS=[2]   # primer: isključi UCL
```

Trenutno u `.env`: `EXCLUDE_LEAGUE_IDS=[]` (UCL aktivna).

Filtriranje na 4 nivoa: ingestion → pipeline load → pick selector → backtest.

### `full_daily_ingest(date)`

1. Fixtures za datum (po prioritetnoj logici)
2. Odds po fixture-u
3. Injuries
4. Match stats (FT)
5. Standings

Lineups se povlače u context refresh (pipeline/scheduler), ne u svakom full ingest-u.

### Odds snapshot semantika

- `opening_odds`, `current_odds`, `closing_odds`
- `fair_prob` (proportional de-vig)
- `captured_at`, `is_closing`, `odds_change_pct`
- Live pickovi koriste **median** kvota na decision time

---

## 13. Legacy podaci i backtest

### Legacy izvori (NE koriste se za train/backtest po defaultu)

| Izvor | Bookmaker tag | Status |
|-------|---------------|--------|
| `data/history.db` | football-data | arhiva |
| `botposlednji1/` folder | legacy bot | READ-ONLY za confidence training |
| API-Football ingest | Bet365, itd. | **pouzdano** |

`exclude_legacy_training=true` (default) filtrira legacy fixtures iz DC treniranja.

Confidence Calibrator koristi merged dataset iz `data/confidence_training/history_merged.csv` (123 validna primera).

### Backtest CLI

```powershell
cd "C:\Users\Miki\Desktop\modelzafudbalbot"
$env:DATABASE_URL="sqlite+aiosqlite:///./data/football_roi.db"
$env:DATABASE_URL_SYNC="sqlite:///./data/football_roi.db"
$env:LOCAL_MODE="true"

python scripts/run_backtest.py --start 2025-01-01 --end 2025-12-31
python scripts/run_backtest.py --start 2026-01-01 --end 2026-07-12
```

---

## 14. A/B testiranje profila

| Fajl | Uloga |
|------|-------|
| `scripts/run_automated_tests.py` | Pokreće profile u izolovanom subprocess-u |
| `scripts/ab_test_profiles.py` | Definicije baseline, test_a–d |
| `scripts/analyze_backtest_errors.py` | ROI/WR po ligi, marketu, bucketu kvota |

```powershell
python scripts/run_automated_tests.py --start 2026-01-01 --end 2026-07-16
python scripts/analyze_backtest_errors.py
```

Rezultati: `data/ab_tests/<timestamp>/`

---

## 15. ROI lifecycle i statistika

### Tok podataka

```
predict → persist_picks (outcome=pending, is_paper=true)
    ↓
ingest ažurira fixtures (FT + golovi)
    ↓
paper_settle (svake 2h) → win/lose/push + profit_units
    ↓
CLV batch update + edge capture
    ↓
PaperTradingService.evaluate(all_time=True) → ROI STATISTIKA
```

**Svi bot tipovi** automatski ulaze u statistiku — nema ručnog unosa.

Profit se računa na **bot kvoti** (`pick.odds`), ne na tvojoj kladioničarskoj kvoti.

### Go-live kriterijumi

| Kriterijum | Prag |
|------------|------|
| Min opklada | 100 (idealno 300+) |
| ROI | ≥ 3% |
| Avg CLV | ≥ 1% |
| Edge capture | ≥ 0.5 |
| Period | ≥ 30 dana |

### Settle

- Scheduler: `job_paper_settle` svake **2h**
- Telegram: dugme **✅ SETTLE NOW**
- Ručno: `python scripts/manual_refresh_settle.py`

---

## 16. Kalibracija i re-kalibracija

### Dixon-Coles MLE

```powershell
python -m app.calibrate_models
python -m app.calibrate_models --if-missing
```

Minimum: **50** FT mečeva sa validnim xG feature-ima.

### Confidence Calibrator

```powershell
python scripts/build_confidence_training_dataset.py
python scripts/train_confidence_calibrator.py
```

Minimum: **40** train + **10** validation primera.

### Auto re-kalibracija (`RetrainManager`)

Triggeri: PSI drift, CLV < 0, edge capture < 0.5, ROI pad > 30%.

```bash
curl -X POST "http://localhost:8000/api/v1/retrain/evaluate?execute=true"
curl -X POST "http://localhost:8000/api/v1/calibrate"
```

---

## 17. Scheduler (automatski poslovi)

Fajl: `app/services/scheduler.py`

| Job | Trigger | Funkcija |
|-----|---------|----------|
| `ingest_fixtures` | 05:00 UTC (**07:00 srpsko**) | Phase 1 build (ingest + features) |
| `update_odds` | 30 min | odds + injuries + lineups (48h/6h window) |
| `capture_closing_odds` | 5 min | closing odds |
| `daily_predictions` | 06:00 UTC (**08:00 srpsko**) | LIVE pipeline + Telegram |
| `paper_settle` | 2h | settle + CLV + edge |

```powershell
python -m app.services.scheduler
# ili
scheduler.bat
```

---

## 18. Baza podataka

### Lokalni / server mod

```env
DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
DATABASE_URL_SYNC=sqlite:///./data/football_roi.db
LOCAL_MODE=true
```

### Docker (PostgreSQL)

```env
DATABASE_URL=postgresql+asyncpg://football:football@postgres:5432/football_roi
DATABASE_URL_SYNC=postgresql://football:football@postgres:5432/football_roi
```

### Ključne tabele

| Tabela | Svrha |
|--------|-------|
| `fixtures` | Mečevi, status, golovi |
| `odds_snapshots` | Opening/current/closing, fair_prob |
| `feature_vectors` | Point-in-time feature JSON |
| `daily_picks` | Tipovi + outcome + CLV + calibrated_confidence |
| `confidence_prediction_logs` | Pre-match snapshot za confidence training |
| `lineups` | Sastavi + rotation_count |
| `injuries` | Povrede po meču |
| `predictions` | Ensemble audit |

### `daily_picks` — kritična polja

```
outcome: pending | win | lose | push
odds, probability, expected_value, confidence, roi_score
calibrated_confidence, calibrated_ev   (display-only, od kalibratora)
profit_units, clv, edge_capture
is_paper: bool (default true)
reasoning: JSON list (DC + context gate notes)
```

---

## 19. REST API

Base: `http://localhost:8000/api/v1` · Swagger: `/docs`

| Method | Endpoint | Opis |
|--------|----------|------|
| GET | `/health` | Health check |
| POST | `/ingest` | Manual ingest |
| POST | `/predict` | Generiši pickove |
| GET | `/picks/today` | Današnji pickovi |
| POST | `/calibrate` | MLE kalibracija DC parametara |
| POST | `/backtest` | Walk-forward backtest |
| GET | `/clv/summary` | CLV agregat |
| POST | `/paper/settle` | Ručno settle |
| GET | `/paper/evaluate` | ROI evaluacija |
| POST | `/telegram/send` | Pošalji pickove |
| GET | `/drift/status` | Feature drift |
| POST | `/retrain/evaluate` | Retrain evaluacija |
| GET | `/config` | Aktivna konfiguracija |

---

## 20. Telegram

### Dva kanala

| Kanal | Fajl | Svrha |
|-------|------|-------|
| Push | `bot.py` | Automatski pickovi posle pipeline-a |
| Interaktivni meni | `interactive_bot.py` + `telegram.bat` | Dugmad, statistika |

### Glavni meni

| Dugme | Funkcija |
|-------|----------|
| 📊 ROI STATISTIKA | Profit, ROI%, winrate, CLV + pending lista |
| 📈 LIVE PICKS | **Samo mečevi pre kickoff-a** |
| 📉 POSLEDNJI REZULTATI | Poslednjih 10 win/lose/push |
| ✅ SETTLE NOW | Ručno settle |
| 🔄 RESTART RUN | Pokreće `startbot.bat` |
| ⚙️ STATUS BOTA | Dixon-Coles, API, uptime |

### Format pick poruke

**Sa kalibracijom** (`USE_CALIBRATED_CONFIDENCE=true`):

```
⚽ FOOTBALL PICKS | 2026-08-08 | Dixon-Coles
━━━━━━━━━━━━━━━

#1 *HOME* vs *AWAY*

🕒 POČETAK: 21:00 (srpsko vreme)

🎯 TIP: Under 2.5 golova (0–2 gola)
💰 KVOTA (bot): 2.10  (implied 48%)
📊 Model verovatnoća: 52%
🎯 Kalibrisana pouzdanost: 27%
📈 EV po modelu: +9%  |  Edge: +4.0pp
💵 PREPORUKA: 1.50u

📋 ANALIZA:
  • Dixon-Coles λ: domaćin 1.20 — gost 0.95
  • DC procena 52% vs fair 48%
  • Umor oba tima: 35%
```

**Bez kalibracije** (`USE_CALIBRATED_CONFIDENCE=false`):

```
📊 DC/Fair: 52% / 48%
📈 EV: +9%  |  Edge: +4.0pp
🔒 CONF: 55%
```

### Autorizacija

Bot odgovara samo na `TELEGRAM_CHAT_ID` iz `.env` (podržava više ID-jeva odvojenih zarezom).

---

## 21. Konfiguracija

Kopiraj `.env.example` → `.env`.

### Obavezno

```env
API_FOOTBALL_KEY=...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

### Praćene lige i ingest

```env
LEAGUE_IDS=[1,2,3,39,61,71,76,78,88,94,103,128,132,135,140,144,218,219,848]
MAX_OPEN_FIXTURES=0
EXCLUDE_LEAGUE_IDS=[]
```

### ROI i filteri

```env
MIN_EV_THRESHOLD=0.015
MIN_CONFIDENCE_THRESHOLD=0.55
MAX_DAILY_PICKS=6
KELLY_FRACTION=0.25
USE_CALIBRATED_CONFIDENCE=true
```

### Context gates

```env
CONTEXT_GATES_ENABLED=true
FATIGUE_GATE_ENABLED=true
MARKET_CONFIRMATION_GATE_ENABLED=false
LINEUP_GATE_ENABLED=true
MIN_TEAM_XG_THRESHOLD=0.15
```

### Lokalni SQLite (`startbot.bat` postavlja automatski)

```env
LOCAL_MODE=true
USE_MEMORY_CACHE=true
APP_DEBUG=false
DATABASE_URL=sqlite+aiosqlite:///./data/football_roi.db
DATABASE_URL_SYNC=sqlite:///./data/football_roi.db
MODEL_DIR=./data/models
POISSON_ONLY_MODE=true
PAPER_TRADING_ENABLED=true
```

### Kompletna env referenca

| Env | Default | Opis |
|-----|---------|------|
| `MAX_OPEN_FIXTURES` | 80 (config.py) / **0** (.env) | Open fallback cap; 0 = isključen |
| `USE_CALIBRATED_CONFIDENCE` | false | Kalibrisani prikaz u Telegramu |
| `MIN_EV_THRESHOLD` | 0.015 | EV prag u selectoru |
| `MIN_CONFIDENCE_THRESHOLD` | 0.55 | CONF prag za filtere |
| `MAX_DAILY_PICKS` | 6 | Max pickova / dan |
| `DECISION_HOURS_BEFORE_KICKOFF` | 1.0 | Decision time |
| `DC_CALIBRATION_MAX_AGE_DAYS` | 14 | Max starost dc_params.json |
| `DC_CALIBRATION_LOOKBACK_DAYS` | 365 | Lookback za MLE |
| `PROBABILITY_SHRINK_WEIGHT` | 0.35 | Shrink DC prema fair implied |
| `PAPER_TRADING_ENABLED` | true | Auto ROI tracking |
| `EXCLUDE_LEGACY_TRAINING` | true | Bez football-data u DC train |

---

## 22. Pokretanje (Windows)

### Preporučeno — jedan klik

```bat
startbot.bat
```

| Korak | Akcija |
|-------|--------|
| 1/4 | `pip install -r requirements.txt` |
| 2/4 | `calibrate_models --if-missing` (MLE DC parametri) |
| 3/4 | `run_local --full-build` → ingest + pickovi + Telegram push |
| 4/5 | `telegram.bat` (minimizovan) |
| 5/5 | `scheduler.bat` (minimizovan) |

Zaustavljanje:

```bat
stopbot.bat
```

### Manual CLI

```powershell
cd "C:\Users\Miki\Desktop\modelzafudbalbot"
$env:LOCAL_MODE="true"
$env:DATABASE_URL="sqlite+aiosqlite:///./data/football_roi.db"
$env:DATABASE_URL_SYNC="sqlite:///./data/football_roi.db"

python -m app.run_local --full-build
python -m app.run_local --live
python -m app.telegram.run_bot
python -m app.services.scheduler
uvicorn app.main:app --reload --port 8000
```

### Operativna pravila

| Pravilo | Razlog |
|---------|--------|
| Ne pokrećite `startbot.bat` više puta istog dana | Pipeline se ponavlja nepotrebno |
| Posle izmene Telegram koda restartujte `telegram.bat` | Stari polling proces |
| `.bat` fajlovi moraju biti **CRLF** | LF-only izaziva `'ho' is not recognized` |

---

## 23. Produkcija na Linux serveru

Bot se deploy-uje u poseban folder (npr. `/home/miki/football-dc-bot`) sa **systemd user servisima**.

### Instalacija

```bash
cd /home/miki/football-dc-bot
bash scripts/server/install_systemd.sh
```

Kreira tri servisa:

| Servis | Tip | Uloga |
|--------|-----|-------|
| `football-dc-startup.service` | oneshot | Startup ingest + picks posle reboota |
| `football-dc-scheduler.service` | simple | APScheduler (ingest, settle, 08:00 picks) |
| `football-dc-telegram.service` | simple | Telegram interaktivni meni |

### Upravljanje

```bash
systemctl --user status football-dc-scheduler
systemctl --user restart football-dc-scheduler
journalctl --user -u football-dc-scheduler -f
bash scripts/server/restart_bot.sh
```

### Deploy sa Windows mašine

```powershell
$env:DEPLOY_HOST="192.168.1.106"
$env:DEPLOY_USER="miki"
$env:DEPLOY_PASS="..."
python scripts/deploy_to_server.py --deploy
python scripts/deploy_to_server.py --deploy --include-env
```

Posle deploy-a restart servisa:

```bash
systemctl --user restart football-dc-scheduler football-dc-telegram
```

---

## 24. Testovi

```powershell
python -m pytest app/tests/ -v
python -m pytest app/tests/ -q --tb=short
```

**236 testova**, uključujući:

| Modul | Oblast |
|-------|--------|
| `test_confidence_calibrator` | Confidence Calibrator pipeline |
| `test_confidence_telegram` | Telegram prikaz kalibracije |
| `test_context_gates` | Fatigue, market, lineup gates |
| `test_pick_status` | Pre-kickoff filter |
| `test_feature_values` | xG validacija |
| `test_pick_selector_*` | EV ladder, diversity, validation |
| `test_selection_quality_filter` | Home/Away bucket filteri |
| `test_dixon_coles_model` | DC matrica |
| `test_probability_layer` | EV, disabled markets |
| `test_pipeline_mode` | LIVE vs FULL_BUILD |
| `test_clv_metrics` | CLV računanje |
| `test_roi_calculations` | ROI iz DB |

---

## 25. Operativni checklist

### Pre prvog pokretanja

- [ ] `.env` sa API-Football + Telegram ključevima
- [ ] Kopiraj `.env.example` → `.env`
- [ ] `startbot.bat` (automatski kreira `data/` foldere)
- [ ] Proveri `LEAGUE_IDS` i `MAX_OPEN_FIXTURES=0`

### Dnevno (automatski ako scheduler radi)

- [ ] 07:00 srpsko (05:00 UTC) ingest
- [ ] 08:00 srpsko (06:00 UTC) pickovi + Telegram push
- [ ] Svake 2h settle
- [ ] **LIVE PICKS** pre kickoff-a, **ROI STATISTIKA** posle mečeva

### Nedeljno

- [ ] Provera ROI trenda u Telegram meniju
- [ ] `python -m scripts.verify_data`
- [ ] Provera `pending` vs `win/lose` u bazi

### Mesečno

- [ ] `python -m app.calibrate_models --if-missing` (refresh DC parametara)
- [ ] `python scripts/train_confidence_calibrator.py` (refresh confidence modela)
- [ ] `python scripts/run_backtest.py` na novom periodu

### Dijagnostika

```powershell
python scripts/check_db.py
python scripts/check_stats.py
python -m scripts.verify_data
python -m scripts.analyze_picks
python scripts/manual_refresh_settle.py
python scripts/verify_calibrator_deploy.py
```

---

## 26. Troubleshooting

### Bot generiše tipove iz nepoznatih liga

Proveri da je `MAX_OPEN_FIXTURES=0` u `.env` i restartuj scheduler. Open fallback dodaje mečeve van `LEAGUE_IDS`.

### Tipovi sa λ=1.0/1.0 i CONF 95%

Nedovoljno FT istorije za timove. DC koristi default lambda → lažno visok edge. Sa isključenim open fallback-om ovo se ređe dešava jer su praćene lige bolje pokrivene.

### Kalibrisana pouzdanost = „nije kalibrisan"

1. Proveri `data/models/confidence_calibrator.joblib` postoji
2. Proveri `USE_CALIBRATED_CONFIDENCE=true`
3. Pokreni `python scripts/train_confidence_calibrator.py`

### LIVE PICKS prikazuje mečeve koji su već počeli

Restartuj `telegram.bat`. Filter `is_fixture_pre_kickoff()` isključuje kickoff ≤ sada.

### 0 pickova iako ima mečeva

- Nema pozitivnog EV
- Context gate blokirao (`fatigue_*`, `insufficient_xg`)
- Home/Away odbijeni quality filterom
- Mečevi nisu u `LEAGUE_IDS` (open fallback isključen)
- Proveri log: `drop_summary`, `DEBUG_FUNNEL`

### Pickovi ostaju `pending`

Scheduler ne radi ili ingest nije ažurirao FT:

```powershell
python scripts/manual_refresh_settle.py
```

### API rate limit (429) / suspendovan ključ

Bot preskače meč i nastavlja. Obnovi API-Football pretplatu.

### `startbot.bat` — `'ho' is not recognized`

`.bat` fajl ima Unix (LF) line endings. Mora biti **CRLF**.

---

## 27. Poznata ograničenja

1. **Dixon-Coles only** — ML ensemble uklonjen iz live pipeline-a.
2. **Min kvota 2.0** — bot ne šalje tipove ispod kvote 2.0.
3. **Open fallback isključen** — bot ne generiše tipove van `LEAGUE_IDS`.
4. **Market Confirmation Gate** — isključen u produkciji.
5. **Confidence Calibrator** — samo display; ne filtrira tipove dok validacija ne potvrdi poboljšanje.
6. **Default lambda** — mečevi bez FT istorije daju slab signal (λ=1.0/1.0).
7. **Live market scope** — samo 1X2, O/U 2.5, BTTS Yes; BTTS No blokiran.
8. **Nema auto-betting** — bot je signal generator + ROI tracker.
9. **Srpsko vreme** — fiksno UTC+2 pri prikazu.
10. **Win rate** — informativan only; nije optimization target.
11. **SQLite** — pri velikom broju odds redova razmotriti PostgreSQL.
12. **Lineup gate** — zavisi od API dostupnosti sastava (~1h pre meča).
13. **Confidence training data** — trenutno ~123 validna primera; potrebno više za robusniji kalibrator.

---

## Licenca i odgovornost

Sistem je namenjen **istraživanju i praćenju performansi signala**. Klađenje nosi finansijski rizik. Autor ne garantuje profit. Koristite na sopstvenu odgovornost u skladu sa lokalnim zakonima.

---

**Verzija:** `3.1.0` · **Python:** 3.10+ · **Stack:** FastAPI · SQLAlchemy · Dixon-Coles MLE · Logistic Regression Calibrator · APScheduler · python-telegram-bot
