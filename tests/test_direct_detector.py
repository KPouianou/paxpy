"""Tests for direct_detector.py."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from paxpy.direct_detector import (
    _assignments_on_same_execution_path,
    _ast_values_differ,
    _call_signature_mismatch,
    _check_cross_call_signature_mismatch,
    _check_override_assignments,
    _check_signature_mismatch_same_func,
    _class_context_compatible,
    _extract_assignments_in_ranges,
    _extract_call_name,
    _find_enclosing_if_arm,
    _get_enclosing_class,
    _get_param_info,
    _is_backward_compatible_growth,
    _is_complement_test,
    _is_self_or_cls_call,
    _target_to_string,
    detect_direct_conflicts,
)
from paxpy.types import ConflictType, DiffResult, FunctionLocation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _add_parent_pointers(tree: ast.Module) -> ast.Module:
    """Add _parent attributes to all nodes in an AST."""
    tree._parent = None  # type: ignore[attr-defined]
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]
    return tree


def _make_func(source: str, name: str = "f") -> ast.FunctionDef:
    """Parse a function from source and return the FunctionDef node."""
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise ValueError(f"Function {name!r} not found in source")


def _make_func_with_parents(source: str, name: str = "f") -> ast.FunctionDef:
    """Parse a function from source with parent pointers and return the FunctionDef node."""
    tree = _add_parent_pointers(ast.parse(textwrap.dedent(source)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise ValueError(f"Function {name!r} not found in source")


def _make_loc(
    source: str,
    name: str = "f",
    filepath: str = "/repo/mod.py",
    branch: str = "A",
    modified_ranges: list[tuple[int, int]] | None = None,
) -> FunctionLocation:
    """Build a FunctionLocation from source text."""
    func = _make_func(source, name)
    if modified_ranges is None:
        # Default: entire function body is modified
        modified_ranges = [(func.lineno, func.end_lineno or func.lineno)]
    return FunctionLocation(
        name=name,
        filepath=Path(filepath),
        lineno=func.lineno,
        end_lineno=func.end_lineno,
        ast_node=func,
        branch=branch,
        modified_ranges=modified_ranges,
    )


def _make_loc_with_parents(
    source: str,
    name: str = "f",
    filepath: str = "/repo/mod.py",
    branch: str = "A",
    modified_ranges: list[tuple[int, int]] | None = None,
) -> FunctionLocation:
    """Build a FunctionLocation from source text, with parent pointers on the AST."""
    func = _make_func_with_parents(source, name)
    if modified_ranges is None:
        modified_ranges = [(func.lineno, func.end_lineno or func.lineno)]
    return FunctionLocation(
        name=name,
        filepath=Path(filepath),
        lineno=func.lineno,
        end_lineno=func.end_lineno,
        ast_node=func,
        branch=branch,
        modified_ranges=modified_ranges,
    )


# ---------------------------------------------------------------------------
# _get_param_info
# ---------------------------------------------------------------------------


class TestGetParamInfo:
    def test_simple_function(self):
        func = _make_func("def f(x, y): pass")
        info = _get_param_info(func)
        assert info["positional"] == ["x", "y"]
        assert info["required_count"] == 2
        assert info["total_positional"] == 2
        assert info["has_vararg"] is False
        assert info["has_kwarg"] is False

    def test_method_excludes_self(self):
        func = _make_func("def f(self, x, y): pass")
        info = _get_param_info(func)
        assert info["positional"] == ["x", "y"]
        assert info["required_count"] == 2

    def test_defaults_reduce_required(self):
        func = _make_func("def f(x, y=1, z=2): pass")
        info = _get_param_info(func)
        assert info["required_count"] == 1
        assert info["total_positional"] == 3

    def test_vararg_and_kwarg(self):
        func = _make_func("def f(x, *args, key=1, **kwargs): pass")
        info = _get_param_info(func)
        assert info["positional"] == ["x"]
        assert info["has_vararg"] is True
        assert info["has_kwarg"] is True
        assert info["kwonly"] == ["key"]


# ---------------------------------------------------------------------------
# _extract_call_name
# ---------------------------------------------------------------------------


class TestExtractCallName:
    def test_simple_call(self):
        tree = ast.parse("foo()")
        call = tree.body[0].value
        assert _extract_call_name(call) == "foo"

    def test_method_call(self):
        tree = ast.parse("self.foo()")
        call = tree.body[0].value
        assert _extract_call_name(call) == "foo"

    def test_chained_attribute(self):
        tree = ast.parse("obj.mod.foo()")
        call = tree.body[0].value
        assert _extract_call_name(call) == "foo"


# ---------------------------------------------------------------------------
# _target_to_string
# ---------------------------------------------------------------------------


class TestTargetToString:
    def test_name(self):
        tree = ast.parse("x = 1")
        target = tree.body[0].targets[0]
        assert _target_to_string(target) == "x"

    def test_attribute(self):
        tree = ast.parse("self.x = 1")
        target = tree.body[0].targets[0]
        assert _target_to_string(target) == "self.x"

    def test_subscript_string_key(self):
        tree = ast.parse("d['key'] = 1")
        target = tree.body[0].targets[0]
        assert _target_to_string(target) == "d['key']"

    def test_subscript_int_key(self):
        tree = ast.parse("d[0] = 1")
        target = tree.body[0].targets[0]
        assert _target_to_string(target) == "d[0]"


# ---------------------------------------------------------------------------
# _ast_values_differ
# ---------------------------------------------------------------------------


class TestAstValuesDiffer:
    def test_same_constant(self):
        a = ast.parse("x = 42").body[0].value
        b = ast.parse("x = 42").body[0].value
        assert _ast_values_differ(a, b) is False

    def test_different_constants(self):
        a = ast.parse("x = 42").body[0].value
        b = ast.parse("x = 99").body[0].value
        assert _ast_values_differ(a, b) is True

    def test_same_string(self):
        a = ast.parse("x = 'hello'").body[0].value
        b = ast.parse("x = 'hello'").body[0].value
        assert _ast_values_differ(a, b) is False


# ---------------------------------------------------------------------------
# Signature mismatch: same function
# ---------------------------------------------------------------------------


class TestSignatureMismatchSameFunc:
    def test_identical_signatures_no_mismatch(self):
        loc_a = _make_loc("def f(x, y): pass", branch="A")
        loc_b = _make_loc("def f(x, y): pass", branch="B")
        assert _check_signature_mismatch_same_func(loc_a, loc_b) is None

    def test_different_param_count_flags(self):
        loc_a = _make_loc("def f(self, magd, npd, hdd): pass", branch="A")
        loc_b = _make_loc("def f(self, mags, nplanes): pass", branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is not None
        assert result.conflict_type == ConflictType.DATA_FLOW

    def test_renamed_param_with_body_usage(self):
        """Both branches use their own param name in the body — merge breaks."""
        src_a = textwrap.dedent("""\
            def f(self, ctx):
                for rec in ctx:
                    print(rec)
        """)
        src_b = textwrap.dedent("""\
            def f(self, rup):
                lons = rup.surface.mesh.lons
                print(lons)
        """)
        loc_a = _make_loc(src_a, branch="A")
        loc_b = _make_loc(src_b, branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is not None

    def test_renamed_param_not_used_in_body(self):
        """Rename without body usage is not flagged (param is dead)."""
        src_a = textwrap.dedent("""\
            def f(self, ctx):
                print("hello")
        """)
        src_b = textwrap.dedent("""\
            def f(self, rup):
                print("hello")
        """)
        loc_a = _make_loc(src_a, branch="A")
        loc_b = _make_loc(src_b, branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is None


# ---------------------------------------------------------------------------
# Override assignment
# ---------------------------------------------------------------------------


class TestOverrideAssignment:
    def test_same_target_different_values(self):
        src_a = textwrap.dedent("""\
            def f():
                x = 10
        """)
        src_b = textwrap.dedent("""\
            def f():
                x = 20
        """)
        loc_a = _make_loc(src_a, branch="A", modified_ranges=[(2, 2)])
        loc_b = _make_loc(src_b, branch="B", modified_ranges=[(2, 2)])
        results = _check_override_assignments(loc_a, loc_b)
        assert len(results) == 1
        assert results[0].conflict_type == ConflictType.OVERRIDE_ASSIGNMENT

    def test_same_target_same_value_no_flag(self):
        src_a = textwrap.dedent("""\
            def f():
                x = 10
        """)
        src_b = textwrap.dedent("""\
            def f():
                x = 10
        """)
        loc_a = _make_loc(src_a, branch="A", modified_ranges=[(2, 2)])
        loc_b = _make_loc(src_b, branch="B", modified_ranges=[(2, 2)])
        results = _check_override_assignments(loc_a, loc_b)
        assert len(results) == 0

    def test_dict_key_override(self):
        """Mimics the chime sidebar switch conflict."""
        src_a = textwrap.dedent("""\
            def configure():
                switches = {}
                switches['show_tables'] = {'value': True}
        """)
        src_b = textwrap.dedent("""\
            def configure():
                switches = {}
                switches['show_tables'] = {'on': True}
        """)
        loc_a = _make_loc(
            src_a,
            name="configure",
            branch="A",
            modified_ranges=[(3, 3)],
        )
        loc_b = _make_loc(
            src_b,
            name="configure",
            branch="B",
            modified_ranges=[(3, 3)],
        )
        results = _check_override_assignments(loc_a, loc_b)
        assert len(results) == 1
        assert results[0].conflict_type == ConflictType.OVERRIDE_ASSIGNMENT

    def test_different_targets_no_flag(self):
        src_a = textwrap.dedent("""\
            def f():
                x = 10
        """)
        src_b = textwrap.dedent("""\
            def f():
                y = 20
        """)
        loc_a = _make_loc(src_a, branch="A", modified_ranges=[(2, 2)])
        loc_b = _make_loc(src_b, branch="B", modified_ranges=[(2, 2)])
        results = _check_override_assignments(loc_a, loc_b)
        assert len(results) == 0

    def test_attribute_override(self):
        src_a = textwrap.dedent("""\
            def f(self):
                self.timeout = 30
        """)
        src_b = textwrap.dedent("""\
            def f(self):
                self.timeout = 60
        """)
        loc_a = _make_loc(src_a, branch="A", modified_ranges=[(2, 2)])
        loc_b = _make_loc(src_b, branch="B", modified_ranges=[(2, 2)])
        results = _check_override_assignments(loc_a, loc_b)
        assert len(results) == 1

    def test_assignment_outside_modified_range_ignored(self):
        """Assignments not in modified ranges shouldn't trigger OA."""
        src_a = textwrap.dedent("""\
            def f():
                x = 10
                y = 20
        """)
        src_b = textwrap.dedent("""\
            def f():
                x = 10
                y = 30
        """)
        # Only line 3 (y=...) is in modified range
        loc_a = _make_loc(src_a, branch="A", modified_ranges=[(3, 3)])
        loc_b = _make_loc(src_b, branch="B", modified_ranges=[(3, 3)])
        results = _check_override_assignments(loc_a, loc_b)
        # y is flagged (different values in modified range), x is not
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Cross-function call signature mismatch
# ---------------------------------------------------------------------------


class TestCrossCallSignatureMismatch:
    def test_caller_uses_old_arg_count(self):
        """Branch A adds a param, branch B calls with old arg count."""
        # A changed get_planin to take 3 args (excluding self)
        src_a_def = textwrap.dedent("""\
            def get_planin(self, magd, npd, hdd):
                return magd + npd + hdd
        """)
        # B has a function that calls get_planin with 2 args
        src_b_caller = textwrap.dedent("""\
            def get_radius(self):
                result = self.get_planin([1.0], nplanes)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="get_planin",
            filepath="/repo/point.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="get_radius",
            filepath="/repo/point.py",
            branch="B",
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) >= 1
        assert results[0].direction == "A_to_B"

    def test_matching_arg_count_no_flag(self):
        """Call matches signature — no mismatch."""
        src_a_def = textwrap.dedent("""\
            def compute(self, x, y):
                return x + y
        """)
        src_b_caller = textwrap.dedent("""\
            def run(self):
                result = self.compute(1, 2)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="compute",
            filepath="/repo/mod.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="run",
            filepath="/repo/other.py",
            branch="B",
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) == 0

    def test_too_many_args(self):
        """Caller passes more args than the new signature accepts."""
        src_a_def = textwrap.dedent("""\
            def process(x):
                return x
        """)
        src_b_caller = textwrap.dedent("""\
            def run():
                result = process(1, 2, 3)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="process",
            filepath="/repo/mod.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="run",
            filepath="/repo/mod.py",
            branch="B",
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) >= 1

    def test_vararg_absorbs_extra_args(self):
        """*args absorbs any number of positional args — no mismatch."""
        src_a_def = textwrap.dedent("""\
            def process(x, *args):
                return x
        """)
        src_b_caller = textwrap.dedent("""\
            def run():
                result = process(1, 2, 3)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="process",
            filepath="/repo/mod.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="run",
            filepath="/repo/mod.py",
            branch="B",
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Full detect_direct_conflicts
# ---------------------------------------------------------------------------


class TestDetectDirectConflicts:
    def test_empty_diff_returns_empty(self):
        diff = DiffResult()
        assert detect_direct_conflicts(diff) == []

    def test_oa_detected_end_to_end(self):
        src_a = textwrap.dedent("""\
            def f():
                config = 'option_a'
        """)
        src_b = textwrap.dedent("""\
            def f():
                config = 'option_b'
        """)
        diff = DiffResult(
            seeds_a=[_make_loc(src_a, branch="A", modified_ranges=[(2, 2)])],
            seeds_b=[_make_loc(src_b, branch="B", modified_ranges=[(2, 2)])],
        )
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) >= 1

    def test_signature_mismatch_detected_end_to_end(self):
        src_a = textwrap.dedent("""\
            def process(self, x, y, z):
                return x + y + z
        """)
        src_b = textwrap.dedent("""\
            def process(self, x):
                return x
        """)
        diff = DiffResult(
            seeds_a=[_make_loc(src_a, name="process", branch="A")],
            seeds_b=[_make_loc(src_b, name="process", branch="B")],
        )
        results = detect_direct_conflicts(diff)
        assert len(results) >= 1

    def test_cross_call_mismatch_detected_end_to_end(self):
        src_a_def = textwrap.dedent("""\
            def target(self, a, b, c):
                return a + b + c
        """)
        src_b_caller = textwrap.dedent("""\
            def caller(self):
                return self.target(1)
        """)
        diff = DiffResult(
            seeds_a=[
                _make_loc(
                    src_a_def,
                    name="target",
                    filepath="/repo/mod.py",
                    branch="A",
                )
            ],
            seeds_b=[
                _make_loc(
                    src_b_caller,
                    name="caller",
                    filepath="/repo/other.py",
                    branch="B",
                )
            ],
        )
        results = detect_direct_conflicts(diff)
        assert len(results) >= 1

    def test_no_overlap_no_conflict(self):
        """Different functions in different files — no conflict."""
        src_a = textwrap.dedent("""\
            def foo():
                return 1
        """)
        src_b = textwrap.dedent("""\
            def bar():
                return 2
        """)
        diff = DiffResult(
            seeds_a=[_make_loc(src_a, name="foo", filepath="/repo/a.py", branch="A")],
            seeds_b=[_make_loc(src_b, name="bar", filepath="/repo/b.py", branch="B")],
        )
        results = detect_direct_conflicts(diff)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# _is_self_or_cls_call
# ---------------------------------------------------------------------------


class TestIsSelfOrClsCall:
    def test_self_call(self):
        tree = ast.parse("self.run()")
        call = tree.body[0].value
        assert _is_self_or_cls_call(call) is True

    def test_cls_call(self):
        tree = ast.parse("cls.create()")
        call = tree.body[0].value
        assert _is_self_or_cls_call(call) is True

    def test_bare_call(self):
        tree = ast.parse("run()")
        call = tree.body[0].value
        assert _is_self_or_cls_call(call) is False

    def test_other_object_call(self):
        tree = ast.parse("obj.run()")
        call = tree.body[0].value
        assert _is_self_or_cls_call(call) is False


# ---------------------------------------------------------------------------
# Cross-file name collision tests (Task A)
# ---------------------------------------------------------------------------


class TestCrossFileNameCollision:
    def test_bare_name_cross_file_no_match(self):
        """Two functions named run() in different files — no cross-call match."""
        # A changes run() signature in file1.py
        src_a_def = textwrap.dedent("""\
            def run(self, x, y, z):
                return x + y + z
        """)
        # B has a function in file2.py that calls run() with 1 arg
        src_b_caller = textwrap.dedent("""\
            def main():
                result = run(config)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="run",
            filepath="/repo/file1.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="main",
            filepath="/repo/file2.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        # Bare run() call in a different file should NOT match
        assert len(results) == 0

    def test_self_call_same_file_still_matches(self):
        """self.get_planin() calling same-file function — should match."""
        src_a_def = textwrap.dedent("""\
            def get_planin(self, magd, npd, hdd):
                return magd + npd + hdd
        """)
        src_b_caller = textwrap.dedent("""\
            def get_radius(self):
                result = self.get_planin([1.0], nplanes)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="get_planin",
            filepath="/repo/point.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="get_radius",
            filepath="/repo/point.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        # Same file, self.method() call — should still match
        assert len(results) >= 1

    def test_self_call_cross_file_matches(self):
        """self.method() cross-file — allowed (inheritance case)."""
        src_a_def = textwrap.dedent("""\
            def process(self, x, y, z):
                return x + y + z
        """)
        src_b_caller = textwrap.dedent("""\
            def run(self):
                result = self.process(1)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="process",
            filepath="/repo/base.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="run",
            filepath="/repo/child.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        # Cross-file but self.method() — should match (inheritance)
        assert len(results) >= 1

    def test_bare_func_cross_file_no_match(self):
        """Bare func() cross-file — should NOT match (name collision risk)."""
        src_a_def = textwrap.dedent("""\
            def get(key):
                return data[key]
        """)
        src_b_caller = textwrap.dedent("""\
            def fetch():
                result = get("value", extra=True)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="get",
            filepath="/repo/cache.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="fetch",
            filepath="/repo/api.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        # Bare get() call in a different file — NO match
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Same-branch path filter tests (Task B)
# ---------------------------------------------------------------------------


class TestSameBranchPathFilter:
    def test_single_branch_changes_no_conflict(self):
        """Changes only in one branch should not produce paths."""
        # Only branch A modifies file1.py, branch B modifies file2.py
        # If both have a function named 'process' with different signatures,
        # the cross-call check might try to match, but the branch filter
        # should suppress single-branch paths.
        src_a_def = textwrap.dedent("""\
            def process(self, x, y, z):
                return x + y + z
        """)
        src_a_caller = textwrap.dedent("""\
            def handler(self):
                result = self.process(1)
                return result
        """)
        # Both seeds are from branch A, in the same file
        seed_a_def = _make_loc(
            src_a_def,
            name="process",
            filepath="/repo/only_a.py",
            branch="A",
        )
        seed_a_caller = _make_loc(
            src_a_caller,
            name="handler",
            filepath="/repo/only_a.py",
            branch="A",
        )
        # Branch B has unrelated changes in a different file
        src_b = textwrap.dedent("""\
            def unrelated():
                return 42
        """)
        seed_b = _make_loc(
            src_b,
            name="unrelated",
            filepath="/repo/only_b.py",
            branch="B",
        )
        # Simulate: seeds_a has both A functions, seeds_b has the B function
        # The cross-call check should not produce paths between A-only files
        diff = DiffResult(
            seeds_a=[seed_a_def, seed_a_caller],
            seeds_b=[seed_b],
        )
        results = detect_direct_conflicts(diff)
        # No cross-branch paths should exist
        assert len(results) == 0


# ---------------------------------------------------------------------------
# False-positive pattern tests (Step 13 — Task A)
# ---------------------------------------------------------------------------


class TestFalsePositivePatterns:
    """Tests that verify specific FP patterns are correctly suppressed."""

    def test_bare_cross_file_name_collision_blocked(self):
        """Bare function call run() across files should NOT match (name collision)."""
        # Branch A: module-level function run(x, y, z) in server.py
        src_a = textwrap.dedent("""\
            def run(x, y, z):
                return x + y + z
        """)
        # Branch B: process() in jobs.py calls run() with 1 arg (bare call)
        src_b = textwrap.dedent("""\
            def process():
                result = run(config)
                return result
        """)
        seed_a = _make_loc(
            src_a,
            name="run",
            filepath="/repo/server.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b,
            name="process",
            filepath="/repo/jobs.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        # Bare cross-file call is blocked by step 12 fix
        assert len(results) == 0

    def test_same_function_independent_variables_no_oa(self):
        """Both branches modify same function but different variables — no OA."""
        src_a = textwrap.dedent("""\
            def process():
                timeout = 30
                return timeout
        """)
        src_b = textwrap.dedent("""\
            def process():
                retries = 3
                return retries
        """)
        seed_a = _make_loc(
            src_a,
            name="process",
            branch="A",
            modified_ranges=[(2, 2)],
        )
        seed_b = _make_loc(
            src_b,
            name="process",
            branch="B",
            modified_ranges=[(2, 2)],
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) == 0

    def test_same_variable_mutually_exclusive_branches_no_oa(self):
        """Same variable in mutually exclusive if/else blocks should not flag OA."""
        src_a = textwrap.dedent("""\
            def handle(error):
                if error:
                    result = "failed"
                return result
        """)
        src_b = textwrap.dedent("""\
            def handle(error):
                if not error:
                    result = "success"
                return result
        """)
        seed_a = _make_loc(
            src_a,
            name="handle",
            branch="A",
            modified_ranges=[(3, 3)],
        )
        seed_b = _make_loc(
            src_b,
            name="handle",
            branch="B",
            modified_ranges=[(3, 3)],
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) == 0

    def test_identical_assignment_no_oa(self):
        """Both branches assign identical values — no OA (convergent)."""
        src_a = textwrap.dedent("""\
            def init(self):
                self.cache = {}
        """)
        src_b = textwrap.dedent("""\
            def init(self):
                self.cache = {}
        """)
        seed_a = _make_loc(
            src_a,
            name="init",
            branch="A",
            modified_ranges=[(2, 2)],
        )
        seed_b = _make_loc(
            src_b,
            name="init",
            branch="B",
            modified_ranges=[(2, 2)],
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) == 0

    def test_single_branch_only_changes_no_conflict(self):
        """A modifies one file, B modifies a different file with no call — 0 paths."""
        src_a = textwrap.dedent("""\
            def process():
                return 42
        """)
        src_b = textwrap.dedent("""\
            def unrelated():
                return "hello"
        """)
        seed_a = _make_loc(
            src_a,
            name="process",
            filepath="/repo/mod.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b,
            name="unrelated",
            filepath="/repo/other.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        assert len(results) == 0

    def test_cross_file_bare_get_call_blocked(self):
        """Bare get() call across files should NOT match (name collision risk)."""
        src_a = textwrap.dedent("""\
            def get(self, key):
                return self.data[key]
        """)
        src_b = textwrap.dedent("""\
            def fetch(self):
                result = get(url)
                return result
        """)
        seed_a = _make_loc(
            src_a,
            name="get",
            filepath="/repo/cache.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b,
            name="fetch",
            filepath="/repo/api.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        assert len(results) == 0

    def test_complement_predicates_no_oa(self):
        """if x body vs if not x body -> mutually exclusive, no OA."""
        src_a = textwrap.dedent("""\
            def handle(flag):
                if flag:
                    mode = "on"
                return mode
        """)
        src_b = textwrap.dedent("""\
            def handle(flag):
                if not flag:
                    mode = "off"
                return mode
        """)
        seed_a = _make_loc(src_a, name="handle", branch="A", modified_ranges=[(3, 3)])
        seed_b = _make_loc(src_b, name="handle", branch="B", modified_ranges=[(3, 3)])
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) == 0

    def test_same_if_arm_still_flags_oa(self):
        """Both in if-body of same predicate -> same path, OA flagged."""
        src_a = textwrap.dedent("""\
            def handle(flag):
                if flag:
                    mode = "fast"
                return mode
        """)
        src_b = textwrap.dedent("""\
            def handle(flag):
                if flag:
                    mode = "slow"
                return mode
        """)
        seed_a = _make_loc(src_a, name="handle", branch="A", modified_ranges=[(3, 3)])
        seed_b = _make_loc(src_b, name="handle", branch="B", modified_ranges=[(3, 3)])
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) >= 1

    def test_top_level_still_flags_oa(self):
        """Both at top level of function -> OA flagged."""
        src_a = textwrap.dedent("""\
            def f():
                x = 1
        """)
        src_b = textwrap.dedent("""\
            def f():
                x = 2
        """)
        seed_a = _make_loc(src_a, branch="A", modified_ranges=[(2, 2)])
        seed_b = _make_loc(src_b, branch="B", modified_ranges=[(2, 2)])
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) >= 1

    def test_nested_if_exclusivity(self):
        """Nested if: opposite arms of same inner if -> mutually exclusive."""
        src_a = textwrap.dedent("""\
            def handle(x, y):
                if x:
                    if y:
                        val = "a"
                return val
        """)
        src_b = textwrap.dedent("""\
            def handle(x, y):
                if x:
                    if y:
                        pass
                    else:
                        val = "b"
                return val
        """)
        seed_a = _make_loc(src_a, name="handle", branch="A", modified_ranges=[(4, 4)])
        seed_b = _make_loc(src_b, name="handle", branch="B", modified_ranges=[(5, 6)])
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)
        oa = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa) == 0


# ---------------------------------------------------------------------------
# Control-flow exclusivity unit tests
# ---------------------------------------------------------------------------


class TestControlFlowExclusivity:
    """Unit tests for the control-flow exclusivity helpers."""

    # -- _find_enclosing_if_arm ---

    def test_top_level_returns_none(self):
        func = _make_func(
            textwrap.dedent("""\
            def f():
                x = 1
        """)
        )
        assert _find_enclosing_if_arm(func, 2) is None

    def test_if_body(self):
        func = _make_func(
            textwrap.dedent("""\
            def f():
                if True:
                    x = 1
        """)
        )
        result = _find_enclosing_if_arm(func, 3)
        assert result is not None
        assert result[1] == "body"
        assert isinstance(result[0], ast.If)

    def test_else_block(self):
        func = _make_func(
            textwrap.dedent("""\
            def f():
                if True:
                    x = 1
                else:
                    x = 2
        """)
        )
        result = _find_enclosing_if_arm(func, 5)
        assert result is not None
        assert result[1] == "orelse"

    def test_nested_if_returns_inner(self):
        func = _make_func(
            textwrap.dedent("""\
            def f():
                if True:
                    if False:
                        x = 1
        """)
        )
        result = _find_enclosing_if_arm(func, 4)
        assert result is not None
        if_node, arm = result
        assert arm == "body"
        # Should be the inner if (test is False constant)
        assert isinstance(if_node.test, ast.Constant)
        assert if_node.test.value is False

    def test_for_inside_if(self):
        func = _make_func(
            textwrap.dedent("""\
            def f():
                if True:
                    for i in range(10):
                        x = i
        """)
        )
        result = _find_enclosing_if_arm(func, 4)
        assert result is not None
        if_node, arm = result
        assert arm == "body"
        # Should be the outer if, not the for loop
        assert isinstance(if_node, ast.If)

    # -- _is_complement_test ---

    def test_x_vs_not_x(self):
        a = ast.parse("x").body[0].value
        b = ast.parse("not x").body[0].value
        assert _is_complement_test(a, b) is True

    def test_not_x_vs_x(self):
        a = ast.parse("not x").body[0].value
        b = ast.parse("x").body[0].value
        assert _is_complement_test(a, b) is True

    def test_x_vs_y(self):
        a = ast.parse("x").body[0].value
        b = ast.parse("y").body[0].value
        assert _is_complement_test(a, b) is False

    def test_not_x_vs_not_x(self):
        a = ast.parse("not x").body[0].value
        b = ast.parse("not x").body[0].value
        assert _is_complement_test(a, b) is False

    def test_attr_complement(self):
        a = ast.parse("a.b").body[0].value
        b = ast.parse("not a.b").body[0].value
        assert _is_complement_test(a, b) is True

    # -- _assignments_on_same_execution_path ---

    def test_both_top_level(self):
        func_a = _make_func("def f():\n    x = 1")
        func_b = _make_func("def f():\n    x = 2")
        assert _assignments_on_same_execution_path(func_a, func_b, 2, 2) is True

    def test_same_if_arm(self):
        func_a = _make_func(
            textwrap.dedent("""\
            def f():
                if cond:
                    x = 1
        """)
        )
        func_b = _make_func(
            textwrap.dedent("""\
            def f():
                if cond:
                    x = 2
        """)
        )
        assert _assignments_on_same_execution_path(func_a, func_b, 3, 3) is True

    def test_opposite_if_else_arms(self):
        func_a = _make_func(
            textwrap.dedent("""\
            def f():
                if cond:
                    x = 1
                else:
                    pass
        """)
        )
        func_b = _make_func(
            textwrap.dedent("""\
            def f():
                if cond:
                    pass
                else:
                    x = 2
        """)
        )
        assert _assignments_on_same_execution_path(func_a, func_b, 3, 5) is False

    def test_complement_predicates_body(self):
        func_a = _make_func(
            textwrap.dedent("""\
            def f():
                if flag:
                    x = 1
        """)
        )
        func_b = _make_func(
            textwrap.dedent("""\
            def f():
                if not flag:
                    x = 2
        """)
        )
        assert _assignments_on_same_execution_path(func_a, func_b, 3, 3) is False

    def test_complement_predicates_orelse(self):
        func_a = _make_func(
            textwrap.dedent("""\
            def f():
                if flag:
                    pass
                else:
                    x = 1
        """)
        )
        func_b = _make_func(
            textwrap.dedent("""\
            def f():
                if not flag:
                    pass
                else:
                    x = 2
        """)
        )
        assert _assignments_on_same_execution_path(func_a, func_b, 5, 5) is False

    def test_one_in_if_one_top_level(self):
        func_a = _make_func(
            textwrap.dedent("""\
            def f():
                if cond:
                    x = 1
        """)
        )
        func_b = _make_func("def f():\n    x = 2")
        assert _assignments_on_same_execution_path(func_a, func_b, 3, 2) is True

    def test_different_unrelated_predicates(self):
        func_a = _make_func(
            textwrap.dedent("""\
            def f():
                if alpha:
                    x = 1
        """)
        )
        func_b = _make_func(
            textwrap.dedent("""\
            def f():
                if beta:
                    x = 2
        """)
        )
        assert _assignments_on_same_execution_path(func_a, func_b, 3, 3) is True


# ---------------------------------------------------------------------------
# Class-awareness filter tests
# ---------------------------------------------------------------------------


class TestGetEnclosingClass:
    def test_module_level_function(self):
        func = _make_func_with_parents("def f(): pass")
        assert _get_enclosing_class(func) is None

    def test_method_in_class(self):
        source = textwrap.dedent("""\
            class Foo:
                def f(self):
                    pass
        """)
        func = _make_func_with_parents(source)
        assert _get_enclosing_class(func) == "Foo"

    def test_no_parent_pointers_returns_none(self):
        # Without parent pointers, conservative fallback
        func = _make_func("def f(): pass")
        assert _get_enclosing_class(func) is None


class TestClassContextCompatible:
    def test_same_class_kept(self):
        """Both target and caller in class Foo -- finding should be kept."""
        source = textwrap.dedent("""\
            class Foo:
                def target(self, a, b, c):
                    return a + b + c
                def caller(self):
                    self.target(1)
        """)
        target = _make_loc_with_parents(source, name="target", branch="A")
        caller = _make_loc_with_parents(source, name="caller", branch="B")
        assert _class_context_compatible(target, caller) is True

    def test_different_class_same_file_suppressed(self):
        """Target in Foo, caller in Bar (no inheritance) -- suppressed."""
        source_target = textwrap.dedent("""\
            class Foo:
                def process(self, x, y, z):
                    return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            class Bar:
                def run(self):
                    self.process(1)
        """)
        target = _make_loc_with_parents(
            source_target, name="process", filepath="/repo/mod.py", branch="A"
        )
        caller = _make_loc_with_parents(
            source_caller, name="run", filepath="/repo/mod.py", branch="B"
        )
        assert _class_context_compatible(target, caller) is False

    def test_both_module_level_kept(self):
        """Both are bare functions at module level -- finding should be kept."""
        source_target = textwrap.dedent("""\
            def process(x, y, z):
                return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            def run():
                process(1)
        """)
        target = _make_loc_with_parents(source_target, name="process", branch="A")
        caller = _make_loc_with_parents(source_caller, name="run", branch="B")
        assert _class_context_compatible(target, caller) is True

    def test_inheritance_kept(self):
        """Target in Base, caller in Child(Base) -- finding should be kept."""
        source_target = textwrap.dedent("""\
            class Base:
                def process(self, x, y, z):
                    return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            class Child(Base):
                def run(self):
                    self.process(1)
        """)
        target = _make_loc_with_parents(
            source_target, name="process", filepath="/repo/mod.py", branch="A"
        )
        caller = _make_loc_with_parents(
            source_caller, name="run", filepath="/repo/mod.py", branch="B"
        )
        assert _class_context_compatible(target, caller) is True

    def test_module_level_target_vs_class_caller_suppressed(self):
        """Target is def run() at module level, caller is in a class -- suppressed."""
        source_target = textwrap.dedent("""\
            def run(x, y, z):
                return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            class Worker:
                def execute(self):
                    self.run()
        """)
        target = _make_loc_with_parents(
            source_target, name="run", filepath="/repo/mod.py", branch="A"
        )
        caller = _make_loc_with_parents(
            source_caller, name="execute", filepath="/repo/mod.py", branch="B"
        )
        assert _class_context_compatible(target, caller) is False

    def test_no_parent_pointers_conservative_keep(self):
        """Without parent pointers, conservative fallback keeps finding."""
        target = _make_loc("def process(x, y, z): pass", name="process", branch="A")
        caller = _make_loc("def run(): process(1)", name="run", branch="B")
        assert _class_context_compatible(target, caller) is True


class TestClassFilterEndToEnd:
    def test_same_class_cross_call_kept(self):
        """Same class: cross-call signature mismatch is detected."""
        source = textwrap.dedent("""\
            class Foo:
                def target(self, a, b, c):
                    return a + b + c
                def caller(self):
                    self.target(1)
        """)
        seed_a = _make_loc_with_parents(source, name="target", filepath="/repo/mod.py", branch="A")
        seed_b = _make_loc_with_parents(source, name="caller", filepath="/repo/mod.py", branch="B")
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) >= 1

    def test_different_class_cross_call_suppressed(self):
        """Different unrelated classes: cross-call mismatch suppressed."""
        source_target = textwrap.dedent("""\
            class Foo:
                def process(self, x, y, z):
                    return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            class Bar:
                def run(self):
                    self.process(1)
        """)
        seed_a = _make_loc_with_parents(
            source_target, name="process", filepath="/repo/mod.py", branch="A"
        )
        seed_b = _make_loc_with_parents(
            source_caller, name="run", filepath="/repo/mod.py", branch="B"
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) == 0

    def test_both_module_level_cross_call_kept(self):
        """Both module-level: cross-call mismatch is detected."""
        source_target = textwrap.dedent("""\
            def process(x, y, z):
                return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            def run():
                process(1)
        """)
        seed_a = _make_loc_with_parents(
            source_target, name="process", filepath="/repo/mod.py", branch="A"
        )
        seed_b = _make_loc_with_parents(
            source_caller, name="run", filepath="/repo/mod.py", branch="B"
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) >= 1

    def test_inheritance_cross_call_kept(self):
        """Inheritance: Child(Base) caller, Base target -- kept."""
        source_target = textwrap.dedent("""\
            class Base:
                def process(self, x, y, z):
                    return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            class Child(Base):
                def run(self):
                    self.process(1)
        """)
        seed_a = _make_loc_with_parents(
            source_target, name="process", filepath="/repo/mod.py", branch="A"
        )
        seed_b = _make_loc_with_parents(
            source_caller, name="run", filepath="/repo/mod.py", branch="B"
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) >= 1

    def test_module_level_target_class_caller_suppressed(self):
        """Module-level target, class caller -- suppressed (name collision)."""
        source_target = textwrap.dedent("""\
            def run(x, y, z):
                return x + y + z
        """)
        source_caller = textwrap.dedent("""\
            class Worker:
                def execute(self):
                    self.run()
        """)
        seed_a = _make_loc_with_parents(
            source_target, name="run", filepath="/repo/mod.py", branch="A"
        )
        seed_b = _make_loc_with_parents(
            source_caller, name="execute", filepath="/repo/mod.py", branch="B"
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Keyword args satisfying positional params (Trigger 1 fix)
# ---------------------------------------------------------------------------


class TestCallSignatureMismatchKeywordArgs:
    """Tests for _call_signature_mismatch handling keyword args that satisfy positional params."""

    def _make_target_info(
        self,
        positional: list[str],
        required_count: int,
        total_positional: int | None = None,
        has_vararg: bool = False,
        has_kwarg: bool = False,
        kwonly: list[str] | None = None,
    ) -> dict:
        return {
            "positional": positional,
            "required_count": required_count,
            "total_positional": total_positional
            if total_positional is not None
            else len(positional),
            "has_vararg": has_vararg,
            "has_kwarg": has_kwarg,
            "kwonly": kwonly or [],
        }

    def test_all_keyword_args_satisfy_positional_no_mismatch(self):
        """All required params satisfied by keyword args -- no mismatch."""
        # def process(self, data, mode) -> required=2, positional=["data", "mode"]
        # call: self.process(data=x, mode="fast") -> n_args=0, kw_names={"data", "mode"}
        info = self._make_target_info(positional=["data", "mode"], required_count=2)
        assert _call_signature_mismatch(0, {"data", "mode"}, info) is False

    def test_partial_keyword_still_fires(self):
        """Some params satisfied by keyword but still too few total."""
        # def process(self, data, mode, format) -> required=3
        # call: self.process(x, mode="fast") -> n_args=1, kw_names={"mode"}
        # satisfied = 1 + 1 = 2 < 3
        info = self._make_target_info(positional=["data", "mode", "format"], required_count=3)
        assert _call_signature_mismatch(1, {"mode"}, info) is True

    def test_all_positional_still_fires_when_too_few(self):
        """No keyword args -- original behavior preserved."""
        # def process(self, data, mode) -> required=2
        # call: self.process(x) -> n_args=1, kw_names={}
        info = self._make_target_info(positional=["data", "mode"], required_count=2)
        assert _call_signature_mismatch(1, set(), info) is True

    def test_mixed_positional_and_keyword_enough_total(self):
        """Positional + keyword together meet the requirement."""
        # def process(self, data, mode, format="json") -> required=2
        # call: self.process(x, mode="fast") -> n_args=1, kw_names={"mode"}
        # satisfied = 1 + 1 = 2 >= 2
        info = self._make_target_info(
            positional=["data", "mode", "format"], required_count=2, total_positional=3
        )
        assert _call_signature_mismatch(1, {"mode"}, info) is False

    def test_keyword_for_nonexistent_param_fires_trigger3(self):
        """Keyword arg not in param list fires Trigger 3 (unknown keyword)."""
        # def process(self, data) -> required=1, no **kwargs
        # call: self.process(x, timeout=30) -> n_args=1, kw_names={"timeout"}
        # Trigger 1: satisfied = 1 + 0 = 1 >= 1 -> no fire
        # Trigger 3: "timeout" not in params -> fires
        info = self._make_target_info(positional=["data"], required_count=1)
        assert _call_signature_mismatch(1, {"timeout"}, info) is True

    def test_keyword_for_kwonly_param_not_counted_as_positional(self):
        """Keyword-only params should not be counted as satisfying positional."""
        # def process(self, data, *, mode) -> required=1, positional=["data"], kwonly=["mode"]
        # call: self.process(mode="fast") -> n_args=0, kw_names={"mode"}
        # "mode" is kwonly, not in positional -> kw_satisfying_positional = 0
        # satisfied = 0 + 0 = 0 < 1 -> fires
        info = self._make_target_info(positional=["data"], required_count=1, kwonly=["mode"])
        assert _call_signature_mismatch(0, {"mode"}, info) is True

    def test_cross_call_keyword_satisfies_positional_integration(self):
        """Integration: cross-call with keyword args satisfying positional params."""
        src_a_def = textwrap.dedent("""\
            def compute(self, x, y):
                return x + y
        """)
        src_b_caller = textwrap.dedent("""\
            def run(self):
                result = self.compute(x=1, y=2)
                return result
        """)
        seed_a = _make_loc(
            src_a_def,
            name="compute",
            filepath="/repo/mod.py",
            branch="A",
        )
        seed_b = _make_loc(
            src_b_caller,
            name="run",
            filepath="/repo/other.py",
            branch="B",
        )
        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = _check_cross_call_signature_mismatch(diff)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Backward-compatible signature growth tests (Exp 11)
# ---------------------------------------------------------------------------


class TestBackwardCompatibleGrowth:
    """Tests for _is_backward_compatible_growth and its integration into
    _check_signature_mismatch_same_func."""

    def _info(self, positional, required_count, total_positional=None, **kw):
        return {
            "positional": positional,
            "required_count": required_count,
            "total_positional": total_positional
            if total_positional is not None
            else len(positional),
            "has_vararg": kw.get("has_vararg", False),
            "has_kwarg": kw.get("has_kwarg", False),
            "kwonly": kw.get("kwonly", []),
        }

    # -- Unit tests for _is_backward_compatible_growth --

    def test_growth_with_defaults_suppressed(self):
        """def foo(a, b) vs def foo(a, b, c=None) -> suppressed."""
        info_a = self._info(["a", "b"], 2)
        info_b = self._info(["a", "b", "c"], 2, 3)
        assert _is_backward_compatible_growth(["a", "b"], ["a", "b", "c"], info_a, info_b) is True

    def test_growth_without_defaults_not_suppressed(self):
        """def foo(a, b) vs def foo(a, b, c) where c has no default -> NOT suppressed."""
        info_a = self._info(["a", "b"], 2)
        info_b = self._info(["a", "b", "c"], 3, 3)
        assert _is_backward_compatible_growth(["a", "b"], ["a", "b", "c"], info_a, info_b) is False

    def test_renamed_param_not_suppressed(self):
        """def foo(a, b) vs def foo(a, x) -> NOT suppressed (not a prefix)."""
        info_a = self._info(["a", "b"], 2)
        info_b = self._info(["a", "x"], 2)
        assert _is_backward_compatible_growth(["a", "b"], ["a", "x"], info_a, info_b) is False

    def test_multiple_defaults_suppressed(self):
        """def foo(a) vs def foo(a, b=1, c=2) -> suppressed."""
        info_a = self._info(["a"], 1)
        info_b = self._info(["a", "b", "c"], 1, 3)
        assert _is_backward_compatible_growth(["a"], ["a", "b", "c"], info_a, info_b) is True

    def test_different_order_not_suppressed(self):
        """def foo(a, b) vs def foo(b, a) -> NOT suppressed."""
        info_a = self._info(["a", "b"], 2)
        info_b = self._info(["b", "a"], 2)
        assert _is_backward_compatible_growth(["a", "b"], ["b", "a"], info_a, info_b) is False

    def test_prefix_plus_rename_not_suppressed(self):
        """def foo(a, b) vs def foo(a, c, d=None) -> NOT suppressed (b->c rename)."""
        info_a = self._info(["a", "b"], 2)
        info_b = self._info(["a", "c", "d"], 2, 3)
        assert _is_backward_compatible_growth(["a", "b"], ["a", "c", "d"], info_a, info_b) is False

    # -- Integration tests: _check_signature_mismatch_same_func --

    def test_growth_with_defaults_suppressed_integration(self):
        """def foo(a, b) vs def foo(a, b, c=None) -> 0 findings."""
        loc_a = _make_loc("def f(a, b): pass", branch="A")
        loc_b = _make_loc("def f(a, b, c=None): pass", branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is None

    def test_growth_without_defaults_not_suppressed_integration(self):
        """def foo(a, b) vs def foo(a, b, c) -> finding produced."""
        loc_a = _make_loc("def f(a, b): pass", branch="A")
        loc_b = _make_loc("def f(a, b, c): pass", branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is not None

    def test_renamed_param_not_suppressed_integration(self):
        """def foo(a, b) vs def foo(a, x) -> finding produced (when used in body)."""
        src_a = textwrap.dedent("""\
            def f(a, b):
                return b
        """)
        src_b = textwrap.dedent("""\
            def f(a, x):
                return x
        """)
        loc_a = _make_loc(src_a, branch="A")
        loc_b = _make_loc(src_b, branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is not None

    def test_multiple_defaults_suppressed_integration(self):
        """def foo(a) vs def foo(a, b=1, c=2) -> suppressed."""
        loc_a = _make_loc("def f(a): pass", branch="A")
        loc_b = _make_loc("def f(a, b=1, c=2): pass", branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is None

    def test_different_order_not_suppressed_integration(self):
        """def foo(a, b) vs def foo(b, a) -> finding produced (when used in body)."""
        src_a = textwrap.dedent("""\
            def f(a, b):
                return a + b
        """)
        src_b = textwrap.dedent("""\
            def f(b, a):
                return b + a
        """)
        loc_a = _make_loc(src_a, branch="A")
        loc_b = _make_loc(src_b, branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is not None

    def test_prefix_plus_rename_not_suppressed_integration(self):
        """def foo(a, b) vs def foo(a, c, d=None) -> NOT suppressed (b->c)."""
        src_a = textwrap.dedent("""\
            def f(a, b):
                return b
        """)
        src_b = textwrap.dedent("""\
            def f(a, c, d=None):
                return c
        """)
        loc_a = _make_loc(src_a, branch="A")
        loc_b = _make_loc(src_b, branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is not None

    def test_existing_defaults_extended_suppressed(self):
        """def foo(a, b, c=None) vs def foo(a, b, c=None, d=0, e='') -> suppressed."""
        loc_a = _make_loc("def f(a, b, c=None): pass", branch="A")
        loc_b = _make_loc("def f(a, b, c=None, d=0, e=''): pass", branch="B")
        result = _check_signature_mismatch_same_func(loc_a, loc_b)
        assert result is None


# ---------------------------------------------------------------------------
# Dict-literal extraction
# ---------------------------------------------------------------------------


class TestDictLiteralExtraction:
    """Tests for dict literal key-value entries detected as assignments."""

    def test_dict_return_different_values_same_key(self):
        """Both branches return a dict with same key but different values -> OA."""
        src_a = textwrap.dedent("""\
            def config():
                return {"sql": read_sql}
        """)
        src_b = textwrap.dedent("""\
            def config():
                return {"sql": db_sql}
        """)
        loc_a = _make_loc(src_a, name="config", branch="A")
        loc_b = _make_loc(src_b, name="config", branch="B")
        paths = _check_override_assignments(loc_a, loc_b)
        assert len(paths) == 1
        assert paths[0].evidence["target"] == "'sql'"

    def test_dict_one_adds_other_changes(self):
        """Branch A changes value, Branch B adds key -> OA on shared key."""
        src_a = textwrap.dedent("""\
            def config():
                return {"sql": frappe_db_sql}
        """)
        src_b = textwrap.dedent("""\
            def config():
                return {"sql": read_sql, "commit": db_commit}
        """)
        loc_a = _make_loc(src_a, name="config", branch="A")
        loc_b = _make_loc(src_b, name="config", branch="B")
        paths = _check_override_assignments(loc_a, loc_b)
        # Should detect OA on "sql" key (different values)
        assert len(paths) == 1
        assert paths[0].evidence["target"] == "'sql'"

    def test_dict_same_value_no_oa(self):
        """Both branches have same key-value -> no OA."""
        src_a = textwrap.dedent("""\
            def config():
                return {"sql": read_sql}
        """)
        src_b = textwrap.dedent("""\
            def config():
                return {"sql": read_sql}
        """)
        loc_a = _make_loc(src_a, name="config", branch="A")
        loc_b = _make_loc(src_b, name="config", branch="B")
        paths = _check_override_assignments(loc_a, loc_b)
        assert len(paths) == 0

    def test_dict_different_keys_no_oa(self):
        """Branches modify different keys -> no overlap -> 0 findings."""
        src_a = textwrap.dedent("""\
            def config():
                return {"timeout": 30}
        """)
        src_b = textwrap.dedent("""\
            def config():
                return {"retries": 3}
        """)
        loc_a = _make_loc(src_a, name="config", branch="A")
        loc_b = _make_loc(src_b, name="config", branch="B")
        paths = _check_override_assignments(loc_a, loc_b)
        assert len(paths) == 0

    def test_dict_in_variable_assignment(self):
        """Dict literal in variable assignment: config = {...}."""
        src_a = textwrap.dedent("""\
            def setup():
                config = {"sql": read_sql}
                return config
        """)
        src_b = textwrap.dedent("""\
            def setup():
                config = {"sql": db_sql}
                return config
        """)
        loc_a = _make_loc(src_a, name="setup", branch="A")
        loc_b = _make_loc(src_b, name="setup", branch="B")
        paths = _check_override_assignments(loc_a, loc_b)
        # Should detect OA on "sql" dict key AND on "config" variable
        targets = {p.evidence["target"] for p in paths}
        assert "'sql'" in targets

    def test_frappe_inspired_integration(self):
        """Integration: Frappe-inspired get_safe_globals pattern (TP 13721)."""
        src_a = textwrap.dedent("""\
            def get_safe_globals():
                return {
                    "sql": frappe.db.sql,
                    "commit": frappe.db.commit,
                }
        """)
        src_b = textwrap.dedent("""\
            def get_safe_globals():
                return {
                    "sql": read_sql,
                    "commit": frappe.db.commit,
                }
        """)
        loc_a = _make_loc(
            src_a,
            name="get_safe_globals",
            branch="A",
            filepath="/repo/frappe/utils/safe_exec.py",
        )
        loc_b = _make_loc(
            src_b,
            name="get_safe_globals",
            branch="B",
            filepath="/repo/frappe/utils/safe_exec.py",
        )
        paths = _check_override_assignments(loc_a, loc_b)
        targets = {p.evidence["target"] for p in paths}
        assert "'sql'" in targets
        # "commit" has the same value in both -> no OA for it
        assert "'commit'" not in targets

    def test_target_to_string_constant(self):
        """_target_to_string handles ast.Constant (dict keys)."""
        node = ast.Constant(value="sql")
        assert _target_to_string(node) == "'sql'"
        node_int = ast.Constant(value=42)
        assert _target_to_string(node_int) == "42"

    def test_extract_assignments_in_ranges_dict_entries(self):
        """_extract_assignments_in_ranges picks up dict key-value pairs."""
        src = textwrap.dedent("""\
            def f():
                return {"sql": read_sql, "commit": db_commit}
        """)
        func = _make_func(src)
        result = _extract_assignments_in_ranges(func, [(1, 3)])
        assert "'sql'" in result
        assert "'commit'" in result
