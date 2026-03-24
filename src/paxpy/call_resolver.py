"""Resolve ast.Call nodes to their callee FunctionLocation(s).

Given a call site (ast.Call node), the repository-wide FunctionIndex, and the
file containing the call, this module attempts to statically determine which
function(s) the call targets. Resolution is conservative (over-approximating):
when a target cannot be uniquely identified, all functions sharing that name
are returned. Empty results are reserved for provably unresolvable calls such
as builtins or fully-qualified external library calls.

Depends on: types, indexer (via FunctionIndex).
"""

from __future__ import annotations

import ast
from pathlib import Path

from paxpy.types import FunctionIndex, FunctionLocation


def resolve_call(
    call_node: ast.Call,
    index: FunctionIndex,
    current_file: Path,
) -> list[FunctionLocation]:
    """Resolve a call site to its possible callee FunctionLocation(s).

    Resolution strategy (in order):
    1. Simple name call (ast.Name): look up the name in the index.
    2. Attribute call (ast.Attribute): look up the attribute name in the index
       as an over-approximation (ignores the receiver type).
    3. Anything else (subscript, starred, etc.): return empty list.

    When a name resolves to multiple locations (same name, different modules),
    all are returned — callers must handle ambiguity as over-approximation.
    Builtins (names present in builtins.__dict__) that are not in the index
    return an empty list.

    Args:
        call_node: The ast.Call node representing the call site.
        index: Repository-wide function index.
        current_file: Absolute path to the file containing the call (reserved
            for future same-module preference heuristics).

    Returns:
        List of candidate FunctionLocation targets. Empty only when the call
        is provably external (builtin or unindexed qualified name).
    """
    raise NotImplementedError("TODO")


def _extract_call_name(call_node: ast.Call) -> str | None:
    """Extract the bare function name from a call node, or None if not applicable.

    Returns the name for ast.Name calls (e.g. `foo()`), the attribute name for
    ast.Attribute calls (e.g. `obj.foo()` → "foo"), and None for all other
    forms.

    Args:
        call_node: The call site node.

    Returns:
        Function name string, or None.
    """
    raise NotImplementedError("TODO")


def _is_builtin(name: str) -> bool:
    """Return True if `name` is a Python builtin that needs no resolution.

    Args:
        name: A bare function name (not qualified).

    Returns:
        True if the name is in builtins.__dict__.
    """
    raise NotImplementedError("TODO")
