"""CLI entry point for paxpy.

Parses arguments and orchestrates the full analysis pipeline:
diff_parser → indexer → sdg_builder → detector → endpoint_analyzer → reporter.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from paxpy import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paxpy",
        description="Detect semantic merge conflicts via partial System Dependence Graphs.",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("."),
        metavar="PATH",
        help="Path to the git repository (default: current directory)",
    )
    parser.add_argument(
        "--base",
        required=True,
        metavar="BRANCH",
        help="Common ancestor branch (e.g. main)",
    )
    parser.add_argument(
        "--branch-a",
        required=True,
        metavar="BRANCH",
        help="First feature branch",
    )
    parser.add_argument(
        "--branch-b",
        required=True,
        metavar="BRANCH",
        help="Second feature branch",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=5,
        metavar="N",
        help="Call-graph expansion depth (default: 5)",
    )
    parser.add_argument(
        "--format",
        choices=["cli", "json", "sarif"],
        default="cli",
        dest="output_format",
        help="Output format (default: cli)",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()  # noqa: F841 — used once pipeline is wired up

    print(f"paxpy v{__version__} — not yet implemented")
