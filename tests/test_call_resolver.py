"""Tests for call_resolver.py."""

from __future__ import annotations

import ast
from pathlib import Path

from paxpy.call_resolver import _extract_call_name, _is_builtin, resolve_call
from paxpy.types import FunctionIndex, FunctionLocation

FILEPATH = Path("/repo/mod.py")


def make_index(*funcs: tuple[str, str]) -> FunctionIndex:
    """Build a FunctionIndex from (name, filepath) pairs."""
    index: dict[str, list[FunctionLocation]] = {}
    for name, fpath in funcs:
        loc = FunctionLocation(name=name, filepath=Path(fpath), lineno=1, end_lineno=5)
        index.setdefault(name, []).append(loc)
    return FunctionIndex(index=index)


def parse_call(expr: str) -> ast.Call:
    """Parse a call expression string and return the ast.Call node."""
    tree = ast.parse(expr, mode="eval")
    assert isinstance(tree.body, ast.Call)
    return tree.body


# ---------------------------------------------------------------------------
# _extract_call_name
# ---------------------------------------------------------------------------


def test_extract_simple_name():
    call = parse_call("foo()")
    assert _extract_call_name(call) == "foo"


def test_extract_attribute_name():
    call = parse_call("obj.method()")
    assert _extract_call_name(call) == "method"


def test_extract_chained_attribute():
    call = parse_call("a.b.method()")
    assert _extract_call_name(call) == "method"


def test_extract_subscript_returns_none():
    call = parse_call("funcs[0]()")
    assert _extract_call_name(call) is None


def test_extract_lambda_call_returns_none():
    # (lambda: None)() — the func is a Lambda node
    call = parse_call("(lambda: None)()")
    assert _extract_call_name(call) is None


# ---------------------------------------------------------------------------
# _is_builtin
# ---------------------------------------------------------------------------


def test_is_builtin_len():
    assert _is_builtin("len") is True


def test_is_builtin_print():
    assert _is_builtin("print") is True


def test_is_builtin_range():
    assert _is_builtin("range") is True


def test_is_builtin_user_func():
    assert _is_builtin("my_custom_func") is False


def test_is_builtin_type():
    assert _is_builtin("type") is True


# ---------------------------------------------------------------------------
# resolve_call
# ---------------------------------------------------------------------------


def test_resolve_indexed_function():
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("foo()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 1
    assert result[0].name == "foo"


def test_resolve_ambiguous_returns_all():
    index = make_index(("process", "/repo/a.py"), ("process", "/repo/b.py"))
    call = parse_call("process()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 2
    assert all(r.name == "process" for r in result)


def test_resolve_builtin_returns_empty():
    index = make_index()  # empty index
    call = parse_call("len(x)")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_unknown_external_returns_empty():
    index = make_index()
    call = parse_call("requests_get()")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_attribute_call_by_method_name():
    """obj.process() resolves to all functions named 'process' (over-approx)."""
    index = make_index(("process", "/repo/a.py"))
    call = parse_call("self.process()")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 1
    assert result[0].name == "process"


def test_resolve_attribute_not_in_index_returns_empty():
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("obj.unknown_method()")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_subscript_call_returns_empty():
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("funcs[0]()")
    result = resolve_call(call, index, FILEPATH)
    assert result == []


def test_resolve_indexed_function_shadows_builtin():
    """If user defines a function named 'len', it appears in index and is returned."""
    index = make_index(("len", "/repo/a.py"))
    call = parse_call("len(x)")
    result = resolve_call(call, index, FILEPATH)
    assert len(result) == 1
    assert result[0].filepath == Path("/repo/a.py")


def test_resolve_returns_list_copy():
    """Result should be a copy, not the live index list."""
    index = make_index(("foo", "/repo/a.py"))
    call = parse_call("foo()")
    result = resolve_call(call, index, FILEPATH)
    result.clear()
    assert len(index.lookup("foo")) == 1
