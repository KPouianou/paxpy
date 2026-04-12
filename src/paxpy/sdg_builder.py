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
from paxpy.types import PDG, SDG, DiffResult, FunctionIndex, FunctionLocation, NodeId


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
    # Map (filepath, lineno) → entry NodeId for functions already processed
    func_entry_ids: dict[tuple[str, int], NodeId] = {}
    # Deferred call edges: callee not yet processed when caller was — resolved after BFS
    pending_call_edges: list[tuple[NodeId, tuple[str, int]]] = []

    def enqueue(loc: FunctionLocation, d: int) -> None:
        key = (str(loc.filepath), loc.lineno)
        if key not in visited:
            visited.add(key)
            queue.append((loc, d))

    for loc in diff_result.seeds_a + diff_result.seeds_b:
        enqueue(loc, 0)

    while queue:
        loc, current_depth = queue.popleft()

        # Always prefer the index's AST — ensure_parsed adds parent pointers
        # to every node (needed by call_resolver for self-method narrowing).
        # loc.ast_node comes from diff_parser which parses its own private AST
        # without parent pointers, so it cannot be used for self.foo() resolution.
        tree = index.ensure_parsed(loc.filepath)
        if tree is not None:
            func_node = _find_func_in_tree(tree, loc.lineno)
        else:
            func_node = None
        if func_node is None:
            # Fallback: use the diff-parser node if the index can't find it
            func_node = loc.ast_node
        if func_node is None:
            continue

        pdg = build_pdg(func_node, loc.filepath)

        # Tag nodes belonging to seed functions with branch attribution
        if loc.branch is not None:
            for node in pdg.nodes:
                node.branch = loc.branch  # type: ignore[assignment]

        # Tag nodes whose line falls within directly modified diff hunks
        if loc.modified_ranges:
            for node in pdg.nodes:
                for range_start, range_end in loc.modified_ranges:
                    if range_start <= node.lineno <= range_end:
                        node.is_direct_modification = True
                        break

        _merge_pdg_into_sdg(sdg, pdg)
        _add_unused_param_flow_edges(sdg, pdg)

        # Record entry node ID for this function so callers can link to it
        if pdg.nodes:
            entry_id = pdg.nodes[0].id
            func_entry_ids[(str(loc.filepath), loc.lineno)] = entry_id
            if loc.branch == "A":
                sdg.seeds_a.add(entry_id)
            elif loc.branch == "B":
                sdg.seeds_b.add(entry_id)

        # Expand call graph up to depth limit
        if current_depth < depth:
            for node in pdg.nodes:
                if node.ast_node is None:
                    continue
                for call_node in _iter_calls_in_stmt(node.ast_node):
                    callees = resolve_call(call_node, index, loc.filepath)
                    for callee in callees:
                        callee_key = (str(callee.filepath), callee.lineno)
                        # Enqueue callee for processing if not yet visited
                        if callee_key not in visited:
                            enqueue(callee, current_depth + 1)
                        # Always add a call edge — even if callee was already visited
                        # (e.g., callee is itself a seed from the other branch).
                        # If the callee hasn't been processed yet, defer the edge
                        # until after the BFS loop so we can look up its entry node.
                        if callee_key in func_entry_ids:
                            _add_call_edge(sdg, node.id, func_entry_ids[callee_key])
                        else:
                            pending_call_edges.append((node.id, callee_key))

    # Resolve deferred call edges now that all functions have been processed
    for caller_id, callee_key in pending_call_edges:
        if callee_key in func_entry_ids:
            _add_call_edge(sdg, caller_id, func_entry_ids[callee_key])

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


def _add_unused_param_flow_edges(sdg: SDG, pdg: object) -> None:
    """Add a data edge from each unused parameter to the first body statement.

    When a function is entered via a call edge, BFS arrives at the first
    parameter node. If that parameter is never used in the body (e.g. an
    intermediate function `def f(x): r = g(); return r` where x is unused),
    BFS has no outgoing edge and cannot explore the function body.

    Adding an edge from each unused param to the first body statement is a
    conservative over-approximation: it allows BFS to continue traversal
    through any function body reached via a call edge, regardless of whether
    parameters are forwarded. This is required for CONTROL_DEPENDENCY paths
    where intermediates call callees with no arguments.

    Args:
        sdg: The SDG being built (mutated in-place).
        pdg: A PDG instance whose param→body edges are added.
    """
    assert isinstance(pdg, PDG)

    param_nodes = [n for n in pdg.nodes if isinstance(n.ast_node, ast.arg)]
    body_nodes = [n for n in pdg.nodes if not isinstance(n.ast_node, ast.arg)]
    if not param_nodes or not body_nodes:
        return

    first_body_id = body_nodes[0].id
    for pn in param_nodes:
        if pn.id not in sdg.data_edges:
            # Param has no outgoing data edges — add one to the first body node
            sdg.data_edges.setdefault(pn.id, set()).add(first_body_id)


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


def _iter_calls_in_stmt(stmt_ast_node: ast.AST) -> list[ast.Call]:
    """Yield ast.Call nodes that are directly in this statement's expression.

    For compound statements (If/While/For/Try/With), only the controlling
    expression is walked (the test, iter, or context), NOT the body statements.
    Body statements are separate PDG nodes and will be walked when their own
    node is processed — walking them here would create spurious call edges from
    the predicate node to callees that live in the body.

    For regular statements (Assign, Return, Expr, etc.) the entire node is
    walked as usual.

    Args:
        stmt_ast_node: The AST node stored on a PDG node.

    Returns:
        List of ast.Call nodes found in the relevant sub-expression.
    """
    if isinstance(stmt_ast_node, ast.If | ast.While):
        root = stmt_ast_node.test
    elif isinstance(stmt_ast_node, ast.For):
        root: ast.AST = stmt_ast_node.iter
    elif isinstance(stmt_ast_node, ast.Try):
        return []
    elif isinstance(stmt_ast_node, ast.With):
        calls: list[ast.Call] = []
        for item in stmt_ast_node.items:
            calls.extend(n for n in ast.walk(item.context_expr) if isinstance(n, ast.Call))
        return calls
    else:
        root = stmt_ast_node

    return [n for n in ast.walk(root) if isinstance(n, ast.Call)]
