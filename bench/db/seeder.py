"""Idempotent seeder: generates all scenarios and inserts them into the database.

Uses INSERT OR IGNORE so running twice is safe — existing scenarios are never
overwritten, preserving stable ground-truth labels across runs.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn

from bench.db.schema import DEFAULT_DB, init_db
from bench.generate.adversarial import all_adversarial
from bench.generate.parametric import ScenarioSpec, all_correctness_params, generate_scenario
from bench.generate.performance import all_performance

console = Console()


def _to_row(spec: ScenarioSpec) -> dict:
    return dict(
        name=spec.name,
        bucket=spec.bucket,
        conflict_type=spec.conflict_type,
        call_depth=spec.call_depth if spec.call_depth >= 0 else None,
        fan_out=spec.fan_out if spec.fan_out >= 0 else None,
        file_count=spec.file_count,
        name_ambiguity=spec.name_ambiguity,
        positive=1 if spec.positive else 0,
        random_seed=spec.random_seed if spec.random_seed >= 0 else None,
        complexity_tier=spec.complexity_tier,
        base_source=json.dumps(spec.base_source),
        branch_a_source=json.dumps(spec.branch_a_source),
        branch_b_source=json.dumps(spec.branch_b_source),
        expected_conflict=1 if spec.expected_conflict else 0,
        expected_direction=spec.expected_direction,
        expected_tier=spec.expected_tier,
        label_rationale=spec.label_rationale,
        mutation_a=spec.mutation_a,
        mutation_b=spec.mutation_b,
        hypothesis=spec.hypothesis or None,
        verified=0,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _insert_batch(conn: sqlite3.Connection, specs: list[ScenarioSpec]) -> tuple[int, int]:
    """Insert specs with INSERT OR IGNORE. Returns (inserted, skipped)."""
    sql = """
        INSERT OR IGNORE INTO scenarios (
            name, bucket, conflict_type, call_depth, fan_out, file_count,
            name_ambiguity, positive, random_seed, complexity_tier,
            base_source, branch_a_source, branch_b_source,
            expected_conflict, expected_direction, expected_tier,
            label_rationale, mutation_a, mutation_b, hypothesis,
            verified, created_at
        ) VALUES (
            :name, :bucket, :conflict_type, :call_depth, :fan_out, :file_count,
            :name_ambiguity, :positive, :random_seed, :complexity_tier,
            :base_source, :branch_a_source, :branch_b_source,
            :expected_conflict, :expected_direction, :expected_tier,
            :label_rationale, :mutation_a, :mutation_b, :hypothesis,
            :verified, :created_at
        )
    """
    inserted = 0
    for spec in specs:
        cur = conn.execute(sql, _to_row(spec))
        inserted += cur.rowcount
    conn.commit()
    skipped = len(specs) - inserted
    return inserted, skipped


def seed(db_path: Path = DEFAULT_DB, bucket: str = "all") -> None:
    """Generate and insert all scenarios into the database.

    Args:
        db_path: Path to the SQLite database file.
        bucket:  Which bucket(s) to seed. One of "correctness", "adversarial",
                 "performance", or "all".
    """
    conn = init_db(db_path)

    buckets_to_run = (
        ["correctness", "adversarial", "performance"]
        if bucket == "all"
        else [bucket]
    )

    total_inserted = 0
    total_skipped  = 0

    for bkt in buckets_to_run:
        if bkt == "correctness":
            specs = _generate_correctness()
        elif bkt == "adversarial":
            specs = all_adversarial()
        elif bkt == "performance":
            specs = all_performance()
        else:
            console.print(f"[red]Unknown bucket: {bkt}[/]")
            continue

        with Progress(
            SpinnerColumn(),
            TextColumn(f"  [bold]{bkt}[/]"),
            BarColumn(bar_width=40),
            MofNCompleteColumn(),
            TextColumn("[dim]{task.fields[status]}[/]"),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task(bkt, total=len(specs), status="")
            batch_size = 50
            for i in range(0, len(specs), batch_size):
                batch = specs[i : i + batch_size]
                ins, skip = _insert_batch(conn, batch)
                total_inserted += ins
                total_skipped  += skip
                progress.advance(task, advance=len(batch))
                progress.update(task, status=f"+{total_inserted} new, {total_skipped} skipped")

    conn.close()

    console.print()
    console.print(
        f"  [green]✓[/] Seeding complete — "
        f"[bold]{total_inserted}[/] new, [dim]{total_skipped} already present[/]"
    )


def _generate_correctness() -> list[ScenarioSpec]:
    params = all_correctness_params()
    specs  = []
    for p in params:
        try:
            specs.append(generate_scenario(**p))
        except Exception as exc:  # noqa: BLE001
            console.print(f"  [yellow]⚠ generation failed for {p}: {exc}[/]")
    return specs
