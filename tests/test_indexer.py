"""Tests for indexer.py."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from paxpy.indexer import _add_parent_pointers, _extract_functions, _parse_file, build_index
from paxpy.types import FunctionIndex

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def write_py(tmp_path: Path, name: str, source: str) -> Path:
    """Write dedented source to tmp_path/<name> and return the path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _add_parent_pointers
# ---------------------------------------------------------------------------


def test_module_root_has_none_parent():
    tree = ast.parse("x = 1")
    _add_parent_pointers(tree)
    assert tree._parent is None


def test_child_nodes_have_parent():
    tree = ast.parse("x = 1")
    _add_parent_pointers(tree)
    # The Assign node is a direct child of the Module
    assign = tree.body[0]
    assert assign._parent is tree


def test_grandchild_parent_is_assign():
    tree = ast.parse("x = 1")
    _add_parent_pointers(tree)
    assign = tree.body[0]
    # The Name target 'x' is a child of the Assign
    name_node = assign.targets[0]
    assert name_node._parent is assign


def test_all_nodes_have_parent_attr():
    source = "def foo(x):\n    return x + 1\n"
    tree = ast.parse(source)
    _add_parent_pointers(tree)
    for node in ast.walk(tree):
        assert hasattr(node, "_parent"), f"Node {node!r} missing _parent"


# ---------------------------------------------------------------------------
# _parse_file
# ---------------------------------------------------------------------------


def test_parse_file_returns_module(tmp_path):
    p = write_py(tmp_path, "a.py", "x = 1\n")
    result = _parse_file(p)
    assert isinstance(result, ast.Module)


def test_parse_file_adds_parent_pointers(tmp_path):
    p = write_py(tmp_path, "a.py", "x = 1\n")
    tree = _parse_file(p)
    assert tree._parent is None
    assert tree.body[0]._parent is tree


def test_parse_file_syntax_error_returns_none(tmp_path, capsys):
    p = write_py(tmp_path, "bad.py", "def (:\n")
    result = _parse_file(p)
    assert result is None
    assert "syntax error" in capsys.readouterr().err.lower()


def test_parse_file_missing_file_returns_none(tmp_path, capsys):
    result = _parse_file(tmp_path / "nonexistent.py")
    assert result is None
    assert "cannot read" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# _extract_functions
# ---------------------------------------------------------------------------


def test_extract_top_level_function(tmp_path):
    p = write_py(
        tmp_path,
        "a.py",
        """\
        def foo():
            pass
    """,
    )
    tree = ast.parse(p.read_text())
    _add_parent_pointers(tree)
    locs = _extract_functions(p, tree)
    assert len(locs) == 1
    assert locs[0].name == "foo"
    assert locs[0].filepath == p
    assert locs[0].lineno == 1
    assert locs[0].branch is None


def test_extract_async_function(tmp_path):
    p = write_py(
        tmp_path,
        "a.py",
        """\
        async def bar():
            pass
    """,
    )
    tree = ast.parse(p.read_text())
    _add_parent_pointers(tree)
    locs = _extract_functions(p, tree)
    assert len(locs) == 1
    assert locs[0].name == "bar"


def test_extract_nested_functions(tmp_path):
    p = write_py(
        tmp_path,
        "a.py",
        """\
        def outer():
            def inner():
                pass
    """,
    )
    tree = ast.parse(p.read_text())
    _add_parent_pointers(tree)
    locs = _extract_functions(p, tree)
    names = {loc.name for loc in locs}
    assert names == {"outer", "inner"}


def test_extract_multiple_top_level(tmp_path):
    p = write_py(
        tmp_path,
        "a.py",
        """\
        def foo(): pass
        def bar(): pass
        def baz(): pass
    """,
    )
    tree = ast.parse(p.read_text())
    _add_parent_pointers(tree)
    locs = _extract_functions(p, tree)
    assert len(locs) == 3


def test_extract_sets_ast_node(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    tree = ast.parse(p.read_text())
    _add_parent_pointers(tree)
    locs = _extract_functions(p, tree)
    assert isinstance(locs[0].ast_node, ast.FunctionDef)
    assert locs[0].ast_node.name == "foo"


def test_extract_no_functions(tmp_path):
    p = write_py(tmp_path, "a.py", "x = 1\ny = 2\n")
    tree = ast.parse(p.read_text())
    _add_parent_pointers(tree)
    locs = _extract_functions(p, tree)
    assert locs == []


def test_extract_end_lineno(tmp_path):
    p = write_py(
        tmp_path,
        "a.py",
        """\
        def foo():
            x = 1
            return x
    """,
    )
    tree = ast.parse(p.read_text())
    _add_parent_pointers(tree)
    locs = _extract_functions(p, tree)
    assert locs[0].end_lineno == 3


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------


def test_build_index_returns_function_index(tmp_path):
    write_py(tmp_path, "a.py", "def foo(): pass\n")
    result = build_index(tmp_path)
    assert isinstance(result, FunctionIndex)


def test_build_index_populates_index(tmp_path):
    write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    assert "foo" in idx.index
    assert len(idx.index["foo"]) == 1


def test_build_index_populates_parsed_asts(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    assert p in idx.parsed_asts
    assert isinstance(idx.parsed_asts[p], ast.Module)


def test_build_index_multiple_files(tmp_path):
    write_py(tmp_path, "a.py", "def foo(): pass\n")
    write_py(tmp_path, "b.py", "def bar(): pass\n")
    idx = build_index(tmp_path)
    assert "foo" in idx.index
    assert "bar" in idx.index


def test_build_index_ambiguous_name(tmp_path):
    """Two files defining the same function name → both locations returned."""
    write_py(tmp_path, "a.py", "def process(): pass\n")
    write_py(tmp_path, "b.py", "def process(): pass\n")
    idx = build_index(tmp_path)
    assert len(idx.index["process"]) == 2


def test_build_index_skips_unparseable(tmp_path, capsys):
    write_py(tmp_path, "good.py", "def foo(): pass\n")
    write_py(tmp_path, "bad.py", "def (:\n")
    idx = build_index(tmp_path)
    assert "foo" in idx.index
    assert len(idx.parsed_asts) == 1
    assert "syntax error" in capsys.readouterr().err.lower()


def test_build_index_lookup(tmp_path):
    write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    result = idx.lookup("foo")
    assert len(result) == 1
    assert result[0].name == "foo"


def test_build_index_lookup_missing(tmp_path):
    write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    assert idx.lookup("nonexistent") == []


def test_build_index_nested_dirs(tmp_path):
    subdir = tmp_path / "pkg"
    subdir.mkdir()
    write_py(subdir, "mod.py", "def nested_func(): pass\n")
    idx = build_index(tmp_path)
    assert "nested_func" in idx.index
    assert idx.index["nested_func"][0].filepath == subdir / "mod.py"


def test_build_index_empty_dir(tmp_path):
    idx = build_index(tmp_path)
    assert idx.index == {}
    assert idx.parsed_asts == {}


def test_build_index_parsed_asts_have_parent_pointers(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    tree = idx.parsed_asts[p]
    assert tree._parent is None
    assert tree.body[0]._parent is tree
