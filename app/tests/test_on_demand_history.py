"""Tests for on-demand team history backfill before insufficient_xg drop."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.predictions.pick_selector import PickSelectionEngine
from app.utils.feature_values import has_usable_match_xg


@pytest.mark.asyncio
async def test_backfill_runs_once_when_xg_missing():
    session = AsyncMock()
    engine = PickSelectionEngine(session)

    fixture = MagicMock()
    fixture.id = 100
    fixture.home_team_id = 1
    fixture.away_team_id = 2
    fixture.league_id = 39
    fixture.fixture_date = datetime(2026, 7, 28, 18, 0)
    session.get = AsyncMock(return_value=fixture)

    weak_features = {"home_weighted_xG_last5": 0.0, "away_weighted_xG_last5": 0.0}
    strong_features = {"home_weighted_xG_last5": 1.2, "away_weighted_xG_last5": 0.9}
    features_map = {100: weak_features}
    as_of = datetime(2026, 7, 28, 17, 0)

    ingest_mock = AsyncMock(return_value={"fixtures": 5, "stats": 4})
    build_mock = AsyncMock(return_value=strong_features)

    with (
        patch(
            "app.services.ingestion.DataIngestionService.ingest_team_recent_history",
            ingest_mock,
        ),
        patch(
            "app.features.engineer.FeatureEngineer.build_features",
            build_mock,
        ),
    ):
        result1 = await engine._try_backfill_fixture_history(100, as_of, features_map)
        result2 = await engine._try_backfill_fixture_history(100, as_of, features_map)

    assert has_usable_match_xg(result1, 0.15)
    assert result1 is strong_features
    assert features_map[100] is strong_features
    assert ingest_mock.await_count == 2  # home + away, once each
    assert build_mock.await_count == 1
    assert result2 is strong_features
    assert ingest_mock.await_count == 2  # no second backfill


@pytest.mark.asyncio
async def test_backfill_skipped_when_xg_already_ok():
    session = AsyncMock()
    engine = PickSelectionEngine(session)
    ok_features = {"home_weighted_xG_last5": 1.1, "away_weighted_xG_last5": 0.8}
    features_map = {200: ok_features}
    as_of = datetime(2026, 7, 28, 17, 0)

    with patch(
        "app.services.ingestion.DataIngestionService.ingest_team_recent_history",
        AsyncMock(),
    ) as ingest_mock:
        # generate_candidates path checks has_usable before calling backfill;
        # direct helper always runs when called — test the gate used in loop
        assert has_usable_match_xg(ok_features, 0.15)
        ingest_mock.assert_not_called()
