"""Metrics reported over a set of scored predictions. See CLAUDE.md > Metrics."""

from collections import defaultdict
from statistics import median

from eval.scorer import Prediction

DEFAULT_THRESHOLDS_KM = (25, 200, 1000)


def median_distance_km(predictions: list[Prediction]) -> float:
    if not predictions:
        raise ValueError("median_distance_km requires at least one prediction")
    return median(p.distance_km for p in predictions)


def percent_within_km(predictions: list[Prediction], threshold_km: float) -> float:
    if not predictions:
        raise ValueError("percent_within_km requires at least one prediction")
    hits = sum(1 for p in predictions if p.distance_km <= threshold_km)
    return 100.0 * hits / len(predictions)


def country_accuracy(predictions: list[Prediction]) -> float:
    """Fraction (0-100) of predictions with a correct country, among those with a country label."""
    labeled = [p for p in predictions if p.country_correct is not None]
    if not labeled:
        raise ValueError("country_accuracy requires at least one prediction with country labels")
    correct = sum(1 for p in labeled if p.country_correct)
    return 100.0 * correct / len(labeled)


def per_region_breakdown(
    predictions: list[Prediction],
    thresholds_km: tuple[float, ...] = DEFAULT_THRESHOLDS_KM,
) -> dict[str, dict[str, float]]:
    """Group predictions by `region` and compute the standard metrics within each group."""
    by_region: dict[str, list[Prediction]] = defaultdict(list)
    for p in predictions:
        region = p.region if p.region is not None else "unknown"
        by_region[region].append(p)

    breakdown: dict[str, dict[str, float]] = {}
    for region, group in by_region.items():
        stats: dict[str, float] = {
            "n": len(group),
            "median_distance_km": median_distance_km(group),
        }
        for threshold in thresholds_km:
            stats[f"pct_within_{int(threshold)}km"] = percent_within_km(group, threshold)
        try:
            stats["country_accuracy"] = country_accuracy(group)
        except ValueError:
            pass
        breakdown[region] = stats

    return breakdown


def summarize(
    predictions: list[Prediction],
    thresholds_km: tuple[float, ...] = DEFAULT_THRESHOLDS_KM,
) -> dict[str, object]:
    """Overall metrics plus the per-region breakdown, in the shape CLAUDE.md asks for."""
    summary: dict[str, object] = {
        "n": len(predictions),
        "median_distance_km": median_distance_km(predictions),
    }
    for threshold in thresholds_km:
        summary[f"pct_within_{int(threshold)}km"] = percent_within_km(predictions, threshold)
    try:
        summary["country_accuracy"] = country_accuracy(predictions)
    except ValueError:
        pass
    summary["per_region"] = per_region_breakdown(predictions, thresholds_km)
    return summary
