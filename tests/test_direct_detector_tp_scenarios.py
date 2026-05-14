"""Tests mirroring the three confirmed TPs that paxpy's SDG detectors missed.

These tests reconstruct simplified versions of the actual conflict patterns
from the evaluation corpus to verify the direct detector catches them.

C3: CodeForPhilly/chime (ed5f1c69) -- OVERRIDE_ASSIGNMENT
C5: gem/oq-engine (4acb38de) -- signature_body_mismatch (arg count)
C6: gem/oq-engine (584c07ac) -- signature_body_mismatch (param name)
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from paxpy.direct_detector import detect_direct_conflicts
from paxpy.types import ConflictType, DiffResult, FunctionLocation


def _make_loc(
    source: str,
    name: str,
    filepath: str,
    branch: str,
    modified_ranges: list[tuple[int, int]] | None = None,
) -> FunctionLocation:
    tree = ast.parse(textwrap.dedent(source))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            if modified_ranges is None:
                modified_ranges = [(node.lineno, node.end_lineno or node.lineno)]
            return FunctionLocation(
                name=name,
                filepath=Path(filepath),
                lineno=node.lineno,
                end_lineno=node.end_lineno,
                ast_node=node,
                branch=branch,
                modified_ranges=modified_ranges,
            )
    raise ValueError(f"Function {name!r} not found")


class TestChimeOverrideAssignment:
    """C3: CodeForPhilly/chime (ed5f1c69) -- OVERRIDE_ASSIGNMENT.

    Both branches modify the sidebar configuration function. Branch A uses
    'value' key for show_tables switch. Branch B uses 'on' key. The accessor
    mapping expects 'on', so A's 'value' key won't be read correctly.
    """

    def test_switch_key_override_detected(self):
        # Branch A: uses 'value' convention for show_tables
        src_a = textwrap.dedent("""\
            def build_sidebar():
                show_tables = st.checkbox("Show tables")
                switches = {
                    'show_tables': {'value': show_tables},
                }
                return switches
        """)
        # Branch B: uses 'on' convention for new switches including show_tables
        src_b = textwrap.dedent("""\
            def build_sidebar():
                show_tables = st.checkbox("Show tables")
                switches = {
                    'show_tables': {'on': show_tables},
                    'spread_parameters': {'on': True},
                }
                return switches
        """)

        seed_a = _make_loc(
            src_a,
            "build_sidebar",
            "/repo/sidebar.py",
            "A",
            modified_ranges=[(3, 5)],  # switches = { ... } assignment block
        )
        seed_b = _make_loc(
            src_b,
            "build_sidebar",
            "/repo/sidebar.py",
            "B",
            modified_ranges=[(3, 6)],
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)

        oa_results = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa_results) >= 1, (
            f"Expected OA detection for show_tables key conflict, got: {results}"
        )


class TestOqEngineArgCountMismatch:
    """C5: gem/oq-engine (4acb38de) -- signature_body_mismatch.

    Left changed get_planin from (self, mags, nplanes) to (self, magd, npd, hdd).
    Right added callers using the old 2-arg signature:
        self.get_planin([mag], nplanes)
    Merge has new 3-arg signature but old 2-arg callers → TypeError.
    """

    def test_arg_count_mismatch_detected(self):
        # Branch A: changed get_planin to require 3 args (excl. self)
        src_a_def = textwrap.dedent("""\
            def get_planin(self, magd, npd, hdd):
                planin = []
                for mag, np_dist, h_dist in zip(magd, npd, hdd):
                    planin.append(self._compute(mag, np_dist, h_dist))
                return planin
        """)

        # Branch B: callers still pass 2 args
        src_b_caller1 = textwrap.dedent("""\
            def _get_max_rupture_projection_radius(self, mag, nplanes):
                planin = self.get_planin([mag], nplanes)
                return max(p.width for p in planin)
        """)
        src_b_caller2 = textwrap.dedent("""\
            def get_radius(self, rup, nplanes):
                result = self.get_planin([rup.mag], [NodalPlane()])
                return result[0].width
        """)

        seed_a = _make_loc(
            src_a_def,
            "get_planin",
            "/repo/point.py",
            "A",
        )
        seed_b1 = _make_loc(
            src_b_caller1,
            "_get_max_rupture_projection_radius",
            "/repo/point.py",
            "B",
        )
        seed_b2 = _make_loc(
            src_b_caller2,
            "get_radius",
            "/repo/point.py",
            "B",
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b1, seed_b2])
        results = detect_direct_conflicts(diff)

        assert len(results) >= 1, (
            f"Expected cross-call mismatch for get_planin 3-arg vs 2-arg callers, got: {results}"
        )


class TestOqEngineParamNameMismatch:
    """C6: gem/oq-engine (584c07ac) -- signature_body_mismatch.

    Left changed set_parameters(self, rup) to set_parameters(self, ctx)
    and rewrote body to iterate ctx as a recarray.
    Right kept set_parameters(self, rup) and body using rup.surface.mesh.lons.
    Merge has right's body (rup) but caller passes ctx recarray → AttributeError.

    Both branches modified set_parameters, so it appears in both seeds.
    """

    def test_param_name_mismatch_detected(self):
        # Branch A: ctx-based interface
        src_a = textwrap.dedent("""\
            def set_parameters(self, ctx):
                for rec in ctx:
                    pt = shapely.geometry.Point(rec.clon, rec.clat)
                    if self.polygon.contains(pt):
                        self.in_cbd = True
        """)

        # Branch B: rup-based interface
        src_b = textwrap.dedent("""\
            def set_parameters(self, rup):
                lons = rup.surface.mesh.lons.flatten()
                lats = rup.surface.mesh.lats.flatten()
                for lon, lat in zip(lons, lats):
                    pt = shapely.geometry.Point(lon, lat)
                    if self.polygon.contains(pt):
                        self.in_cbd = True
        """)

        seed_a = _make_loc(
            src_a,
            "set_parameters",
            "/repo/bradley_2013.py",
            "A",
        )
        seed_b = _make_loc(
            src_b,
            "set_parameters",
            "/repo/bradley_2013.py",
            "B",
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)

        assert len(results) >= 1, (
            f"Expected signature mismatch for set_parameters ctx vs rup, got: {results}"
        )


class TestNoFalsePositiveOnCleanRename:
    """Ensure clean parameter renames (consistent across signature + body + callers)
    don't trigger false positives."""

    def test_consistent_rename_no_flag(self):
        # Both branches have the same signature (sites → vs30s rename applied everywhere)
        src_a = textwrap.dedent("""\
            def get_amplification(self, imt, pga, vs30s):
                f760 = compute(vs30s)
                return f760
        """)
        src_b = textwrap.dedent("""\
            def get_amplification(self, imt, pga, vs30s):
                result = process(vs30s, imt)
                return result
        """)

        seed_a = _make_loc(
            src_a,
            "get_amplification",
            "/repo/gsim.py",
            "A",
        )
        seed_b = _make_loc(
            src_b,
            "get_amplification",
            "/repo/gsim.py",
            "B",
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)

        # Should produce no signature mismatch (same param names)
        sig_results = [r for r in results if r.conflict_type == ConflictType.DATA_FLOW]
        assert len(sig_results) == 0


class TestFrappeSecurityRegression:
    """Frappe-inspired security regression: sandbox setup OA.

    Base has setup_sandbox() assigning self.sql = self.read_sql (safe read-only).
    Branch A changes self.sql to frappe_db_sql (unrestricted write access).
    Branch B keeps self.sql = self.read_sql but adds self.commit = frappe_db_commit.

    Both assign to self.sql with different values, creating an OA conflict.
    In the merged code, one branch's security-sensitive binding silently
    overwrites the other's.
    """

    def test_sandbox_sql_override_detected(self):
        src_a = textwrap.dedent("""\
            def setup_sandbox(self):
                self.sql = frappe_db_sql
        """)
        src_b = textwrap.dedent("""\
            def setup_sandbox(self):
                self.sql = self.read_sql
                self.commit = frappe_db_commit
        """)

        seed_a = _make_loc(
            src_a,
            "setup_sandbox",
            "/repo/safe_exec.py",
            "A",
            modified_ranges=[(2, 2)],
        )
        seed_b = _make_loc(
            src_b,
            "setup_sandbox",
            "/repo/safe_exec.py",
            "B",
            modified_ranges=[(2, 3)],
        )

        diff = DiffResult(seeds_a=[seed_a], seeds_b=[seed_b])
        results = detect_direct_conflicts(diff)

        oa_results = [r for r in results if r.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
        assert len(oa_results) >= 1, (
            f"Expected OA for self.sql divergent assignment, got: {results}"
        )
