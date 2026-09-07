"""Split data/manifest.csv into train/dev/test, stratified by continent for test.

Test is 20% of the manifest, with each continent contributing 20% of its own rows
so the held-out set matches the manifest's continent proportions. Train/dev split
the remaining 80% by a plain (non-stratified) random shuffle -- CLAUDE.md doesn't
require that split to be exact.

test.csv is written to eval/test.csv, not data/. Per working agreement: nothing
outside eval/ should read it until told otherwise -- don't add a loader for it in
agent/ or data/, and don't glob data/*.csv in a way that would pick it up.

Usage:
    uv run python data/split_manifest.py
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from pathlib import Path

SEED = 20260905
TEST_FRACTION = 0.2
TRAIN_FRACTION_OF_REST = 0.8  # of the non-test rows; the remainder goes to dev

DATA_DIR = Path(__file__).parent
MANIFEST_PATH = DATA_DIR / "manifest.csv"
TRAIN_PATH = DATA_DIR / "train.csv"
DEV_PATH = DATA_DIR / "dev.csv"
TEST_PATH = DATA_DIR.parent / "eval" / "test.csv"
SPLIT_META_PATH = DATA_DIR / "split_meta.json"


def read_manifest(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(rows: list[dict], fieldnames: list[str], path: Path) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def split(rows: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(SEED)

    by_continent: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_continent[row["continent"]].append(row)

    test: list[dict] = []
    rest: list[dict] = []
    for continent, group in by_continent.items():
        shuffled = list(group)
        rng.shuffle(shuffled)
        n_test = round(len(shuffled) * TEST_FRACTION)
        test.extend(shuffled[:n_test])
        rest.extend(shuffled[n_test:])

    rng.shuffle(rest)
    n_train = round(len(rest) * TRAIN_FRACTION_OF_REST)
    train = rest[:n_train]
    dev = rest[n_train:]

    return train, dev, test


def main() -> None:
    rows = read_manifest(MANIFEST_PATH)
    fieldnames = list(rows[0].keys())

    train, dev, test = split(rows)

    write_csv(train, fieldnames, TRAIN_PATH)
    write_csv(dev, fieldnames, DEV_PATH)
    write_csv(test, fieldnames, TEST_PATH)

    counts_by_continent = {
        split_name: dict(
            sorted(
                (
                    (continent, sum(1 for r in split_rows if r["continent"] == continent))
                    for continent in sorted({r["continent"] for r in rows})
                )
            )
        )
        for split_name, split_rows in [("train", train), ("dev", dev), ("test", test)]
    }
    SPLIT_META_PATH.write_text(
        json.dumps(
            {
                "seed": SEED,
                "test_fraction": TEST_FRACTION,
                "train_fraction_of_rest": TRAIN_FRACTION_OF_REST,
                "n_total": len(rows),
                "n_train": len(train),
                "n_dev": len(dev),
                "n_test": len(test),
                "counts_by_continent": counts_by_continent,
            },
            indent=2,
        )
        + "\n"
    )

    print(f"train: {len(train)} -> {TRAIN_PATH}")
    print(f"dev:   {len(dev)} -> {DEV_PATH}")
    print(f"test:  {len(test)} -> {TEST_PATH}  (eval/ only -- do not read elsewhere)")
    print(f"\nsplit metadata: {SPLIT_META_PATH}")
    print("\ntest set continent breakdown:")
    for continent, count in counts_by_continent["test"].items():
        print(f"  {continent:15s} {count}")


if __name__ == "__main__":
    main()
