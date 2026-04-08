"""Cheap structural heuristics for detecting semantic merge conflicts.

Each heuristic takes three source dicts and returns bool.

  base_source:     dict[str, str]  — {filename: source_code}
  branch_a_source: dict[str, str]
  branch_b_source: dict[str, str]

No full AST parsing, no call graphs, no git repos.  These are deliberately
cheap baselines whose precision/recall we compare against paxpy's graph analysis.

Public names
------------
HEURISTICS : dict[str, Callable] mapping display name → function
same_function(base, a, b) -> bool
diff_proximity(base, a, b, n) -> bool
import_overlap(base, a, b) -> bool
any_shared_file(base, a, b) -> bool
"""

from __future__ import annotations

import re
from typing import Callable


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _changed_files(
    base: dict[str, str],
    branch: dict[str, str],
) -> set[str]:
    """Return filenames whose content changed between base and branch."""
    changed: set[str] = set()
    for name, src in branch.items():
        if src != base.get(name, ""):
            changed.add(name)
    # Files deleted in branch (present in base, absent in branch)
    for name in base:
        if name not in branch:
            changed.add(name)
    return changed


def _parse_function_bodies(source: str) -> dict[str, str]:
    """Extract top-level function bodies keyed by name.

    Uses a simple indentation heuristic: a function runs until the next
    line at indent-level 0 (or EOF).  Fast, no AST needed.
    """
    funcs: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in source.splitlines(keepends=True):
        m = re.match(r"^def (\w+)\s*\(", line)
        if m:
            if current_name is not None:
                funcs[current_name] = "".join(current_lines)
            current_name = m.group(1)
            current_lines = [line]
        elif current_name is not None:
            # A non-blank, non-indented line that isn't a def ends the function
            if line.strip() and not line[0].isspace() and not line.startswith("#"):
                funcs[current_name] = "".join(current_lines)
                current_name = None
                current_lines = []
            else:
                current_lines.append(line)

    if current_name is not None:
        funcs[current_name] = "".join(current_lines)
    # Strip trailing whitespace so a trailing blank line between functions
    # doesn't make a function body look different just because something follows it.
    return {name: body.rstrip() for name, body in funcs.items()}


def _changed_function_names(base_src: str, branch_src: str) -> set[str]:
    """Names of functions that were added, removed, or modified."""
    base_fns = _parse_function_bodies(base_src)
    branch_fns = _parse_function_bodies(branch_src)
    changed: set[str] = set()
    for name, body in branch_fns.items():
        if base_fns.get(name) != body:
            changed.add(name)
    for name in base_fns:
        if name not in branch_fns:
            changed.add(name)
    return changed


def _changed_lines(base_src: str, branch_src: str) -> set[int]:
    """1-indexed line numbers that differ between base and branch source."""
    base_lines = base_src.splitlines()
    branch_lines = branch_src.splitlines()
    changed: set[int] = set()
    for i, (b, br) in enumerate(zip(base_lines, branch_lines), start=1):
        if b != br:
            changed.add(i)
    # Lines beyond the shorter version
    longer = max(len(base_lines), len(branch_lines))
    for i in range(min(len(base_lines), len(branch_lines)) + 1, longer + 1):
        changed.add(i)
    return changed


_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.,\t ]+))",
    re.MULTILINE,
)


def _imported_modules(source: str) -> set[str]:
    """Top-level module names imported by this source."""
    modules: set[str] = set()
    for m in _IMPORT_RE.finditer(source):
        if m.group(1):
            # from X.Y import … → module is X
            modules.add(m.group(1).split(".")[0])
        else:
            # import X, Y → each top-level name
            for part in m.group(2).split(","):
                modules.add(part.strip().split(".")[0])
    return modules


def _stem(filename: str) -> str:
    """'module_a.py' → 'module_a'"""
    return filename.rsplit(".", 1)[0]


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

def same_function(
    base: dict[str, str],
    branch_a: dict[str, str],
    branch_b: dict[str, str],
) -> bool:
    """H1: Both branches modify the same function in any shared file."""
    files_a = _changed_files(base, branch_a)
    files_b = _changed_files(base, branch_b)
    shared = files_a & files_b
    for filename in shared:
        base_src = base.get(filename, "")
        a_src = branch_a.get(filename, "")
        b_src = branch_b.get(filename, "")
        changed_a = _changed_function_names(base_src, a_src)
        changed_b = _changed_function_names(base_src, b_src)
        if changed_a & changed_b:
            return True
    return False


def diff_proximity(
    base: dict[str, str],
    branch_a: dict[str, str],
    branch_b: dict[str, str],
    n: int,
) -> bool:
    """H2: Any changed line in A is within N lines of any changed line in B,
    in the same file.
    """
    files_a = _changed_files(base, branch_a)
    files_b = _changed_files(base, branch_b)
    shared = files_a & files_b
    for filename in shared:
        base_src = base.get(filename, "")
        lines_a = _changed_lines(base_src, branch_a.get(filename, base_src))
        lines_b = _changed_lines(base_src, branch_b.get(filename, base_src))
        for la in lines_a:
            for lb in lines_b:
                if abs(la - lb) <= n:
                    return True
    return False


def import_overlap(
    base: dict[str, str],
    branch_a: dict[str, str],
    branch_b: dict[str, str],
) -> bool:
    """H3: A file modified by A imports a module modified by B, or vice versa.

    Only meaningful for multi-file scenarios; always False for single-file.
    """
    files_a = _changed_files(base, branch_a)
    files_b = _changed_files(base, branch_b)

    stems_a = {_stem(f) for f in files_a}
    stems_b = {_stem(f) for f in files_b}

    # Check if any file changed by A imports a stem changed by B
    for filename in files_a:
        src = branch_a.get(filename, base.get(filename, ""))
        if _imported_modules(src) & stems_b:
            return True

    # Check if any file changed by B imports a stem changed by A
    for filename in files_b:
        src = branch_b.get(filename, base.get(filename, ""))
        if _imported_modules(src) & stems_a:
            return True

    return False


def any_shared_file(
    base: dict[str, str],
    branch_a: dict[str, str],
    branch_b: dict[str, str],
) -> bool:
    """H4: Both branches modify at least one common file.  Trivial baseline."""
    return bool(_changed_files(base, branch_a) & _changed_files(base, branch_b))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

HEURISTICS: dict[str, Callable[[dict[str, str], dict[str, str], dict[str, str]], bool]] = {
    "same_function":      same_function,
    "diff_proximity_5":   lambda b, a, br: diff_proximity(b, a, br, 5),
    "diff_proximity_10":  lambda b, a, br: diff_proximity(b, a, br, 10),
    "diff_proximity_25":  lambda b, a, br: diff_proximity(b, a, br, 25),
    "diff_proximity_50":  lambda b, a, br: diff_proximity(b, a, br, 50),
    "import_overlap":     import_overlap,
    "any_shared_file":    any_shared_file,
}
