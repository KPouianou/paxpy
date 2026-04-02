"""Build a repository-wide function index via fast line-scanning.

Walks every .py file in the repository once using a lightweight regex scan
(no AST parsing) to extract function names and line numbers. The resulting
FunctionIndex supports O(1) name lookup and defers AST parsing to on-demand
calls via FunctionIndex.ensure_parsed() — only files actually reached during
SDG expansion are ever fully parsed.

This replaces the previous approach of parsing every file upfront with
ast.parse, which dominated runtime on large repositories.
"""

from __future__ import annotations

import re
from pathlib import Path

from paxpy.types import FunctionIndex, FunctionLocation

# Matches `def name(` or `async def name(` at any indent level.
# Group 1 captures the function name.
_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")


def build_index(repo_path: Path) -> FunctionIndex:
    """Walk the repository and build a function name index via line-scanning.

    Uses a lightweight regex scan instead of full AST parsing. Function names
    and line numbers are recorded; AST nodes are left as None and populated
    lazily by FunctionIndex.ensure_parsed() when sdg_builder needs to expand
    into a function.

    Args:
        repo_path: Absolute path to the root of the repository. All .py files
            beneath this directory are scanned.

    Returns:
        FunctionIndex with .index populated (name → [FunctionLocation, ...])
        and .parsed_asts empty (populated lazily on demand).
    """
    index: dict[str, list[FunctionLocation]] = {}

    for filepath in sorted(repo_path.rglob("*.py")):
        for name, lineno in _scan_defs(filepath):
            loc = FunctionLocation(
                name=name,
                filepath=filepath,
                lineno=lineno,
                end_lineno=None,
                ast_node=None,
                branch=None,
            )
            index.setdefault(name, []).append(loc)

    return FunctionIndex(index=index, parsed_asts={})


def _scan_defs(filepath: Path) -> list[tuple[str, int]]:
    """Fast scan for function definitions using regex, without AST parsing.

    Reads the file as text and applies a regex to each line to detect
    'def name(' and 'async def name(' patterns. Does not validate Python
    syntax — strings or comments containing def-like patterns may produce
    false positives, but these are harmless over-approximations for the
    purpose of name-based call resolution.

    Args:
        filepath: Absolute path to the .py file to scan.

    Returns:
        List of (function_name, 1-indexed line number) pairs.
    """
    results: list[tuple[str, int]] = []
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return results

    for lineno, line in enumerate(text.splitlines(), 1):
        m = _DEF_RE.match(line)
        if m:
            results.append((m.group(1), lineno))

    return results
