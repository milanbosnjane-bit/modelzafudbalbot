"""Tests for two-phase pipeline modes."""

from app.predictions.pipeline import PipelineMode


class TestPipelineMode:
    def test_live_is_default_enum(self):
        assert PipelineMode.LIVE.value == "live"

    def test_full_build_enum(self):
        assert PipelineMode.FULL_BUILD.value == "full-build"
