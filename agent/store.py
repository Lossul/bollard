"""SQLite storage for prediction runs. See CLAUDE.md > Stack, > Conventions.

One predictions.db under runs/, appended to by every run -- never overwritten,
never rewritten in place. `score --run <id>` and `compare --runs` (not built
yet) read from here.

RUNS_DIR defaults to a local `runs/` folder for dev, but is overridable via
BOLLARD_RUNS_DIR -- set this to a mounted persistent volume's path when
deployed to a platform with an ephemeral container filesystem (see README >
Deployment). webapp/rate_limit.py's counter file shares this same directory.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

RUNS_DIR = Path(os.environ.get("BOLLARD_RUNS_DIR", Path(__file__).parent.parent / "runs"))
DB_PATH = RUNS_DIR / "predictions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    split TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    config TEXT NOT NULL,
    n_total INTEGER NOT NULL,
    n_ok INTEGER,
    n_failed INTEGER
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    image_id TEXT NOT NULL,
    split TEXT NOT NULL,
    continent TEXT,
    true_lat REAL,
    true_lon REAL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    pred_lat REAL,
    pred_lon REAL,
    pred_country TEXT,
    confidence REAL,
    evidence TEXT,
    reasoning TEXT,
    raw_response TEXT,
    error TEXT,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def insert_run(
    conn: sqlite3.Connection,
    run_id: str,
    started_at: str,
    split: str,
    model: str,
    prompt_version: str,
    config: str,
    n_total: int,
) -> None:
    conn.execute(
        """
        INSERT INTO runs (run_id, started_at, split, model, prompt_version, config, n_total)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (run_id, started_at, split, model, prompt_version, config, n_total),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, finished_at: str, n_ok: int, n_failed: int) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, n_ok = ?, n_failed = ? WHERE run_id = ?",
        (finished_at, n_ok, n_failed, run_id),
    )
    conn.commit()


def insert_prediction(conn: sqlite3.Connection, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO predictions (
            run_id, image_id, split, continent, true_lat, true_lon, status,
            pred_lat, pred_lon, pred_country, confidence, evidence, reasoning,
            raw_response, error, model, prompt_version, created_at
        ) VALUES (
            :run_id, :image_id, :split, :continent, :true_lat, :true_lon, :status,
            :pred_lat, :pred_lon, :pred_country, :confidence, :evidence, :reasoning,
            :raw_response, :error, :model, :prompt_version, :created_at
        )
        """,
        row,
    )
    conn.commit()
