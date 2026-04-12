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
import builtins
from pathlib import Path

from paxpy.types import FunctionIndex, FunctionLocation

_BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins))


def resolve_call(
    call_node: ast.Call,
    index: FunctionIndex,
    current_file: Path,
) -> list[FunctionLocation]:
    """Resolve a call site to its possible callee FunctionLocation(s).

    Resolution strategy (in order of narrowing power):

    1. Self-method call (self.foo()): walk parent pointers to find the
       enclosing ClassDef. Restrict candidates to methods named foo defined
       on that class (and its resolvable base classes). Falls back to
       all-names-match if the enclosing class cannot be found.

    2. Import-scoped resolution for bare names: if the calling file imports
       the name explicitly (via ``from module import name`` or
       ``import module`` followed by ``module.name``), restrict to the
       function(s) in the corresponding source file. Falls back to
       all-names-match if no import covers the name.

    3. Same-file preference for bare names: when the name is defined both
       locally and in other files, return local definitions first. Cross-file
       definitions are still included so that over-approximation is preserved.

    4. Attribute call (non-self): look up the attribute name across the full
       index (over-approximation ignoring receiver type).

    5. Fallback: return all functions with that name from the index.

    When a name resolves to multiple locations (same name, different modules),
    all are returned — callers must handle ambiguity as over-approximation.
    Builtins (names present in builtins.__dict__) that are not in the index
    return an empty list.

    Args:
        call_node: The ast.Call node representing the call site.
        index: Repository-wide function index.
        current_file: Absolute path to the file containing the call.

    Returns:
        List of candidate FunctionLocation targets. Empty only when the call
        is provably external (builtin or unindexed qualified name).
    """
    func = call_node.func

    # ------------------------------------------------------------------
    # Self-method call: self.foo()
    # ------------------------------------------------------------------
    if (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
    ):
        name = func.attr
        candidates = index.lookup(name)
        if not candidates:
            return []

        resolved = _resolve_self_method(call_node, name, index, current_file, candidates)
        if resolved is not None:
            return resolved
        # Fall through to full name-based over-approximation
        return list(candidates)

    # ------------------------------------------------------------------
    # Simple name call: foo()
    # ------------------------------------------------------------------
    if isinstance(func, ast.Name):
        name = func.id
        candidates = index.lookup(name)

        if not candidates:
            return [] if _is_builtin(name) else []

        # Try import-scoped resolution first
        import_resolved = _resolve_via_imports(name, index, current_file, candidates)
        if import_resolved is not None:
            return import_resolved

        # Same-file preference: local defs first, then others
        return _prefer_local(name, current_file, candidates)

    # ------------------------------------------------------------------
    # Attribute call (non-self): obj.foo(), chained, etc.
    # ------------------------------------------------------------------
    if isinstance(func, ast.Attribute):
        name = func.attr
        candidates = index.lookup(name)
        if candidates:
            return list(candidates)
        return []

    # ------------------------------------------------------------------
    # Unresolvable: subscripts, lambdas, etc.
    # ------------------------------------------------------------------
    return []


# ---------------------------------------------------------------------------
# Self-method resolution helpers
# ---------------------------------------------------------------------------


def _resolve_self_method(
    call_node: ast.Call,
    method_name: str,
    index: FunctionIndex,
    current_file: Path,
    all_candidates: list[FunctionLocation],
) -> list[FunctionLocation] | None:
    """Restrict self.method() to the enclosing class and its bases.

    Walks the call node's parent chain (requires parent pointers added by
    FunctionIndex.ensure_parsed) to find the nearest enclosing ClassDef.
    Returns only FunctionLocation entries whose (filepath, lineno) correspond
    to methods defined inside that class body or any resolvable base class.

    Returns None if the enclosing ClassDef cannot be found, signalling that
    the caller should fall back to over-approximation.
    """
    enclosing_class = _find_enclosing_class(call_node)
    if enclosing_class is None:
        return None

    # Collect class defs to search: this class + resolvable bases
    class_defs: list[tuple[ast.ClassDef, Path]] = [(enclosing_class, current_file)]

    for base in enclosing_class.bases:
        base_name = _ast_name(base)
        if base_name is None:
            continue
        # Find a ClassDef named base_name in the parsed ASTs
        base_class = _find_class_in_index(base_name, index)
        if base_class is not None:
            class_defs.append(base_class)

    # Build the set of (filepath, lineno) for methods named method_name
    # that are directly inside these class bodies
    valid_locations: set[tuple[Path, int]] = set()
    for cls, cls_file in class_defs:
        for node in ast.iter_child_nodes(cls):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == method_name
            ):
                valid_locations.add((cls_file, node.lineno))

    if not valid_locations:
        # Class found but no matching method — still fall back so we don't
        # miss inherited methods from unparsed/external base classes
        return None

    result = [
        loc
        for loc in all_candidates
        if (loc.filepath, loc.lineno) in valid_locations
    ]
    return result if result else None


def _find_enclosing_class(node: ast.AST) -> ast.ClassDef | None:
    """Walk parent pointers to find the nearest enclosing ClassDef.

    Stops at module level or if parent pointers are absent.
    """
    current = node
    while True:
        parent = getattr(current, "_parent", None)
        if parent is None:
            return None
        if isinstance(parent, ast.ClassDef):
            return parent
        # Stop if we crossed a function boundary without finding a class
        # (i.e., a nested function that is not a method)
        if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
            # Keep going — the function itself may be inside a class
            pass
        current = parent


def _ast_name(node: ast.expr) -> str | None:
    """Extract a simple dotted name from a base-class expression, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_class_in_index(
    class_name: str, index: FunctionIndex
) -> tuple[ast.ClassDef, Path] | None:
    """Search parsed ASTs for a ClassDef with the given name.

    Maintains a cache on the index object itself (``_class_cache``) so that
    repeated lookups — common during BFS expansion — do not re-walk all parsed
    ASTs on every call. The cache is keyed by class name; it is built lazily
    the first time a name is requested, covering all files parsed so far.
    Newly parsed files are detected by comparing the cached AST-count against
    the current ``index.parsed_asts`` size.
    """
    # Attach a lazy cache directly to the index object.  FunctionIndex is a
    # plain dataclass without __slots__, so dynamic attributes are allowed.
    cache: dict[str, tuple[ast.ClassDef, Path] | None] = getattr(
        index, "_class_cache", {}
    )
    cached_size: int = getattr(index, "_class_cache_size", 0)

    current_size = len(index.parsed_asts)

    if class_name in cache and cached_size == current_size:
        return cache[class_name]

    # Cache is stale (new files were parsed) or name is new — scan new files.
    if cached_size < current_size:
        # Iterate only the files added since the last scan.
        for filepath, tree in list(index.parsed_asts.items()):
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # First occurrence wins; don't overwrite earlier finds.
                    cache.setdefault(node.name, (node, filepath))
        object.__setattr__(index, "_class_cache", cache) if hasattr(type(index), "__slots__") else setattr(index, "_class_cache", cache)
        object.__setattr__(index, "_class_cache_size", current_size) if hasattr(type(index), "__slots__") else setattr(index, "_class_cache_size", current_size)

    return cache.get(class_name)


# ---------------------------------------------------------------------------
# Import-scoped resolution helpers
# ---------------------------------------------------------------------------


def _resolve_via_imports(
    name: str,
    index: FunctionIndex,
    current_file: Path,
    all_candidates: list[FunctionLocation],
) -> list[FunctionLocation] | None:
    """Restrict a bare-name call to the module it was imported from.

    Parses the import statements of current_file (using the cached AST if
    available, otherwise parsing on demand without caching). Handles:

    - ``from pkg.mod import name`` / ``from pkg.mod import name as alias``
    - ``import mod`` (only useful if the call were ``mod.name()``, but
      included for completeness — bare import gives us no info for bare call)

    Returns the subset of all_candidates whose filepath matches the resolved
    module path, or None when no import covers this name (caller falls back).
    """
    tree = _get_ast_for_file(current_file, index)
    if tree is None:
        return None

    # Collect ``from X import name`` mappings: local_name → source_module_path
    from_imports: dict[str, str] = {}  # local name → dotted module string
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.ImportFrom) and stmt.module:
            for alias in stmt.names:
                local_name = alias.asname if alias.asname else alias.name
                if local_name == name:
                    from_imports[local_name] = stmt.module

    if name not in from_imports:
        return None

    module_dotted = from_imports[name]
    source_file = _resolve_module_to_file(module_dotted, current_file, index)
    if source_file is None:
        return None

    result = [loc for loc in all_candidates if loc.filepath == source_file]
    return result if result else None


def _get_ast_for_file(filepath: Path, index: FunctionIndex) -> ast.Module | None:
    """Return the AST for a file, using the index cache when available."""
    if filepath in index.parsed_asts:
        return index.parsed_asts[filepath]
    # Parse without caching — we don't want to side-effect the index here
    try:
        source = filepath.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return None


def _resolve_module_to_file(
    module_dotted: str, current_file: Path, index: FunctionIndex
) -> Path | None:
    """Map a dotted module string to an absolute .py file path.

    Tries two strategies:
    1. Relative to the directory containing current_file (sibling module).
    2. Relative to any parent directory (package root search).

    Only returns a path that exists on disk AND is in the function index.
    """
    # Convert dotted module to relative path segments
    parts = module_dotted.split(".")
    rel = Path(*parts).with_suffix(".py")

    # Strategy 1: resolve relative to the calling file's directory
    candidate = current_file.parent / rel
    if candidate.exists() and _file_in_index(candidate, index):
        return candidate

    # Strategy 2: walk up the directory tree looking for a package root
    parent = current_file.parent
    while True:
        candidate = parent / rel
        if candidate.exists() and _file_in_index(candidate, index):
            return candidate
        new_parent = parent.parent
        if new_parent == parent:
            break
        parent = new_parent

    # Strategy 3: check all indexed file paths for a suffix match
    for filepath in index.parsed_asts:
        if _path_matches_module(filepath, module_dotted):
            return filepath

    # Also check non-parsed files by scanning index locations
    for locs in index.index.values():
        for loc in locs:
            if _path_matches_module(loc.filepath, module_dotted):
                return loc.filepath

    return None


def _file_in_index(filepath: Path, index: FunctionIndex) -> bool:
    """Return True if at least one function in the index lives in filepath."""
    for locs in index.index.values():
        for loc in locs:
            if loc.filepath == filepath:
                return True
    return False


def _path_matches_module(filepath: Path, module_dotted: str) -> bool:
    """Return True if filepath's suffix matches the dotted module path."""
    parts = module_dotted.split(".")
    # Check if the filepath ends with parts[0]/parts[1]/.../parts[-1].py
    fp_parts = filepath.parts
    if len(fp_parts) < len(parts):
        return False
    # Compare the tail of the path
    file_tail = [p.removesuffix(".py") if i == len(parts) - 1 else p
                 for i, p in enumerate(fp_parts[-(len(parts)):])]
    return file_tail == parts


# ---------------------------------------------------------------------------
# Same-file preference helper
# ---------------------------------------------------------------------------


def _prefer_local(
    name: str,
    current_file: Path,
    candidates: list[FunctionLocation],
) -> list[FunctionLocation]:
    """Return candidates with local definitions first, then remote ones.

    When a function name is defined both in the calling file and elsewhere,
    same-file definitions are more likely to be the intended target. We
    preserve all candidates (over-approximation) but put local ones first so
    that downstream heuristics can weight them appropriately.
    """
    local = [loc for loc in candidates if loc.filepath == current_file]
    remote = [loc for loc in candidates if loc.filepath != current_file]
    return local + remote


# ---------------------------------------------------------------------------
# Name extraction and builtin check (preserved from original)
# ---------------------------------------------------------------------------


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
    match call_node.func:
        case ast.Name(id=name):
            return name
        case ast.Attribute(attr=name):
            return name
        case _:
            return None


def _is_builtin(name: str) -> bool:
    """Return True if `name` is a Python builtin that needs no resolution.

    Args:
        name: A bare function name (not qualified).

    Returns:
        True if the name is in builtins.__dict__.
    """
    return name in _BUILTIN_NAMES
