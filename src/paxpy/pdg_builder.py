"""Build an intraprocedural Program Dependence Graph (PDG) for a single function.

Given a FunctionDef (or AsyncFunctionDef) AST node and its containing file
path, this module computes:

  - Data dependence edges via reaching definitions: an edge u → v means that
    a definition at u reaches a use at v with no intervening re-definition on
    every path.
  - Control dependence edges: an edge u → v means that the execution of v is
    directly controlled by the predicate at u (the condition of an if/while/for
    or the try header).

One Node is created per statement or expression of interest. Nodes carry their
AST node reference, source location, and the enclosing function name.

Depends on: types only.
"""

from __future__ import annotations

import ast
from pathlib import Path

from paxpy.types import PDG, Node, NodeId, make_node_id

# Maximum label length for human-readable node descriptions
_MAX_LABEL = 60


def build_pdg(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
) -> PDG:
    """Build the Program Dependence Graph for a single function.

    Creates one Node per statement/expression of interest (assignments, calls,
    returns, conditions). Computes data edges via a reaching-definitions
    dataflow pass and control edges from predicate nodes to their dominated
    body statements.

    Args:
        func_node: The AST node of the function to analyse. Must have lineno
            and col_offset attributes set (i.e., parsed with ast.parse, not
            hand-constructed without location info).
        filepath: Absolute path to the file containing `func_node`.

    Returns:
        PDG with nodes, data_edges, and control_edges populated.
    """
    nodes = _collect_nodes(func_node, filepath)
    data_edges = _compute_data_edges(nodes, func_node)
    control_edges = _compute_control_edges(nodes, func_node, filepath)

    return PDG(
        function_name=func_node.name,
        filepath=filepath,
        nodes=nodes,
        data_edges=data_edges,
        control_edges=control_edges,
    )


# ---------------------------------------------------------------------------
# Node collection
# ---------------------------------------------------------------------------


def _collect_nodes(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
) -> list[Node]:
    """Walk the function body and create a Node for each statement of interest.

    Statements of interest: assignments (Assign, AugAssign, AnnAssign),
    expression statements (Expr containing a Call), return statements, and
    condition nodes (the test of If/While/For, the orelse heads).

    Args:
        func_node: Function whose body is walked.
        filepath: Used to construct NodeId values.

    Returns:
        Flat list of Node objects in source order.
    """
    nodes: list[Node] = []

    # Add one node per parameter (they are definitions at the function entry)
    for arg in func_node.args.args + func_node.args.posonlyargs + func_node.args.kwonlyargs:
        if func_node.args.vararg and func_node.args.vararg is arg:
            continue
        nid = make_node_id(filepath, func_node.lineno, arg.col_offset)
        nodes.append(
            Node(
                id=nid,
                filepath=filepath,
                lineno=func_node.lineno,
                col_offset=arg.col_offset,
                ast_node=arg,
                label=f"param:{arg.arg}",
                enclosing_function=func_node.name,
            )
        )
    if func_node.args.vararg:
        arg = func_node.args.vararg
        nid = make_node_id(filepath, func_node.lineno, arg.col_offset)
        nodes.append(
            Node(
                id=nid,
                filepath=filepath,
                lineno=func_node.lineno,
                col_offset=arg.col_offset,
                ast_node=arg,
                label=f"param:*{arg.arg}",
                enclosing_function=func_node.name,
            )
        )
    if func_node.args.kwarg:
        arg = func_node.args.kwarg
        nid = make_node_id(filepath, func_node.lineno, arg.col_offset)
        nodes.append(
            Node(
                id=nid,
                filepath=filepath,
                lineno=func_node.lineno,
                col_offset=arg.col_offset,
                ast_node=arg,
                label=f"param:**{arg.arg}",
                enclosing_function=func_node.name,
            )
        )

    _walk_body(func_node.body, filepath, func_node.name, nodes)
    return nodes


def _walk_body(
    stmts: list[ast.stmt],
    filepath: Path,
    func_name: str,
    out: list[Node],
) -> None:
    """Recursively walk a list of statements and append nodes to `out`."""
    for stmt in stmts:
        match stmt:
            case ast.Assign() | ast.AugAssign() | ast.AnnAssign() | ast.Return():
                out.append(_make_node(stmt, filepath, func_name))

            case ast.Expr(value=ast.Call()):
                out.append(_make_node(stmt, filepath, func_name))

            case ast.If(body=body, orelse=orelse):
                # Predicate node for the condition
                out.append(_make_node(stmt, filepath, func_name))
                _walk_body(body, filepath, func_name, out)
                _walk_body(orelse, filepath, func_name, out)

            case ast.While(body=body, orelse=orelse):
                out.append(_make_node(stmt, filepath, func_name))
                _walk_body(body, filepath, func_name, out)
                _walk_body(orelse, filepath, func_name, out)

            case ast.For(body=body, orelse=orelse):
                out.append(_make_node(stmt, filepath, func_name))
                _walk_body(body, filepath, func_name, out)
                _walk_body(orelse, filepath, func_name, out)

            case ast.Try():
                # Try header itself
                out.append(_make_node(stmt, filepath, func_name))
                _walk_body(stmt.body, filepath, func_name, out)
                for handler in stmt.handlers:
                    _walk_body(handler.body, filepath, func_name, out)
                _walk_body(stmt.orelse, filepath, func_name, out)
                _walk_body(stmt.finalbody, filepath, func_name, out)

            case ast.With(body=body):
                out.append(_make_node(stmt, filepath, func_name))
                _walk_body(body, filepath, func_name, out)

            case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
                # Don't descend into nested definitions
                pass

            case _:
                # Other statements (pass, break, continue, raise, assert, delete…)
                out.append(_make_node(stmt, filepath, func_name))


def _make_node(stmt: ast.stmt, filepath: Path, func_name: str) -> Node:
    """Create a Node for a statement."""
    nid = make_node_id(filepath, stmt.lineno, stmt.col_offset)
    try:
        label = ast.unparse(stmt)[:_MAX_LABEL]
    except Exception:
        label = type(stmt).__name__
    return Node(
        id=nid,
        filepath=filepath,
        lineno=stmt.lineno,
        col_offset=stmt.col_offset,
        ast_node=stmt,
        label=label,
        enclosing_function=func_name,
    )


# ---------------------------------------------------------------------------
# Data edges — reaching definitions
# ---------------------------------------------------------------------------


def _compute_data_edges(
    nodes: list[Node],
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[NodeId, set[NodeId]]:
    """Compute def-use data dependence edges via reaching definitions.

    For each variable name, tracks all statements that define it. For each use
    of a name, adds an edge from each reaching definition to the using node.
    Uses a simple intra-block reaching-definitions pass (linear scan, no SSA).

    Args:
        nodes: All nodes in the function, in source order.
        func_node: The function AST node (used to extract parameter names as
            initial definitions).

    Returns:
        Forward adjacency dict: def_node_id → {use_node_id, ...}.
    """
    edges: dict[NodeId, set[NodeId]] = {}

    # Build a lookup: (lineno, col_offset) → node_id for fast matching
    node_by_loc: dict[tuple[int, int], NodeId] = {(n.lineno, n.col_offset): n.id for n in nodes}

    # Initial defs: one per parameter, all at function lineno
    defs: dict[str, list[NodeId]] = {}
    for arg in func_node.args.args + func_node.args.posonlyargs + func_node.args.kwonlyargs:
        nid = node_by_loc.get((func_node.lineno, arg.col_offset))
        if nid:
            defs.setdefault(arg.arg, []).append(nid)
    if func_node.args.vararg:
        arg = func_node.args.vararg
        nid = node_by_loc.get((func_node.lineno, arg.col_offset))
        if nid:
            defs.setdefault(arg.arg, []).append(nid)
    if func_node.args.kwarg:
        arg = func_node.args.kwarg
        nid = node_by_loc.get((func_node.lineno, arg.col_offset))
        if nid:
            defs.setdefault(arg.arg, []).append(nid)

    # Linear scan over statement nodes (skip param nodes at top)
    stmt_nodes = [n for n in nodes if n.ast_node and not isinstance(n.ast_node, ast.arg)]
    _scan_stmts_for_edges(stmt_nodes, defs, edges)

    return edges


def _scan_stmts_for_edges(
    nodes: list[Node],
    defs: dict[str, list[NodeId]],
    edges: dict[NodeId, set[NodeId]],
) -> None:
    """Process a flat list of statement nodes, updating defs and edges in-place."""
    for node in nodes:
        stmt = node.ast_node
        if stmt is None:
            continue

        # Find names used by this statement
        used = _get_used_names(stmt)
        for name in used:
            for def_nid in defs.get(name, []):
                edges.setdefault(def_nid, set()).add(node.id)

        # Find names defined by this statement — update reaching defs
        defined = _get_defined_names(stmt)
        for name in defined:
            defs[name] = [node.id]


def _get_used_names(node: ast.AST) -> set[str]:
    """Collect all Name(ctx=Load) identifiers in the subtree.

    Special case: AugAssign (x += 1) reads its target even though the AST
    marks it with Store context, so we include the target name as a use.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.add(child.id)
    # AugAssign reads target before writing — treat target name as used
    if isinstance(node, ast.AugAssign):
        names.update(_extract_store_names(node.target))
    return names


def _get_defined_names(node: ast.AST) -> set[str]:
    """Collect all names defined (stored to) by this statement."""
    names: set[str] = set()
    match node:
        case ast.Assign(targets=targets):
            for t in targets:
                names.update(_extract_store_names(t))
        case ast.AugAssign(target=target):
            names.update(_extract_store_names(target))
        case ast.AnnAssign(target=target) if (
            isinstance(node, ast.AnnAssign) and node.value is not None
        ):
            names.update(_extract_store_names(target))
        case ast.For(target=target):
            names.update(_extract_store_names(target))
    return names


def _extract_store_names(node: ast.expr) -> set[str]:
    """Recursively extract Name nodes being stored to (handles tuples)."""
    match node:
        case ast.Name(id=name):
            return {name}
        case ast.Tuple(elts=elts) | ast.List(elts=elts):
            result: set[str] = set()
            for elt in elts:
                result.update(_extract_store_names(elt))
            return result
        case ast.Starred(value=v):
            return _extract_store_names(v)
        case _:
            return set()


# ---------------------------------------------------------------------------
# Control edges
# ---------------------------------------------------------------------------


def _compute_control_edges(
    nodes: list[Node],
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    filepath: Path,
) -> dict[NodeId, set[NodeId]]:
    """Compute control dependence edges from predicate nodes to their body nodes.

    An edge predicate → statement means `statement` is directly control-
    dependent on `predicate`. Covers: If (test → body + orelse), While
    (test → body), For (target/iter → body), Try (header → handlers).

    Args:
        nodes: All nodes in the function, in source order.
        func_node: Used to walk the AST structure for control predicates.

    Returns:
        Forward adjacency dict: predicate_node_id → {body_node_id, ...}.
    """
    edges: dict[NodeId, set[NodeId]] = {}

    # Build lookup: (lineno, col_offset) → node_id
    node_by_loc: dict[tuple[int, int], NodeId] = {(n.lineno, n.col_offset): n.id for n in nodes}

    _walk_control(func_node.body, filepath, node_by_loc, edges)
    return edges


def _walk_control(
    stmts: list[ast.stmt],
    filepath: Path,
    node_by_loc: dict[tuple[int, int], NodeId],
    edges: dict[NodeId, set[NodeId]],
) -> None:
    """Recursively walk statements, adding control edges for compound statements."""
    for stmt in stmts:
        pred_key = (stmt.lineno, stmt.col_offset)
        pred_nid = node_by_loc.get(pred_key)

        match stmt:
            case ast.If(body=body, orelse=orelse):
                if pred_nid:
                    _add_control_edges_for_body(pred_nid, body + orelse, node_by_loc, edges)
                _walk_control(body, filepath, node_by_loc, edges)
                _walk_control(orelse, filepath, node_by_loc, edges)

            case ast.While(body=body, orelse=orelse):
                if pred_nid:
                    _add_control_edges_for_body(pred_nid, body, node_by_loc, edges)
                _walk_control(body, filepath, node_by_loc, edges)

            case ast.For(body=body, orelse=orelse):
                if pred_nid:
                    _add_control_edges_for_body(pred_nid, body, node_by_loc, edges)
                _walk_control(body, filepath, node_by_loc, edges)

            case ast.Try():
                handler_bodies = [s for h in stmt.handlers for s in h.body]
                all_body = stmt.body + handler_bodies + stmt.orelse + stmt.finalbody
                if pred_nid:
                    _add_control_edges_for_body(pred_nid, all_body, node_by_loc, edges)
                _walk_control(stmt.body, filepath, node_by_loc, edges)
                for handler in stmt.handlers:
                    _walk_control(handler.body, filepath, node_by_loc, edges)

            case ast.With(body=body):
                if pred_nid:
                    _add_control_edges_for_body(pred_nid, body, node_by_loc, edges)
                _walk_control(body, filepath, node_by_loc, edges)


def _add_control_edges_for_body(
    pred_nid: NodeId,
    body: list[ast.stmt],
    node_by_loc: dict[tuple[int, int], NodeId],
    edges: dict[NodeId, set[NodeId]],
) -> None:
    """Add direct control edges from pred_nid to each statement in body."""
    for stmt in body:
        target_nid = node_by_loc.get((stmt.lineno, stmt.col_offset))
        if target_nid:
            edges.setdefault(pred_nid, set()).add(target_nid)
