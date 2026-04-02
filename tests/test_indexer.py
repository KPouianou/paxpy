"""Tests for indexer.py and the lazy-indexing behaviour in FunctionIndex."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from paxpy.indexer import _scan_defs, build_index
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
# _scan_defs  (fast line-scan, no AST)
# ---------------------------------------------------------------------------


def test_scan_defs_top_level(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    results = _scan_defs(p)
    assert results == [("foo", 1)]


def test_scan_defs_async(tmp_path):
    p = write_py(tmp_path, "a.py", "async def bar(): pass\n")
    results = _scan_defs(p)
    assert results == [("bar", 1)]


def test_scan_defs_nested(tmp_path):
    p = write_py(
        tmp_path,
        "a.py",
        """\
        def outer():
            def inner():
                pass
        """,
    )
    results = _scan_defs(p)
    names = [r[0] for r in results]
    assert "outer" in names
    assert "inner" in names


def test_scan_defs_multiple(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\ndef bar(): pass\n")
    results = _scan_defs(p)
    assert len(results) == 2
    assert results[0][0] == "foo"
    assert results[1][0] == "bar"


def test_scan_defs_no_functions(tmp_path):
    p = write_py(tmp_path, "a.py", "x = 1\ny = 2\n")
    assert _scan_defs(p) == []


def test_scan_defs_missing_file(tmp_path):
    assert _scan_defs(tmp_path / "nonexistent.py") == []


def test_scan_defs_returns_line_numbers(tmp_path):
    p = write_py(
        tmp_path,
        "a.py",
        """\
        x = 1
        y = 2
        def foo(): pass
        """,
    )
    results = _scan_defs(p)
    assert results == [("foo", 3)]


# ---------------------------------------------------------------------------
# FunctionIndex.ensure_parsed  (lazy AST parse + parent pointers)
# ---------------------------------------------------------------------------


def test_ensure_parsed_returns_module(tmp_path):
    p = write_py(tmp_path, "a.py", "x = 1\n")
    idx = build_index(tmp_path)
    tree = idx.ensure_parsed(p)
    assert isinstance(tree, ast.Module)


def test_ensure_parsed_adds_parent_pointers(tmp_path):
    p = write_py(tmp_path, "a.py", "x = 1\n")
    idx = build_index(tmp_path)
    tree = idx.ensure_parsed(p)
    assert tree._parent is None
    assert tree.body[0]._parent is tree


def test_ensure_parsed_all_nodes_have_parent(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(x):\n    return x + 1\n")
    idx = build_index(tmp_path)
    tree = idx.ensure_parsed(p)
    for node in ast.walk(tree):
        assert hasattr(node, "_parent"), f"Node {node!r} missing _parent"


def test_ensure_parsed_caches_result(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    tree1 = idx.ensure_parsed(p)
    tree2 = idx.ensure_parsed(p)
    assert tree1 is tree2


def test_ensure_parsed_populates_parsed_asts(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    assert p not in idx.parsed_asts  # lazy: not populated yet
    idx.ensure_parsed(p)
    assert p in idx.parsed_asts


def test_ensure_parsed_backfills_ast_node(tmp_path):
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    assert idx.lookup("foo")[0].ast_node is None  # not yet parsed
    idx.ensure_parsed(p)
    assert isinstance(idx.lookup("foo")[0].ast_node, ast.FunctionDef)


def test_ensure_parsed_syntax_error_returns_none(tmp_path):
    p = write_py(tmp_path, "bad.py", "def (:\n")
    idx = build_index(tmp_path)
    result = idx.ensure_parsed(p)
    assert result is None


def test_ensure_parsed_missing_file_returns_none(tmp_path):
    idx = FunctionIndex()
    result = idx.ensure_parsed(tmp_path / "nonexistent.py")
    assert result is None


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


def test_build_index_parsed_asts_empty_until_accessed(tmp_path):
    """parsed_asts is empty after build_index (lazy)."""
    write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    assert idx.parsed_asts == {}


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


def test_build_index_unparseable_file_not_indexed(tmp_path):
    """Unparseable files are excluded from the fast scan too (def (: is not a valid def)."""
    write_py(tmp_path, "good.py", "def foo(): pass\n")
    write_py(tmp_path, "bad.py", "def (:\n")  # no valid identifier after def
    idx = build_index(tmp_path)
    assert "foo" in idx.index
    # bad.py has no valid def lines so nothing from it is indexed
    assert all(loc.filepath.name != "bad.py" for locs in idx.index.values() for loc in locs)


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


def test_build_index_ast_node_none_until_parsed(tmp_path):
    """build_index leaves ast_node as None; it's populated lazily."""
    p = write_py(tmp_path, "a.py", "def foo(): pass\n")
    idx = build_index(tmp_path)
    assert idx.lookup("foo")[0].ast_node is None
    idx.ensure_parsed(p)
    assert idx.lookup("foo")[0].ast_node is not None
