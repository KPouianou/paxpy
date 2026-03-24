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

from paxpy.types import CompatibilityResult, InterferencePath


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
    raise NotImplementedError("TODO")


def _extract_return_type(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Extract the return type annotation as a source string, if present.

    Args:
        func_node: A function definition node with optional returns annotation.

    Returns:
        The unparsed annotation string (e.g. "int", "list[str]"), or None.
    """
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")
