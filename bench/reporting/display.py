"""Terminal and markdown report generator.

Answers the six key questions in order:
  1. Verdict       — headline precision / recall / F1
  2. By type × tier — where does it work?
  3. Breakpoints   — at what complexity does recall collapse?
  4. Adversarial   — which hard cases pass/fail?
  5. Performance   — how does runtime scale?
  6. Honest gaps   — what we did not test
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bench.db.schema import DEFAULT_DB, connect

console = Console()


# ---------------------------------------------------------------------------
# Data access helpers
# ---------------------------------------------------------------------------


def _fetch_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def _fetch_results(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT r.*, s.conflict_type, s.complexity_tier, s.bucket,
                  s.call_depth, s.fan_out, s.file_count, s.name_ambiguity,
                  s.positive, s.expected_conflict, s.hypothesis, s.name AS scenario_name
           FROM results r
           JOIN scenarios s ON s.id = r.scenario_id
           WHERE r.run_id = ?""",
        (run_id,),
    ).fetchall()


def _latest_run_id(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def _safe_div(num: float, den: float) -> float:
    return num / den if den else 0.0


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = _safe_div(tp, tp + fp)
    r = _safe_div(tp, tp + fn)
    f1 = _safe_div(2 * p * r, p + r)
    return p, r, f1


def _bar(value: float, width: int = 20, char: str = "█", empty: str = "░") -> str:
    filled = round(value * width)
    return char * filled + empty * (width - filled)


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _colour_f1(f1: float) -> str:
    if f1 >= 0.80:
        return "green"
    if f1 >= 0.50:
        return "yellow"
    return "red"


def _colour_p(p: float) -> str:
    if p >= 0.85:
        return "green"
    if p >= 0.60:
        return "yellow"
    return "red"


# ---------------------------------------------------------------------------
# Section 1 — Headline verdict
# ---------------------------------------------------------------------------


def _headline(results: list[sqlite3.Row], run: sqlite3.Row) -> Panel:
    tp = fp = fn = tn = errors = 0
    for r in results:
        if r["bucket"] == "performance":
            continue
        tp += r["is_tp"] or 0
        fp += r["is_fp"] or 0
        fn += r["is_fn"] or 0
        tn += r["is_tn"] or 0
        errors += 1 if r["error"] else 0

    total = tp + fp + fn + tn
    p, recall, f1 = _prf(tp, fp, fn)

    t = Table.grid(padding=(0, 2))
    t.add_column(min_width=18)
    t.add_column(min_width=36)
    t.add_column()

    def metric_row(label: str, value: float, colour: str) -> None:
        t.add_row(
            f"[bold]{label}[/]",
            f"[{colour}]{_bar(value)}[/]",
            f"[bold {colour}]{_pct(value)}[/]",
        )

    metric_row("Precision", p, _colour_p(p))
    metric_row("Recall", recall, _colour_f1(recall))
    metric_row("F1", f1, _colour_f1(f1))
    t.add_row("", "", "")
    t.add_row("[dim]Scenarios[/]", f"[dim]{total} evaluated ({errors} errors)[/]", "")
    t.add_row(
        "[dim]Run[/]", f"[dim]#{run['id']} · {run['run_at'][:19]} · depth={run['depth']}[/]", ""
    )
    if run["git_commit"]:
        t.add_row("[dim]Commit[/]", f"[dim]{run['git_commit']}[/]", "")

    t.add_row("", "", "")
    t.add_row(
        "[dim]Baseline[/]",
        "[dim]Santos de Jesus et al. ICSE 2024 (Java) — F1 = 0.50[/]",
        "",
    )

    return Panel(t, title="[bold]paxpy benchmark — verdict[/]", border_style="bright_blue")


# ---------------------------------------------------------------------------
# Section 2 — By conflict type × complexity tier
# ---------------------------------------------------------------------------


_TIERS_ORDER = ["simple", "moderate", "complex", "adversarial", "performance"]
_TYPES_ORDER = ["DATA_FLOW", "CONFLUENCE", "OVERRIDE_ASSIGNMENT", "CONTROL_DEPENDENCY"]


def _breakdown_table(results: list[sqlite3.Row]) -> Table:
    # Build: {conflict_type: {tier: {tp,fp,fn,tn}}}
    data: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    )
    for r in results:
        ct = r["conflict_type"] or "NEGATIVE"
        tier = r["complexity_tier"] or "?"
        data[ct][tier]["tp"] += r["is_tp"] or 0
        data[ct][tier]["fp"] += r["is_fp"] or 0
        data[ct][tier]["fn"] += r["is_fn"] or 0
        data[ct][tier]["tn"] += r["is_tn"] or 0

    tiers_present = [t for t in _TIERS_ORDER if any(t in data[ct] for ct in data)]

    t = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
    t.add_column("Conflict type", min_width=22)
    for tier in tiers_present:
        t.add_column(tier.capitalize(), justify="center", min_width=10)

    for ct in _TYPES_ORDER:
        if ct not in data:
            continue
        row_cells: list[str] = [f"[bold]{ct}[/]"]
        for tier in tiers_present:
            s = data[ct].get(tier, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
            _, _, f1 = _prf(s["tp"], s["fp"], s["fn"])
            col = _colour_f1(f1)
            n = s["tp"] + s["fp"] + s["fn"] + s["tn"]
            row_cells.append(f"[{col}]{f1:.2f}[/] [dim]n={n}[/]" if n else "[dim]—[/]")
        t.add_row(*row_cells)

    return Panel(t, title="[bold]F1 by conflict type × complexity tier[/]", border_style="dim")


# ---------------------------------------------------------------------------
# Section 3 — Breakpoints
# ---------------------------------------------------------------------------


def _breakpoint_chart(results: list[sqlite3.Row]) -> Panel:
    # Recall vs call_depth
    by_depth: dict[int, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0})
    for r in results:
        if r["bucket"] != "correctness" or not r["positive"]:
            continue
        cd = r["call_depth"]
        if cd is None:
            continue
        by_depth[cd]["tp"] += r["is_tp"] or 0
        by_depth[cd]["fn"] += r["is_fn"] or 0

    # Precision vs name_ambiguity
    by_na: dict[int, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fp": 0})
    for r in results:
        if r["bucket"] != "correctness":
            continue
        na = r["name_ambiguity"]
        if na is None:
            continue
        by_na[na]["tp"] += r["is_tp"] or 0
        by_na[na]["fp"] += r["is_fp"] or 0

    lines = Text()
    lines.append("  Recall vs call depth\n\n", style="bold")
    for cd in sorted(by_depth):
        s = by_depth[cd]
        recall = _safe_div(s["tp"], s["tp"] + s["fn"])
        col = _colour_f1(recall)
        n = s["tp"] + s["fn"]
        lines.append(f"  CD={cd:<3} ")
        lines.append(_bar(recall, width=24), style=col)
        lines.append(f"  {_pct(recall)}  [dim]n={n}[/]\n")

    lines.append("\n  Precision vs name ambiguity (false positive pressure)\n\n", style="bold")
    for na in sorted(by_na):
        s = by_na[na]
        prec = _safe_div(s["tp"], s["tp"] + s["fp"])
        col = _colour_p(prec)
        n = s["tp"] + s["fp"]
        lines.append(f"  NA={na:<3} ")
        lines.append(_bar(prec, width=24), style=col)
        lines.append(f"  {_pct(prec)}  [dim]n={n}[/]\n")

    return Panel(lines, title="[bold]Where it breaks[/]", border_style="dim")


# ---------------------------------------------------------------------------
# Section 4 — Adversarial
# ---------------------------------------------------------------------------


def _adversarial_table(results: list[sqlite3.Row]) -> Panel:
    adv = [r for r in results if r["bucket"] == "adversarial"]
    if not adv:
        return Panel("[dim]No adversarial results.[/]", title="Adversarial", border_style="dim")

    t = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
    t.add_column("Scenario", min_width=36)
    t.add_column("Expected", justify="center", min_width=12)
    t.add_column("Result", justify="center", min_width=10)
    t.add_column("Verdict", justify="center", min_width=10)
    t.add_column("Notes", min_width=26)

    for r in adv:
        exp = "conflict" if r["expected_conflict"] else "clean"
        if r["error"]:
            result_str = "[red]CRASH[/]"
            verdict = "[red]✗ UNEXPECTED[/]"
            note = r["error"][:40].replace("\n", " ")
        else:
            detected = bool(r["detected"])
            result_str = "conflict" if detected else "clean"
            correct = detected == bool(r["expected_conflict"])
            # For adversarial: "correct" means "behaved as hypothesised"
            verdict = "[green]✓[/]" if correct else "[yellow]✗[/]"
            cts = json.loads(r["conflict_types"] or "[]")
            note = ", ".join(cts) if cts else "[dim]—[/]"

        t.add_row(
            r["scenario_name"].replace("adversarial_", ""),
            exp,
            result_str,
            verdict,
            note,
        )

    return Panel(t, title="[bold]Adversarial cases[/]", border_style="dim")


# ---------------------------------------------------------------------------
# Section 5 — Performance
# ---------------------------------------------------------------------------


def _performance_chart(results: list[sqlite3.Row]) -> Panel:
    perf = [r for r in results if r["bucket"] == "performance" and not r["error"]]
    if not perf:
        return Panel("[dim]No performance results.[/]", title="Performance", border_style="dim")

    # Group by node count bucket
    buckets: dict[str, list[int]] = defaultdict(list)
    for r in perf:
        n = r["sdg_node_count"] or 0
        if n < 100:
            bkt = "  <100 nodes"
        elif n < 500:
            bkt = " 100–499 nodes"
        elif n < 2000:
            bkt = " 500–1999 nodes"
        elif n < 8000:
            bkt = "2000–7999 nodes"
        elif n < 25000:
            bkt = "8000–24999 nodes"
        else:
            bkt = "25000+ nodes"
        if r["total_ms"] is not None:
            buckets[bkt].append(r["total_ms"])

    # Find max for bar scaling
    all_ms = [ms for mss in buckets.values() for ms in mss]
    max_ms = max(all_ms) if all_ms else 1

    lines = Text()
    lines.append("  Runtime vs SDG node count  (total pipeline ms)\n\n", style="bold")
    for bkt in sorted(buckets):
        mss = buckets[bkt]
        avg = sum(mss) / len(mss)
        bar = _bar(avg / max_ms, width=36, char="▪", empty=" ")
        col = "green" if avg < 500 else ("yellow" if avg < 3000 else "red")
        lines.append(f"  {bkt}  ")
        lines.append(f"{bar}", style=col)
        lines.append(f"  avg [bold]{avg:.0f}ms[/]  [dim]n={len(mss)}[/]\n")

    lines.append("\n")

    # O(n) estimate
    if len(buckets) >= 2:
        lines.append(
            "  [dim]Scaling hint: compare avg ms across size groups to estimate growth.[/]\n"
        )

    # Timeout / error count
    n_err = sum(1 for r in results if r["bucket"] == "performance" and r["error"])
    if n_err:
        lines.append(f"\n  [red]⚠ {n_err} performance scenarios errored / timed out[/]\n")

    return Panel(lines, title="[bold]Performance[/]", border_style="dim")


# ---------------------------------------------------------------------------
# Section 6 — Honest gaps
# ---------------------------------------------------------------------------

_GAPS_TEXT = """\
  What this benchmark does NOT tell you

  • Real-world recall: we cannot measure what paxpy misses on actual codebases
    without labeled ground truth from real merge conflicts. Synthetic recall may
    overestimate real recall if real conflicts differ structurally from generated ones.

  • Distribution match: synthetic complexity levels may not reflect the distribution
    of real conflicts. Real conflicts may cluster at depths 2–3 or be structurally
    unlike the parametric patterns used here.

  • Python-specific modes: decorator-chain rebinding, generator exhaustion,
    comprehension scoping, and protocol drift are deliberately excluded from positive
    scenarios (known limitations). Adversarial results show actual failure behaviour.

  • Cross-file precision: name-based call resolution over-approximates. Scenarios with
    name_ambiguity>0 stress this; real codebases with common function names will see
    similar FP pressure.

  Next step to close gaps: mine merge history from requests/flask, run paxpy on
  all merges that touch Python functions, manually review the flags → real-world
  precision estimate."""


def _gaps_panel() -> Panel:
    return Panel(
        Text.from_markup(_GAPS_TEXT),
        title="[bold]Honest gaps[/]",
        border_style="dim",
    )


# ---------------------------------------------------------------------------
# Public terminal display
# ---------------------------------------------------------------------------


def show(db_path: Path = DEFAULT_DB, run_id: int | None = None) -> None:
    """Print the full benchmark report to the terminal."""
    conn = connect(db_path)

    if run_id is None:
        run_id = _latest_run_id(conn)
    if run_id is None:
        console.print("[red]No runs found in database.[/]")
        return

    run = _fetch_run(conn, run_id)
    results = _fetch_results(conn, run_id)
    conn.close()

    if not run:
        console.print(f"[red]Run #{run_id} not found.[/]")
        return

    console.print()
    console.print(_headline(results, run))
    console.print()
    console.print(_breakdown_table(results))
    console.print()
    console.print(_breakpoint_chart(results))
    console.print()
    console.print(_adversarial_table(results))
    console.print()
    console.print(_performance_chart(results))
    console.print()
    console.print(_gaps_panel())
    console.print()


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------


def _md_bar(value: float, width: int = 20) -> str:
    filled = round(value * width)
    return "█" * filled + "░" * (width - filled)


def write_markdown(
    db_path: Path = DEFAULT_DB,
    run_id: int | None = None,
    output_path: Path | None = None,
) -> Path:
    """Write a human-readable markdown report and return the output path."""
    conn = connect(db_path)
    if run_id is None:
        run_id = _latest_run_id(conn)
    run = _fetch_run(conn, run_id)  # type: ignore[arg-type]
    results = _fetch_results(conn, run_id)  # type: ignore[arg-type]
    conn.close()

    if output_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = Path(__file__).parent.parent / "reports" / f"run_{run_id}_{ts}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []

    def h(n: int, text: str) -> None:
        lines.append(f"{'#' * n} {text}\n")

    def p(text: str) -> None:
        lines.append(text + "\n")

    def blank() -> None:
        lines.append("")

    # Header
    h(1, "paxpy Benchmark Report")
    p(f"**Run #{run_id}** · {run['run_at'][:19]} UTC · depth={run['depth']}")
    if run["git_commit"]:
        p(f"**Commit:** `{run['git_commit']}`")
    blank()

    # 1. Verdict
    h(2, "1. Headline Verdict")
    tp = fp = fn = tn = errors = 0
    for r in results:
        if r["bucket"] == "performance":
            continue
        tp += r["is_tp"] or 0
        fp += r["is_fp"] or 0
        fn += r["is_fn"] or 0
        tn += r["is_tn"] or 0
        errors += 1 if r["error"] else 0
    prec, recall, f1 = _prf(tp, fp, fn)

    p("```")
    p(f"Precision   {_md_bar(prec)}  {_pct(prec)}")
    p(f"Recall      {_md_bar(recall)}  {_pct(recall)}")
    p(f"F1          {_md_bar(f1)}  {_pct(f1)}")
    p("")
    p(f"Scenarios: {tp + fp + fn + tn} evaluated  ({errors} errors/crashes)")
    p("Baseline:  Santos de Jesus et al. ICSE 2024 (Java) — F1 = 50.0%")
    p("```")
    blank()

    # 2. Breakdown
    h(2, "2. F1 by Conflict Type × Complexity Tier")
    data: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    )
    tiers_seen: set[str] = set()
    for r in results:
        ct = r["conflict_type"] or "NEGATIVE"
        tier = r["complexity_tier"] or "?"
        tiers_seen.add(tier)
        data[ct][tier]["tp"] += r["is_tp"] or 0
        data[ct][tier]["fp"] += r["is_fp"] or 0
        data[ct][tier]["fn"] += r["is_fn"] or 0
        data[ct][tier]["tn"] += r["is_tn"] or 0
    tiers_present = [t for t in _TIERS_ORDER if t in tiers_seen]

    header = "| Conflict Type | " + " | ".join(t.capitalize() for t in tiers_present) + " |"
    sep = "|---|" + "---|" * len(tiers_present)
    lines.append(header)
    lines.append(sep)
    for ct in _TYPES_ORDER:
        if ct not in data:
            continue
        cells = [ct]
        for tier in tiers_present:
            s = data[ct].get(tier, {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
            _, _, f1v = _prf(s["tp"], s["fp"], s["fn"])
            n = s["tp"] + s["fp"] + s["fn"] + s["tn"]
            cells.append(f"{f1v:.2f} (n={n})" if n else "—")
        lines.append("| " + " | ".join(cells) + " |")
    blank()
    blank()

    # 3. Breakpoints
    h(2, "3. Where It Breaks")
    by_depth: dict[int, dict[str, int]] = defaultdict(lambda: {"tp": 0, "fn": 0})
    for r in results:
        if r["bucket"] != "correctness" or not r["positive"]:
            continue
        cd = r["call_depth"]
        if cd is None:
            continue
        by_depth[cd]["tp"] += r["is_tp"] or 0
        by_depth[cd]["fn"] += r["is_fn"] or 0

    p("**Recall vs call depth:**")
    p("```")
    for cd in sorted(by_depth):
        s = by_depth[cd]
        rec = _safe_div(s["tp"], s["tp"] + s["fn"])
        n = s["tp"] + s["fn"]
        p(f"  CD={cd:<3}  {_md_bar(rec, 24)}  {_pct(rec)}  (n={n})")
    p("```")
    blank()

    # 4. Adversarial
    h(2, "4. Adversarial Cases")
    adv = [r for r in results if r["bucket"] == "adversarial"]
    if adv:
        lines.append("| Scenario | Expected | Result | Verdict | Notes |")
        lines.append("|---|---|---|---|---|")
        for r in adv:
            exp = "conflict" if r["expected_conflict"] else "clean"
            if r["error"]:
                res, verdict, note = "CRASH", "✗ UNEXPECTED", r["error"][:50]
            else:
                det = bool(r["detected"])
                res = "conflict" if det else "clean"
                correct = det == bool(r["expected_conflict"])
                cts = json.loads(r["conflict_types"] or "[]")
                verdict = "✓" if correct else "✗"
                note = ", ".join(cts) or "—"
            sname = r["scenario_name"].replace("adversarial_", "")
            lines.append(f"| {sname} | {exp} | {res} | {verdict} | {note} |")
        blank()
    else:
        p("_No adversarial results._")
        blank()

    # 5. Performance
    h(2, "5. Performance")
    perf = [r for r in results if r["bucket"] == "performance" and not r["error"]]
    if perf:
        buckets_ms: dict[str, list[int]] = defaultdict(list)
        for r in perf:
            n = r["sdg_node_count"] or 0
            bkt = (
                "<100"
                if n < 100
                else "100–499"
                if n < 500
                else "500–1999"
                if n < 2000
                else "2000–7999"
                if n < 8000
                else "8000–24999"
                if n < 25000
                else "25000+"
            )
            if r["total_ms"] is not None:
                buckets_ms[bkt].append(r["total_ms"])
        p("**Runtime vs SDG node count:**")
        p("```")
        max_avg = max(sum(v) / len(v) for v in buckets_ms.values()) if buckets_ms else 1
        for bkt in sorted(buckets_ms):
            mss = buckets_ms[bkt]
            avg = sum(mss) / len(mss)
            p(f"  {bkt:<14}  {_md_bar(avg / max_avg, 32)}  avg {avg:.0f}ms  (n={len(mss)})")
        p("```")
        blank()
    else:
        p("_No performance results._")
        blank()

    # 6. Gaps
    h(2, "6. Honest Gaps")
    p(_GAPS_TEXT.strip())
    blank()

    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path
