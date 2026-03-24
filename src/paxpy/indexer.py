"""Build a repository-wide function index from parsed Python ASTs.

Walks every .py file in the repository once, parses each with ast.parse,
annotates all AST nodes with parent pointers (for upward traversal), and
builds an inverted index mapping function names to their FunctionLocation
records. The resulting FunctionIndex supports O(1) lookup by name and provides
direct access to every file's parsed AST for downstream modules.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from paxpy.types import FunctionIndex, FunctionLocation


def build_index(repo_path: Path) -> FunctionIndex:
    """Walk the repository and build a complete function index.

    Skips files that cannot be parsed (syntax errors, encoding issues) with a
    warning. Adds `_parent` attributes to every AST node so callers can walk
    upward toward the module root.

    Args:
        repo_path: Absolute path to the root of the repository. All .py files
            beneath this directory are indexed.

    Returns:
        FunctionIndex with .index populated (name → [FunctionLocation, ...])
        and .parsed_asts populated (Path → ast.Module).
    """
    index: dict[str, list[FunctionLocation]] = {}
    parsed_asts: dict[Path, ast.Module] = {}

    for filepath in sorted(repo_path.rglob("*.py")):
        tree = _parse_file(filepath)
        if tree is None:
            continue

        parsed_asts[filepath] = tree

        for loc in _extract_functions(filepath, tree):
            index.setdefault(loc.name, []).append(loc)

    return FunctionIndex(index=index, parsed_asts=parsed_asts)


def _parse_file(filepath: Path) -> ast.Module | None:
    """Parse a single Python file and return its AST module, or None on error.

    On success, annotates every node in the tree with a `_parent` attribute
    pointing to its parent node (None for the module root).

    Args:
        filepath: Absolute path to the .py file.

    Returns:
        Parsed ast.Module with parent pointers set, or None if the file could
        not be parsed.
    """
    try:
        source = filepath.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"paxpy/indexer: cannot read {filepath}: {exc}", file=sys.stderr)
        return None

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as exc:
        print(f"paxpy/indexer: syntax error in {filepath}: {exc}", file=sys.stderr)
        return None

    _add_parent_pointers(tree)
    return tree


def _add_parent_pointers(tree: ast.Module) -> None:
    """Annotate every node in `tree` with a `_parent` attribute.

    The module root gets `_parent = None`. All other nodes get `_parent` set
    to their direct parent node. Modifies the tree in-place.

    Args:
        tree: A parsed AST module.
    """
    tree._parent = None  # type: ignore[attr-defined]
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]


def _extract_functions(
    filepath: Path,
    tree: ast.Module,
) -> list[FunctionLocation]:
    """Extract all top-level and nested function definitions from `tree`.

    Includes both FunctionDef and AsyncFunctionDef nodes. Records name,
    filepath, lineno, and end_lineno. The ast_node field is set to the node
    itself. branch is left as None (set by diff_parser).

    Args:
        filepath: Used to populate FunctionLocation.filepath.
        tree: Parsed AST module (parent pointers already set).

    Returns:
        List of FunctionLocation for every function defined in the file.
    """
    locations: list[FunctionLocation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            locations.append(
                FunctionLocation(
                    name=node.name,
                    filepath=filepath,
                    lineno=node.lineno,
                    end_lineno=node.end_lineno,
                    ast_node=node,
                    branch=None,
                )
            )

    return locations
