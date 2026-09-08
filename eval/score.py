"""Score command: read a run from runs/predictions.db and print its metrics.

Converts every successful prediction row into an eval.scorer.Prediction, runs
eval.metrics.summarize over them, and prints the result. Failed predictions
are excluded from scoring but their count is always reported, not hidden.

Usage:
    uv run python -m eval.score                # scores the most recent run
    uv run python -m eval.score --run <run_id>
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path

from agent.store import connect
from eval.metrics import summarize
from eval.scorer import Prediction

DATA_DIR = Path(__file__).parent.parent / "data"
EVAL_DIR = Path(__file__).parent

# Ground-truth country isn't stored in predictions.db (predictions were logged
# before the country column existed) -- look it up from the split's own CSV,
# joined by image_id. eval/test.csv is only ever read from here, inside eval/.
SPLIT_CSV_PATHS = {
    "train": DATA_DIR / "train.csv",
    "dev": DATA_DIR / "dev.csv",
    "test": EVAL_DIR / "test.csv",
}


def load_true_countries(split: str) -> dict[str, str]:
    path = SPLIT_CSV_PATHS.get(split)
    if path is None or not path.exists():
        return {}
    with path.open(newline="") as f:
        return {row["image_id"]: row.get("country", "") for row in csv.DictReader(f)}


def latest_run_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def load_run_meta(conn: sqlite3.Connection, run_id: str) -> dict | None:
    row = conn.execute(
        "SELECT run_id, split, model, prompt_version, n_total FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(["run_id", "split", "model", "prompt_version", "n_total"], row))


def load_true_countries_for_run(conn: sqlite3.Connection, run_id: str) -> dict[str, str]:
    # A run's own `split` label (e.g. "train+dev") isn't a lookup key -- read
    # each row's actual originating split instead and merge their ground truth.
    # Splits partition the manifest with no overlapping image_ids, so merging
    # is safe.
    row_splits = [
        r[0] for r in conn.execute("SELECT DISTINCT split FROM predictions WHERE run_id = ?", (run_id,)).fetchall()
    ]
    true_countries: dict[str, str] = {}
    for split in row_splits:
        true_countries.update(load_true_countries(split))
    return true_countries


def load_predictions(conn: sqlite3.Connection, run_id: str) -> tuple[list[Prediction], int]:
    true_countries = load_true_countries_for_run(conn, run_id)

    rows = conn.execute(
        "SELECT image_id, status, pred_lat, pred_lon, true_lat, true_lon, pred_country, continent "
        "FROM predictions WHERE run_id = ?",
        (run_id,),
    ).fetchall()

    predictions: list[Prediction] = []
    n_failed = 0
    for image_id, status, pred_lat, pred_lon, true_lat, true_lon, pred_country, continent in rows:
        if status != "ok":
            n_failed += 1
            continue
        predictions.append(
            Prediction(
                pred_lat=pred_lat,
                pred_lon=pred_lon,
                true_lat=true_lat,
                true_lon=true_lon,
                pred_country=pred_country,
                true_country=true_countries.get(image_id) or None,
                region=continent,
            )
        )
    return predictions, n_failed


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a prediction run from runs/predictions.db")
    parser.add_argument("--run", dest="run_id", default=None, help="run id to score (default: most recent)")
    args = parser.parse_args()

    conn = connect()

    run_id = args.run_id or latest_run_id(conn)
    if run_id is None:
        print("no runs found in runs/predictions.db")
        return

    meta = load_run_meta(conn, run_id)
    if meta is None:
        print(f"run {run_id!r} not found in runs/predictions.db")
        return

    predictions, n_failed = load_predictions(conn, run_id)
    conn.close()

    print(f"run {meta['run_id']}")
    print(f"  split={meta['split']} model={meta['model']} prompt_version={meta['prompt_version']}")
    print(f"  n_total={meta['n_total']}  scored={len(predictions)}  excluded_failed={n_failed}")

    if not predictions:
        print("\nno successful predictions to score")
        return

    summary = summarize(predictions)
    print("\n" + json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
