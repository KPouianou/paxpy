"""Direct AST comparison detector for conflict patterns not captured by SDG analysis.

Detects two interference patterns that the SDG-based detectors miss because they
don't create reachable source→sink paths between branches:

- **OVERRIDE_ASSIGNMENT**: Both branches assign different values to the same
  variable/attribute/key within a shared function's modified regions.  A
  control-flow exclusivity filter suppresses false positives where the two
  assignments live in mutually exclusive branches (opposite arms of the same
  ``if/else``, or complement predicates like ``if x`` vs ``if not x``).
- **signature_body_mismatch**: One branch changes a function's parameter list
  while the other branch changes its body (or has callers using the old
  signature). Manifests as TypeError / AttributeError / NameError at runtime.

These patterns are detected by comparing AST nodes from each branch's seeds
directly, without SDG traversal.

Depends only on types.py. Does not import from any other paxpy module.
"""

from __future__ import annotations

import ast
from pathlib import Path

from paxpy.types import (
    ConflictType,
    DiffResult,
    FunctionLocation,
    InterferencePath,
    make_node_id,
)


def detect_direct_conflicts(
    diff_result: DiffResult,
) -> list[InterferencePath]:
    """Detect conflicts via direct AST comparison of branch seeds.

    Complements SDG-based detectors by catching patterns where the conflict
    is visible from comparing the two branches' function definitions without
    data-flow or control-flow analysis.

    Args:
        diff_result: Parsed diff with seeds for both branches. Each seed's
            ``ast_node`` must be populated (the FunctionDef parsed from
            that branch's tip).

    Returns:
        Interference paths for detected conflicts. These use synthetic
        NodeIds (not present in any SDG) and should bypass SDG-based
        endpoint analysis in the pipeline.
    """
    paths: list[InterferencePath] = []

    # Check 1 & 2: Same-function conflicts (signature mismatch + OA)
    paths.extend(_check_same_function_conflicts(diff_result))

    # Check 3: Cross-function call signature mismatch
    paths.extend(_check_cross_call_signature_mismatch(diff_result))

    # Post-hoc filter: discard paths that don't cross branch boundaries.
    # Each path's source must belong to one branch's seeds and its sink
    # to the other branch's seeds (or both, for same-function checks).
    paths = _filter_same_branch_paths(paths, diff_result)

    return paths


# ---------------------------------------------------------------------------
# Same-function checks: both branches modified the same function
# ---------------------------------------------------------------------------


def _check_same_function_conflicts(
    diff_result: DiffResult,
) -> list[InterferencePath]:
    """Find conflicts in functions modified by both branches."""
    paths: list[InterferencePath] = []

    # Build maps: (filepath, func_name) -> FunctionLocation
    a_funcs: dict[tuple[Path, str], FunctionLocation] = {}
    for seed in diff_result.seeds_a:
        a_funcs[(seed.filepath, seed.name)] = seed

    b_funcs: dict[tuple[Path, str], FunctionLocation] = {}
    for seed in diff_result.seeds_b:
        b_funcs[(seed.filepath, seed.name)] = seed

    common = set(a_funcs) & set(b_funcs)

    for key in common:
        loc_a = a_funcs[key]
        loc_b = b_funcs[key]
        if loc_a.ast_node is None or loc_b.ast_node is None:
            continue

        sig = _check_signature_mismatch_same_func(loc_a, loc_b)
        if sig is not None:
            paths.append(sig)

        paths.extend(_check_override_assignments(loc_a, loc_b))

    return paths


def _check_signature_mismatch_same_func(
    loc_a: FunctionLocation,
    loc_b: FunctionLocation,
) -> InterferencePath | None:
    """Check if both branches changed a function's signature incompatibly.

    Detects:
    - Different required-arg count (will cause TypeError for some callers).
    - Different param names at the same position (indicates semantic divergence;
      merge picks one side's body with the other's signature → NameError or
      AttributeError when body references the param by name).
    """
    assert loc_a.ast_node is not None and loc_b.ast_node is not None

    params_a = _get_param_info(loc_a.ast_node)
    params_b = _get_param_info(loc_b.ast_node)

    # Identical parameter lists → no mismatch
    if (
        params_a["positional"] == params_b["positional"]
        and params_a["kwonly"] == params_b["kwonly"]
    ):
        return None

    # Backward-compatible growth: one side is a strict prefix of the other
    # with all extras having defaults.  This is a safe API extension, not a
    # conflict (callers using the shorter signature still work).
    if _is_backward_compatible_growth(
        params_a["positional"],
        params_b["positional"],
        params_a,
        params_b,
    ):
        return None

    mismatch = False

    # Count mismatch: required positional args differ → TypeError for callers
    if params_a["required_count"] != params_b["required_count"]:
        mismatch = True

    # Total positional count differs (and no *args to absorb extras)
    if (
        params_a["total_positional"] != params_b["total_positional"]
        and not params_a["has_vararg"]
        and not params_b["has_vararg"]
    ):
        mismatch = True

    # Name mismatch at corresponding positions → merge body references one
    # name but signature defines another (NameError / AttributeError).
    # Example: A has (self, ctx), B has (self, rup). Merge picks one
    # signature + other body → body uses undefined name.
    min_len = min(len(params_a["positional"]), len(params_b["positional"]))
    for i in range(min_len):
        if params_a["positional"][i] != params_b["positional"][i]:
            # Verify the renamed param is actually used in the body
            a_name = params_a["positional"][i]
            b_name = params_b["positional"][i]
            a_body = _collect_name_references(loc_a.ast_node)
            b_body = _collect_name_references(loc_b.ast_node)
            # A's body uses a_name, B's body uses b_name — diverged semantics
            if a_name in a_body and b_name in b_body:
                mismatch = True
                break

    if not mismatch:
        return None

    source_id = make_node_id(loc_a.filepath, loc_a.lineno, 0)
    sink_id = make_node_id(loc_b.filepath, loc_b.lineno, 0)

    # Build human-readable mismatch detail
    detail_parts: list[str] = []
    if params_a["required_count"] != params_b["required_count"]:
        detail_parts.append(
            f"Branch A requires {params_a['required_count']} args, "
            f"Branch B requires {params_b['required_count']}"
        )
    if params_a["total_positional"] != params_b["total_positional"]:
        detail_parts.append(
            f"Branch A has {params_a['total_positional']} params, "
            f"Branch B has {params_b['total_positional']}"
        )
    # Name mismatches
    for i in range(min(len(params_a["positional"]), len(params_b["positional"]))):
        if params_a["positional"][i] != params_b["positional"][i]:
            detail_parts.append(
                f"param {i}: A={params_a['positional'][i]!r} vs B={params_b['positional'][i]!r}"
            )
    detail = "; ".join(detail_parts) if detail_parts else "Parameter lists differ"

    return InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,  # closest existing type
        tier=1,
        path_nodes=[source_id, sink_id],
        source_node=source_id,
        sink_node=sink_id,
        evidence={
            "pattern": "signature_mismatch",
            "params_a": params_a["positional"],
            "params_b": params_b["positional"],
            "detail": detail,
        },
    )


# ---------------------------------------------------------------------------
# Control-flow exclusivity filter for OA check
# ---------------------------------------------------------------------------


def _line_range(stmts: list[ast.stmt]) -> tuple[int, int] | None:
    """Return (first_line, last_line) spanned by a list of statements, or None if empty."""
    if not stmts:
        return None
    start = stmts[0].lineno
    end = max(getattr(s, "end_lineno", s.lineno) or s.lineno for s in stmts)
    return (start, end)


def _find_enclosing_if_arm(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    target_line: int,
) -> tuple[ast.If, str] | None:
    """Find the innermost If node enclosing a given line, and which arm it's in.

    Returns (if_node, "body") or (if_node, "orelse"), or None if not inside any If.
    """

    def _search(stmts: list[ast.stmt]) -> tuple[ast.If, str] | None:
        for stmt in stmts:
            if isinstance(stmt, ast.If):
                body_range = _line_range(stmt.body)
                if body_range and body_range[0] <= target_line <= body_range[1]:
                    # Recurse deeper into the body
                    deeper = _search(stmt.body)
                    return deeper if deeper is not None else (stmt, "body")
                orelse_range = _line_range(stmt.orelse)
                if orelse_range and orelse_range[0] <= target_line <= orelse_range[1]:
                    deeper = _search(stmt.orelse)
                    return deeper if deeper is not None else (stmt, "orelse")
            # Recurse into compound statements that aren't branching
            elif isinstance(stmt, (ast.For, ast.While, ast.With, ast.AsyncFor, ast.AsyncWith)):
                deeper = _search(stmt.body)
                if deeper is not None:
                    return deeper
            elif isinstance(stmt, ast.Try):
                for block in [stmt.body, stmt.handlers, stmt.orelse, stmt.finalbody]:
                    deeper = _search(block)
                    if deeper is not None:
                        return deeper
        return None

    return _search(func_node.body)


def _is_complement_test(test_a: ast.expr, test_b: ast.expr) -> bool:
    """Check if two If-tests are complements: ``x`` vs ``not x``, or ``not x`` vs ``x``."""
    if isinstance(test_a, ast.UnaryOp) and isinstance(test_a.op, ast.Not):
        return ast.dump(test_a.operand) == ast.dump(test_b)
    if isinstance(test_b, ast.UnaryOp) and isinstance(test_b.op, ast.Not):
        return ast.dump(test_b.operand) == ast.dump(test_a)
    return False


def _assignments_on_same_execution_path(
    func_node_a: ast.FunctionDef | ast.AsyncFunctionDef,
    func_node_b: ast.FunctionDef | ast.AsyncFunctionDef,
    line_a: int,
    line_b: int,
) -> bool:
    """Check if two assignments can override each other.

    Returns True if they CAN override (same path or indeterminate).
    Returns False if provably in mutually exclusive branches.
    Conservative: ambiguous cases return True (flag OA).

    Known limitations (all conservative -- will flag OA, not suppress):
    - elif chains: nested If in orelse, different If nodes
    - match/case: not analyzed
    - Complex boolean predicates: only simple ``not`` complement detected
    """
    arm_a = _find_enclosing_if_arm(func_node_a, line_a)
    arm_b = _find_enclosing_if_arm(func_node_b, line_b)

    # Both top-level -> same path
    if arm_a is None and arm_b is None:
        return True
    # One top-level, one inside if -> can execute together
    if arm_a is None or arm_b is None:
        return True

    if_node_a, which_a = arm_a
    if_node_b, which_b = arm_b

    # Same predicate (by AST structure), opposite arms -> mutually exclusive
    if ast.dump(if_node_a.test) == ast.dump(if_node_b.test) and which_a != which_b:
        return False

    # Complement predicates (if x body vs if not x body), same arm type -> mutually exclusive
    # Different predicates or same arm -> conservative (True)
    return not (_is_complement_test(if_node_a.test, if_node_b.test) and which_a == which_b)


# ---------------------------------------------------------------------------
# Override-assignment check
# ---------------------------------------------------------------------------


def _check_override_assignments(
    loc_a: FunctionLocation,
    loc_b: FunctionLocation,
) -> list[InterferencePath]:
    """Check if both branches assign different values to the same target.

    Only considers assignments within each branch's modified line ranges
    so that unchanged assignments inherited from the base don't trigger
    false positives.
    """
    assert loc_a.ast_node is not None and loc_b.ast_node is not None

    targets_a = _extract_assignments_in_ranges(loc_a.ast_node, loc_a.modified_ranges)
    targets_b = _extract_assignments_in_ranges(loc_b.ast_node, loc_b.modified_ranges)

    paths: list[InterferencePath] = []
    for name in set(targets_a) & set(targets_b):
        val_a, line_a = targets_a[name]
        val_b, line_b = targets_b[name]

        if _ast_values_differ(val_a, val_b):
            # Control-flow exclusivity filter: skip if assignments are in
            # mutually exclusive branches (e.g. opposite if/else arms)
            if not _assignments_on_same_execution_path(
                loc_a.ast_node, loc_b.ast_node, line_a, line_b
            ):
                continue

            source_id = make_node_id(loc_a.filepath, line_a, 0)
            sink_id = make_node_id(loc_b.filepath, line_b, 0)
            paths.append(
                InterferencePath(
                    direction="A_to_B",
                    conflict_type=ConflictType.OVERRIDE_ASSIGNMENT,
                    tier=1,
                    path_nodes=[source_id, sink_id],
                    source_node=source_id,
                    sink_node=sink_id,
                    evidence={
                        "pattern": "override_assignment",
                        "target": name,
                        "value_a_summary": ast.dump(val_a)[:80],
                        "value_b_summary": ast.dump(val_b)[:80],
                        "line_a": line_a,
                        "line_b": line_b,
                    },
                )
            )

    return paths


# ---------------------------------------------------------------------------
# Cross-function call signature mismatch
# ---------------------------------------------------------------------------


def _check_cross_call_signature_mismatch(
    diff_result: DiffResult,
) -> list[InterferencePath]:
    """Detect calls in one branch incompatible with signatures changed by the other.

    Pattern: Branch A changes function F's parameter list (adds/removes params).
    Branch B has a function G that calls F with the old argument count. The
    merged code gets A's new signature but B's old call → TypeError at runtime.
    """
    paths: list[InterferencePath] = []

    # Build signature maps keyed by function name
    a_sigs = _build_sig_map(diff_result.seeds_a)
    b_sigs = _build_sig_map(diff_result.seeds_b)

    # A changed signature, B has callers
    paths.extend(_find_mismatched_calls(a_sigs, diff_result.seeds_b, "A_to_B"))
    # B changed signature, A has callers
    paths.extend(_find_mismatched_calls(b_sigs, diff_result.seeds_a, "B_to_A"))

    return paths


def _build_sig_map(
    seeds: list[FunctionLocation],
) -> dict[str, list[tuple[FunctionLocation, dict]]]:
    """Map function name → [(location, param_info)] for seeds with AST nodes."""
    sigs: dict[str, list[tuple[FunctionLocation, dict]]] = {}
    for seed in seeds:
        if seed.ast_node is not None:
            info = _get_param_info(seed.ast_node)
            sigs.setdefault(seed.name, []).append((seed, info))
    return sigs


def _find_mismatched_calls(
    changed_sigs: dict[str, list[tuple[FunctionLocation, dict]]],
    caller_seeds: list[FunctionLocation],
    direction: str,
) -> list[InterferencePath]:
    """Find calls in caller_seeds whose arg count doesn't match changed_sigs."""
    paths: list[InterferencePath] = []
    seen: set[tuple[str, int, str, int]] = set()  # dedup

    for caller in caller_seeds:
        if caller.ast_node is None:
            continue

        for node in ast.walk(caller.ast_node):
            if not isinstance(node, ast.Call):
                continue

            call_name = _extract_call_name(node)
            if call_name is None or call_name not in changed_sigs:
                continue

            n_args = len(node.args)
            kw_names = {kw.arg for kw in node.keywords if kw.arg is not None}

            for target_loc, target_info in changed_sigs[call_name]:
                # Skip if target and caller are in the same file + same function
                # (co-modified, unlikely to be a cross-branch issue)
                if target_loc.filepath == caller.filepath and target_loc.name == caller.name:
                    continue

                # Cross-file bare-name matches (e.g. run(), get(), main()) are
                # almost always name collisions between unrelated functions.
                # Only allow cross-file matches for self.method() / cls.method()
                # calls, which can legitimately target a different file via
                # inheritance.
                if target_loc.filepath != caller.filepath and not _is_self_or_cls_call(node):
                    continue

                # Class-awareness filter: suppress when target and caller
                # are in different, unrelated classes within the same file.
                # Keeps: same class, both module-level, inheritance, or
                # indeterminate (conservative fallback).
                if not _class_context_compatible(target_loc, caller):
                    continue

                mismatch = _call_signature_mismatch(n_args, kw_names, target_info)
                if not mismatch:
                    continue

                key = (
                    str(target_loc.filepath),
                    target_loc.lineno,
                    str(caller.filepath),
                    node.lineno,
                )
                if key in seen:
                    continue
                seen.add(key)

                source_id = make_node_id(target_loc.filepath, target_loc.lineno, 0)
                sink_id = make_node_id(caller.filepath, node.lineno, node.col_offset)
                paths.append(
                    InterferencePath(
                        direction=direction,
                        conflict_type=ConflictType.DATA_FLOW,
                        tier=1,
                        path_nodes=[source_id, sink_id],
                        source_node=source_id,
                        sink_node=sink_id,
                        evidence={
                            "pattern": "cross_call_signature_mismatch",
                            "changed_function": target_loc.name,
                            "changed_file": str(target_loc.filepath),
                            "caller_function": caller.name,
                            "caller_file": str(caller.filepath),
                            "params_new": target_info["positional"],
                            "call_args_count": n_args + len(kw_names),
                            "detail": (
                                f"Caller passes {n_args + len(kw_names)} args "
                                f"but new signature requires {target_info['required_count']}."
                            ),
                        },
                    )
                )

    return paths


def _class_context_compatible(
    target_loc: FunctionLocation,
    caller_loc: FunctionLocation,
) -> bool:
    """Return True if the call from *caller_loc* could plausibly target *target_loc*.

    **KEEP** (return True) when:
    - Target and caller are in the same class.
    - Both are module-level functions (neither in a class).
    - Caller's class inherits from target's class.
    - Class context cannot be determined (conservative fallback).

    **SUPPRESS** (return False) when:
    - Target is in ClassA, caller is in ClassB, and ClassB does not inherit
      from ClassA.
    - Target is module-level but caller is in a class (name collision between
      a bare function and a same-named method).
    """
    if target_loc.ast_node is None or caller_loc.ast_node is None:
        return True  # indeterminate — keep

    target_class = _get_enclosing_class(target_loc.ast_node)
    caller_class = _get_enclosing_class(caller_loc.ast_node)

    # Both module-level — keep
    if target_class is None and caller_class is None:
        return True

    # Same class — keep
    if target_class is not None and target_class == caller_class:
        return True

    # Target is module-level, caller is in a class — name collision; suppress
    if target_class is None and caller_class is not None:
        return False

    # Target is in a class, caller is module-level — name collision; suppress
    if target_class is not None and caller_class is None:
        return False

    # Both in different classes — check inheritance
    assert target_class is not None and caller_class is not None
    caller_bases = _get_class_bases(caller_loc.ast_node)
    if caller_bases is not None and target_class in caller_bases:
        return True  # caller's class inherits from target's class

    # Also check the reverse: target's class inherits from caller's class
    target_bases = _get_class_bases(target_loc.ast_node)
    return target_bases is not None and caller_class in target_bases


def _call_signature_mismatch(
    n_args: int,
    kw_names: set[str],
    target_info: dict,
) -> bool:
    """Return True if the call args are incompatible with the target's params."""
    required = target_info["required_count"]
    max_pos = target_info["total_positional"]
    has_vararg = target_info["has_vararg"]
    has_kwarg = target_info["has_kwarg"]

    # Too few args (count keyword args that satisfy positional parameters)
    kw_satisfying_positional = len(kw_names & set(target_info["positional"]))
    satisfied_count = n_args + kw_satisfying_positional
    if satisfied_count < required and not has_vararg:
        return True

    # Too many positional args
    if n_args > max_pos and not has_vararg:
        return True

    # Keyword args that don't exist in the target
    if kw_names and not has_kwarg:
        all_params = set(target_info["positional"] + target_info["kwonly"])
        if kw_names - all_params:
            return True

    return False


# ---------------------------------------------------------------------------
# Post-hoc branch-boundary filter
# ---------------------------------------------------------------------------


def _filter_same_branch_paths(
    paths: list[InterferencePath],
    diff_result: DiffResult,
) -> list[InterferencePath]:
    """Remove paths where both endpoints belong to the same branch only.

    A valid direct-detector path must cross branch boundaries: the source
    should be covered by one branch's seeds and the sink by the other (or
    both endpoints are in a function modified by both branches).
    """
    # Build sets of (filepath, func_name) per branch for fast lookup
    a_keys: set[tuple[str, str]] = set()
    for seed in diff_result.seeds_a:
        a_keys.add((str(seed.filepath), seed.name))

    b_keys: set[tuple[str, str]] = set()
    for seed in diff_result.seeds_b:
        b_keys.add((str(seed.filepath), seed.name))

    # Build file-level sets as a fallback: if a NodeId's file is only in
    # one branch, the endpoint belongs exclusively to that branch.
    a_files: set[str] = {str(s.filepath) for s in diff_result.seeds_a}
    b_files: set[str] = {str(s.filepath) for s in diff_result.seeds_b}

    def _endpoint_branches(node_id: str) -> set[str]:
        """Return {'A'}, {'B'}, or {'A','B'} depending on which branch(es) own this endpoint."""
        # NodeId format: "filepath:lineno:col_offset"
        parts = node_id.rsplit(":", 2)
        if len(parts) < 3:
            return {"A", "B"}  # can't parse → assume cross-branch
        filepath = parts[0]
        branches: set[str] = set()
        if filepath in a_files:
            branches.add("A")
        if filepath in b_files:
            branches.add("B")
        return branches if branches else {"A", "B"}

    result: list[InterferencePath] = []
    for path in paths:
        src_branches = _endpoint_branches(path.source_node)
        snk_branches = _endpoint_branches(path.sink_node)

        # Keep if: source is in A and sink is in B (or vice versa),
        # OR both are in a file touched by both branches (same-function checks).
        if src_branches == snk_branches and len(src_branches) == 1:
            # Both endpoints exclusively in one branch → same-branch FP
            continue
        result.append(path)

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_backward_compatible_growth(
    positional_a: list[str],
    positional_b: list[str],
    info_a: dict,
    info_b: dict,
) -> bool:
    """Check if one param list is a strict prefix of the other with all extras having defaults.

    Returns True (suppress) when:
    - The shorter list's params are a prefix of the longer list (same names, same order).
    - All extra params in the longer list have defaults (the longer side's
      required_count <= the shorter side's total_positional).

    This covers backward-compatible API growth: ``def foo(a, b)`` vs
    ``def foo(a, b, c=None, d=0)`` is safe because callers using the old
    ``(a, b)`` signature still work.
    """
    if len(positional_a) == len(positional_b):
        return False  # same length but different names — not a growth pattern

    if len(positional_a) < len(positional_b):
        shorter, longer = positional_a, positional_b
        info_shorter, info_longer = info_a, info_b
    else:
        shorter, longer = positional_b, positional_a
        info_shorter, info_longer = info_b, info_a

    # Check prefix: shorter's params must match the first N params of longer
    n = len(shorter)
    if longer[:n] != shorter:
        return False

    # Check that all extra params in the longer list have defaults.
    # This means the longer side's required_count must be <= shorter's total_positional.
    return info_longer["required_count"] <= info_shorter["total_positional"]


def _get_param_info(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict:
    """Extract parameter metadata from a function definition.

    Returns a dict with keys:
        positional: list[str]  — positional param names (excluding self/cls)
        required_count: int    — positional params without defaults
        total_positional: int  — all positional params (with or without defaults)
        has_vararg: bool       — has *args
        has_kwarg: bool        — has **kwargs
        kwonly: list[str]      — keyword-only param names
    """
    args = func_node.args

    positional = [a.arg for a in args.args if a.arg not in ("self", "cls")]
    n_defaults = len(args.defaults)
    required = len(positional) - n_defaults

    return {
        "positional": positional,
        "required_count": max(required, 0),
        "total_positional": len(positional),
        "has_vararg": args.vararg is not None,
        "has_kwarg": args.kwarg is not None,
        "kwonly": [a.arg for a in args.kwonlyargs],
    }


def _get_enclosing_class(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """Return the name of the class enclosing *func_node*, or None if module-level.

    Requires parent pointers (``_parent`` attribute) on AST nodes, as set by
    ``diff_parser._map_ranges_to_functions`` or ``FunctionIndex.ensure_parsed``.
    Falls back to None if ``_parent`` is not set (conservative -- allows the finding).
    """
    node: ast.AST = func_node
    while hasattr(node, "_parent") and node._parent is not None:  # type: ignore[attr-defined]
        node = node._parent  # type: ignore[attr-defined]
        if isinstance(node, ast.ClassDef):
            return node.name
    return None


def _get_class_bases(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[str] | None:
    """Return the base-class names of the enclosing class, or None if not in a class.

    Only returns simple ``Name`` bases (e.g. ``class Child(Base)`` yields ``["Base"]``).
    Attribute bases like ``mod.Base`` yield the attribute name (``"Base"``).
    """
    node: ast.AST = func_node
    while hasattr(node, "_parent") and node._parent is not None:  # type: ignore[attr-defined]
        node = node._parent  # type: ignore[attr-defined]
        if isinstance(node, ast.ClassDef):
            bases: list[str] = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(base.attr)
            return bases
    return None


def _is_self_or_cls_call(call_node: ast.Call) -> bool:
    """Return True if call is self.method() or cls.method()."""
    func = call_node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id in ("self", "cls")
    return False


def _extract_call_name(call_node: ast.Call) -> str | None:
    """Extract the function/method name from a Call node.

    ``foo()`` → ``"foo"``, ``self.foo()`` → ``"foo"``,
    ``obj.foo()`` → ``"foo"``.
    """
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _collect_name_references(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    """Collect all ``ast.Name`` identifiers referenced in a function body."""
    names: set[str] = set()
    for stmt in func_node.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name):
                names.add(node.id)
    return names


def _extract_assignments_in_ranges(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    modified_ranges: list[tuple[int, int]],
) -> dict[str, tuple[ast.expr, int]]:
    """Extract ``{target_string: (value_node, lineno)}`` for assignments in modified ranges.

    Handles three kinds of assignments:

    1. Regular assignments: ``x = val``, ``x += val``, ``x: int = val``
    2. Dict literal entries: key-value pairs inside dict literals (``{...}``)
       that appear in return statements or variable assignments. Each
       ``key: value`` pair whose line falls within *modified_ranges* is
       treated as an assignment where the target is the key (as a constant
       string) and the value is the value node.
    """
    result: dict[str, tuple[ast.expr, int]] = {}

    for node in ast.walk(func_node):
        if not hasattr(node, "lineno"):
            continue
        if not any(s <= node.lineno <= e for s, e in modified_ranges):
            continue

        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = _target_to_string(target)
                if name is not None:
                    result[name] = (node.value, node.lineno)
        elif isinstance(node, ast.AugAssign) or (
            isinstance(node, ast.AnnAssign) and node.value is not None
        ):
            name = _target_to_string(node.target)
            if name is not None:
                result[name] = (node.value, node.lineno)

    # Second pass: extract dict literal key-value entries within modified ranges.
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=False):
            if key is None:
                continue  # **splat entry
            if not hasattr(key, "lineno"):
                continue
            if not any(s <= key.lineno <= e for s, e in modified_ranges):
                continue
            name = _target_to_string(key)
            if name is not None:
                result[name] = (value, key.lineno)

    # Third pass: extract keyword arguments in function calls within modified
    # ranges.  Patterns like ``NamespaceDict(sql=frappe.db.sql, ...)`` use
    # keyword args as key-value bindings, semantically equivalent to dict
    # entries.  Each keyword whose value line falls within *modified_ranges*
    # is treated as an assignment where the target is the keyword name.
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg is None:
                continue  # **kwargs unpacking
            if not hasattr(kw, "lineno") and hasattr(kw.value, "lineno"):
                lineno = kw.value.lineno
            elif hasattr(kw, "lineno"):
                lineno = kw.lineno
            else:
                continue
            if not any(s <= lineno <= e for s, e in modified_ranges):
                continue
            # Use the keyword name as the target string
            result[kw.arg] = (kw.value, lineno)

    return result


def _target_to_string(node: ast.AST) -> str | None:
    """Convert an assignment target to a canonical string.

    ``x`` → ``"x"``, ``self.x`` → ``"self.x"``,
    ``d['key']`` → ``"d['key']"``, ``ast.Constant("sql")`` → ``"'sql'"``.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return repr(node.value)
    if isinstance(node, ast.Attribute):
        base = _target_to_string(node.value)
        return f"{base}.{node.attr}" if base else None
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        base = _target_to_string(node.value)
        return f"{base}[{node.slice.value!r}]" if base else None
    return None


def _ast_values_differ(val_a: ast.expr, val_b: ast.expr) -> bool:
    """Conservative check: return True unless both ASTs are structurally identical."""
    try:
        return ast.dump(val_a) != ast.dump(val_b)
    except Exception:
        return True
