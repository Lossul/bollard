"""Batch agent loop: every image in the given split(s) -> one logged prediction each.

For each row, fetches its Mapillary thumbnail, sends it to Claude with the
prompt in prompts/geolocate_v1.txt, and logs the outcome to runs/predictions.db
(see agent/store.py) -- one row per prediction, raw response included, tagged
with the run id, model, prompt version, and the row's own originating split.
A failed image is logged with status='failed' and its error, not retried or
skipped. No scoring here -- that's a separate step over the logged run.

Only train and dev are selectable here. eval/test.csv is intentionally not an
option -- nothing outside eval/ should read it.

Usage:
    uv run python -m agent.predict                    # dev only (default)
    uv run python -m agent.predict --splits train dev  # both, as one run
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import sys
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from agent.image_utils import crop_bottom, strip_exif
from agent.store import connect, finish_run, insert_prediction, insert_run

MODEL = "claude-sonnet-5"
PROMPT_VERSION = "geolocate_v1"
PROMPT_PATH = Path(__file__).parent.parent / "prompts" / f"{PROMPT_VERSION}.txt"
DATA_DIR = Path(__file__).parent.parent / "data"
MAPILLARY_THUMB_FIELD = "thumb_2048_url"

# eval/test.csv is deliberately absent -- agent/ must never read it, only eval/ may.
SPLIT_CSV_PATHS = {
    "train": DATA_DIR / "train.csv",
    "dev": DATA_DIR / "dev.csv",
}


def load_dotenv_value(key: str) -> str | None:
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def read_split_rows(splits: list[str]) -> list[tuple[str, dict]]:
    """Read and concatenate the given splits, tagging each row with its origin."""
    rows: list[tuple[str, dict]] = []
    for split in splits:
        with SPLIT_CSV_PATHS[split].open(newline="") as f:
            rows.extend((split, row) for row in csv.DictReader(f))
    return rows


def fetch_thumbnail_url(image_id: str, token: str) -> str:
    params = {"access_token": token, "fields": MAPILLARY_THUMB_FIELD}
    url = f"https://graph.mapillary.com/{image_id}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read())
    return payload[MAPILLARY_THUMB_FIELD]


def fetch_image_base64(image_url: str) -> str:
    # Anthropic's URL image source respects the target host's robots.txt, which
    # blocks Mapillary's CDN -- so fetch the bytes ourselves and send base64 instead.
    with urllib.request.urlopen(image_url, timeout=30) as response:
        image_bytes = response.read()
    # The model must never see embedded ground-truth metadata. Most Mapillary
    # thumbnails carry no EXIF at all, but it isn't consistent -- strip whatever's
    # there regardless rather than trusting that to hold.
    image_bytes = strip_exif(image_bytes)
    # Some Mapillary images are dash-mounted and show a vehicle dashboard/hood
    # at the bottom of frame (confirmed in a spot-check); crop it uniformly
    # since it isn't detected per-image. Applies to what the model sees and,
    # via the webapp reusing this same function, to what the user sees too.
    image_bytes = crop_bottom(image_bytes)
    return base64.standard_b64encode(image_bytes).decode("utf-8")


def parse_prediction_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[len("json") :]
    return json.loads(text.strip())


class PredictionParseError(Exception):
    """Model responded, but the response couldn't be turned into a prediction.

    Carries the raw response text so the caller can still log what the model
    actually said, instead of losing it once the exception propagates.
    """

    def __init__(self, message: str, raw_response: str):
        super().__init__(message)
        self.raw_response = raw_response


def fetch_and_encode_image(image_id: str, mapillary_token: str) -> str:
    """Fetch a Mapillary image's thumbnail and return it EXIF-stripped, base64-encoded."""
    image_url = fetch_thumbnail_url(image_id, mapillary_token)
    return fetch_image_base64(image_url)


def call_model(
    client: anthropic.Anthropic, prompt_text: str, image_b64: str, media_type: str = "image/jpeg"
) -> dict:
    """Send an already-encoded image to the model and parse its response.

    Returns evidence as a plain list (not pre-serialized) -- callers that need
    it as TEXT for SQLite storage (see predict_one) serialize it themselves.
    Raises PredictionParseError (carrying the raw text) if parsing fails.
    """
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }
        ],
    )
    raw_text = next((block.text for block in response.content if block.type == "text"), "")

    try:
        parsed = parse_prediction_json(raw_text)
        fields = {
            "pred_lat": float(parsed["lat"]),
            "pred_lon": float(parsed["lon"]),
            "pred_country": parsed.get("country"),
            "confidence": parsed.get("confidence"),
            "evidence": parsed.get("evidence"),
            "reasoning": parsed.get("reasoning"),
        }
    except Exception as exc:
        # We have the model's raw text even though it didn't parse -- keep it
        # attached to the exception so the caller can still log it.
        raise PredictionParseError(f"{type(exc).__name__}: {exc}", raw_text) from exc

    fields["raw_response"] = raw_text
    return fields


def predict_one(client: anthropic.Anthropic, mapillary_token: str, prompt_text: str, row: dict) -> dict:
    """Run one image through the model. Raises on any failure -- caller logs it."""
    image_b64 = fetch_and_encode_image(row["image_id"], mapillary_token)
    fields = call_model(client, prompt_text, image_b64)
    fields["evidence"] = json.dumps(fields["evidence"])  # predictions.evidence column is TEXT
    return fields


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the agent over one or more splits as a single run")
    parser.add_argument(
        "--splits",
        nargs="+",
        default=["dev"],
        choices=sorted(SPLIT_CSV_PATHS),
        help="splits to combine into this run (default: dev)",
    )
    parser.add_argument(
        "--image-ids",
        nargs="+",
        default=None,
        help="restrict to these image_ids, searched across all splits (overrides --splits filtering)",
    )
    args = parser.parse_args()

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or load_dotenv_value("ANTHROPIC_API_KEY")
    mapillary_token = os.environ.get("MAPILLARY_TOKEN") or load_dotenv_value("MAPILLARY_TOKEN")
    if not anthropic_key:
        print("ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    if not mapillary_token:
        print("MAPILLARY_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    if args.image_ids:
        wanted = set(args.image_ids)
        split_rows = [
            (split, row) for split, row in read_split_rows(sorted(SPLIT_CSV_PATHS)) if row["image_id"] in wanted
        ]
        found = {row["image_id"] for _, row in split_rows}
        missing = wanted - found
        if missing:
            print(f"warning: image_id(s) not found in any split: {sorted(missing)}", file=sys.stderr)
    else:
        split_rows = read_split_rows(args.splits)

    prompt_text = PROMPT_PATH.read_text()
    client = anthropic.Anthropic(api_key=anthropic_key)

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    if args.image_ids:
        run_split_label = "+".join(sorted({split for split, _ in split_rows}))
        config = {"image_ids": args.image_ids, "n_rows": len(split_rows)}
    else:
        run_split_label = "+".join(args.splits)
        config = {
            "source_csvs": [str(SPLIT_CSV_PATHS[s]) for s in args.splits],
            "n_rows": len(split_rows),
        }
    conn = connect()
    insert_run(
        conn,
        run_id=run_id,
        started_at=now_iso(),
        split=run_split_label,
        model=MODEL,
        prompt_version=PROMPT_VERSION,
        config=json.dumps(config),
        n_total=len(split_rows),
    )

    n_ok = 0
    n_failed = 0
    print(f"run {run_id}: {len(split_rows)} images from splits {args.splits}")

    for i, (split, row) in enumerate(split_rows, start=1):
        prefix = f"[{i}/{len(split_rows)}] {row['image_id']} ({split}/{row['continent']})"
        db_row = {
            "run_id": run_id,
            "image_id": row["image_id"],
            "split": split,
            "continent": row["continent"],
            "true_lat": float(row["lat"]),
            "true_lon": float(row["lon"]),
            "status": "ok",
            "pred_lat": None,
            "pred_lon": None,
            "pred_country": None,
            "confidence": None,
            "evidence": None,
            "reasoning": None,
            "raw_response": None,
            "error": None,
            "model": MODEL,
            "prompt_version": PROMPT_VERSION,
            "created_at": now_iso(),
        }

        try:
            result = predict_one(client, mapillary_token, prompt_text, row)
            db_row.update(result)
            n_ok += 1
            print(f"{prefix} -> ok")
        except Exception as exc:
            db_row["status"] = "failed"
            if isinstance(exc, PredictionParseError):
                db_row["error"] = str(exc)
                db_row["raw_response"] = exc.raw_response
            else:
                db_row["error"] = f"{type(exc).__name__}: {exc}"
            n_failed += 1
            print(f"{prefix} -> failed: {db_row['error']}")

        insert_prediction(conn, db_row)

    finish_run(conn, run_id, finished_at=now_iso(), n_ok=n_ok, n_failed=n_failed)
    conn.close()

    print(f"\nrun {run_id} done: {n_ok} ok, {n_failed} failed -> runs/predictions.db")


if __name__ == "__main__":
    main()
