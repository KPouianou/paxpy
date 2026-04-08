"""paxpy-bench — CLI entry point.

Subcommands
-----------
  seed        Populate the database with scenarios (idempotent).
  run         Execute paxpy against all pending scenarios with a live display.
  report      Show results for a run (terminal + optional markdown file).
  heuristics  Run all cheap heuristics against correctness scenarios.
  compare     Comparative analysis: paxpy depth sweep vs heuristics.
  clean       Drop and recreate the database (requires --confirm).

Usage
-----
  paxpy-bench seed [--db PATH] [--bucket correctness|adversarial|performance|all]
  paxpy-bench run  [--db PATH] [--depth N] [--max-call-hops N] [--bucket BUCKET]
  paxpy-bench report [--db PATH] [--run-id N] [--md]
  paxpy-bench heuristics [--db PATH] [--bucket correctness|all] [--notes TEXT]
  paxpy-bench compare [--db PATH] [--paxpy-runs N [N ...]] [--heuristic-run N] [--md]
  paxpy-bench clean [--db PATH] [--confirm]

Install
-------
  pip install -e ".[bench]"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bench.db.schema import DEFAULT_DB


def _cmd_seed(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.db.seeder import seed

    Console().print(f"\n[bold]Seeding[/]  db={args.db}  bucket={args.bucket}\n")
    seed(db_path=Path(args.db), bucket=args.bucket)


def _cmd_run(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.reporting.display import show, write_markdown
    from bench.run.runner import run_all

    c = Console()
    c.print(
        f"\n[bold]Running[/]  db={args.db}  "
        f"bucket={args.bucket}  depth={args.depth}  "
        f"max-call-hops={args.max_call_hops}"
    )
    if args.scenario:
        c.print(f"  filter: {args.scenario}*")
    c.print()

    run_id = run_all(
        db_path=Path(args.db),
        bucket=args.bucket,
        depth=args.depth,
        max_call_hops=args.max_call_hops,
        scenario_filter=args.scenario or None,
        notes=args.notes or None,
    )

    c.print(f"\n[dim]Run #{run_id} complete. Generating report…[/]\n")
    show(db_path=Path(args.db), run_id=run_id)

    md_path = write_markdown(db_path=Path(args.db), run_id=run_id)
    c.print(f"\n[dim]Markdown report written to {md_path}[/]\n")


def _cmd_report(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.reporting.display import show, write_markdown

    c = Console()
    show(db_path=Path(args.db), run_id=args.run_id)

    if args.md:
        md_path = write_markdown(db_path=Path(args.db), run_id=args.run_id)
        c.print(f"\n[dim]Markdown report written to {md_path}[/]\n")


def _cmd_heuristics(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.run.heuristic_runner import run_heuristics

    c = Console()
    c.print(f"\n[bold]Heuristics[/]  db={args.db}  bucket={args.bucket}\n")
    run_id = run_heuristics(
        db_path=Path(args.db),
        bucket=args.bucket,
        notes=args.notes or None,
    )
    c.print(f"\n[dim]Heuristic run #{run_id} stored.[/]")


def _cmd_compare(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.reporting.compare import show_compare, write_compare_markdown

    c = Console()
    paxpy_ids = args.paxpy_runs or None
    h_id = args.heuristic_run or None
    show_compare(
        db_path=Path(args.db),
        paxpy_run_ids=paxpy_ids,
        heuristic_run_id=h_id,
        bucket=args.bucket,
    )
    if args.md:
        md_path = write_compare_markdown(
            db_path=Path(args.db),
            paxpy_run_ids=paxpy_ids,
            heuristic_run_id=h_id,
            bucket=args.bucket,
        )
        c.print(f"\n[dim]Markdown report written to {md_path}[/]\n")


def _cmd_clean(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.db.schema import drop_and_recreate

    c = Console()
    if not args.confirm:
        c.print(
            "[yellow]⚠  This will delete all scenarios and results. Add --confirm to proceed.[/]"
        )
        sys.exit(1)

    drop_and_recreate(Path(args.db))
    c.print(f"[green]✓[/] Database at {args.db} has been reset.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paxpy-bench",
        description="paxpy benchmark: synthetic evaluation harness.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        metavar="PATH",
        help=f"Path to SQLite database (default: {DEFAULT_DB})",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # seed
    p_seed = sub.add_parser("seed", help="Populate database with scenarios (idempotent).")
    p_seed.add_argument(
        "--bucket",
        choices=["correctness", "adversarial", "performance", "all"],
        default="all",
        help="Which bucket to seed (default: all).",
    )

    # run
    p_run = sub.add_parser("run", help="Run paxpy against pending scenarios.")
    p_run.add_argument(
        "--bucket",
        choices=["correctness", "adversarial", "performance", "all"],
        default="all",
        help="Restrict to this bucket (default: all).",
    )
    p_run.add_argument(
        "--depth",
        type=int,
        default=3,
        metavar="N",
        help="Call-graph expansion depth passed to paxpy (default: 3).",
    )
    p_run.add_argument(
        "--max-call-hops",
        type=int,
        default=2,
        dest="max_call_hops",
        metavar="N",
        help=("Suppress paths crossing more than N call-graph boundaries (default: 2)."),
    )
    p_run.add_argument(
        "--scenario",
        metavar="PREFIX",
        default=None,
        help="Only run scenarios whose name starts with PREFIX.",
    )
    p_run.add_argument(
        "--notes",
        metavar="TEXT",
        default=None,
        help="Optional free-text notes stored with the run record.",
    )

    # report
    p_rep = sub.add_parser("report", help="Show results for a run.")
    p_rep.add_argument(
        "--run-id",
        type=int,
        default=None,
        dest="run_id",
        metavar="N",
        help="Run ID to report on (default: latest).",
    )
    p_rep.add_argument(
        "--md",
        action="store_true",
        help="Also write a markdown report file to bench/reports/.",
    )

    # heuristics
    p_heur = sub.add_parser(
        "heuristics",
        help="Run cheap structural heuristics against scenarios.",
    )
    p_heur.add_argument(
        "--bucket",
        choices=["correctness", "all"],
        default="correctness",
        help="Which scenario bucket to evaluate (default: correctness; skips performance).",
    )
    p_heur.add_argument(
        "--notes",
        metavar="TEXT",
        default=None,
        help="Optional free-text stored with the heuristic run record.",
    )

    # compare
    p_cmp = sub.add_parser(
        "compare",
        help="Comparative analysis: paxpy runs vs heuristic baselines.",
    )
    p_cmp.add_argument(
        "--paxpy-runs",
        type=int,
        nargs="+",
        dest="paxpy_runs",
        metavar="N",
        default=None,
        help="paxpy run IDs to include (default: all runs in DB).",
    )
    p_cmp.add_argument(
        "--heuristic-run",
        type=int,
        dest="heuristic_run",
        metavar="N",
        default=None,
        help="Heuristic run ID to use (default: latest).",
    )
    p_cmp.add_argument(
        "--bucket",
        choices=["correctness", "adversarial", "all"],
        default="correctness",
        help="Which scenario bucket to analyse (default: correctness).",
    )
    p_cmp.add_argument(
        "--md",
        action="store_true",
        help="Also write a markdown report file to bench/reports/.",
    )

    # clean
    p_clean = sub.add_parser("clean", help="Drop and recreate the database.")
    p_clean.add_argument(
        "--confirm",
        action="store_true",
        help="Required to actually drop the database.",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "seed":        _cmd_seed,
        "run":         _cmd_run,
        "report":      _cmd_report,
        "heuristics":  _cmd_heuristics,
        "compare":     _cmd_compare,
        "clean":       _cmd_clean,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
