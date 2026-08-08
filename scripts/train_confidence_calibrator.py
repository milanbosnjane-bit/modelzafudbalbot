#!/usr/bin/env python3
"""
Train isolated confidence calibrator from historical pre-match snapshots.

Uses confidence_prediction_logs when available; otherwise reconstructs partial
rows from settled daily_picks (win/lose only — void/push excluded).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.model.confidence_calibrator import (
    DEFAULT_META_PATH,
    DEFAULT_MODEL_PATH,
    CalibratorInput,
    ConfidenceCalibrator,
    TrainingReport,
    detect_default_lambda,
    parse_lambdas_from_reasoning,
)

settings = get_settings()


def _connect():
    alt = ROOT / "data" / "football_roi.db"
    if alt.is_file():
        return create_engine(f"sqlite:///{alt}")
    url = settings.database_url_sync
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "")
        if Path(path).is_file():
            return create_engine(url)
    return create_engine(settings.database_url_sync)


def team_ft_count(conn, team_id: int, before: str) -> int:
    return conn.execute(
        text(
            """
            SELECT COUNT(*) FROM fixtures
            WHERE status = 'FT' AND fixture_date < :before
              AND (home_team_id = :tid OR away_team_id = :tid)
            """
        ),
        {"before": before, "tid": team_id},
    ).scalar_one()


def load_training_rows(conn) -> tuple[list[CalibratorInput], list[int], str]:
    """Return samples, binary outcomes, data source label."""
    from sqlalchemy import inspect

    inspector = inspect(conn)
    tables = set(inspector.get_table_names())

    if "confidence_prediction_logs" in tables:
        rows = conn.execute(
            text(
                """
                SELECT l.*, COALESCE(l.outcome, p.outcome) AS final_outcome
                FROM confidence_prediction_logs l
                LEFT JOIN daily_picks p ON p.id = l.daily_pick_id
                WHERE COALESCE(l.outcome, p.outcome) IN ('win', 'lose')
                ORDER BY l.predicted_at
                """
            )
        ).mappings().all()
        if rows:
            samples: list[CalibratorInput] = []
            outcomes: list[int] = []
            for r in rows:
                predicted_at = r["predicted_at"]
                if isinstance(predicted_at, str):
                    predicted_at = datetime.fromisoformat(predicted_at.replace("Z", ""))
                samples.append(
                    CalibratorInput(
                        dixon_coles_probability=r["dixon_coles_probability"],
                        market_fair_probability=r["market_fair_probability"],
                        edge=r["edge"],
                        raw_ev=r["raw_ev"],
                        odds=r["odds"],
                        market=r["market"],
                        selection=r["selection"],
                        league_id=r["league_id"],
                        home_ft_count=r["home_ft_count"],
                        away_ft_count=r["away_ft_count"],
                        used_default_lambda=bool(r["used_default_lambda"]),
                        home_lambda=r["home_lambda"],
                        away_lambda=r["away_lambda"],
                        feature_quality=r["feature_quality"] or 0.0,
                        hours_to_kickoff=r["hours_to_kickoff"],
                        old_confidence=r["old_confidence"],
                        predicted_at=predicted_at,
                    )
                )
                outcomes.append(1 if r["final_outcome"] == "win" else 0)
            return samples, outcomes, "confidence_prediction_logs"

    rows = conn.execute(
        text(
            """
            SELECT p.*, f.league_id, f.fixture_date, f.home_team_id, f.away_team_id
            FROM daily_picks p
            JOIN fixtures f ON f.id = p.fixture_id
            WHERE p.outcome IN ('win', 'lose')
              AND p.pick_date < f.fixture_date
            ORDER BY p.pick_date
            """
        )
    ).mappings().all()

    samples = []
    outcomes = []
    for r in rows:
        reasoning = r["reasoning"]
        if isinstance(reasoning, str):
            try:
                reasoning = json.loads(reasoning)
            except json.JSONDecodeError:
                reasoning = []
        home_l, away_l = parse_lambdas_from_reasoning(reasoning)
        fair = r["fair_implied_prob"] or (1.0 / r["odds"] if r["odds"] > 1 else 0.5)
        prob = r["probability"]
        pick_date = r["pick_date"]
        fixture_date = r["fixture_date"]
        if isinstance(pick_date, str):
            pick_date = datetime.fromisoformat(pick_date.replace("Z", ""))
        if isinstance(fixture_date, str):
            fixture_date = datetime.fromisoformat(fixture_date.replace("Z", ""))
        hours = max(0.0, (fixture_date - pick_date).total_seconds() / 3600.0)
        h_ft = team_ft_count(conn, r["home_team_id"], str(fixture_date))
        a_ft = team_ft_count(conn, r["away_team_id"], str(fixture_date))
        samples.append(
            CalibratorInput(
                dixon_coles_probability=prob,
                market_fair_probability=fair,
                edge=prob - fair,
                raw_ev=r["expected_value"],
                odds=r["odds"],
                market=r["market"],
                selection=r["selection"],
                league_id=r["league_id"],
                home_ft_count=h_ft,
                away_ft_count=a_ft,
                used_default_lambda=detect_default_lambda(home_l, away_l),
                home_lambda=home_l,
                away_lambda=away_l,
                feature_quality=0.5,
                hours_to_kickoff=hours,
                old_confidence=r["confidence"],
                predicted_at=pick_date,
            )
        )
        outcomes.append(1 if r["outcome"] == "win" else 0)

    return samples, outcomes, "daily_picks_reconstructed"


def print_metrics_block(title: str, metrics) -> None:
    if metrics is None:
        print(f"\n{title}: N/A")
        return
    print(f"\n{title} (n={metrics.n_samples})")
    print(f"  Brier: {metrics.brier_score:.4f}")
    print(f"  Log loss: {metrics.log_loss:.4f}")
    print(f"  Calibration error (ECE): {metrics.calibration_error:.4f}")
    print("  Buckets:")
    for row in metrics.bucket_table:
        if row["n"]:
            print(
                f"    {row['bucket']}: n={row['n']} "
                f"pred={row['mean_pred']} winrate={row['winrate']}"
            )
    if metrics.by_market:
        print("  By market:")
        for mk, vals in metrics.by_market.items():
            print(f"    {mk}: n={vals['n']} brier={vals['brier']:.4f}")
    if metrics.ev_gt_35:
        ev = metrics.ev_gt_35
        print(
            f"  EV>35%: n={ev['n']} brier={ev['brier']:.4f} "
            f"mean_pred={ev['mean_pred']:.3f} winrate={ev['winrate']:.3f}"
        )


def load_merged_csv() -> tuple[list[CalibratorInput], list[int], dict] | None:
    csv_path = ROOT / "data" / "confidence_training" / "history_merged.csv"
    meta_path = ROOT / "data" / "confidence_training" / "history_merged_meta.json"
    if not csv_path.is_file():
        return None

    import csv

    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    samples: list[CalibratorInput] = []
    outcomes: list[int] = []
    with csv_path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ts = datetime.fromisoformat(row["prediction_timestamp"])
            samples.append(
                CalibratorInput(
                    dixon_coles_probability=float(row["dixon_coles_probability"]),
                    market_fair_probability=float(row["market_fair_probability"]),
                    edge=float(row["edge"]),
                    raw_ev=float(row["raw_ev"]),
                    odds=float(row["odds"]),
                    market=row["market"],
                    selection=row["selection"],
                    league_id=int(row["league_id"]) if row.get("league_id") else None,
                    home_ft_count=int(row["home_ft_count"]) if row.get("home_ft_count") else None,
                    away_ft_count=int(row["away_ft_count"]) if row.get("away_ft_count") else None,
                    used_default_lambda=row.get("used_default_lambda") in ("True", "true", "1"),
                    home_lambda=float(row["home_lambda"]) if row.get("home_lambda") else None,
                    away_lambda=float(row["away_lambda"]) if row.get("away_lambda") else None,
                    feature_quality=float(row.get("feature_quality") or 0.0),
                    hours_to_kickoff=float(row["hours_to_kickoff"]) if row.get("hours_to_kickoff") else None,
                    old_confidence=float(row["old_confidence"]),
                    predicted_at=ts,
                )
            )
            outcomes.append(1 if row["outcome"] == "win" else 0)
    return samples, outcomes, meta


def print_report(report: TrainingReport, source: str, total: int, meta: dict | None = None) -> None:
    print("=" * 60)
    print("CONFIDENCE CALIBRATOR — TRAINING REPORT")
    print("=" * 60)
    print(f"Izvor podataka: {source}")
    print(f"Validnih primera (win/lose): {total}")
    if meta:
        src = meta.get("sources", {})
        print(f"  botposlednji1: {src.get('botposlednji1', 0)}")
        print(f"  current_bot: {src.get('current_bot', 0)}")
        print(f"  Pobede / porazi: {meta.get('wins', '?')} / {meta.get('losses', '?')}")
        period = meta.get("period", {})
        if period:
            print(
                f"  Period predikcija: {period.get('prediction_min')} -> {period.get('prediction_max')}"
            )
    print(f"Dovoljno za trening: {'DA' if report.sufficient_data else 'NE'}")
    print(report.message)
    if report.sufficient_data and report.train_samples:
        print(f"Train: {report.train_samples} ({report.train_start} -> {report.train_end})")
    if report.sufficient_data and report.val_samples:
        print(f"Val:   {report.val_samples} ({report.val_start} -> {report.val_end})")
    print_metrics_block("Stari CONF (validation)", report.old_metrics)
    print_metrics_block("DC raw probability (validation)", report.dc_metrics)
    new_title = (
        "Kalibrisani CONF (validation, logistic)"
        if report.sufficient_data
        else "Dixon-Coles raw probability (referenca, ceo uzorak)"
    )
    print_metrics_block(new_title, report.new_metrics)


def main() -> int:
    meta: dict | None = None
    merged = load_merged_csv()
    if merged:
        samples, outcomes, meta = merged
        source = "history_merged.csv (botposlednji1 + current_bot)"
    else:
        engine = _connect()
        with engine.connect() as conn:
            samples, outcomes, source = load_training_rows(conn)
    total = len(samples)

    calibrator = ConfidenceCalibrator()
    report = calibrator.fit(samples, outcomes)

    if not report.sufficient_data and total >= 10:
        from app.model.confidence_calibrator import compute_metrics
        import numpy as np

        old_probs = np.asarray(
            [s.old_confidence if s.old_confidence is not None else 0.5 for s in samples],
            dtype=np.float64,
        )
        dc_probs = np.asarray([s.dixon_coles_probability for s in samples], dtype=np.float64)
        raw_evs = np.asarray([s.raw_ev for s in samples], dtype=np.float64)
        markets = [s.market for s in samples]
        y = np.asarray(outcomes, dtype=np.int32)
        report.old_metrics = compute_metrics(old_probs, y, markets=markets, raw_evs=raw_evs)
        report.new_metrics = compute_metrics(dc_probs, y, markets=markets, raw_evs=raw_evs)
        report.message += (
            " (Informativno: prikazane metrike starog CONF i DC raw probability na celom uzorku.)"
        )

    print_report(report, source, total, meta=meta)

    if not report.sufficient_data:
        print(
            "\nKalibrator nije sačuvan. Nastavi logging pre-match snapshot-a; "
            "pokreni ponovo kada bude >= 50 settled primera."
        )
        return 0

    model_path = settings.model_dir / "confidence_calibrator.joblib"
    meta_path = settings.model_dir / "confidence_calibrator_meta.json"
    calibrator.save(model_path, meta_path)
    print(f"\nModel sačuvan: {model_path}")
    print(f"Meta: {meta_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
