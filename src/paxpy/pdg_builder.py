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

from paxpy.types import PDG, Node, NodeId


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
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")


def _compute_control_edges(
    nodes: list[Node],
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
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
    raise NotImplementedError("TODO")
