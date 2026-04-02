"""Tests for endpoint_analyzer.py.

Covers the two filtering layers:
  1. Unchanged body filter — both nodes branch=None → COMPATIBLE (suppress).
  2. Type/operation compatibility — source return type vs. sink usage pattern.

Tests are organised by the scenario described in the specification:
  A. Source return type incompatible with sink operation → flag (INCOMPATIBLE/SUSPICIOUS)
  B. Source return type compatible with sink operation → suppress (COMPATIBLE)
  C. Neither source nor sink changed in diff → suppress (COMPATIBLE)
  D. Source changed, sink just passes value through → report conservatively (UNKNOWN)
"""

from __future__ import annotations

import ast
from pathlib import Path

from paxpy.endpoint_analyzer import (
    _annotation_to_tag,
    _assess_compatibility,
    _classify_sink_operation,
    _extract_return_type,
    _find_enclosing_function,
    _infer_source_type,
    analyze_endpoints,
)
from paxpy.types import Compatibility, ConflictType, InterferencePath, Node

_FP = Path("/repo/test.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_parents(tree: ast.Module) -> None:
    """Attach _parent pointers to every node in the tree."""
    tree._parent = None  # type: ignore[attr-defined]
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


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


def node_from_stmt(src: str, branch: str | None, lineno: int = 1) -> Node:
    """Build a Node whose ast_node is the first statement of *src*."""
    tree = ast.parse(src)
    _add_parents(tree)
    return Node(
        id=f"{_FP}:{lineno}:0",
        filepath=_FP,
        lineno=lineno,
        col_offset=0,
        ast_node=tree.body[0],
        branch=branch,  # type: ignore[arg-type]
    )


def node_from_func(src: str, branch: str | None, lineno: int = 1) -> Node:
    """Build a Node whose ast_node is the FunctionDef from *src*."""
    tree = ast.parse(src)
    _add_parents(tree)
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef)
    return Node(
        id=f"{_FP}:{lineno}:0",
        filepath=_FP,
        lineno=lineno,
        col_offset=0,
        ast_node=func,
        branch=branch,  # type: ignore[arg-type]
    )


# ---------------------------------------------------------------------------
# _annotation_to_tag
# ---------------------------------------------------------------------------


def test_annotation_to_tag_primitives():
    assert _annotation_to_tag("int") == "int"
    assert _annotation_to_tag("str") == "str"
    assert _annotation_to_tag("float") == "float"
    assert _annotation_to_tag("bool") == "bool"
    assert _annotation_to_tag("None") == "None"
    assert _annotation_to_tag("list") == "list"
    assert _annotation_to_tag("dict") == "dict"


def test_annotation_to_tag_generics():
    assert _annotation_to_tag("list[str]") == "list"
    assert _annotation_to_tag("dict[str, int]") == "dict"
    assert _annotation_to_tag("tuple[int, ...]") == "tuple"


def test_annotation_to_tag_optional():
    assert _annotation_to_tag("Optional[int]") == "int"
    assert _annotation_to_tag("Optional[list[str]]") == "list"


def test_annotation_to_tag_union():
    assert _annotation_to_tag("int | None") == "int"
    assert _annotation_to_tag("None | str") == "str"
    assert _annotation_to_tag("list[int] | None") == "list"


def test_annotation_to_tag_unknown_type():
    assert _annotation_to_tag("Session") is None
    assert _annotation_to_tag("MyClass") is None


# ---------------------------------------------------------------------------
# _extract_return_type  (now returns simplified tag via _annotation_to_tag)
# ---------------------------------------------------------------------------


def test_extract_return_type_int():
    func = parse_func("def foo() -> int: pass")
    assert _extract_return_type(func) == "int"


def test_extract_return_type_str():
    func = parse_func("def foo() -> str: pass")
    assert _extract_return_type(func) == "str"


def test_extract_return_type_list_simplified():
    # list[str] is simplified to "list" — the tag the rules understand
    func = parse_func("def foo() -> list[str]: pass")
    assert _extract_return_type(func) == "list"


def test_extract_return_type_none_annotation():
    func = parse_func("def foo() -> None: pass")
    assert _extract_return_type(func) == "None"


def test_extract_return_type_no_annotation():
    func = parse_func("def foo(): pass")
    assert _extract_return_type(func) is None


def test_extract_return_type_optional_int():
    func = parse_func("def foo() -> Optional[int]: pass")
    assert _extract_return_type(func) == "int"


def test_extract_return_type_union_with_none():
    func = parse_func("def foo() -> int | None: pass")
    assert _extract_return_type(func) == "int"


# ---------------------------------------------------------------------------
# _find_enclosing_function
# ---------------------------------------------------------------------------


def test_find_enclosing_function_finds_def():
    src = "def foo() -> int:\n    x = 1\n"
    tree = ast.parse(src)
    _add_parents(tree)
    func = tree.body[0]
    stmt = func.body[0]  # x = 1
    result = _find_enclosing_function(stmt)
    assert isinstance(result, ast.FunctionDef)
    assert result.name == "foo"


def test_find_enclosing_function_no_parent_attrs():
    tree = ast.parse("x = 1")
    stmt = tree.body[0]  # no _parent added
    assert _find_enclosing_function(stmt) is None


def test_find_enclosing_function_module_level():
    tree = ast.parse("x = 1")
    _add_parents(tree)
    stmt = tree.body[0]
    assert _find_enclosing_function(stmt) is None


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


def test_infer_source_type_walks_up_to_enclosing_func():
    """A plain call-result assignment inside a typed function uses the annotation."""
    src = "def foo() -> list:\n    x = some_call()\n"
    tree = ast.parse(src)
    _add_parents(tree)
    stmt = tree.body[0].body[0]  # x = some_call()
    assert _infer_source_type(stmt) == "list"


def test_infer_source_type_generic_annotation_simplified():
    src = "def foo() -> list[str]:\n    pass\n"
    tree = ast.parse(src)
    _add_parents(tree)
    func = tree.body[0]
    assert _infer_source_type(func) == "list"


# ---------------------------------------------------------------------------
# _assess_compatibility — rule coverage
# ---------------------------------------------------------------------------


def test_assess_source_none_returns_unknown():
    assert _assess_compatibility(None, "subscript").compatibility == Compatibility.UNKNOWN


def test_assess_subscript_on_list_compatible():
    assert _assess_compatibility("list", "subscript").compatibility == Compatibility.COMPATIBLE


def test_assess_subscript_on_dict_compatible():
    assert _assess_compatibility("dict", "subscript").compatibility == Compatibility.COMPATIBLE


def test_assess_subscript_on_str_compatible():
    assert _assess_compatibility("str", "subscript").compatibility == Compatibility.COMPATIBLE


def test_assess_subscript_on_int_incompatible():
    assert _assess_compatibility("int", "subscript").compatibility == Compatibility.INCOMPATIBLE


def test_assess_subscript_on_none_incompatible():
    assert _assess_compatibility("None", "subscript").compatibility == Compatibility.INCOMPATIBLE


def test_assess_arithmetic_on_numeric_compatible():
    assert _assess_compatibility("int", "arithmetic").compatibility == Compatibility.COMPATIBLE
    assert _assess_compatibility("float", "arithmetic").compatibility == Compatibility.COMPATIBLE


def test_assess_arithmetic_on_str_suspicious():
    assert _assess_compatibility("str", "arithmetic").compatibility == Compatibility.SUSPICIOUS


def test_assess_arithmetic_on_none_suspicious():
    assert _assess_compatibility("None", "arithmetic").compatibility == Compatibility.SUSPICIOUS


def test_assess_arithmetic_on_unknown_complex_suspicious():
    assert _assess_compatibility("call", "arithmetic").compatibility == Compatibility.SUSPICIOUS


def test_assess_attribute_on_none_incompatible():
    assert _assess_compatibility("None", "attribute").compatibility == Compatibility.INCOMPATIBLE


def test_assess_attribute_on_int_suspicious():
    assert _assess_compatibility("int", "attribute").compatibility == Compatibility.SUSPICIOUS


def test_assess_attribute_on_list_compatible():
    assert _assess_compatibility("list", "attribute").compatibility == Compatibility.COMPATIBLE


def test_assess_attribute_on_dict_compatible():
    assert _assess_compatibility("dict", "attribute").compatibility == Compatibility.COMPATIBLE


def test_assess_attribute_on_str_compatible():
    assert _assess_compatibility("str", "attribute").compatibility == Compatibility.COMPATIBLE


def test_assess_comparison_always_compatible():
    for t in ("int", "str", "list", "None", "dict", "bool"):
        result = _assess_compatibility(t, "comparison")
        assert result.compatibility == Compatibility.COMPATIBLE, f"failed for {t}"


def test_assess_call_always_compatible():
    for t in ("int", "str", "list", "dict", "None", "call"):
        result = _assess_compatibility(t, "call")
        assert result.compatibility == Compatibility.COMPATIBLE, f"failed for {t}"


def test_assess_no_sink_op_returns_unknown():
    # sink_op=None is conservative: value may be forwarded downstream
    assert _assess_compatibility("int", None).compatibility == Compatibility.UNKNOWN


# ---------------------------------------------------------------------------
# analyze_endpoints — integration: Scenario A (flag)
# ---------------------------------------------------------------------------


def test_analyze_both_none_returns_unknown():
    result = analyze_endpoints(make_path(), None, None)
    assert result.compatibility == Compatibility.UNKNOWN


def test_analyze_int_source_subscript_sink_incompatible():
    """Source returns int, sink does subscript → INCOMPATIBLE → flag."""
    source = node_from_stmt("return 42", "A")
    sink = node_from_stmt("x = value[0]", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE
    assert "subscript" in result.explanation.lower() or "int" in result.explanation


def test_analyze_none_source_attribute_sink_incompatible():
    """Source returns None, sink does attribute access → INCOMPATIBLE → flag."""
    source = node_from_stmt("return None", "A")
    sink = node_from_stmt("x = value.attr", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


def test_analyze_int_source_arithmetic_compatible():
    source = node_from_stmt("return 5", "A")
    sink = node_from_stmt("result = value + 1", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_str_source_arithmetic_suspicious():
    """Source returns str, sink does arithmetic → SUSPICIOUS → flag."""
    source = node_from_stmt("return 'hello'", "A")
    sink = node_from_stmt("result = value - 1", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.SUSPICIOUS


def test_analyze_int_source_attribute_sink_suspicious():
    """Source returns int, sink reads a plain attribute → SUSPICIOUS → flag.

    Note: method-call syntax like result.bit_length() is classified as 'call'
    (the Call node is visited first in the walk), not 'attribute'. We use plain
    attribute access here to exercise the 'attribute' case.
    """
    source = node_from_func("def source() -> int:\n    return 0\n", "A")
    sink = node_from_stmt("x = result.numerator", "B", lineno=5)  # plain attr read
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.SUSPICIOUS


def test_analyze_annotated_func_subscript_none_incompatible():
    """Source annotated -> None, sink subscripts → INCOMPATIBLE."""
    source = node_from_func("def foo() -> None: pass", "A")
    sink = node_from_stmt("x = result[0]", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


# ---------------------------------------------------------------------------
# analyze_endpoints — integration: Scenario B (suppress)
# ---------------------------------------------------------------------------


def test_analyze_list_source_subscript_sink_compatible():
    """Source returns list, sink subscripts → COMPATIBLE → suppress."""
    source = node_from_stmt("return [1, 2, 3]", "A")
    sink = node_from_stmt("x = value[0]", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_dict_source_subscript_sink_compatible():
    source = node_from_func("def source() -> dict:\n    return {}\n", "A")
    sink = node_from_stmt("x = result['key']", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_any_source_comparison_compatible():
    source = node_from_stmt("return 42", "A")
    sink = node_from_stmt("if value > 0: pass", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_any_source_call_sink_compatible():
    """Sink does a function call — cannot determine incompatibility → COMPATIBLE."""
    source = node_from_func("def source() -> dict:\n    return {}\n", "A")
    sink = node_from_stmt("process(result)", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_list_source_attribute_sink_compatible():
    """Source returns list (object type), sink accesses attribute → COMPATIBLE."""
    source = node_from_func("def source() -> list:\n    return []\n", "A")
    sink = node_from_stmt("result.append(x)", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_generic_annotation_list_subscript_compatible():
    """list[str] → simplified to 'list', subscript → COMPATIBLE."""
    source = node_from_func("def source() -> list[str]:\n    return []\n", "A")
    sink = node_from_stmt("x = result[0]", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_optional_int_subscript_incompatible():
    """Optional[int] → simplified to 'int', subscript → INCOMPATIBLE."""
    source = node_from_func("def source() -> Optional[int]:\n    return None\n", "A")
    sink = node_from_stmt("x = result[0]", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


def test_analyze_statement_in_func_uses_enclosing_annotation():
    """Source node is a plain statement inside an annotated function.

    The analyzer walks up to the FunctionDef via _parent and uses its return
    annotation, rather than returning UNKNOWN for the plain statement.
    """
    src = "def producer() -> int:\n    x = compute()\n    return x\n"
    tree = ast.parse(src)
    _add_parents(tree)
    func = tree.body[0]
    # Use the first body statement (x = compute()) as the source — NOT the FunctionDef
    stmt_node = Node(
        id=f"{_FP}:2:4",
        filepath=_FP,
        lineno=2,
        col_offset=4,
        ast_node=func.body[0],
        branch="A",
    )
    sink = node_from_stmt("y = result[0]", "B", lineno=10)  # subscript on int → INCOMPATIBLE
    result = analyze_endpoints(make_path(), stmt_node, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


# ---------------------------------------------------------------------------
# analyze_endpoints — integration: Scenario C (unchanged body → suppress)
# ---------------------------------------------------------------------------


def test_analyze_both_branch_none_compatible():
    """Both endpoints branch=None → pre-existing connectivity → COMPATIBLE."""
    source = node_from_stmt("x = some_func()", None, lineno=1)
    sink = node_from_stmt("y = other_func()", None, lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_both_branch_none_overrides_type_analysis():
    """Unchanged-body filter fires even when types would indicate INCOMPATIBLE."""
    source = node_from_func("def source() -> int:\n    return 0\n", None)
    sink = node_from_stmt("x = result[0]", None, lineno=5)  # int + subscript → INCOMPATIBLE
    result = analyze_endpoints(make_path(), source, sink)
    # Branch=None check fires first → COMPATIBLE
    assert result.compatibility == Compatibility.COMPATIBLE


def test_analyze_only_source_none_branch_not_suppressed():
    """Only source has branch=None — filter requires BOTH to be None."""
    source = node_from_func("def source() -> int:\n    return 0\n", None)
    sink = node_from_stmt("x = result[0]", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


def test_analyze_only_sink_none_branch_not_suppressed():
    """Only sink has branch=None — filter requires BOTH to be None."""
    source = node_from_func("def source() -> int:\n    return 0\n", "A")
    sink = node_from_stmt("x = result[0]", None, lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.INCOMPATIBLE


# ---------------------------------------------------------------------------
# analyze_endpoints — integration: Scenario D (pass-through → flag conservatively)
# ---------------------------------------------------------------------------


def test_analyze_source_changed_sink_no_operations_unknown():
    """Source body changed (branch set), sink just assigns value (no operations).

    Per spec: conservative — value may be forwarded further. Returns UNKNOWN (not
    suppressed), because the downstream caller might misuse the value.
    """
    source = node_from_func("def source() -> int:\n    return 0\n", "A")
    sink = node_from_stmt("x = result", "B", lineno=5)  # plain assignment, no operations
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.UNKNOWN


def test_analyze_result_has_explanation():
    source = node_from_stmt("return 1", "A")
    sink = node_from_stmt("x = value[0]", "B", lineno=5)
    result = analyze_endpoints(make_path(), source, sink)
    assert isinstance(result.explanation, str)
    assert len(result.explanation) > 0


def test_analyze_no_ast_node_unknown():
    """Node present but ast_node=None → cannot analyse → UNKNOWN."""
    source = Node(id=f"{_FP}:1:0", filepath=_FP, lineno=1, col_offset=0, branch="A")
    sink = Node(id=f"{_FP}:5:0", filepath=_FP, lineno=5, col_offset=0, branch="B")
    result = analyze_endpoints(make_path(), source, sink)
    assert result.compatibility == Compatibility.UNKNOWN


def test_analyze_source_none_node_unknown():
    result = analyze_endpoints(make_path(), None, node_from_stmt("x = 1", "B"))
    assert result.compatibility == Compatibility.UNKNOWN


def test_analyze_sink_none_node_unknown():
    result = analyze_endpoints(make_path(), node_from_stmt("x = 1", "A"), None)
    assert result.compatibility == Compatibility.UNKNOWN
