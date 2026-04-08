"""Comparative analysis: paxpy depth sweep vs cheap heuristics.

Produces:
  1. Overall precision / recall / F1 / flag-rate for every method
  2. Recall by call_depth  (most important chart)
  3. Recall by conflict_type
  4. Recall by file_count
  5. Redundancy matrix (what % of method X's TPs are also caught by method Y)
  6. Verdict: at which call_depth does paxpy first exceed all heuristics?

Public API
----------
show_compare(db_path, paxpy_run_ids, heuristic_run_id)
write_compare_markdown(db_path, paxpy_run_ids, heuristic_run_id) -> Path
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from bench.db.schema import DEFAULT_DB, connect


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class MethodStats(NamedTuple):
    label: str          # display label
    tp: int
    fp: int
    fn: int
    tn: int

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else math.nan

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else math.nan

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (not math.isnan(p) and not math.isnan(r) and (p + r) > 0) else math.nan

    @property
    def flag_rate(self) -> float:
        n = self.tp + self.fp + self.fn + self.tn
        return (self.tp + self.fp) / n if n > 0 else math.nan

    @property
    def n(self) -> int:
        return self.tp + self.fp + self.fn + self.tn


def _pct(v: float, decimals: int = 1) -> str:
    if math.isnan(v):
        return " n/a"
    return f"{v * 100:.{decimals}f}%"


def _bar(v: float, width: int = 8) -> str:
    if math.isnan(v):
        return "░" * width
    filled = round(v * width)
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_paxpy_run(conn, run_id: int) -> tuple[str, dict[int, dict]]:
    """Load a paxpy run. Returns (label, {scenario_id: result_row})."""
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"paxpy run #{run_id} not found")
    hops = ""
    if run["notes"]:
        # notes may contain max_call_hops info
        import re
        m = re.search(r"max.call.hops[=\s]+(\d+)", run["notes"], re.IGNORECASE)
        if m:
            hops = f"/hops={m.group(1)}"
    label = f"paxpy d={run['depth']}{hops}"
    rows = conn.execute(
        "SELECT * FROM results WHERE run_id=?", (run_id,)
    ).fetchall()
    return label, {r["scenario_id"]: dict(r) for r in rows}


def _load_heuristic_run(conn, run_id: int) -> dict[str, dict[int, dict]]:
    """Load a heuristic run. Returns {heuristic_name: {scenario_id: result_row}}."""
    rows = conn.execute(
        "SELECT * FROM heuristic_results WHERE heuristic_run_id=?", (run_id,)
    ).fetchall()
    by_heuristic: dict[str, dict[int, dict]] = defaultdict(dict)
    for r in rows:
        by_heuristic[r["heuristic"]][r["scenario_id"]] = dict(r)
    return dict(by_heuristic)


def _load_scenarios(conn, bucket: str = "correctness") -> list[dict]:
    """Load correctness scenarios with their metadata."""
    rows = conn.execute(
        "SELECT * FROM scenarios WHERE bucket=?", (bucket,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _aggregate(results: dict[int, dict], scenarios: list[dict]) -> MethodStats:
    """Compute overall TP/FP/FN/TN from a scenario-id→result mapping."""
    tp = fp = fn = tn = 0
    for s in scenarios:
        sid = s["id"]
        if sid not in results:
            continue
        r = results[sid]
        tp += r.get("is_tp") or 0
        fp += r.get("is_fp") or 0
        fn += r.get("is_fn") or 0
        tn += r.get("is_tn") or 0
    return MethodStats("", tp, fp, fn, tn)


def _recall_by_depth(
    results: dict[int, dict],
    scenarios: list[dict],
) -> dict[int, tuple[int, int]]:
    """Return {call_depth: (tp, tp+fn)} for all depths present in scenarios."""
    depth_tp: dict[int, int] = defaultdict(int)
    depth_pos: dict[int, int] = defaultdict(int)
    for s in scenarios:
        d = s.get("call_depth")
        if d is None or not s["positive"]:
            continue
        sid = s["id"]
        depth_pos[d] += 1
        r = results.get(sid)
        if r and (r.get("is_tp") or 0):
            depth_tp[d] += 1
    depths = sorted(set(depth_pos) | set(depth_tp))
    return {d: (depth_tp[d], depth_pos[d]) for d in depths}


def _recall_by_conflict_type(
    results: dict[int, dict],
    scenarios: list[dict],
) -> dict[str, tuple[int, int]]:
    """Return {conflict_type: (tp, total_positives)}."""
    ct_tp: dict[str, int] = defaultdict(int)
    ct_pos: dict[str, int] = defaultdict(int)
    for s in scenarios:
        ct = s.get("conflict_type")
        if ct is None or not s["positive"]:
            continue
        sid = s["id"]
        ct_pos[ct] += 1
        r = results.get(sid)
        if r and (r.get("is_tp") or 0):
            ct_tp[ct] += 1
    types = sorted(set(ct_pos) | set(ct_tp))
    return {ct: (ct_tp[ct], ct_pos[ct]) for ct in types}


def _recall_by_file_count(
    results: dict[int, dict],
    scenarios: list[dict],
) -> dict[int, tuple[int, int]]:
    """Return {file_count: (tp, total_positives)}."""
    fc_tp: dict[int, int] = defaultdict(int)
    fc_pos: dict[int, int] = defaultdict(int)
    for s in scenarios:
        fc = s.get("file_count")
        if fc is None or not s["positive"]:
            continue
        sid = s["id"]
        fc_pos[fc] += 1
        r = results.get(sid)
        if r and (r.get("is_tp") or 0):
            fc_tp[fc] += 1
    fcs = sorted(set(fc_pos) | set(fc_tp))
    return {fc: (fc_tp[fc], fc_pos[fc]) for fc in fcs}


def _tp_set(results: dict[int, dict], scenarios: list[dict]) -> set[int]:
    """Scenario IDs that are true positives for this method."""
    return {
        s["id"]
        for s in scenarios
        if s["positive"] and (results.get(s["id"]) or {}).get("is_tp")
    }


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

def _find_crossover(
    paxpy_by_depth: dict[int, tuple[int, int]],
    heuristic_by_depths: list[dict[int, tuple[int, int]]],
) -> int | None:
    """Return the first call_depth where paxpy's recall exceeds ALL heuristics."""
    for depth in sorted(paxpy_by_depth):
        tp_p, pos_p = paxpy_by_depth[depth]
        if pos_p == 0:
            continue
        paxpy_recall = tp_p / pos_p
        heuristic_max = 0.0
        for hbd in heuristic_by_depths:
            tp_h, pos_h = hbd.get(depth, (0, 0))
            if pos_h > 0:
                heuristic_max = max(heuristic_max, tp_h / pos_h)
        if paxpy_recall > heuristic_max:
            return depth
    return None


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _fmt_row(label: str, stats: MethodStats, label_w: int = 28) -> str:
    return (
        f"  {label:<{label_w}}"
        f"  {_pct(stats.precision):>7}"
        f"  {_pct(stats.recall):>7}"
        f"  {_pct(stats.f1):>7}"
        f"  {_pct(stats.flag_rate):>7}"
        f"  {stats.tp:>5} {stats.fp:>5} {stats.fn:>5} {stats.tn:>5}"
    )


def _depth_table(
    methods: list[tuple[str, dict[int, tuple[int, int]]]],
    depths: list[int],
    label_w: int = 28,
) -> list[str]:
    lines = []
    header = f"  {'depth':<{label_w}}" + "".join(f"  {d:>5}" for d in depths)
    lines.append(header)
    lines.append("  " + "-" * (label_w + len(depths) * 7 + 2))
    for label, by_depth in methods:
        row = f"  {label:<{label_w}}"
        for d in depths:
            tp, pos = by_depth.get(d, (0, 0))
            if pos == 0:
                row += "   n/a"
            else:
                row += f"  {tp/pos*100:>4.0f}%"
        lines.append(row)
    return lines


def _type_table(
    methods: list[tuple[str, dict[str, tuple[int, int]]]],
    types: list[str],
    label_w: int = 28,
) -> list[str]:
    type_short = {"DATA_FLOW": "DF", "CONFLUENCE": "CF",
                  "OVERRIDE_ASSIGNMENT": "OA", "CONTROL_DEPENDENCY": "PDG"}
    lines = []
    header = f"  {'method':<{label_w}}" + "".join(
        f"  {type_short.get(t, t[:4]):>5}" for t in types
    )
    lines.append(header)
    lines.append("  " + "-" * (label_w + len(types) * 7 + 2))
    for label, by_type in methods:
        row = f"  {label:<{label_w}}"
        for t in types:
            tp, pos = by_type.get(t, (0, 0))
            if pos == 0:
                row += "   n/a"
            else:
                row += f"  {tp/pos*100:>4.0f}%"
        lines.append(row)
    return lines


def _redundancy_matrix(
    method_names: list[str],
    tp_sets: dict[str, set[int]],
) -> list[str]:
    """What % of row method's TPs are also in column method's TPs."""
    lines = []
    w = max(len(n) for n in method_names)
    header = f"  {'':>{w}}" + "".join(f"  {n[:8]:>8}" for n in method_names)
    lines.append(header)
    lines.append("  " + "-" * (w + len(method_names) * 10 + 4))
    for row_name in method_names:
        row_tps = tp_sets[row_name]
        line = f"  {row_name:>{w}}"
        for col_name in method_names:
            col_tps = tp_sets[col_name]
            if not row_tps:
                line += "      —  "
            else:
                overlap = len(row_tps & col_tps) / len(row_tps)
                line += f"  {overlap*100:>5.0f}%  " if row_name != col_name else "  [self]  "
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Main report builder
# ---------------------------------------------------------------------------

def _build_report(
    db_path: Path,
    paxpy_run_ids: list[int],
    heuristic_run_id: int,
    bucket: str = "correctness",
) -> list[str]:
    conn = connect(db_path)
    scenarios = _load_scenarios(conn, bucket)
    pos_scenarios = [s for s in scenarios if s["positive"]]

    # Load paxpy runs
    paxpy_runs: list[tuple[str, dict[int, dict]]] = []
    for rid in paxpy_run_ids:
        label, results = _load_paxpy_run(conn, rid)
        paxpy_runs.append((label, results))

    # Load heuristic run
    heuristic_data = _load_heuristic_run(conn, heuristic_run_id)

    # All methods: paxpy runs first, then heuristics
    all_methods: list[tuple[str, dict[int, dict]]] = list(paxpy_runs) + [
        (name, {sid: {"is_tp": r["is_tp"], "is_fp": r["is_fp"],
                      "is_fn": r["is_fn"], "is_tn": r["is_tn"]}
                for sid, r in results.items()})
        for name, results in sorted(heuristic_data.items())
    ]

    # Overall stats
    all_stats: list[tuple[str, MethodStats]] = []
    for label, results in all_methods:
        stats = _aggregate(results, scenarios)
        all_stats.append((label, stats._replace(label=label)))

    # Depth breakdown
    depths = sorted({s["call_depth"] for s in scenarios if s.get("call_depth") is not None})
    all_by_depth: list[tuple[str, dict]] = [
        (label, _recall_by_depth(results, pos_scenarios))
        for label, results in all_methods
    ]

    # Best paxpy run by F1 (for crossover calc)
    best_paxpy_label, best_paxpy_results = max(
        paxpy_runs,
        key=lambda lr: _aggregate(lr[1], scenarios).f1 if not math.isnan(_aggregate(lr[1], scenarios).f1) else -1,
    ) if paxpy_runs else ("", {})
    best_paxpy_by_depth = _recall_by_depth(best_paxpy_results, pos_scenarios)
    heuristic_by_depths = [
        _recall_by_depth(results, pos_scenarios)
        for _, results in sorted(heuristic_data.items())
    ]
    crossover = _find_crossover(best_paxpy_by_depth, heuristic_by_depths)

    # Conflict type breakdown
    types = sorted({s["conflict_type"] for s in scenarios if s.get("conflict_type")})
    all_by_type: list[tuple[str, dict]] = [
        (label, _recall_by_conflict_type(results, pos_scenarios))
        for label, results in all_methods
    ]

    # File count breakdown
    file_counts = sorted({s["file_count"] for s in scenarios if s.get("file_count") is not None})
    all_by_fc: list[tuple[str, dict]] = [
        (label, _recall_by_file_count(results, pos_scenarios))
        for label, results in all_methods
    ]

    # TP sets for redundancy matrix
    method_names = [label for label, _ in all_methods]
    tp_sets = {
        label: _tp_set(results, scenarios)
        for label, results in all_methods
    }

    # --- Build lines ---
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines += [
        "",
        "╔══════════════════════════════════════════════════════════════════╗",
        "║         HEURISTIC BASELINE EXPERIMENT — paxpy vs Heuristics     ║",
        "╚══════════════════════════════════════════════════════════════════╝",
        f"  Generated: {ts}",
        f"  paxpy run IDs: {paxpy_run_ids}   heuristic run: #{heuristic_run_id}",
        f"  Scenarios: {len(scenarios)} total  ({len(pos_scenarios)} positive, "
        f"{len(scenarios)-len(pos_scenarios)} negative)  bucket={bucket}",
        "",
    ]

    # ── 1. Overall comparison ─────────────────────────────────────────────
    label_w = max(len(l) for l, _ in all_stats) + 1
    lines += [
        "── 1. OVERALL COMPARISON ─────────────────────────────────────────",
        "",
        f"  {'METHOD':<{label_w}}  {'PREC':>7}  {'RECALL':>7}  {'F1':>7}  {'FLAG%':>7}"
        f"  {'TP':>5} {'FP':>5} {'FN':>5} {'TN':>5}",
        "  " + "─" * (label_w + 50),
    ]
    for label, stats in all_stats:
        lines.append(_fmt_row(label, stats, label_w))
    lines.append("")

    # ── 2. Recall by call_depth ──────────────────────────────────────────
    lines += [
        "── 2. RECALL BY CALL_DEPTH (key question) ────────────────────────",
        "  (% of positive scenarios detected at each call depth)",
        "",
    ]
    lines += _depth_table(all_by_depth, depths, label_w)
    lines += [""]

    # Crossover verdict
    if crossover is not None:
        lines += [
            f"  ★  VERDICT: paxpy ({best_paxpy_label}) first exceeds ALL heuristics",
            f"     at call_depth = {crossover}.",
            f"     At depths < {crossover}, cheap heuristics match or beat paxpy.",
            "",
        ]
    else:
        lines += [
            "  ★  VERDICT: paxpy did NOT exceed all heuristics at any call_depth",
            "     in the tested configurations.  Either the depth config needs",
            "     adjustment or the heuristics are sufficient for this dataset.",
            "",
        ]

    # ── 3. Recall by conflict_type ────────────────────────────────────────
    lines += [
        "── 3. RECALL BY CONFLICT TYPE ────────────────────────────────────",
        "  (DF=DATA_FLOW  CF=CONFLUENCE  OA=OVERRIDE_ASSIGNMENT  PDG=CONTROL_DEPENDENCY)",
        "",
    ]
    lines += _type_table(all_by_type, types, label_w)
    lines += [""]

    # ── 4. Recall by file_count ───────────────────────────────────────────
    lines += [
        "── 4. RECALL BY FILE COUNT ───────────────────────────────────────",
        "  (single-file vs multi-file scenarios)",
        "",
        f"  {'METHOD':<{label_w}}" + "".join(f"  {fc}-file" for fc in file_counts),
    ]
    for label, by_fc in all_by_fc:
        row = f"  {label:<{label_w}}"
        for fc in file_counts:
            tp, pos = by_fc.get(fc, (0, 0))
            row += f"  {tp/pos*100:>4.0f}% " if pos > 0 else "  n/a  "
        lines.append(row)
    lines += [""]

    # ── 5. Redundancy matrix ──────────────────────────────────────────────
    lines += [
        "── 5. REDUNDANCY MATRIX ──────────────────────────────────────────",
        "  Cell[row, col] = % of row's TPs that are ALSO in col's TP set.",
        "  100% in a row = col is a strict superset of row.",
        "",
    ]
    lines += _redundancy_matrix(method_names, tp_sets)
    lines += [""]

    # ── 6. Paxpy-exclusive TPs ────────────────────────────────────────────
    if paxpy_runs:
        best_tp_set = tp_sets[best_paxpy_label]
        heuristic_union = set().union(*[tp_sets[n] for n in method_names if not n.startswith("paxpy")])
        exclusive = best_tp_set - heuristic_union
        lines += [
            "── 6. PAXPY-EXCLUSIVE DETECTIONS ─────────────────────────────────",
            f"  Best paxpy config ({best_paxpy_label}):",
            f"    Total TPs:           {len(best_tp_set)}",
            f"    Also caught by ≥1 heuristic: {len(best_tp_set & heuristic_union)}",
            f"    EXCLUSIVE to paxpy:  {len(exclusive)}  "
            f"({len(exclusive)/len(best_tp_set)*100:.1f}% of paxpy TPs)" if best_tp_set else "",
            "",
        ]
        # Show exclusive TP call depths
        if exclusive:
            depth_dist: dict[int, int] = defaultdict(int)
            type_dist: dict[str, int] = defaultdict(int)
            scen_by_id = {s["id"]: s for s in scenarios}
            for sid in exclusive:
                s = scen_by_id.get(sid)
                if s:
                    depth_dist[s.get("call_depth", 0)] += 1
                    type_dist[s.get("conflict_type", "?")] += 1
            lines.append("    Depth distribution of paxpy-exclusive TPs:")
            for d in sorted(depth_dist):
                lines.append(f"      depth={d}: {depth_dist[d]}")
            lines.append("    Conflict type distribution:")
            for ct, cnt in sorted(type_dist.items()):
                lines.append(f"      {ct}: {cnt}")
            lines.append("")

    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def show_compare(
    db_path: Path = DEFAULT_DB,
    paxpy_run_ids: list[int] | None = None,
    heuristic_run_id: int | None = None,
    bucket: str = "correctness",
) -> None:
    """Print the comparative analysis to stdout."""
    conn = connect(db_path)

    if paxpy_run_ids is None:
        rows = conn.execute("SELECT id FROM runs ORDER BY id").fetchall()
        paxpy_run_ids = [r["id"] for r in rows]

    if heuristic_run_id is None:
        row = conn.execute("SELECT id FROM heuristic_runs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            print("No heuristic run found. Run: paxpy-bench heuristics first.")
            return
        heuristic_run_id = row["id"]

    if not paxpy_run_ids:
        print("No paxpy runs found. Run: paxpy-bench run first.")
        return

    lines = _build_report(db_path, paxpy_run_ids, heuristic_run_id, bucket)
    try:
        from rich.console import Console
        from rich.text import Text
        c = Console()
        for line in lines:
            c.print(line)
    except ImportError:
        print("\n".join(lines))


def write_compare_markdown(
    db_path: Path = DEFAULT_DB,
    paxpy_run_ids: list[int] | None = None,
    heuristic_run_id: int | None = None,
    bucket: str = "correctness",
    output_path: Path | None = None,
) -> Path:
    """Write the comparative analysis as a markdown file and return its path."""
    conn = connect(db_path)

    if paxpy_run_ids is None:
        rows = conn.execute("SELECT id FROM runs ORDER BY id").fetchall()
        paxpy_run_ids = [r["id"] for r in rows]

    if heuristic_run_id is None:
        row = conn.execute("SELECT id FROM heuristic_runs ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            raise RuntimeError("No heuristic run found.")
        heuristic_run_id = row["id"]

    lines = _build_report(db_path, paxpy_run_ids, heuristic_run_id, bucket)

    if output_path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        reports_dir = Path(__file__).parent.parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        output_path = reports_dir / f"compare_{ts}.md"

    md_lines = ["```\n" + "\n".join(lines) + "\n```"]
    # Also render as plain markdown tables
    md_lines.insert(0, "# Heuristic Baseline Experiment Report\n")
    output_path.write_text("\n".join(md_lines))
    return output_path
