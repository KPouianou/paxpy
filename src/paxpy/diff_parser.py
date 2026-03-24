"""Parse git diffs and map changed line ranges to FunctionDef AST nodes.

For each branch (A and B), this module computes the diff against the common
base branch, identifies which line ranges were modified, and maps those ranges
to the enclosing Python function definitions. The result is a DiffResult
containing two lists of FunctionLocation seeds — one per branch — that seed
the SDG expansion in sdg_builder.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import git

from paxpy.types import DiffResult, FunctionLocation

# Regex to parse unified diff hunk headers: @@ -old +new[,count] @@
_HUNK_RE = re.compile(r"^\+(\d+)(?:,(\d+))?")


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
    repo = git.Repo(repo_path)

    seeds_a = _collect_seeds(repo, repo_path, base, branch_a, "A")
    seeds_b = _collect_seeds(repo, repo_path, base, branch_b, "B")

    return DiffResult(seeds_a=seeds_a, seeds_b=seeds_b)


def _collect_seeds(
    repo: git.Repo,
    repo_path: Path,
    base: str,
    branch: str,
    tag: str,
) -> list[FunctionLocation]:
    """Collect FunctionLocation seeds for one branch against base."""
    changed = _get_changed_ranges(repo_path, base, branch)
    seeds: list[FunctionLocation] = []

    for filepath, ranges in changed.items():
        relative = filepath.relative_to(repo_path)
        try:
            source = repo.git.show(f"{branch}:{relative}")
        except git.GitCommandError:
            # File removed on this branch or other git error — skip
            continue

        locs = _map_ranges_to_functions(filepath, source, ranges, tag)
        seeds.extend(locs)

    return seeds


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
    repo = git.Repo(repo_path)
    try:
        diff_text = repo.git.diff(f"{base}...{branch}", unified=0)
    except git.GitCommandError as exc:
        print(f"paxpy/diff_parser: git diff failed: {exc}", file=sys.stderr)
        return {}

    result: dict[Path, list[tuple[int, int]]] = {}
    current_file: Path | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = None
            continue

        if line.startswith("+++ "):
            path_str = line[4:]
            if path_str == "/dev/null":
                current_file = None
                continue
            # Strip the "b/" prefix that git prepends
            if path_str.startswith("b/"):
                path_str = path_str[2:]
            filepath = repo_path / path_str
            if filepath.suffix == ".py":
                current_file = filepath
                result.setdefault(current_file, [])
            else:
                current_file = None
            continue

        if line.startswith("@@ ") and current_file is not None:
            # Extract the +start[,count] part
            parts = line.split(" ")
            for part in parts:
                if part.startswith("+"):
                    m = _HUNK_RE.match(part)
                    if m:
                        start = int(m.group(1))
                        count_str = m.group(2)
                        count = int(count_str) if count_str is not None else 1
                        if count == 0:
                            # Pure deletion — no added lines
                            break
                        end = start + count - 1
                        result[current_file].append((start, end))
                    break

    return result


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
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    seen: set[tuple[str, int]] = set()
    locations: list[FunctionLocation] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue

        func_start = node.lineno
        func_end = node.end_lineno if node.end_lineno is not None else node.lineno

        for range_start, range_end in ranges:
            if func_start <= range_end and func_end >= range_start:
                key = (node.name, node.lineno)
                if key not in seen:
                    seen.add(key)
                    locations.append(
                        FunctionLocation(
                            name=node.name,
                            filepath=filepath,
                            lineno=node.lineno,
                            end_lineno=node.end_lineno,
                            ast_node=node,
                            branch=branch,  # type: ignore[arg-type]
                        )
                    )
                break  # Already added this function, no need to check more ranges

    return locations
