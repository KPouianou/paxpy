"""Batch runner for cheap heuristics against the bench scenario database.

Much faster than the paxpy runner — no temp git repos, pure in-memory
string comparison.  Writes results to heuristic_runs / heuristic_results.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from bench.db.schema import DEFAULT_DB, init_db
from bench.run.heuristics import HEURISTICS


def run_heuristics(
    db_path: Path = DEFAULT_DB,
    bucket: str = "correctness",
    notes: str | None = None,
) -> int:
    """Run all heuristics on every scenario in *bucket*.

    Parameters
    ----------
    bucket:
        Which scenario bucket to process.  Default 'correctness' (skip
        performance scenarios — they're for timing, not detection).
    notes:
        Optional free-text stored with the run record.

    Returns
    -------
    heuristic_run_id of the newly-created run.
    """
    try:
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            SpinnerColumn,
            TextColumn,
            TimeElapsedColumn,
        )
        _rich = True
    except ImportError:
        _rich = False

    conn = init_db(db_path)

    # Create run record
    run_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO heuristic_runs (run_at, notes) VALUES (?, ?)",
        (run_at, notes),
    )
    conn.commit()
    run_id: int = cur.lastrowid  # type: ignore[assignment]

    # Fetch scenarios
    if bucket == "all":
        rows = conn.execute(
            "SELECT * FROM scenarios WHERE bucket != 'performance'"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM scenarios WHERE bucket = ?", (bucket,)
        ).fetchall()

    total = len(rows)
    if total == 0:
        print(f"No scenarios found for bucket={bucket!r}. Run paxpy-bench seed first.")
        return run_id

    heuristic_names = list(HEURISTICS)
    batch: list[tuple] = []
    BATCH_SIZE = 200

    def _flush(batch: list[tuple]) -> None:
        conn.executemany(
            """INSERT OR IGNORE INTO heuristic_results
               (heuristic_run_id, scenario_id, heuristic, detected, is_tp, is_fp, is_fn, is_tn)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            batch,
        )
        conn.commit()

    def _process(row) -> list[tuple]:
        base   = json.loads(row["base_source"])
        a_src  = json.loads(row["branch_a_source"])
        b_src  = json.loads(row["branch_b_source"])
        expected = bool(row["expected_conflict"])
        scen_id  = row["id"]
        rows_out = []
        for name, fn in HEURISTICS.items():
            detected = fn(base, a_src, b_src)
            tp = int(expected and detected)
            fp = int(not expected and detected)
            fn_ = int(expected and not detected)
            tn = int(not expected and not detected)
            rows_out.append((run_id, scen_id, name, int(detected), tp, fp, fn_, tn))
        return rows_out

    if _rich:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
        ) as progress:
            task = progress.add_task(
                f"Heuristics  ({len(heuristic_names)} methods × {total} scenarios)", total=total
            )
            for row in rows:
                batch.extend(_process(row))
                if len(batch) >= BATCH_SIZE * len(heuristic_names):
                    _flush(batch)
                    batch = []
                progress.advance(task)
    else:
        for i, row in enumerate(rows, 1):
            batch.extend(_process(row))
            if len(batch) >= BATCH_SIZE * len(heuristic_names):
                _flush(batch)
                batch = []
            if i % 500 == 0:
                print(f"  {i}/{total}")

    if batch:
        _flush(batch)

    print(f"\nHeuristic run #{run_id} complete — {total} scenarios × {len(heuristic_names)} methods.")
    return run_id
