"""Tests for eval.metrics: aggregate stats over a list of scored Predictions."""

from math import pi

import pytest

from eval.metrics import (
    country_accuracy,
    median_distance_km,
    per_region_breakdown,
    percent_within_km,
    summarize,
)
from eval.scorer import EARTH_RADIUS_KM, Prediction

QUARTER_KM = EARTH_RADIUS_KM * (pi / 2)
HALF_KM = EARTH_RADIUS_KM * pi


def _predictions_with_known_distances(region: str | None = None) -> list[Prediction]:
    return [
        Prediction(0.0, 0.0, 0.0, 0.0, region=region),  # distance 0
        Prediction(0.0, 0.0, 0.0, 90.0, region=region),  # distance QUARTER_KM
        Prediction(0.0, 0.0, 0.0, 180.0, region=region),  # distance HALF_KM
    ]


def test_median_distance_km():
    preds = _predictions_with_known_distances()
    assert median_distance_km(preds) == pytest.approx(QUARTER_KM, rel=1e-6)


def test_median_distance_km_empty_raises():
    with pytest.raises(ValueError):
        median_distance_km([])


def test_percent_within_km_all_within():
    preds = _predictions_with_known_distances()
    assert percent_within_km(preds, HALF_KM) == pytest.approx(100.0)


def test_percent_within_km_none_within():
    preds = [Prediction(0.0, 0.0, 0.0, 90.0), Prediction(0.0, 0.0, 0.0, 180.0)]
    assert percent_within_km(preds, 5.0) == pytest.approx(0.0)


def test_percent_within_km_partial():
    preds = _predictions_with_known_distances()
    # 0 km and QUARTER_KM both qualify (2/3); HALF_KM does not.
    assert percent_within_km(preds, QUARTER_KM) == pytest.approx(200.0 / 3.0)


def test_percent_within_km_boundary_is_inclusive():
    preds = [Prediction(0.0, 0.0, 0.0, 90.0)]
    assert percent_within_km(preds, QUARTER_KM) == pytest.approx(100.0)


def test_percent_within_km_empty_raises():
    with pytest.raises(ValueError):
        percent_within_km([], 25.0)


def test_country_accuracy():
    preds = [
        Prediction(0, 0, 0, 0, pred_country="US", true_country="US"),  # correct
        Prediction(0, 0, 0, 0, pred_country="FR", true_country="FR"),  # correct
        Prediction(0, 0, 0, 0, pred_country="US", true_country="CA"),  # incorrect
        Prediction(0, 0, 0, 0, pred_country=None, true_country=None),  # excluded
    ]
    assert country_accuracy(preds) == pytest.approx(200.0 / 3.0)


def test_country_accuracy_no_labels_raises():
    preds = [Prediction(0, 0, 0, 0)]
    with pytest.raises(ValueError):
        country_accuracy(preds)


def test_per_region_breakdown_groups_and_counts():
    eu = _predictions_with_known_distances(region="EU")
    na = [Prediction(0.0, 0.0, 0.0, 0.0, region="NA")]

    breakdown = per_region_breakdown(eu + na)

    assert set(breakdown.keys()) == {"EU", "NA"}
    assert breakdown["EU"]["n"] == 3
    assert breakdown["NA"]["n"] == 1
    assert breakdown["EU"]["median_distance_km"] == pytest.approx(QUARTER_KM, rel=1e-6)
    assert breakdown["NA"]["median_distance_km"] == pytest.approx(0.0, abs=1e-9)


def test_per_region_breakdown_uses_unknown_bucket_for_missing_region():
    preds = [Prediction(0.0, 0.0, 0.0, 0.0, region=None)]
    breakdown = per_region_breakdown(preds)
    assert "unknown" in breakdown


def test_per_region_breakdown_default_thresholds_present():
    preds = _predictions_with_known_distances(region="EU")
    breakdown = per_region_breakdown(preds)
    stats = breakdown["EU"]
    assert "pct_within_25km" in stats
    assert "pct_within_200km" in stats
    assert "pct_within_1000km" in stats


def test_per_region_breakdown_omits_country_accuracy_when_unlabeled():
    preds = [Prediction(0.0, 0.0, 0.0, 0.0, region="EU")]
    breakdown = per_region_breakdown(preds)
    assert "country_accuracy" not in breakdown["EU"]


def test_summarize_shape():
    preds = _predictions_with_known_distances(region="EU")
    summary = summarize(preds)

    assert summary["n"] == 3
    assert summary["median_distance_km"] == pytest.approx(QUARTER_KM, rel=1e-6)
    assert "pct_within_25km" in summary
    assert "pct_within_200km" in summary
    assert "pct_within_1000km" in summary
    assert "per_region" in summary
    assert summary["per_region"] == per_region_breakdown(preds)
