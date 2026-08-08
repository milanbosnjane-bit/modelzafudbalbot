"""Pure edge capture calculations — stabilized denominator."""

from dataclasses import dataclass

EDGE_CAPTURE_EPSILON = 0.01
EDGE_CAPTURE_CLIP = 3.0


@dataclass
class EdgeMetrics:
    model_edge: float
    closing_edge: float
    edge_capture: float | None
    raw_edge_capture: float | None
    adjusted_edge_capture: float | None
    fair_implied_at_bet: float
    model_probability: float
    closing_fair_probability: float | None


def compute_edge_metrics(
    model_probability: float,
    fair_implied_at_bet: float,
    closing_fair_probability: float | None,
    epsilon: float = EDGE_CAPTURE_EPSILON,
    clip: float = EDGE_CAPTURE_CLIP,
) -> EdgeMetrics:
    model_edge = model_probability - fair_implied_at_bet
    closing_edge = 0.0
    raw_edge_capture = None
    adjusted_edge_capture = None

    if closing_fair_probability is not None:
        closing_edge = closing_fair_probability - fair_implied_at_bet
        denom = max(abs(closing_edge), epsilon)
        raw = model_edge / closing_edge if abs(closing_edge) > 1e-9 else None
        adj = model_edge / denom
        adjusted_edge_capture = max(-clip, min(clip, adj))
        raw_edge_capture = raw

    return EdgeMetrics(
        model_edge=model_edge,
        closing_edge=closing_edge,
        edge_capture=adjusted_edge_capture,
        raw_edge_capture=raw_edge_capture,
        adjusted_edge_capture=adjusted_edge_capture,
        fair_implied_at_bet=fair_implied_at_bet,
        model_probability=model_probability,
        closing_fair_probability=closing_fair_probability,
    )
