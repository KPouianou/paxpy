"""paxpy-bench — CLI entry point.

Subcommands
-----------
  seed    Populate the database with scenarios (idempotent).
  run     Execute paxpy against all pending scenarios with a live display.
  report  Show results for a run (terminal + optional markdown file).
  clean   Drop and recreate the database (requires --confirm).

Usage
-----
  paxpy-bench seed [--db PATH] [--bucket correctness|adversarial|performance|all]
  paxpy-bench run  [--db PATH] [--depth N] [--bucket BUCKET] [--scenario PREFIX]
  paxpy-bench report [--db PATH] [--run-id N] [--md]
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

    from bench.report.display import show, write_markdown
    from bench.run.runner import run_all

    c = Console()
    c.print(
        f"\n[bold]Running[/]  db={args.db}  "
        f"bucket={args.bucket}  depth={args.depth}"
    )
    if args.scenario:
        c.print(f"  filter: {args.scenario}*")
    c.print()

    run_id = run_all(
        db_path=Path(args.db),
        bucket=args.bucket,
        depth=args.depth,
        scenario_filter=args.scenario or None,
        notes=args.notes or None,
    )

    c.print(f"\n[dim]Run #{run_id} complete. Generating report…[/]\n")
    show(db_path=Path(args.db), run_id=run_id)

    md_path = write_markdown(db_path=Path(args.db), run_id=run_id)
    c.print(f"\n[dim]Markdown report written to {md_path}[/]\n")


def _cmd_report(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.report.display import show, write_markdown

    c = Console()
    show(db_path=Path(args.db), run_id=args.run_id)

    if args.md:
        md_path = write_markdown(db_path=Path(args.db), run_id=args.run_id)
        c.print(f"\n[dim]Markdown report written to {md_path}[/]\n")


def _cmd_clean(args: argparse.Namespace) -> None:
    from rich.console import Console

    from bench.db.schema import drop_and_recreate

    c = Console()
    if not args.confirm:
        c.print(
            "[yellow]⚠  This will delete all scenarios and results. "
            "Add --confirm to proceed.[/]"
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
        default=5,
        metavar="N",
        help="Call-graph expansion depth passed to paxpy (default: 5).",
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
    args   = parser.parse_args()

    commands = {
        "seed":   _cmd_seed,
        "run":    _cmd_run,
        "report": _cmd_report,
        "clean":  _cmd_clean,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
