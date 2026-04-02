"""CLI entry point for paxpy.

Parses arguments and orchestrates the full analysis pipeline:
diff_parser → indexer → sdg_builder → detector → endpoint_analyzer → reporter.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


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
    args = parser.parse_args()

    repo_path = args.repo.resolve()

    # Lazy imports so startup is fast when --help is used
    from paxpy.detector import detect
    from paxpy.diff_parser import parse_diffs
    from paxpy.endpoint_analyzer import analyze_endpoints
    from paxpy.indexer import build_index
    from paxpy.reporter import format_cli, format_json, format_sarif
    from paxpy.sdg_builder import build_sdg
    from paxpy.types import ConflictReport

    # 1. Parse diffs
    diff_result = parse_diffs(repo_path, args.base, args.branch_a, args.branch_b)

    if not diff_result.seeds_a and not diff_result.seeds_b:
        print("paxpy: no changed functions found in either branch.", file=sys.stderr)
        sys.exit(0)

    # 2. Build function index
    index = build_index(repo_path)

    # 3. Build partial SDG
    sdg = build_sdg(diff_result, index, depth=args.depth)

    # 4. Detect interference paths
    paths = detect(sdg)

    if not paths:
        output = format_cli([]) if args.output_format == "cli" else json.dumps(format_json([]))
        print(output)
        sys.exit(0)

    # 5. Analyse endpoints and build ConflictReports
    from paxpy.types import Compatibility

    reports: list[ConflictReport] = []
    for path in paths:
        source_node = sdg.nodes.get(path.source_node)
        sink_node = sdg.nodes.get(path.sink_node)

        compatibility = analyze_endpoints(path, source_node, sink_node)

        # Suppress findings where endpoint analysis confirms no incompatibility.
        # compatible → suppress; suspicious/unknown/incompatible → report.
        if compatibility.compatibility == Compatibility.COMPATIBLE:
            continue

        report = ConflictReport(
            interference=path,
            compatibility=compatibility,
            source_function=source_node.enclosing_function or "" if source_node else "",
            sink_function=sink_node.enclosing_function or "" if sink_node else "",
            source_file=str(source_node.filepath) if source_node else "",
            sink_file=str(sink_node.filepath) if sink_node else "",
        )
        reports.append(report)

    # 6. Format and print output
    match args.output_format:
        case "cli":
            print(format_cli(reports))
        case "json":
            print(json.dumps(format_json(reports), indent=2))
        case "sarif":
            print(json.dumps(format_sarif(reports), indent=2))
