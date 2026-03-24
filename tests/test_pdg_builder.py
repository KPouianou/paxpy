"""Tests for pdg_builder.py."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from paxpy.pdg_builder import build_pdg
from paxpy.types import PDG, make_node_id

FILEPATH = Path("/repo/test.py")


def parse_func(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Parse dedented source and return the first function definition."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            return node
    raise ValueError("No function found in source")


def node_ids(pdg: PDG) -> set[str]:
    return {n.id for n in pdg.nodes}


def labels(pdg: PDG) -> set[str]:
    return {n.label for n in pdg.nodes}


def find_node(pdg: PDG, ast_type: type) -> object | None:
    """Find the first PDG node whose ast_node is an instance of ast_type."""
    return next((n for n in pdg.nodes if isinstance(n.ast_node, ast_type)), None)


def find_nodes(pdg: PDG, ast_type: type) -> list:
    """Find all PDG nodes whose ast_node is an instance of ast_type."""
    return [n for n in pdg.nodes if isinstance(n.ast_node, ast_type)]


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_build_pdg_returns_pdg():
    func = parse_func("def foo(): pass\n")
    pdg = build_pdg(func, FILEPATH)
    assert isinstance(pdg, PDG)


def test_build_pdg_function_name():
    func = parse_func("def myfunc(): pass\n")
    pdg = build_pdg(func, FILEPATH)
    assert pdg.function_name == "myfunc"


def test_build_pdg_filepath():
    func = parse_func("def foo(): pass\n")
    pdg = build_pdg(func, FILEPATH)
    assert pdg.filepath == FILEPATH


def test_empty_function_no_edges():
    func = parse_func("def foo(): pass\n")
    pdg = build_pdg(func, FILEPATH)
    assert pdg.data_edges == {}
    assert pdg.control_edges == {}


def test_node_ids_match_make_node_id_format():
    func = parse_func("""\
        def foo():
            x = 1
    """)
    pdg = build_pdg(func, FILEPATH)
    for node in pdg.nodes:
        expected = make_node_id(FILEPATH, node.lineno, node.col_offset)
        assert node.id == expected


def test_enclosing_function_set():
    func = parse_func("""\
        def myfunc():
            x = 1
    """)
    pdg = build_pdg(func, FILEPATH)
    stmt_nodes = [n for n in pdg.nodes if "x" in n.label]
    assert all(n.enclosing_function == "myfunc" for n in stmt_nodes)


# ---------------------------------------------------------------------------
# Node collection
# ---------------------------------------------------------------------------


def test_assignment_creates_node():
    func = parse_func("""\
        def foo():
            x = 1
    """)
    pdg = build_pdg(func, FILEPATH)
    assert any("x" in n.label for n in pdg.nodes)


def test_return_creates_node():
    func = parse_func("""\
        def foo():
            return 1
    """)
    pdg = build_pdg(func, FILEPATH)
    assert any("return" in n.label for n in pdg.nodes)


def test_call_expr_creates_node():
    func = parse_func("""\
        def foo():
            bar()
    """)
    pdg = build_pdg(func, FILEPATH)
    assert any("bar()" in n.label for n in pdg.nodes)


def test_param_creates_node():
    func = parse_func("def foo(x, y): pass\n")
    pdg = build_pdg(func, FILEPATH)
    assert any("param:x" in n.label for n in pdg.nodes)
    assert any("param:y" in n.label for n in pdg.nodes)


def test_if_test_creates_node():
    func = parse_func("""\
        def foo(x):
            if x > 0:
                pass
    """)
    pdg = build_pdg(func, FILEPATH)
    assert any("if" in n.label for n in pdg.nodes)


def test_nested_function_not_included():
    func = parse_func("""\
        def outer():
            def inner():
                x = 1
            return 2
    """)
    pdg = build_pdg(func, FILEPATH)
    # inner's body (x = 1) should not appear
    assert not any("x = 1" in n.label for n in pdg.nodes)
    assert any("return" in n.label for n in pdg.nodes)


def test_async_function():
    func = parse_func("""\
        async def foo():
            x = 1
            return x
    """)
    pdg = build_pdg(func, FILEPATH)
    assert pdg.function_name == "foo"
    assert any("x" in n.label for n in pdg.nodes)


def test_augassign_creates_node():
    func = parse_func("""\
        def foo():
            x = 0
            x += 1
    """)
    pdg = build_pdg(func, FILEPATH)
    assert any("x += 1" in n.label for n in pdg.nodes)


# ---------------------------------------------------------------------------
# Data edges
# ---------------------------------------------------------------------------


def test_data_edge_simple_def_use():
    func = parse_func("""\
        def foo():
            x = 1
            return x
    """)
    pdg = build_pdg(func, FILEPATH)
    # Should have at least one data edge (x=1 → return x)
    assert len(pdg.data_edges) > 0
    all_targets = {t for targets in pdg.data_edges.values() for t in targets}
    return_nodes = [n for n in pdg.nodes if "return" in n.label]
    assert any(n.id in all_targets for n in return_nodes)


def test_data_edge_param_to_return():
    func = parse_func("def foo(x): return x\n")
    pdg = build_pdg(func, FILEPATH)
    param_nodes = [n for n in pdg.nodes if "param:x" in n.label]
    return_nodes = [n for n in pdg.nodes if "return" in n.label]
    assert param_nodes and return_nodes
    # param should have a data edge to return
    param_id = param_nodes[0].id
    return_id = return_nodes[0].id
    assert return_id in pdg.data_edges.get(param_id, set())


def test_data_edge_redefinition():
    """Second assignment to x should reach return, not first."""
    func = parse_func("""\
        def foo():
            x = 1
            x = 2
            return x
    """)
    pdg = build_pdg(func, FILEPATH)
    assign2 = next((n for n in pdg.nodes if n.label == "x = 2"), None)
    return_node = next((n for n in pdg.nodes if "return" in n.label), None)
    if assign2 and return_node:
        assert return_node.id in pdg.data_edges.get(assign2.id, set())


def test_data_edge_augassign_chain():
    func = parse_func("""\
        def foo():
            x = 0
            x += 1
    """)
    pdg = build_pdg(func, FILEPATH)
    # x = 0 should have edge to x += 1 (x is used in augassign)
    assign_node = find_node(pdg, ast.Assign)
    augassign_node = find_node(pdg, ast.AugAssign)
    assert assign_node and augassign_node
    assert augassign_node.id in pdg.data_edges.get(assign_node.id, set())


def test_no_spurious_data_edges_for_unrelated_vars():
    func = parse_func("""\
        def foo():
            x = 1
            y = 2
            return y
    """)
    pdg = build_pdg(func, FILEPATH)
    x_node = next((n for n in pdg.nodes if n.label == "x = 1"), None)
    return_node = next((n for n in pdg.nodes if "return" in n.label), None)
    if x_node and return_node:
        # x should NOT have an edge to return (return uses y, not x)
        assert return_node.id not in pdg.data_edges.get(x_node.id, set())


# ---------------------------------------------------------------------------
# Control edges
# ---------------------------------------------------------------------------


def test_control_edge_if():
    func = parse_func("""\
        def foo(x):
            if x > 0:
                return x
    """)
    pdg = build_pdg(func, FILEPATH)
    if_node = find_node(pdg, ast.If)
    return_node = find_node(pdg, ast.Return)
    assert if_node and return_node
    assert return_node.id in pdg.control_edges.get(if_node.id, set())


def test_control_edge_while():
    func = parse_func("""\
        def foo(n):
            while n > 0:
                n -= 1
    """)
    pdg = build_pdg(func, FILEPATH)
    while_node = find_node(pdg, ast.While)
    body_node = find_node(pdg, ast.AugAssign)
    assert while_node and body_node
    assert body_node.id in pdg.control_edges.get(while_node.id, set())


def test_control_edge_for():
    func = parse_func("""\
        def foo(items):
            for item in items:
                process(item)
    """)
    pdg = build_pdg(func, FILEPATH)
    for_node = find_node(pdg, ast.For)
    body_node = find_node(pdg, ast.Expr)
    assert for_node and body_node
    assert body_node.id in pdg.control_edges.get(for_node.id, set())


def test_control_edge_nested_if():
    func = parse_func("""\
        def foo(x, y):
            if x:
                if y:
                    return 1
    """)
    pdg = build_pdg(func, FILEPATH)
    if_nodes = find_nodes(pdg, ast.If)
    return_node = find_node(pdg, ast.Return)
    # The inner if should control the return
    assert return_node is not None and len(if_nodes) == 2
    controlled = any(return_node.id in pdg.control_edges.get(if_n.id, set()) for if_n in if_nodes)
    assert controlled


def test_no_control_edges_simple_sequence():
    func = parse_func("""\
        def foo():
            x = 1
            y = 2
    """)
    pdg = build_pdg(func, FILEPATH)
    assert pdg.control_edges == {}


def test_if_else_both_branches_controlled():
    func = parse_func("""\
        def foo(x):
            if x:
                return 1
            else:
                return 2
    """)
    pdg = build_pdg(func, FILEPATH)
    if_node = find_node(pdg, ast.If)
    return_nodes = find_nodes(pdg, ast.Return)
    assert if_node and len(return_nodes) == 2
    controlled = pdg.control_edges.get(if_node.id, set())
    assert all(r.id in controlled for r in return_nodes)
