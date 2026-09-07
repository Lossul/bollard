"""Tests for eval.scorer: haversine distance and the Prediction wrapper."""

from math import pi

import pytest

from eval.scorer import EARTH_RADIUS_KM, Prediction, haversine_km


def test_same_point_is_zero():
    assert haversine_km(40.7128, -74.0060, 40.7128, -74.0060) == pytest.approx(0.0, abs=1e-9)


def test_origin_to_origin_is_zero():
    assert haversine_km(0.0, 0.0, 0.0, 0.0) == pytest.approx(0.0, abs=1e-9)


def test_quarter_of_equator_is_quarter_circumference():
    # (0,0) to (0,90) subtends a quarter of a great circle.
    expected = EARTH_RADIUS_KM * (pi / 2)
    assert haversine_km(0.0, 0.0, 0.0, 90.0) == pytest.approx(expected, abs=1e-6)


def test_poles_are_half_circumference_apart():
    expected = EARTH_RADIUS_KM * pi
    assert haversine_km(90.0, 0.0, -90.0, 0.0) == pytest.approx(expected, abs=1e-6)


def test_antipodal_points_are_half_circumference_apart():
    # (0,0) and (0,180) are antipodal: maximum possible great-circle distance.
    expected = EARTH_RADIUS_KM * pi
    assert haversine_km(0.0, 0.0, 0.0, 180.0) == pytest.approx(expected, abs=1e-6)


def test_general_antipodal_pair():
    # (lat, lon) and (-lat, lon +/- 180) are antipodal for any lat/lon.
    expected = EARTH_RADIUS_KM * pi
    assert haversine_km(40.0, 50.0, -40.0, -130.0) == pytest.approx(expected, abs=1e-3)


def test_antipodal_distance_is_the_maximum():
    # No two points on the sphere can be farther apart than antipodes.
    max_dist = EARTH_RADIUS_KM * pi
    for lat1, lon1, lat2, lon2 in [
        (0, 0, 89.9, 179.9),
        (10, -50, -80, 60),
        (-33.8688, 151.2093, 48.8566, 2.3522),
    ]:
        assert haversine_km(lat1, lon1, lat2, lon2) <= max_dist + 1e-6


def test_symmetry():
    a = haversine_km(48.8566, 2.3522, -33.8688, 151.2093)
    b = haversine_km(-33.8688, 151.2093, 48.8566, 2.3522)
    assert a == pytest.approx(b, abs=1e-9)


@pytest.mark.parametrize(
    ("lat1", "lon1", "lat2", "lon2", "expected_km"),
    [
        # New York <-> Los Angeles, commonly cited as ~3,936 km / 2,451 mi great-circle.
        (40.7128, -74.0060, 34.0522, -118.2437, 3935.75),
        # New York <-> London, commonly cited as ~5,570 km great-circle.
        (40.7128, -74.0060, 51.5074, -0.1278, 5570.23),
        # Paris <-> Sydney, commonly cited as ~16,960 km great-circle.
        (48.8566, 2.3522, -33.8688, 151.2093, 16960.52),
    ],
)
def test_known_city_pairs(lat1, lon1, lat2, lon2, expected_km):
    assert haversine_km(lat1, lon1, lat2, lon2) == pytest.approx(expected_km, rel=1e-3)


def test_small_delta_is_small_and_positive():
    d = haversine_km(51.5074, -0.1278, 51.5084, -0.1268)
    assert 0.0 < d < 2.0


def test_prediction_distance_km_matches_haversine():
    pred = Prediction(pred_lat=40.7128, pred_lon=-74.0060, true_lat=34.0522, true_lon=-118.2437)
    assert pred.distance_km == pytest.approx(3935.75, rel=1e-3)


def test_prediction_country_correct_case_insensitive():
    pred = Prediction(0, 0, 0, 0, pred_country="us", true_country="US")
    assert pred.country_correct is True


def test_prediction_country_incorrect():
    pred = Prediction(0, 0, 0, 0, pred_country="US", true_country="CA")
    assert pred.country_correct is False


def test_prediction_country_correct_none_when_missing():
    pred = Prediction(0, 0, 0, 0, pred_country="US", true_country=None)
    assert pred.country_correct is None
