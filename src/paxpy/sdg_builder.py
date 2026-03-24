"""Build a partial System Dependence Graph (SDG) seeded from git diff results.

Starting from the function seeds in a DiffResult, this module expands the
call graph outward via depth-limited BFS (depth measured in call-graph edges,
not raw dependence edges). For each reachable function it builds the
intraprocedural PDG, then stitches the per-function PDGs together with
inter-procedural call edges — linking actual arguments to formal parameters
and return values back to call sites.

Both forward and reverse adjacency lists are maintained in lockstep so that
downstream modules (detector) can do backward BFS without reversing the graph.

Depends on: types, pdg_builder, call_resolver (and transitively indexer).
"""

from __future__ import annotations

import ast
from collections import deque

from paxpy.call_resolver import resolve_call
from paxpy.pdg_builder import build_pdg
from paxpy.types import SDG, DiffResult, FunctionIndex, FunctionLocation, NodeId


def build_sdg(
    diff_result: DiffResult,
    index: FunctionIndex,
    depth: int = 5,
) -> SDG:
    """Build a partial SDG from diff seeds up to `depth` call-graph levels.

    Algorithm:
    1. Seed the BFS queue with all functions in diff_result.seeds_a and
       diff_result.seeds_b (depth 0).
    2. For each function at depth d < `depth`: build its PDG, scan it for
       ast.Call nodes, resolve each call via call_resolver, enqueue resolved
       callees at depth d+1.
    3. Merge all per-function PDGs into the SDG node/edge dicts.
    4. Add inter-procedural call edges (caller call-site node → callee entry
       node) and parameter-binding data edges.
    5. Build reverse adjacency lists from forward lists.
    6. Populate sdg.seeds_a and sdg.seeds_b with NodeIds of the seed nodes.

    Args:
        diff_result: Seeds from diff_parser, tagged with branch A or B.
        index: Repository-wide function index from indexer.
        depth: Maximum number of call-graph edges to traverse. Intraprocedural
            chains do not consume depth. Default 5.

    Returns:
        Fully constructed SDG ready for detector.
    """
    sdg = SDG()

    # BFS queue: (FunctionLocation, current_depth)
    queue: deque[tuple[FunctionLocation, int]] = deque()
    # Track visited functions by (filepath, lineno) to avoid re-processing
    visited: set[tuple[str, int]] = set()

    def enqueue(loc: FunctionLocation, d: int) -> None:
        key = (str(loc.filepath), loc.lineno)
        if key not in visited:
            visited.add(key)
            queue.append((loc, d))

    for loc in diff_result.seeds_a + diff_result.seeds_b:
        enqueue(loc, 0)

    while queue:
        loc, current_depth = queue.popleft()

        func_node = loc.ast_node
        if func_node is None:
            # Attempt to load from parsed ASTs in the index
            tree = index.parsed_asts.get(loc.filepath)
            if tree is None:
                continue
            func_node = _find_func_in_tree(tree, loc.lineno)
            if func_node is None:
                continue

        pdg = build_pdg(func_node, loc.filepath)

        # Tag nodes belonging to seed functions with branch attribution
        if loc.branch is not None:
            for node in pdg.nodes:
                node.branch = loc.branch  # type: ignore[assignment]

        _merge_pdg_into_sdg(sdg, pdg)

        # Collect seed NodeIds (first node of the function body, or param node)
        if pdg.nodes:
            entry_id = pdg.nodes[0].id
            if loc.branch == "A":
                sdg.seeds_a.add(entry_id)
            elif loc.branch == "B":
                sdg.seeds_b.add(entry_id)

        # Expand call graph up to depth limit
        if current_depth < depth:
            for node in pdg.nodes:
                if node.ast_node is None:
                    continue
                for call_node in ast.walk(node.ast_node):
                    if not isinstance(call_node, ast.Call):
                        continue
                    callees = resolve_call(call_node, index, loc.filepath)
                    for callee in callees:
                        callee_key = (str(callee.filepath), callee.lineno)
                        if callee_key not in visited:
                            enqueue(callee, current_depth + 1)
                            # Add call edge: caller node → callee entry node
                            if callee.ast_node is not None:
                                callee_pdg = build_pdg(callee.ast_node, callee.filepath)
                                if callee_pdg.nodes:
                                    callee_entry_id = callee_pdg.nodes[0].id
                                    _add_call_edge(sdg, node.id, callee_entry_id)

    _build_reverse_edges(sdg)
    return sdg


def _find_func_in_tree(
    tree: ast.Module,
    lineno: int,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Find a function definition at the given line number in an AST module."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.lineno == lineno:
            return node
    return None


def _merge_pdg_into_sdg(sdg: SDG, pdg: object) -> None:
    """Merge a single PDG's nodes and edges into the SDG.

    Adds PDG nodes to sdg.nodes and merges data/control edges into
    sdg.data_edges / sdg.control_edges. Also updates the corresponding reverse
    adjacency lists.

    Args:
        sdg: The SDG being built (mutated in-place).
        pdg: A PDG instance from pdg_builder.
    """
    from paxpy.types import PDG

    assert isinstance(pdg, PDG)

    for node in pdg.nodes:
        sdg.nodes[node.id] = node

    for src, targets in pdg.data_edges.items():
        sdg.data_edges.setdefault(src, set()).update(targets)
        for tgt in targets:
            sdg.reverse_data_edges.setdefault(tgt, set()).add(src)

    for src, targets in pdg.control_edges.items():
        sdg.control_edges.setdefault(src, set()).update(targets)
        for tgt in targets:
            sdg.reverse_control_edges.setdefault(tgt, set()).add(src)


def _add_call_edge(sdg: SDG, caller_node_id: NodeId, callee_node_id: NodeId) -> None:
    """Record a call edge (and its reverse) between two nodes in the SDG.

    Both sdg.call_edges and sdg.reverse_call_edges are updated.

    Args:
        sdg: The SDG being built (mutated in-place).
        caller_node_id: NodeId of the call-site node in the caller.
        callee_node_id: NodeId of the entry node in the callee.
    """
    sdg.call_edges.setdefault(caller_node_id, set()).add(callee_node_id)
    sdg.reverse_call_edges.setdefault(callee_node_id, set()).add(caller_node_id)


def _build_reverse_edges(sdg: SDG) -> None:
    """Populate reverse_* adjacency dicts from the forward adjacency dicts.

    Rebuilds reverse_data_edges, reverse_control_edges, and reverse_call_edges
    from scratch. Called once after all forward edges are finalized.

    Args:
        sdg: The SDG whose reverse dicts are populated in-place.
    """
    sdg.reverse_data_edges = {}
    sdg.reverse_control_edges = {}
    sdg.reverse_call_edges = {}

    for src, targets in sdg.data_edges.items():
        for tgt in targets:
            sdg.reverse_data_edges.setdefault(tgt, set()).add(src)

    for src, targets in sdg.control_edges.items():
        for tgt in targets:
            sdg.reverse_control_edges.setdefault(tgt, set()).add(src)

    for src, targets in sdg.call_edges.items():
        for tgt in targets:
            sdg.reverse_call_edges.setdefault(tgt, set()).add(src)
