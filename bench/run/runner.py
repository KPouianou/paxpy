"""Scenario runner: executes the paxpy pipeline on each scenario and records results.

For each scenario the runner:
  1. Creates a fresh git repository in a temporary directory
  2. Writes base source, commits, creates branch-a, writes A source, commits,
     returns to main, creates branch-b, writes B source, commits
  3. Runs the full paxpy pipeline (parse_diffs → build_index → build_sdg → detect)
  4. Measures sdg_build_ms, detection_ms, total_ms, node/edge counts
  5. Computes is_tp/fp/fn/tn against the expected_conflict ground truth
  6. Writes the result row to the database

The live terminal display shows a progress bar and an updating stats table
(TP/FP/FN/TN and running F1 per conflict type) using rich.Live.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import git
from rich.console import Console, Group
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.text import Text

from bench.db.schema import DEFAULT_DB, init_db

console = Console()

# ---------------------------------------------------------------------------
# Git repo helpers (mirrors integration test conftest.py exactly)
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "bench-runner")
        cw.set_value("user", "email", "bench@paxpy.local")
    placeholder = tmp_path / ".gitkeep"
    placeholder.write_text("", encoding="utf-8")
    repo.git.add(".")
    repo.git.commit("-m", "init")
    return repo


def _write_sources(repo_path: Path, sources: dict[str, str]) -> None:
    for filename, source in sources.items():
        fpath = repo_path / filename
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(source, encoding="utf-8")


def _commit_all(repo: git.Repo, message: str) -> None:
    repo.git.add(".")
    repo.git.commit("-m", message)


def _setup_scenario_repo(
    tmp_path: Path,
    base_source: dict[str, str],
    branch_a_source: dict[str, str],
    branch_b_source: dict[str, str],
) -> git.Repo:
    """Write base/A/B sources into a fresh git repo with correct branch topology."""
    repo = _init_repo(tmp_path)

    # Base commit on main
    _write_sources(tmp_path, base_source)
    _commit_all(repo, "base")

    # Branch A
    repo.git.checkout("-b", "branch-a")
    _write_sources(tmp_path, branch_a_source)
    _commit_all(repo, "branch-a changes")

    # Branch B (off main)
    repo.git.checkout("main")
    repo.git.checkout("-b", "branch-b")
    _write_sources(tmp_path, branch_b_source)
    _commit_all(repo, "branch-b changes")

    # Leave working tree on branch-b (build_index reads filesystem)
    return repo


# ---------------------------------------------------------------------------
# Pipeline execution
# ---------------------------------------------------------------------------


def _run_pipeline(repo_path: Path, depth: int, max_call_hops: int) -> dict:
    """Run the full paxpy pipeline and return a metrics dict."""
    from paxpy.detector import count_call_hops, detect, filter_by_call_hops
    from paxpy.diff_parser import parse_diffs
    from paxpy.indexer import build_index
    from paxpy.sdg_builder import build_sdg

    t0 = time.perf_counter()

    diff   = parse_diffs(repo_path, "main", "branch-a", "branch-b")
    index  = build_index(repo_path)

    t1 = time.perf_counter()
    sdg    = build_sdg(diff, index, depth=depth)
    sdg_ms = int((time.perf_counter() - t1) * 1000)

    node_count = len(sdg.nodes)
    edge_count = (
        sum(len(v) for v in sdg.data_edges.values())
        + sum(len(v) for v in sdg.call_edges.values())
        + sum(len(v) for v in sdg.control_edges.values())
    )

    t2 = time.perf_counter()
    paths  = detect(sdg)
    paths  = filter_by_call_hops(paths, sdg, max_hops=max_call_hops)
    det_ms = int((time.perf_counter() - t2) * 1000)

    total_ms = int((time.perf_counter() - t0) * 1000)

    detected       = len(paths) > 0
    directions     = [p.direction for p in paths]
    tiers          = [p.tier for p in paths]
    conflict_types = [p.conflict_type.value for p in paths]

    # Shortest call-hop count across surviving paths (actual inter-procedural depth).
    # Uses count_call_hops() so the value reflects call-graph edges, not raw node hops.
    call_depth_actual: int | None = None
    if paths:
        hops = [count_call_hops(p.path_nodes, sdg) for p in paths if p.path_nodes]
        call_depth_actual = min(hops) if hops else None

    return dict(
        detected=detected,
        path_count=len(paths),
        directions=json.dumps(directions),
        tiers=json.dumps(tiers),
        conflict_types=json.dumps(conflict_types),
        sdg_node_count=node_count,
        sdg_edge_count=edge_count,
        call_depth_actual=call_depth_actual,
        sdg_build_ms=sdg_ms,
        detection_ms=det_ms,
        total_ms=total_ms,
        error=None,
    )


def _classify(expected_conflict: bool, detected: bool) -> dict[str, int]:
    tp = int(expected_conflict and detected)
    fp = int((not expected_conflict) and detected)
    fn = int(expected_conflict and (not detected))
    tn = int((not expected_conflict) and (not detected))
    return dict(is_tp=tp, is_fp=fp, is_fn=fn, is_tn=tn)


def _run_one(scenario: sqlite3.Row, depth: int, max_call_hops: int) -> dict:
    """Execute paxpy on one scenario and return the full result dict."""
    base   = json.loads(scenario["base_source"])
    a_src  = json.loads(scenario["branch_a_source"])
    b_src  = json.loads(scenario["branch_b_source"])
    expected = bool(scenario["expected_conflict"])

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            _setup_scenario_repo(tmp_path, base, a_src, b_src)
            metrics = _run_pipeline(tmp_path, depth, max_call_hops)
        except Exception:  # noqa: BLE001
            err = traceback.format_exc(limit=6)
            return dict(
                detected=None, path_count=None,
                directions=None, tiers=None, conflict_types=None,
                sdg_node_count=None, sdg_edge_count=None,
                call_depth_actual=None,
                sdg_build_ms=None, detection_ms=None, total_ms=None,
                error=err[:2000],
                is_tp=0, is_fp=0, is_fn=0, is_tn=0,
            )

    clf = _classify(expected, metrics["detected"])
    return {**metrics, **clf}


# ---------------------------------------------------------------------------
# Live display helpers
# ---------------------------------------------------------------------------

_TYPE_COLOURS = {
    "DATA_FLOW":           "cyan",
    "CONFLUENCE":          "magenta",
    "OVERRIDE_ASSIGNMENT": "yellow",
    "CONTROL_DEPENDENCY":  "blue",
    "NEGATIVE":            "white",
    "ADVERSARIAL":         "red",
    "PERFORMANCE":         "green",
}


def _f1(tp: int, fp: int, fn: int) -> float:
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return 2 * p * r / (p + r) if (p + r) else 0.0


def _bar(value: float, width: int = 16) -> str:
    filled = round(value * width)
    return "█" * filled + "░" * (width - filled)


def _f1_colour(f1: float) -> str:
    if f1 >= 0.80:
        return "green"
    if f1 >= 0.50:
        return "yellow"
    return "red"


def _make_stats_table(stats: dict[str, dict[str, int]], errors: int) -> Table:
    t = Table(show_header=True, header_style="bold", box=None, padding=(0, 1))
    t.add_column("Type",   style="bold", min_width=20)
    t.add_column("TP",   justify="right", style="green")
    t.add_column("FP",   justify="right", style="red")
    t.add_column("FN",   justify="right", style="yellow")
    t.add_column("TN",   justify="right", style="dim")
    t.add_column("F1",   justify="right", min_width=18)

    for ct, s in sorted(stats.items()):
        f1  = _f1(s["tp"], s["fp"], s["fn"])
        col = _f1_colour(f1)
        bar = _bar(f1)
        t.add_row(
            f"[{_TYPE_COLOURS.get(ct, 'white')}]{ct}[/]",
            str(s["tp"]), str(s["fp"]), str(s["fn"]), str(s["tn"]),
            f"[{col}]{bar}[/] [bold {col}]{f1:.2f}[/]",
        )

    if errors:
        t.add_row("[dim]errors / crashes[/]", "", "", "", "", f"[red]{errors}[/]")

    return t


def _make_recent_panel(recent: list[str]) -> Panel:
    body = "\n".join(recent[-6:]) if recent else "[dim]waiting…[/]"
    return Panel(body, title="[dim]recent[/]", border_style="dim", padding=(0, 1))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection,
    bucket: str,
    depth: int,
    scenario_filter: str | None,
    notes: str | None,
    max_call_hops: int = 2,
) -> int:
    """Insert a new run record and return its id."""
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent.parent,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        git_commit = None

    run_notes = f"max_call_hops={max_call_hops}" + (f"; {notes}" if notes else "")

    cur = conn.execute(
        """INSERT INTO runs (bucket, run_at, git_commit, depth, scenario_filter, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (bucket, datetime.now(timezone.utc).isoformat(), git_commit, depth, scenario_filter, run_notes),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def fetch_pending(
    conn: sqlite3.Connection,
    run_id: int,
    bucket: str,
    scenario_filter: str | None,
) -> list[sqlite3.Row]:
    """Return scenarios not yet run in this run_id."""
    bucket_clause = "" if bucket == "all" else "AND s.bucket = ?"
    filter_clause = "" if not scenario_filter else "AND s.name LIKE ?"

    sql = f"""
        SELECT s.*
        FROM scenarios s
        WHERE 1=1
          {bucket_clause}
          {filter_clause}
          AND s.id NOT IN (
              SELECT scenario_id FROM results WHERE run_id = ?
          )
        ORDER BY s.bucket, s.complexity_tier, s.id
    """
    args: list = []
    if bucket != "all":
        args.append(bucket)
    if scenario_filter:
        args.append(f"{scenario_filter}%")
    args.append(run_id)

    return conn.execute(sql, args).fetchall()


def record_result(
    conn: sqlite3.Connection,
    run_id: int,
    scenario_id: int,
    result: dict,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO results (
               run_id, scenario_id,
               detected, path_count, directions, tiers, conflict_types,
               is_tp, is_fp, is_fn, is_tn,
               sdg_node_count, sdg_edge_count, call_depth_actual,
               sdg_build_ms, detection_ms, total_ms, error
           ) VALUES (
               :run_id, :scenario_id,
               :detected, :path_count, :directions, :tiers, :conflict_types,
               :is_tp, :is_fp, :is_fn, :is_tn,
               :sdg_node_count, :sdg_edge_count, :call_depth_actual,
               :sdg_build_ms, :detection_ms, :total_ms, :error
           )""",
        {"run_id": run_id, "scenario_id": scenario_id, **result},
    )
    conn.commit()


def run_all(
    db_path: Path = DEFAULT_DB,
    bucket: str = "all",
    depth: int = 3,
    max_call_hops: int = 2,
    scenario_filter: str | None = None,
    notes: str | None = None,
) -> int:
    """Run paxpy on all pending scenarios, display live results, return run_id."""
    conn   = init_db(db_path)
    run_id = create_run(conn, bucket, depth, scenario_filter, notes, max_call_hops)

    scenarios = fetch_pending(conn, run_id, bucket, scenario_filter)
    if not scenarios:
        console.print("[yellow]No pending scenarios found.[/]")
        conn.close()
        return run_id

    total  = len(scenarios)
    done   = 0
    errors = 0
    recent: list[str] = []
    stats:  dict[str, dict[str, int]] = {}

    progress = Progress(
        SpinnerColumn(),
        TextColumn(f"  [bold]run #{run_id}[/]  {bucket}  depth={depth}  max-hops={max_call_hops}"),
        BarColumn(bar_width=44),
        MofNCompleteColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=False,
    )
    task = progress.add_task("running", total=total)

    def make_display() -> Group:
        return Group(
            Padding(progress, (0, 0, 1, 0)),
            _make_stats_table(stats, errors),
            Padding(Text(""), (0,)),
            _make_recent_panel(recent),
        )

    with Live(make_display(), refresh_per_second=6, console=console, vertical_overflow="visible"):
        for row in scenarios:
            ct_key = row["conflict_type"] or (
                "ADVERSARIAL" if row["bucket"] == "adversarial"
                else "PERFORMANCE" if row["bucket"] == "performance"
                else "NEGATIVE"
            )
            if ct_key not in stats:
                stats[ct_key] = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}

            result = _run_one(row, depth, max_call_hops)
            record_result(conn, run_id, row["id"], result)

            if result["error"]:
                errors += 1
                icon = "[red]✗[/]"
            elif result["is_tp"] or result["is_tn"]:
                icon = "[green]✓[/]"
            else:
                icon = "[yellow]✗[/]"

            stats[ct_key]["tp"] += result["is_tp"]
            stats[ct_key]["fp"] += result["is_fp"]
            stats[ct_key]["fn"] += result["is_fn"]
            stats[ct_key]["tn"] += result["is_tn"]

            done += 1
            progress.advance(task)

            label = row["name"][:52]
            ms    = result.get("total_ms") or 0
            recent.append(f"  {icon}  {label:<54} [dim]{ms:>6}ms[/]")

    conn.close()
    return run_id
