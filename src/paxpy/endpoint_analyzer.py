"""Analyse the source and sink endpoints of an interference path for compatibility.

Given an InterferencePath and the SDG Node objects at its endpoints, this module
examines the source function's return type annotations and concrete return value
patterns alongside the sink's usage pattern for the received value. It classifies
the pair as INCOMPATIBLE, SUSPICIOUS, COMPATIBLE, or UNKNOWN, and produces a
human-readable explanation.

Filtering rules applied by callers (main.py):
    incompatible  → report at current tier
    suspicious    → report (lower confidence)
    compatible    → suppress entirely
    unknown       → report (conservative default)

This module operates entirely on AST structure — no runtime tracing, no type
inference beyond reading annotation nodes.

Depends on: types only.
"""

from __future__ import annotations

import ast

from paxpy.types import Compatibility, CompatibilityResult, InterferencePath, Node

# Primitive type names whose mismatch is clear-cut
_NUMERIC_TYPES = frozenset({"int", "float", "complex"})
_SEQUENCE_TYPES = frozenset({"list", "tuple", "set", "frozenset"})
_MAPPING_TYPES = frozenset({"dict"})
_STRING_TYPES = frozenset({"str", "bytes"})
_BOOL_TYPES = frozenset({"bool"})


def analyze_endpoints(
    path: InterferencePath,
    source_node: Node | None,
    sink_node: Node | None,
) -> CompatibilityResult:
    """Assess whether the source and sink endpoints are compatible.

    Check 1 — unchanged body: if neither endpoint node has a branch tag (i.e.
    neither was changed in the diff), the path is pre-existing connectivity and
    is suppressed as COMPATIBLE.

    Check 2 — type/operation matching: infers the source function's return type
    (via annotation or value expression) and the sink's usage pattern (subscript,
    attribute, arithmetic, comparison, call), then applies rule-based assessment.
    Return-type inference walks up _parent pointers to the enclosing FunctionDef
    when the endpoint node is a plain statement rather than a FunctionDef itself.

    Args:
        path: The interference path being analysed (used for conflict_type).
        source_node: SDG Node at the source end (carries ast_node and branch).
        sink_node: SDG Node at the sink end (carries ast_node and branch).

    Returns:
        CompatibilityResult with a Compatibility grade and explanation string.
    """
    # --- Check 1: unchanged body filter ---
    # If neither endpoint was directly changed in the diff (both branch=None),
    # the path represents pre-existing graph connectivity, not branch interference.
    if (
        source_node is not None
        and sink_node is not None
        and source_node.branch is None
        and sink_node.branch is None
    ):
        return CompatibilityResult(
            compatibility=Compatibility.COMPATIBLE,
            explanation=(
                "Pre-existing connectivity: neither endpoint was changed in this diff. "
                "Path exists in the base graph independent of these branches."
            ),
        )

    source_ast = source_node.ast_node if source_node else None
    sink_ast = sink_node.ast_node if sink_node else None

    if source_ast is None or sink_ast is None:
        return CompatibilityResult(
            compatibility=Compatibility.UNKNOWN,
            explanation="Cannot analyse endpoints: AST nodes not available.",
        )

    # --- Check 2: type / operation compatibility ---
    source_type = _infer_source_type(source_ast)
    sink_op = _classify_sink_operation(sink_ast)

    if source_type is None and sink_op is None:
        return CompatibilityResult(
            compatibility=Compatibility.UNKNOWN,
            explanation="No type annotation or value pattern found at source or sink.",
        )

    return _assess_compatibility(source_type, sink_op)


# ---------------------------------------------------------------------------
# Source type inference
# ---------------------------------------------------------------------------


def _infer_source_type(node: ast.AST) -> str | None:
    """Infer what type/kind of value the source node produces.

    Checks (in order):
    1. If node is a FunctionDef with a return annotation, parse and return it.
    2. If node is a Return statement, inspect its value expression.
    3. If node is an Assign, inspect the right-hand side value.
    4. Otherwise walk up _parent pointers to the enclosing FunctionDef and use
       its return annotation — this handles the common case where the source
       node is a statement inside a changed function rather than the function
       definition itself.

    Returns a string tag like "int", "str", "list", "None", "numeric_expr",
    "string_literal", "dict", "call", or None if unknown.
    """
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            if node.returns is not None:  # type: ignore[union-attr]
                return _extract_return_type(node)  # type: ignore[arg-type]

        case ast.Return(value=value):
            if value is not None:
                tag = _type_tag_from_expr(value)
                if tag is not None and tag != "call":
                    return tag
                # "call" is weak — fall through to enclosing annotation below

        case ast.Assign(value=value):
            tag = _type_tag_from_expr(value)
            if tag is not None and tag != "call":
                return tag
            # "call" is weak — fall through to enclosing annotation below

    # Fall back: walk up parent pointers to the enclosing function definition
    # and use its return annotation. This is the common case — the source node
    # is a statement inside the changed function, not the function header.
    # Also used when the RHS of a Return/Assign is a bare function call ("call"
    # tag) which carries no type information on its own.
    enclosing = _find_enclosing_function(node)
    if enclosing is not None and enclosing.returns is not None:
        return _extract_return_type(enclosing)

    return None


def _find_enclosing_function(
    node: ast.AST,
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Walk up _parent pointers to find the innermost enclosing function def.

    Returns None if _parent pointers are not present or no enclosing function
    is found (e.g. module-level statement).
    """
    current = getattr(node, "_parent", None)
    while current is not None:
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            return current
        current = getattr(current, "_parent", None)
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
    """Extract and simplify the return type annotation to a coarse tag.

    Parses the annotation string (e.g. "list[str]", "int | None", "Optional[dict]")
    and maps it to one of the recognised primitive tags. Returns None for complex
    user-defined types where we cannot determine compatibility.

    Args:
        func_node: A function definition node with optional returns annotation.

    Returns:
        A simple type tag string, or None if the annotation is absent or
        cannot be simplified to a known tag.
    """
    if func_node.returns is None:
        return None
    try:
        raw = ast.unparse(func_node.returns)
        return _annotation_to_tag(raw)
    except Exception:
        return None


def _annotation_to_tag(annotation: str) -> str | None:
    """Map a type annotation string to a simple type tag.

    Handles Optional[X], X | Y unions (ignoring None), and generic aliases
    like list[str] or dict[str, int]. Returns None for unrecognised types.
    """
    ann = annotation.strip()

    # Optional[X] → X
    if ann.startswith("Optional[") and ann.endswith("]"):
        return _annotation_to_tag(ann[9:-1])

    # X | Y union — take the first non-None member
    if "|" in ann:
        parts = [p.strip() for p in ann.split("|")]
        for part in parts:
            if part not in ("None", "NoneType"):
                tag = _annotation_to_tag(part)
                if tag is not None:
                    return tag
        return "None"

    # Generic alias: list[X] → list, dict[K, V] → dict, etc.
    if "[" in ann:
        base = ann[: ann.index("[")].strip()
        return _annotation_to_tag(base)

    _KNOWN: dict[str, str] = {
        "int": "int",
        "float": "float",
        "complex": "complex",
        "str": "str",
        "bytes": "bytes",
        "bool": "bool",
        "list": "list",
        "List": "list",
        "tuple": "tuple",
        "Tuple": "tuple",
        "set": "set",
        "Set": "set",
        "frozenset": "frozenset",
        "FrozenSet": "frozenset",
        "dict": "dict",
        "Dict": "dict",
        "None": "None",
        "NoneType": "None",
    }
    return _KNOWN.get(ann)


# ---------------------------------------------------------------------------
# Sink operation classification
# ---------------------------------------------------------------------------


def _classify_sink_operation(sink_node: ast.AST) -> str | None:
    """Classify the operation applied to the received value at the sink.

    Looks for patterns like subscript access (value[key]), attribute access
    (value.attr), arithmetic operations, and boolean comparisons. The first
    recognised pattern wins.

    Args:
        sink_node: The AST node representing the sink statement.

    Returns:
        A short string describing the operation (e.g. "subscript", "attribute",
        "arithmetic", "comparison", "call"), or None if unrecognised.
    """
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


# ---------------------------------------------------------------------------
# Compatibility assessment
# ---------------------------------------------------------------------------


def _assess_compatibility(
    source_type: str | None,
    sink_op: str | None,
) -> CompatibilityResult:
    """Produce a CompatibilityResult from type and operation tags.

    Rules:
    - source_type=None → UNKNOWN (can't assess source)
    - sink_op=None → UNKNOWN (conservative: value may be passed through)
    - subscript on sequence/mapping/str/bytes → COMPATIBLE
    - subscript on numeric/bool/None → INCOMPATIBLE
    - arithmetic on numeric/bool → COMPATIBLE
    - arithmetic on string/sequence/mapping/None → SUSPICIOUS
    - attribute access on None → INCOMPATIBLE
    - attribute access on numeric/bool → SUSPICIOUS
    - attribute access on any other known type → COMPATIBLE (objects support attrs)
    - comparison → COMPATIBLE (broadly safe for any type)
    - call → COMPATIBLE (cannot determine call-site incompatibility from type alone)
    - Otherwise → UNKNOWN
    """
    if source_type is None:
        return CompatibilityResult(
            compatibility=Compatibility.UNKNOWN,
            explanation=f"Source type unknown; sink performs {sink_op or 'unknown'} operation.",
        )

    match sink_op:
        case None:
            # Sink doesn't perform any detectable operation on the value.
            # Per spec: treat conservatively — value may be forwarded downstream.
            return CompatibilityResult(
                compatibility=Compatibility.UNKNOWN,
                explanation=(
                    f"Source type '{source_type}' — no specific sink operation detected; "
                    f"value may be passed through to further callers."
                ),
            )

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
            # Unknown complex type doing arithmetic: suspicious
            return CompatibilityResult(
                compatibility=Compatibility.SUSPICIOUS,
                explanation=(
                    f"Source type '{source_type}' (complex/unknown) with arithmetic "
                    f"at sink — potential incompatibility."
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
            # Any other type (str, list, dict, complex objects): attribute access
            # is generally valid — we cannot determine incompatibility without
            # knowing the object's API.
            return CompatibilityResult(
                compatibility=Compatibility.COMPATIBLE,
                explanation=(
                    f"Source type '{source_type}' is an object type; "
                    f"attribute access is expected and compatible."
                ),
            )

        case "comparison":
            return CompatibilityResult(
                compatibility=Compatibility.COMPATIBLE,
                explanation=f"Comparison against '{source_type}' value is broadly safe.",
            )

        case "call":
            # The sink invokes a function (or passes the value to one).
            # We cannot determine call-site type compatibility from AST alone
            # without full signature analysis — suppress as COMPATIBLE rather
            # than flagging spuriously.
            return CompatibilityResult(
                compatibility=Compatibility.COMPATIBLE,
                explanation=(
                    f"Sink performs a function call with source type '{source_type}'; "
                    f"call-site compatibility cannot be determined from type alone."
                ),
            )

    return CompatibilityResult(
        compatibility=Compatibility.UNKNOWN,
        explanation=(
            f"Source type '{source_type}', sink operation '{sink_op}': "
            f"insufficient information to determine compatibility."
        ),
    )
