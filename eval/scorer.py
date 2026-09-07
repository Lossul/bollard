#ruler itself
"""Haversine distance scoring between predicted and true coordinates."""

from dataclasses import dataclass
from math import atan2, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points (WGS84 mean radius)."""
    phi1, phi2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)

    a = sin(dphi / 2) ** 2 + cos(phi1) * cos(phi2) * sin(dlambda / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return EARTH_RADIUS_KM * c


@dataclass(frozen=True)
class Prediction:
    """One scored prediction: a guessed point against ground truth."""

    pred_lat: float
    pred_lon: float
    true_lat: float
    true_lon: float
    pred_country: str | None = None
    true_country: str | None = None
    region: str | None = None

    @property
    def distance_km(self) -> float:
        return haversine_km(self.pred_lat, self.pred_lon, self.true_lat, self.true_lon)

    @property
    def country_correct(self) -> bool | None:
        if self.pred_country is None or self.true_country is None:
            return None
        return self.pred_country.upper() == self.true_country.upper()
