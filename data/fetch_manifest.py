"""Pull a manifest of Mapillary image references, stratified by continent.

Fetches only metadata (id, lat/lon, capture time) via the Mapillary Graph API
and writes it to data/manifest.csv. Does not download image bytes.

Requires a Mapillary access token in the MAPILLARY_TOKEN env var (or .env).
Get one at https://www.mapillary.com/dashboard/developers -> create an app
-> use its "Client Token".

Usage:
    uv run python data/fetch_manifest.py
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_BASE = "https://graph.mapillary.com/images"
FIELDS = "id,computed_geometry,geometry,captured_at"
PAGE_LIMIT = 20
MAX_PAGES_PER_TILE = 5
REQUEST_TIMEOUT_S = 30
REQUEST_DELAY_S = 0.2

# Mapillary's /images endpoint rejects bbox queries once too many images match, well
# below its documented 0.01 sq-degree area cap in practice (empirically ~0.0025 deg
# side in a dense area). TILE_SIDE stays safely under that; TILE_GRID_RADIUS spreads
# several such tiles around each city center so a subregion samples more than one
# street/hotspot instead of a single intersection.
TILE_SIDE = 0.002
TILE_SPACING = 0.006
TILE_GRID_RADIUS = 1  # -> a (2*radius+1)^2 grid of tiles per city

SEED = 20260905  # fixed so the sampled manifest is reproducible
TOTAL_TARGET = 300

MANIFEST_PATH = Path(__file__).parent / "manifest.csv"

# (continent -> [(sub-region label, center_lon, center_lat)]).
# Several sub-regions per continent so one city can't dominate that continent's slice.
REGIONS: dict[str, list[tuple[str, float, float]]] = {
    "Africa": [
        ("Cairo, EG", 31.25, 30.05),
        ("Marrakech, MA", -7.95, 31.625),
        ("Lagos, NG", 3.4, 6.5),
        ("Nairobi, KE", 36.85, -1.25),
        ("Cape Town, ZA", 18.45, -33.95),
    ],
    "Asia": [
        ("Tokyo, JP", 139.75, 35.675),
        ("Seoul, KR", 127.0, 37.575),
        ("Delhi, IN", 77.2, 28.625),
        ("Bangkok, TH", 100.55, 13.775),
        ("Istanbul, TR", 29.0, 41.05),
    ],
    "Europe": [
        ("Paris, FR", 2.35, 48.85),
        ("Rome, IT", 12.5, 41.9),
        ("Warsaw, PL", 21.0, 52.225),
        ("Stockholm, SE", 18.05, 59.35),
        ("Barcelona, ES", 2.175, 41.4),
    ],
    "North America": [
        ("Los Angeles, US", -118.25, 34.05),
        ("New York, US", -73.95, 40.75),
        ("Chicago, US", -87.65, 41.875),
        ("Toronto, CA", -79.35, 43.675),
        ("Mexico City, MX", -99.1, 19.425),
    ],
    "South America": [
        ("Sao Paulo, BR", -46.65, -23.55),
        ("Buenos Aires, AR", -58.4, -34.6),
        ("Bogota, CO", -74.05, 4.675),
        ("Santiago, CL", -70.625, -33.45),
    ],
    "Oceania": [
        ("Sydney, AU", 151.2, -33.875),
        ("Melbourne, AU", 144.975, -37.8),
        ("Auckland, NZ", 174.775, -36.85),
    ],
}


def city_tiles(center_lon: float, center_lat: float) -> list[tuple[float, float, float, float]]:
    """A small grid of tiny bboxes around a city center, each under the API's density cap."""
    half = TILE_SIDE / 2
    offsets = range(-TILE_GRID_RADIUS, TILE_GRID_RADIUS + 1)
    tiles = []
    for dx in offsets:
        for dy in offsets:
            clon = center_lon + dx * TILE_SPACING
            clat = center_lat + dy * TILE_SPACING
            tiles.append((clon - half, clat - half, clon + half, clat + half))
    return tiles


def load_dotenv_token() -> str | None:
    """Minimal .env reader so MAPILLARY_TOKEN can live next to ANTHROPIC_API_KEY."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "MAPILLARY_TOKEN":
            return value.strip().strip('"').strip("'")
    return None


def fetch_bbox_images(
    bbox: tuple[float, float, float, float], token: str, max_results: int
) -> list[dict]:
    """Page through the Mapillary images endpoint for one bounding box."""
    min_lon, min_lat, max_lon, max_lat = bbox
    params = {
        "access_token": token,
        "fields": FIELDS,
        "bbox": f"{min_lon},{min_lat},{max_lon},{max_lat}",
        "limit": str(PAGE_LIMIT),
    }
    url = f"{API_BASE}?{urllib.parse.urlencode(params)}"

    results: list[dict] = []
    for _ in range(MAX_PAGES_PER_TILE):
        if len(results) >= max_results:
            break
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_S) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Mapillary API error {exc.code} for bbox {bbox}: {body}") from exc

        results.extend(payload.get("data", []))
        next_url = payload.get("paging", {}).get("next")
        if not next_url:
            break
        url = next_url
        time.sleep(REQUEST_DELAY_S)

    return results[:max_results]


def extract_lat_lon(image: dict) -> tuple[float, float] | None:
    geometry = image.get("computed_geometry") or image.get("geometry")
    if not geometry or "coordinates" not in geometry:
        return None
    lon, lat = geometry["coordinates"]
    return lat, lon


def build_manifest(token: str) -> list[dict]:
    rng = random.Random(SEED)
    continents = list(REGIONS)
    base_quota, remainder = divmod(TOTAL_TARGET, len(continents))

    rows: list[dict] = []
    per_continent_counts: dict[str, int] = {}

    for i, continent in enumerate(continents):
        quota = base_quota + (1 if i < remainder else 0)
        subregions = REGIONS[continent]
        per_subregion_quota = -(-quota // len(subregions))  # ceil, so we can oversample then trim

        candidates: list[dict] = []
        for label, center_lon, center_lat in subregions:
            print(f"  fetching {label} ...", file=sys.stderr)
            seen_ids: set[str] = set()
            for tile in city_tiles(center_lon, center_lat):
                if len(seen_ids) >= per_subregion_quota * 3:
                    break
                try:
                    images = fetch_bbox_images(tile, token, max_results=per_subregion_quota * 3)
                except RuntimeError as exc:
                    print(f"    tile {tile} failed, skipping: {exc}", file=sys.stderr)
                    continue
                for image in images:
                    if image["id"] in seen_ids:
                        continue
                    latlon = extract_lat_lon(image)
                    if latlon is None:
                        continue
                    seen_ids.add(image["id"])
                    lat, lon = latlon
                    candidates.append(
                        {
                            "image_id": image["id"],
                            "lat": lat,
                            "lon": lon,
                            "continent": continent,
                            "region": label,
                            "captured_at": image.get("captured_at", ""),
                        }
                    )
                time.sleep(REQUEST_DELAY_S)

        rng.shuffle(candidates)
        selected = candidates[:quota]
        rows.extend(selected)
        per_continent_counts[continent] = len(selected)

    return rows, per_continent_counts


def write_manifest(rows: list[dict], path: Path) -> None:
    fieldnames = ["image_id", "lat", "lon", "continent", "region", "captured_at"]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    token = os.environ.get("MAPILLARY_TOKEN") or load_dotenv_token()
    if not token:
        print(
            "MAPILLARY_TOKEN is not set. Get a Client Token from "
            "https://www.mapillary.com/dashboard/developers and add it to .env as "
            "MAPILLARY_TOKEN=... or export it in your shell.",
            file=sys.stderr,
        )
        sys.exit(1)

    rows, per_continent_counts = build_manifest(token)
    write_manifest(rows, MANIFEST_PATH)

    print(f"\nwrote {len(rows)} rows to {MANIFEST_PATH}")
    print("count per continent:")
    for continent, count in per_continent_counts.items():
        print(f"  {continent:15s} {count}")


if __name__ == "__main__":
    main()
