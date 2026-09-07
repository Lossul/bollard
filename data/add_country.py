"""Reverse-geocode each manifest row's true lat/lon to a country code.

Adds a `country` column (ISO-3166 alpha-2, uppercase) to data/manifest.csv,
derived from ground-truth coordinates via Nominatim -- not from any model
prediction. Then propagates that column into data/train.csv, data/dev.csv,
and eval/test.csv by joining on image_id, without touching which images are
in which split.

Respects Nominatim's usage policy: max 1 request/second, no concurrency, and
an identifying User-Agent. Progress is cached to data/.country_cache.json
(image_id -> country) so an interrupted run resumes without re-querying rows
already resolved.

Usage:
    uv run python data/add_country.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "bollard-geolocation-eval/0.1 (non-commercial research project)"
REQUEST_INTERVAL_S = 1.1  # Nominatim usage policy: max 1 req/sec

DATA_DIR = Path(__file__).parent
EVAL_DIR = DATA_DIR.parent / "eval"
MANIFEST_PATH = DATA_DIR / "manifest.csv"
CACHE_PATH = DATA_DIR / ".country_cache.json"

SPLIT_PATHS = [DATA_DIR / "train.csv", DATA_DIR / "dev.csv", EVAL_DIR / "test.csv"]


def load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def reverse_geocode(lat: float, lon: float) -> str:
    params = {
        "format": "jsonv2",
        "lat": f"{lat:.6f}",
        "lon": f"{lon:.6f}",
        "zoom": "3",
        "addressdetails": "1",
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read())
    return payload.get("address", {}).get("country_code", "").upper()


def geocode_manifest() -> list[dict]:
    with MANIFEST_PATH.open(newline="") as f:
        rows = list(csv.DictReader(f))

    cache = load_cache()
    n_cached = n_fetched = n_missing = 0

    for i, row in enumerate(rows, start=1):
        image_id = row["image_id"]
        if image_id in cache:
            row["country"] = cache[image_id]
            n_cached += 1
            if not row["country"]:
                n_missing += 1
            continue

        lat, lon = float(row["lat"]), float(row["lon"])
        print(f"[{i}/{len(rows)}] geocoding {image_id} ({lat:.4f}, {lon:.4f}) ...", file=sys.stderr)
        try:
            country = reverse_geocode(lat, lon)
            if not country:
                print(f"    warning: no country_code returned for {image_id}", file=sys.stderr)
                n_missing += 1
        except Exception as exc:
            print(f"    warning: reverse geocode failed for {image_id}: {exc}", file=sys.stderr)
            country = ""
            n_missing += 1

        row["country"] = country
        cache[image_id] = country
        n_fetched += 1
        save_cache(cache)
        time.sleep(REQUEST_INTERVAL_S)

    fieldnames = list(rows[0].keys())
    with MANIFEST_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nwrote country for {len(rows)} rows to {MANIFEST_PATH}")
    print(f"  {n_cached} from cache, {n_fetched} freshly geocoded, {n_missing} missing/unresolved")

    return rows


def propagate_to_splits(manifest_rows: list[dict]) -> None:
    country_by_id = {row["image_id"]: row["country"] for row in manifest_rows}

    for split_path in SPLIT_PATHS:
        if not split_path.exists():
            print(f"skipping {split_path} (not found)", file=sys.stderr)
            continue

        with split_path.open(newline="") as f:
            split_rows = list(csv.DictReader(f))

        missing_ids = [r["image_id"] for r in split_rows if r["image_id"] not in country_by_id]
        if missing_ids:
            raise RuntimeError(
                f"{split_path}: {len(missing_ids)} image_id(s) not found in manifest, e.g. {missing_ids[:3]}"
            )

        for row in split_rows:
            row["country"] = country_by_id[row["image_id"]]

        fieldnames = list(split_rows[0].keys())
        with split_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(split_rows)

        print(f"propagated country -> {split_path} ({len(split_rows)} rows)")


def main() -> None:
    manifest_rows = geocode_manifest()
    propagate_to_splits(manifest_rows)


if __name__ == "__main__":
    main()
