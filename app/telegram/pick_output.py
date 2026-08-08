"""Output contract — LIVE PICKS display (ROI stats uses separate SQL filters)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import structlog

from app.config import get_settings
from app.predictions.pick_selector import SelectedPick

log = structlog.get_logger()

LIVE_DISPLAY_STATUSES = frozenset({"PENDING", "LIVE"})


def pick_identity_key(row: Any) -> tuple[int, str, str]:
    p: SelectedPick = row.pick
    return (
        p.fixture_id,
        (p.market or "").strip().lower(),
        (p.selection or "").strip().lower(),
    )


def dedupe_picks(rows: list[Any]) -> tuple[list[Any], int]:
    seen: set[tuple[int, str, str]] = set()
    out: list[Any] = []
    removed = 0
    for row in rows:
        key = pick_identity_key(row)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        out.append(row)
    return out, removed


def filter_live_status(rows: list[Any]) -> list[Any]:
    return [r for r in rows if r.status in LIVE_DISPLAY_STATUSES]


def sort_picks_by_ev_score(rows: list[Any]) -> list[Any]:
    """Najbolji prvi: EV, pa pick_rank_score."""
    return sorted(
        rows,
        key=lambda r: (r.pick.expected_value, r.pick.pick_rank_score),
        reverse=True,
    )


def take_top_picks(rows: list[Any], limit: int | None) -> tuple[list[Any], int]:
    if limit is None:
        return rows, 0
    if len(rows) <= limit:
        return rows, 0
    return rows[:limit], len(rows) - limit


def assign_global_ranks(rows: list[Any]) -> list[Any]:
    ranked: list[Any] = []
    for index, row in enumerate(rows, start=1):
        ranked.append(
            type(row)(
                status=row.status,
                pick=replace(row.pick, rank=index),
            )
        )
    return ranked


def prepare_live_picks(
    rows: list[Any],
    *,
    max_display: int | None = 6,
) -> tuple[list[Any], dict[str, int]]:
    """Sort po EV. max_display=None → svi; podrazumevano top 6."""
    limit = max_display
    stats: dict[str, int] = {
        "total_before": len(rows),
        "max_display": limit if limit is not None else len(rows),
        "unlimited": limit is None,
    }

    deduped, dup_removed = dedupe_picks(rows)
    stats["duplicates_removed"] = dup_removed
    stats["total_after_dedupe"] = len(deduped)

    live_only = filter_live_status(deduped)
    stats["total_after_status_filter"] = len(live_only)

    by_ev = sort_picks_by_ev_score(live_only)
    top, trimmed = take_top_picks(by_ev, limit)
    stats["ev_trimmed"] = trimmed

    ranked = assign_global_ranks(top)
    stats["total_render"] = len(ranked)

    log.info("live_picks_pipeline", **stats)
    return ranked, stats
