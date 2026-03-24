"""Parse git diffs and map changed line ranges to FunctionDef AST nodes.

For each branch (A and B), this module computes the diff against the common
base branch, identifies which line ranges were modified, and maps those ranges
to the enclosing Python function definitions. The result is a DiffResult
containing two lists of FunctionLocation seeds — one per branch — that seed
the SDG expansion in sdg_builder.
"""

from __future__ import annotations

from pathlib import Path

from paxpy.types import DiffResult, FunctionLocation


def parse_diffs(
    repo_path: Path,
    base: str,
    branch_a: str,
    branch_b: str,
) -> DiffResult:
    """Compute diffs for both branches and return seeding function locations.

    Diffs branch_a against base and branch_b against base. For each changed
    hunk, finds the enclosing FunctionDef or AsyncFunctionDef in the AST and
    records it as a seed. Functions changed in multiple hunks appear once.
    Seeds are tagged with their branch attribution ("A" or "B").

    Args:
        repo_path: Absolute path to the root of the git repository.
        base: Name of the common ancestor branch (e.g. "main").
        branch_a: Name of the first feature branch.
        branch_b: Name of the second feature branch.

    Returns:
        DiffResult with seeds_a (functions changed by branch_a) and
        seeds_b (functions changed by branch_b).
    """
    raise NotImplementedError("TODO")


def _get_changed_ranges(
    repo_path: Path,
    base: str,
    branch: str,
) -> dict[Path, list[tuple[int, int]]]:
    """Return per-file lists of (start_line, end_line) ranges changed in branch vs base.

    Uses GitPython to compute the diff. Lines are 1-indexed and inclusive.

    Args:
        repo_path: Root of the git repository.
        base: Base branch name.
        branch: Feature branch name.

    Returns:
        Mapping from absolute file path to list of changed line ranges.
    """
    raise NotImplementedError("TODO")


def _map_ranges_to_functions(
    filepath: Path,
    source: str,
    ranges: list[tuple[int, int]],
    branch: str,
) -> list[FunctionLocation]:
    """Map a list of changed line ranges to enclosing function definitions.

    Parses `source` with ast.parse and walks FunctionDef / AsyncFunctionDef
    nodes. A function is included if any changed range overlaps with its body
    lines. Each function appears at most once regardless of how many hunks
    touch it.

    Args:
        filepath: Absolute path to the Python file (used for FunctionLocation).
        source: Source text of the file.
        ranges: List of (start_line, end_line) changed ranges (1-indexed).
        branch: "A" or "B" — stored on the returned FunctionLocation.

    Returns:
        Deduplicated list of FunctionLocation instances for functions that
        overlap with the changed ranges.
    """
    raise NotImplementedError("TODO")
