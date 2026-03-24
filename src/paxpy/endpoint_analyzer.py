"""Analyse the source and sink endpoints of an interference path for compatibility.

Given an InterferencePath and the AST nodes at its endpoints, this module
examines the source function's return type annotations and concrete return
value patterns alongside the sink's usage pattern for the received value.
It classifies the pair as INCOMPATIBLE, SUSPICIOUS, COMPATIBLE, or UNKNOWN,
and produces a human-readable explanation.

This module operates entirely on AST structure — no runtime tracing, no type
inference beyond reading annotation nodes.

Depends on: types only.
"""

from __future__ import annotations

import ast

from paxpy.types import Compatibility, CompatibilityResult, InterferencePath

# Primitive type names whose mismatch is clear-cut
_NUMERIC_TYPES = frozenset({"int", "float", "complex"})
_SEQUENCE_TYPES = frozenset({"list", "tuple", "set", "frozenset"})
_MAPPING_TYPES = frozenset({"dict"})
_STRING_TYPES = frozenset({"str", "bytes"})
_BOOL_TYPES = frozenset({"bool"})


def analyze_endpoints(
    path: InterferencePath,
    source_ast_node: ast.AST | None,
    sink_ast_node: ast.AST | None,
) -> CompatibilityResult:
    """Assess whether the source and sink endpoints are compatible.

    Examines source return annotations and return value expressions (literals,
    typed constructors) and compares them against the sink's operations on the
    received value (attribute access, subscript, arithmetic, comparison). Grades
    the pair and returns a CompatibilityResult.

    Args:
        path: The interference path being analysed (used for conflict_type).
        source_ast_node: AST node at the source end of the path (e.g., the
            function def or the specific statement that produced the value).
        sink_ast_node: AST node at the sink end of the path (e.g., the
            statement that consumes the value).

    Returns:
        CompatibilityResult with a Compatibility grade and explanation string.
    """
    if source_ast_node is None or sink_ast_node is None:
        return CompatibilityResult(
            compatibility=Compatibility.UNKNOWN,
            explanation="Cannot analyse endpoints: AST nodes not available.",
        )

    # Extract what the source produces
    source_type = _infer_source_type(source_ast_node)

    # Extract what the sink does with the value
    sink_op = _classify_sink_operation(sink_ast_node)

    if source_type is None and sink_op is None:
        return CompatibilityResult(
            compatibility=Compatibility.UNKNOWN,
            explanation="No type annotation or value pattern found at source or sink.",
        )

    # Check for clear incompatibilities
    verdict = _assess_compatibility(source_type, sink_op)
    return verdict


def _infer_source_type(node: ast.AST) -> str | None:
    """Infer what type/kind of value the source node produces.

    Checks (in order):
    1. If node is a FunctionDef with a return annotation, use that.
    2. If node is a Return statement, inspect its value expression.
    3. If node is an Assign, inspect the right-hand side value.

    Returns a string tag like "int", "str", "list", "None", "numeric_literal",
    "string_literal", "dict", "call", or None if unknown.
    """
    match node:
        case ast.FunctionDef(returns=returns) | ast.AsyncFunctionDef(returns=returns):
            if returns is not None:
                return _extract_return_type(node)  # type: ignore[arg-type]

        case ast.Return(value=value):
            if value is not None:
                return _type_tag_from_expr(value)

        case ast.Assign(value=value):
            return _type_tag_from_expr(value)

    return None


def _type_tag_from_expr(expr: ast.expr) -> str | None:
    """Classify an expression into a coarse type tag."""
    match expr:
        case ast.Constant(value=v):
            if isinstance(v, bool):
                return "bool"
            if isinstance(v, int):
                return "int"
            if isinstance(v, float):
                return "float"
            if isinstance(v, str):
                return "str"
            if isinstance(v, bytes):
                return "bytes"
            if v is None:
                return "None"
        case ast.List():
            return "list"
        case ast.Tuple():
            return "tuple"
        case ast.Dict():
            return "dict"
        case ast.Set():
            return "set"
        case ast.Call(func=ast.Name(id=name)):
            # Constructor calls like list(), dict(), int(), str()
            if name in {"list", "dict", "set", "tuple", "int", "float", "str", "bytes"}:
                return name
            return "call"
        case ast.JoinedStr():
            return "str"  # f-string
        case ast.BinOp():
            return "numeric_expr"
    return None


def _extract_return_type(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Extract the return type annotation as a source string, if present.

    Args:
        func_node: A function definition node with optional returns annotation.

    Returns:
        The unparsed annotation string (e.g. "int", "list[str]"), or None.
    """
    if func_node.returns is None:
        return None
    try:
        return ast.unparse(func_node.returns)
    except Exception:
        return None


def _classify_sink_operation(sink_node: ast.AST) -> str | None:
    """Classify the operation applied to the received value at the sink.

    Looks for patterns like subscript access (value[key]), attribute access
    (value.attr), arithmetic operations, and boolean comparisons.

    Args:
        sink_node: The AST node representing the sink statement.

    Returns:
        A short string describing the operation (e.g. "subscript", "attribute",
        "arithmetic"), or None if the operation is unrecognised.
    """
    # Walk the sink node looking for interesting operations
    for node in ast.walk(sink_node):
        match node:
            case ast.Subscript():
                return "subscript"
            case ast.Attribute():
                return "attribute"
            case ast.BinOp(op=ast.Add() | ast.Sub() | ast.Mult() | ast.Div() | ast.Mod()):
                return "arithmetic"
            case ast.Compare():
                return "comparison"
            case ast.Call():
                return "call"
    return None


def _assess_compatibility(
    source_type: str | None,
    sink_op: str | None,
) -> CompatibilityResult:
    """Produce a CompatibilityResult from type and operation tags.

    Rules:
    - source_type=None → UNKNOWN (can't assess source)
    - sink_op subscript on non-sequence/dict source → INCOMPATIBLE
    - sink_op arithmetic on non-numeric source → SUSPICIOUS
    - sink_op attribute on None source → INCOMPATIBLE
    - source_type None (literal) + sink arithmetic → SUSPICIOUS
    - Otherwise → UNKNOWN (not enough info to decide)
    """
    if source_type is None:
        return CompatibilityResult(
            compatibility=Compatibility.UNKNOWN,
            explanation=f"Source type unknown; sink performs {sink_op or 'unknown'} operation.",
        )

    match sink_op:
        case "subscript":
            if source_type in _SEQUENCE_TYPES | _MAPPING_TYPES | {"str", "bytes"}:
                return CompatibilityResult(
                    compatibility=Compatibility.COMPATIBLE,
                    explanation=f"Source type '{source_type}' supports subscript access.",
                )
            if source_type in _NUMERIC_TYPES | _BOOL_TYPES | {"None"}:
                return CompatibilityResult(
                    compatibility=Compatibility.INCOMPATIBLE,
                    explanation=(
                        f"Source type '{source_type}' does not support subscript access "
                        f"but sink uses subscript operator."
                    ),
                )

        case "arithmetic":
            if source_type in _NUMERIC_TYPES | _BOOL_TYPES:
                return CompatibilityResult(
                    compatibility=Compatibility.COMPATIBLE,
                    explanation=f"Source type '{source_type}' supports arithmetic.",
                )
            if source_type in _STRING_TYPES | _SEQUENCE_TYPES | _MAPPING_TYPES | {"None"}:
                return CompatibilityResult(
                    compatibility=Compatibility.SUSPICIOUS,
                    explanation=(
                        f"Source type '{source_type}' may not support arithmetic; "
                        f"sink performs arithmetic operation."
                    ),
                )

        case "attribute":
            if source_type == "None":
                return CompatibilityResult(
                    compatibility=Compatibility.INCOMPATIBLE,
                    explanation="Source returns None but sink accesses an attribute on the value.",
                )
            if source_type in _NUMERIC_TYPES | _BOOL_TYPES:
                return CompatibilityResult(
                    compatibility=Compatibility.SUSPICIOUS,
                    explanation=(
                        f"Source type '{source_type}' has few attributes; "
                        f"sink accesses an attribute."
                    ),
                )

        case "comparison":
            # Comparisons are broadly compatible
            return CompatibilityResult(
                compatibility=Compatibility.COMPATIBLE,
                explanation=f"Comparison against '{source_type}' value is broadly safe.",
            )

    return CompatibilityResult(
        compatibility=Compatibility.UNKNOWN,
        explanation=(
            f"Source type '{source_type}', sink operation '{sink_op}': "
            f"insufficient information to determine compatibility."
        ),
    )
