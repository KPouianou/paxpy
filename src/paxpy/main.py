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
        default=3,
        metavar="N",
        help="Call-graph expansion depth (default: 3)",
    )
    parser.add_argument(
        "--max-call-hops",
        type=int,
        default=2,
        dest="max_call_hops",
        metavar="N",
        help=(
            "Suppress interference paths that cross more than N call-graph "
            "boundaries. Lower values reduce false positives at the cost of "
            "missing conflicts through deep call chains (default: 2)."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["cli", "json", "sarif"],
        default="cli",
        dest="output_format",
        help="Output format (default: cli)",
    )
    parser.add_argument(
        "--detector",
        choices=["chop", "neighborhood"],
        default="neighborhood",
        help="Detection algorithm: neighborhood overlap (default) or approximate chop",
    )
    parser.add_argument(
        "--radius",
        type=int,
        default=2,
        metavar="N",
        help="Neighborhood radius for --detector neighborhood (default: 2)",
    )
    return parser


def _is_test_file(filepath: str) -> bool:
    """Return True if filepath looks like a test file.

    Matches paths containing a ``/tests/`` or ``/test/`` directory segment,
    filenames beginning with ``test_``, or filenames ending with ``_test.py``.
    """
    from pathlib import PurePosixPath
    p = PurePosixPath(filepath.replace("\\", "/"))
    parts = p.parts
    if any(part in ("tests", "test") for part in parts[:-1]):
        return True
    name = p.name
    return name.startswith("test_") or name.endswith("_test.py")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    repo_path = args.repo.resolve()

    # Lazy imports so startup is fast when --help is used
    from paxpy.detector import detect, filter_by_call_hops
    from paxpy.diff_parser import parse_diffs
    from paxpy.endpoint_analyzer import analyze_endpoints
    from paxpy.indexer import build_index
    from paxpy.reporter import format_cli, format_json, format_sarif
    from paxpy.sdg_builder import build_sdg
    from paxpy.types import ConflictReport

    if args.detector == "neighborhood":
        from paxpy.neighborhood_detector import detect_interferences as _detect_fn
    else:
        _detect_fn = None  # use chop below

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
    if args.detector == "neighborhood":
        paths = _detect_fn(sdg, radius=args.radius)
    else:
        paths = detect(sdg)
        paths = filter_by_call_hops(paths, sdg, max_hops=args.max_call_hops)

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

        src_file = str(source_node.filepath) if source_node else ""
        snk_file = str(sink_node.filepath) if sink_node else ""
        src_fn = source_node.enclosing_function or "" if source_node else ""
        snk_fn = sink_node.enclosing_function or "" if sink_node else ""

        # Suppress test-file seeding: paths where a test function is an endpoint
        # are almost never real semantic conflicts — they represent test code
        # calling the production functions it tests.
        if _is_test_file(src_file) or _is_test_file(snk_file):
            continue

        # Suppress self-referential paths: source and sink in the same function
        # of the same file. These arise when both branches modified the same
        # function, creating two seed nodes whose own data edges connect them.
        if src_fn and src_fn == snk_fn and src_file == snk_file:
            continue

        report = ConflictReport(
            interference=path,
            compatibility=compatibility,
            source_function=src_fn,
            sink_function=snk_fn,
            source_file=src_file,
            sink_file=snk_file,
        )
        reports.append(report)

    # 6. Format and output
    match args.output_format:
        case "cli":
            print(format_cli(reports))
        case "json":
            print(json.dumps(format_json(reports), indent=2))
        case "sarif":
            print(json.dumps(format_sarif(reports), indent=2))
