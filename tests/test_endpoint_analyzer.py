"""Tests for endpoint_analyzer.py."""

from __future__ import annotations

import ast

from paxpy.endpoint_analyzer import (
    _classify_sink_operation,
    _extract_return_type,
    _infer_source_type,
    analyze_endpoints,
)
from paxpy.types import Compatibility, ConflictType, InterferencePath


def parse_func(src: str) -> ast.FunctionDef:
    tree = ast.parse(src)
    return tree.body[0]  # type: ignore[return-value]


def parse_stmt(src: str) -> ast.stmt:
    return ast.parse(src).body[0]


def make_path() -> InterferencePath:
    return InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
    )


# ---------------------------------------------------------------------------
# _extract_return_type
# ---------------------------------------------------------------------------


def test_extract_return_type_int():
    func = parse_func("def foo() -> int: pass")
    assert _extract_return_type(func) == "int"


def test_extract_return_type_str():
    func = parse_func("def foo() -> str: pass")
    assert _extract_return_type(func) == "str"


def test_extract_return_type_list_str():
    func = parse_func("def foo() -> list[str]: pass")
    assert _extract_return_type(func) == "list[str]"


def test_extract_return_type_none_annotation():
    func = parse_func("def foo() -> None: pass")
    assert _extract_return_type(func) == "None"


def test_extract_return_type_no_annotation():
    func = parse_func("def foo(): pass")
    assert _extract_return_type(func) is None


# ---------------------------------------------------------------------------
# _classify_sink_operation
# ---------------------------------------------------------------------------


def test_classify_subscript():
    stmt = parse_stmt("x = data[0]")
    assert _classify_sink_operation(stmt) == "subscript"


def test_classify_attribute():
    stmt = parse_stmt("x = obj.attr")
    assert _classify_sink_operation(stmt) == "attribute"


def test_classify_arithmetic():
    stmt = parse_stmt("x = value + 1")
    assert _classify_sink_operation(stmt) == "arithmetic"


def test_classify_comparison():
    stmt = parse_stmt("x = value > 0")
    assert _classify_sink_operation(stmt) == "comparison"


def test_classify_call():
    stmt = parse_stmt("foo(value)")
    assert _classify_sink_operation(stmt) == "call"


def test_classify_simple_assignment_unknown():
    stmt = parse_stmt("x = y")
    # Plain name assignment has no interesting operation
    assert _classify_sink_operation(stmt) is None


# ---------------------------------------------------------------------------
# _infer_source_type
# ---------------------------------------------------------------------------


def test_infer_source_type_from_int_literal():
    stmt = parse_stmt("x = 42")
    assert _infer_source_type(stmt) == "int"


def test_infer_source_type_from_str_literal():
    stmt = parse_stmt("x = 'hello'")
    assert _infer_source_type(stmt) == "str"


def test_infer_source_type_from_list_literal():
    stmt = parse_stmt("x = [1, 2, 3]")
    assert _infer_source_type(stmt) == "list"


def test_infer_source_type_from_dict_literal():
    stmt = parse_stmt("x = {}")
    assert _infer_source_type(stmt) == "dict"


def test_infer_source_type_from_none():
    stmt = parse_stmt("x = None")
    assert _infer_source_type(stmt) == "None"


def test_infer_source_type_from_return_int():
    stmt = parse_stmt("return 1")
    assert _infer_source_type(stmt) == "int"


def test_infer_source_type_from_func_annotation():
    func = parse_func("def foo() -> int: pass")
    assert _infer_source_type(func) == "int"


def test_infer_source_type_name_expr_unknown():
    stmt = parse_stmt("x = some_var")
    assert _infer_source_type(stmt) is None


# ---------------------------------------------------------------------------
# analyze_endpoints
# ---------------------------------------------------------------------------


def test_analyze_both_none_returns_unknown():
    result = analyze_endpoints(make_path(), None, None)
    assert result.compatibility == Compatibility.UNKNOWN


def test_analyze_int_source_subscript_sink_incompatible():
    source = parse_stmt("return 42")
    sink = parse_stmt("x = value[0]")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE
    assert "subscript" in result.explanation.lower() or "int" in result.explanation


def test_analyze_list_source_subscript_sink_compatible():
    source = parse_stmt("return [1, 2, 3]")
    sink = parse_stmt("x = value[0]")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_none_source_attribute_sink_incompatible():
    source = parse_stmt("return None")
    sink = parse_stmt("x = value.attr")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


def test_analyze_int_source_arithmetic_compatible():
    source = parse_stmt("return 5")
    sink = parse_stmt("result = value + 1")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_str_source_arithmetic_suspicious():
    source = parse_stmt("return 'hello'")
    sink = parse_stmt("result = value - 1")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.SUSPICIOUS


def test_analyze_any_source_comparison_compatible():
    source = parse_stmt("return 42")
    sink = parse_stmt("if value > 0: pass")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_annotated_func_subscript_none_incompatible():
    source = parse_func("def foo() -> None: pass")
    sink = parse_stmt("x = result[0]")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


def test_analyze_result_has_explanation():
    source = parse_stmt("return 1")
    sink = parse_stmt("x = value[0]")
    result = analyze_endpoints(make_path(), source, sink)
    assert isinstance(result.explanation, str)
    assert len(result.explanation) > 0
