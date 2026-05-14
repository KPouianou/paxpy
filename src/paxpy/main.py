"""CLI entry point for paxpy.

Parses arguments and orchestrates the full analysis pipeline:
diff_parser → indexer → sdg_builder → detector → endpoint_analyzer → reporter.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from paxpy.types import SDG, InterferencePath, Node, NodeId


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
        default=1,
        metavar="N",
        help="Neighborhood radius for --detector neighborhood (default: 1)",
    )
    parser.add_argument(
        "--no-mod-relevance-filter",
        action="store_true",
        default=False,
        help="Disable modification-relevance filter for SDG paths",
    )
    return parser


# ---------------------------------------------------------------------------
# Modification-relevance filter for SDG-based paths
# ---------------------------------------------------------------------------


def _get_function_nodes(
    sdg: SDG,
    fn_name: str,
    filepath: str,
) -> dict[NodeId, Node]:
    """All SDG nodes belonging to a specific function in a specific file."""
    return {
        nid: node
        for nid, node in sdg.nodes.items()
        if node.enclosing_function == fn_name and str(node.filepath) == filepath
    }


def _find_call_boundary(
    path_nodes: list[NodeId],
    sdg: SDG,
) -> NodeId | None:
    """The node on the path that has a call edge to the next node."""
    for u, v in zip(path_nodes, path_nodes[1:], strict=False):
        if v in sdg.call_edges.get(u, set()):
            return u
    return None


def _bfs_forward_intra(
    sdg: SDG,
    starts: set[NodeId],
    allowed: set[NodeId],
) -> set[NodeId]:
    """Forward BFS through data_edges only, constrained to allowed node set."""
    reachable = set(starts)
    frontier = list(starts)
    while frontier:
        current = frontier.pop()
        for neighbor in sdg.data_edges.get(current, set()):
            if neighbor in allowed and neighbor not in reachable:
                reachable.add(neighbor)
                frontier.append(neighbor)
    return reachable


def _bfs_backward_intra(
    sdg: SDG,
    starts: set[NodeId],
    allowed: set[NodeId],
) -> set[NodeId]:
    """Backward BFS through reverse_data_edges only, constrained to allowed node set."""
    reachable = set(starts)
    frontier = list(starts)
    while frontier:
        current = frontier.pop()
        for neighbor in sdg.reverse_data_edges.get(current, set()):
            if neighbor in allowed and neighbor not in reachable:
                reachable.add(neighbor)
                frontier.append(neighbor)
    return reachable


def _path_has_modification_relevance(
    path: InterferencePath,
    sdg: SDG,
) -> bool:
    """Check whether both endpoint functions' modifications participate
    in the inter-procedural data flow through the call edge.

    Source side (bidirectional from call boundary):
      - Backward: does the modification feed into call arguments?
      - Forward: does the modification use the call's return value?

    Sink side (forward from entry):
      - Does the modification depend on incoming parameters?

    Both sides must connect. Returns True to keep, False to suppress.
    """
    source_entry = sdg.nodes.get(path.source_node)
    sink_entry = sdg.nodes.get(path.sink_node)
    if source_entry is None or sink_entry is None:
        return True  # Missing metadata — conservative keep

    source_fn = source_entry.enclosing_function
    source_file = str(source_entry.filepath)
    sink_fn = sink_entry.enclosing_function
    sink_file = str(sink_entry.filepath)

    if not source_fn or not sink_fn:
        return True  # Missing function name — conservative keep

    source_fn_nodes = _get_function_nodes(sdg, source_fn, source_file)
    sink_fn_nodes = _get_function_nodes(sdg, sink_fn, sink_file)

    source_modified = {nid for nid, n in source_fn_nodes.items() if n.is_direct_modification}
    sink_modified = {nid for nid, n in sink_fn_nodes.items() if n.is_direct_modification}

    # Tier 1: both functions must have modified nodes
    if not source_modified or not sink_modified:
        return False

    call_boundary = _find_call_boundary(path.path_nodes, sdg)
    if call_boundary is None:
        return True  # No call edge on path — conservative keep

    # Source side: bidirectional from call boundary
    source_fn_nids = set(source_fn_nodes.keys())
    backward_from_call = _bfs_backward_intra(sdg, {call_boundary}, source_fn_nids)
    source_connects = bool(source_modified & backward_from_call)

    if not source_connects:
        forward_from_call = _bfs_forward_intra(sdg, {call_boundary}, source_fn_nids)
        source_connects = bool(source_modified & forward_from_call)

    # Sink side: forward from entry
    sink_fn_nids = set(sink_fn_nodes.keys())
    forward_from_entry = _bfs_forward_intra(sdg, {path.sink_node}, sink_fn_nids)
    sink_connects = bool(sink_modified & forward_from_entry)

    return source_connects and sink_connects


def _build_sdg_evidence(
    path: InterferencePath,
    sdg: SDG,
) -> dict:
    """Build structured evidence dict for an SDG-based interference path."""
    # path_labels: human-readable label for each node on the path
    path_labels: list[str] = []
    for nid in path.path_nodes:
        node = sdg.nodes.get(nid)
        if node is not None:
            fn = node.enclosing_function or "?"
            path_labels.append(f"{node.label} ({fn}, line {node.lineno})")
        else:
            path_labels.append(nid)

    evidence: dict = {"path_labels": path_labels}

    # source_modifications
    source_entry = sdg.nodes.get(path.source_node)
    if source_entry and source_entry.enclosing_function:
        src_fn_nodes = _get_function_nodes(
            sdg, source_entry.enclosing_function, str(source_entry.filepath)
        )
        src_mods = {nid: n for nid, n in src_fn_nodes.items() if n.is_direct_modification}
        evidence["source_modifications"] = {
            "function": source_entry.enclosing_function,
            "file": str(source_entry.filepath),
            "branch": source_entry.branch,
            "modified_lines": sorted({n.lineno for n in src_mods.values()}),
            "modified_labels": [n.label for n in src_mods.values()],
        }

    # sink_modifications
    sink_entry = sdg.nodes.get(path.sink_node)
    if sink_entry and sink_entry.enclosing_function:
        snk_fn_nodes = _get_function_nodes(
            sdg, sink_entry.enclosing_function, str(sink_entry.filepath)
        )
        snk_mods = {nid: n for nid, n in snk_fn_nodes.items() if n.is_direct_modification}
        evidence["sink_modifications"] = {
            "function": sink_entry.enclosing_function,
            "file": str(sink_entry.filepath),
            "branch": sink_entry.branch,
            "modified_lines": sorted({n.lineno for n in snk_mods.values()}),
            "modified_labels": [n.label for n in snk_mods.values()],
        }

    # call_interface
    call_boundary = _find_call_boundary(path.path_nodes, sdg)
    if call_boundary is not None:
        call_node = sdg.nodes.get(call_boundary)
        # Find the callee entry (next node on path after call boundary via call edge)
        callee_entry_label = None
        for u, v in zip(path.path_nodes, path.path_nodes[1:], strict=False):
            if u == call_boundary and v in sdg.call_edges.get(u, set()):
                callee_node = sdg.nodes.get(v)
                if callee_node:
                    callee_entry_label = callee_node.label
                break
        evidence["call_interface"] = {
            "call_site": call_node.label if call_node else call_boundary,
            "callee_entry": callee_entry_label,
        }

    return evidence


def _all_intermediates_unchanged(
    path: InterferencePath,
    sdg: SDG,
    seed_functions: set[tuple[str, str]],
) -> bool:
    """Return True if ALL intermediate nodes on the path are in unchanged functions.

    An intermediate node is any node in ``path.path_nodes`` between the first
    and last (i.e. ``path_nodes[1:-1]``).  If the enclosing function of every
    intermediate is *not* in the set of seed (modified) functions, the path
    represents pre-existing connectivity (both branches call the same unchanged
    utility) rather than a genuine interference and should be suppressed.

    Conservative fallback: if a path has no intermediates (length <= 2) or any
    intermediate's enclosing function cannot be determined, return False (keep).
    """
    if len(path.path_nodes) <= 2:
        return False  # No intermediates → keep

    intermediate_ids = path.path_nodes[1:-1]
    for mid in intermediate_ids:
        mid_node = sdg.nodes.get(mid)
        if mid_node is None or not mid_node.enclosing_function:
            return False  # Indeterminate → conservative keep
        mid_key = (mid_node.enclosing_function, str(mid_node.filepath))
        if mid_key in seed_functions:
            return False  # At least one intermediate is in a modified function → keep
    return True  # All intermediates in unchanged functions → suppress


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
    from paxpy.direct_detector import detect_direct_conflicts
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

    # 4. Detect interference paths (SDG-based)
    if args.detector == "neighborhood":
        paths = _detect_fn(sdg, radius=args.radius)
    else:
        paths = detect(sdg)
        paths = filter_by_call_hops(paths, sdg, max_hops=args.max_call_hops)

    # 4b. Direct AST comparison — catches OVERRIDE_ASSIGNMENT and
    # signature_body_mismatch patterns that SDG detectors miss.
    direct_paths = detect_direct_conflicts(diff_result)

    if not paths and not direct_paths:
        output = format_cli([]) if args.output_format == "cli" else json.dumps(format_json([]))
        print(output)
        sys.exit(0)

    # 5. Analyse endpoints and build ConflictReports
    from paxpy.types import Compatibility, CompatibilityResult

    reports: list[ConflictReport] = []

    # 5a. Direct-detector paths bypass endpoint analysis and self-referential
    # suppression (OA and signature mismatch are inherently same-function).
    for path in direct_paths:
        # Extract file/function info from the synthetic NodeIds
        src_file = path.source_node.rsplit(":", 2)[0] if path.source_node else ""
        snk_file = path.sink_node.rsplit(":", 2)[0] if path.sink_node else ""

        if _is_test_file(src_file) or _is_test_file(snk_file):
            continue

        report = ConflictReport(
            interference=path,
            compatibility=CompatibilityResult(
                compatibility=Compatibility.INCOMPATIBLE,
                explanation="Direct AST comparison",
            ),
            source_function="",
            sink_function="",
            source_file=src_file,
            sink_file=snk_file,
            evidence=path.evidence,
        )
        reports.append(report)

    # 5b. SDG-based paths go through endpoint analysis and filtering.

    # Build seed-function set: functions modified by either branch.
    # Used to suppress paths whose intermediates are all in unchanged functions.
    seed_functions: set[tuple[str, str]] = set()
    for nid in list(sdg.seeds_a) + list(sdg.seeds_b):
        node = sdg.nodes.get(nid)
        if node and node.enclosing_function:
            seed_functions.add((node.enclosing_function, str(node.filepath)))
    # Also include directly-modified nodes (which may differ from seeds when
    # the neighborhood detector is used).
    for _nid, node in sdg.nodes.items():
        if node.is_direct_modification and node.enclosing_function:
            seed_functions.add((node.enclosing_function, str(node.filepath)))

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

        # Modification-relevance filter: suppress paths where
        # the branches' modifications don't participate in data flow
        # through the call edge connecting the seed functions.
        if not args.no_mod_relevance_filter and not _path_has_modification_relevance(path, sdg):
            continue

        # Unchanged-intermediate filter: suppress paths where all
        # intermediate nodes are in functions not modified by either branch.
        # This targets pre-existing connectivity (both branches call the
        # same unchanged utility).
        if _all_intermediates_unchanged(path, sdg, seed_functions):
            continue

        report = ConflictReport(
            interference=path,
            compatibility=compatibility,
            source_function=src_fn,
            sink_function=snk_fn,
            source_file=src_file,
            sink_file=snk_file,
            evidence=_build_sdg_evidence(path, sdg),
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
