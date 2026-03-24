"""SQLite schema, connection helper, and migration for the paxpy benchmark database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).parent.parent / "bench.db"

_DDL = """
CREATE TABLE IF NOT EXISTS scenarios (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    UNIQUE NOT NULL,
    bucket            TEXT    NOT NULL,      -- 'correctness' | 'adversarial' | 'performance'
    conflict_type     TEXT,                  -- 'DATA_FLOW' etc., NULL for negatives
    call_depth        INTEGER,
    fan_out           INTEGER,
    file_count        INTEGER,
    name_ambiguity    INTEGER,
    positive          INTEGER NOT NULL,      -- 1=conflict expected, 0=clean
    random_seed       INTEGER,
    complexity_tier   TEXT,                  -- 'simple' | 'moderate' | 'complex' | 'adversarial' | 'performance'
    base_source       TEXT    NOT NULL,      -- JSON: {"filename.py": "source..."}
    branch_a_source   TEXT    NOT NULL,
    branch_b_source   TEXT    NOT NULL,
    expected_conflict INTEGER NOT NULL,      -- 1 or 0
    expected_direction TEXT,                 -- 'B_to_A' | 'A_to_B' | NULL
    expected_tier     INTEGER,               -- 1 | 2 | NULL
    label_rationale   TEXT    NOT NULL,
    mutation_a        TEXT,
    mutation_b        TEXT,
    hypothesis        TEXT,                  -- adversarial: what failure mode is expected
    verified          INTEGER DEFAULT 0,
    created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bucket          TEXT    NOT NULL,
    run_at          TEXT    NOT NULL,
    git_commit      TEXT,
    depth           INTEGER NOT NULL DEFAULT 5,
    scenario_filter TEXT,                    -- NULL=all, or scenario name prefix
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES runs(id),
    scenario_id       INTEGER NOT NULL REFERENCES scenarios(id),
    detected          INTEGER,               -- 1=conflict found, 0=clean, NULL=error
    path_count        INTEGER,
    directions        TEXT,                  -- JSON: ["B_to_A"]
    tiers             TEXT,                  -- JSON: [1]
    conflict_types    TEXT,                  -- JSON: ["DATA_FLOW"]
    is_tp             INTEGER,
    is_fp             INTEGER,
    is_fn             INTEGER,
    is_tn             INTEGER,
    sdg_node_count    INTEGER,
    sdg_edge_count    INTEGER,
    call_depth_actual INTEGER,               -- shortest witness path length, if found
    sdg_build_ms      INTEGER,
    detection_ms      INTEGER,
    total_ms          INTEGER,
    error             TEXT,                  -- exception message if paxpy raised
    UNIQUE(run_id, scenario_id)
);

CREATE INDEX IF NOT EXISTS idx_results_run    ON results(run_id);
CREATE INDEX IF NOT EXISTS idx_results_scen   ON results(scenario_id);
CREATE INDEX IF NOT EXISTS idx_scenarios_buck ON scenarios(bucket);
CREATE INDEX IF NOT EXISTS idx_scenarios_tier ON scenarios(complexity_tier);
"""


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    """Open (creating if necessary) the benchmark database and return a connection.

    Foreign-key enforcement and WAL mode are enabled on every connection.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    """Create tables and indexes if they do not already exist."""
    conn = connect(db_path)
    conn.executescript(_DDL)
    conn.commit()
    return conn


def drop_and_recreate(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    """Destroy all data and recreate the schema from scratch."""
    conn = connect(db_path)
    conn.executescript("""
        DROP TABLE IF EXISTS results;
        DROP TABLE IF EXISTS runs;
        DROP TABLE IF EXISTS scenarios;
    """)
    conn.commit()
    conn.executescript(_DDL)
    conn.commit()
    return conn
