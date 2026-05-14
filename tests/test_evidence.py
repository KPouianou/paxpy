"""Tests for structured evidence diagnostics."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from paxpy.direct_detector import detect_direct_conflicts
from paxpy.main import _build_sdg_evidence
from paxpy.reporter import format_cli, format_json
from paxpy.types import (
    SDG,
    Compatibility,
    CompatibilityResult,
    ConflictReport,
    ConflictType,
    DiffResult,
    FunctionLocation,
    InterferencePath,
    Node,
    make_node_id,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_func(name: str, params: list[str], body_src: str = "pass") -> ast.FunctionDef:
    """Build a FunctionDef AST node with the given params and body."""
    param_str = ", ".join(params)
    src = f"def {name}({param_str}):\n"
    for line in body_src.strip().splitlines():
        src += f"    {line}\n"
    tree = ast.parse(src)
    return tree.body[0]  # type: ignore[return-value]


def _make_report(
    evidence: dict | None = None,
    conflict_type: ConflictType = ConflictType.DATA_FLOW,
) -> ConflictReport:
    ip = InterferencePath(
        direction="A_to_B",
        conflict_type=conflict_type,
        tier=1,
        source_node="/repo/a.py:10:0",
        sink_node="/repo/b.py:20:0",
        path_nodes=["/repo/a.py:10:0", "/repo/b.py:20:0"],
    )
    cr = CompatibilityResult(
        compatibility=Compatibility.INCOMPATIBLE,
        explanation="test",
    )
    return ConflictReport(
        interference=ip,
        compatibility=cr,
        source_function="foo",
        sink_function="bar",
        source_file="/repo/a.py",
        sink_file="/repo/b.py",
        evidence=evidence,
    )


# ---------------------------------------------------------------------------
# Test 1: Direct detector evidence -- signature mismatch
# ---------------------------------------------------------------------------


def test_signature_mismatch_evidence():
    """Signature mismatch produces evidence with params_a, params_b, pattern."""
    fp = Path("/fake/mod.py")
    func_a = _make_func("process", ["x", "y"], "return x + y")
    func_b = _make_func("process", ["x", "y", "z"], "return x + y + z")

    diff = DiffResult(
        seeds_a=[
            FunctionLocation(
                name="process",
                filepath=fp,
                lineno=1,
                end_lineno=2,
                ast_node=func_a,
                branch="A",
                modified_ranges=[(1, 2)],
            )
        ],
        seeds_b=[
            FunctionLocation(
                name="process",
                filepath=fp,
                lineno=1,
                end_lineno=2,
                ast_node=func_b,
                branch="B",
                modified_ranges=[(1, 2)],
            )
        ],
    )

    paths = detect_direct_conflicts(diff)
    sig_paths = [
        p for p in paths if p.evidence and p.evidence.get("pattern") == "signature_mismatch"
    ]
    assert len(sig_paths) >= 1
    ev = sig_paths[0].evidence
    assert ev is not None
    assert ev["params_a"] == ["x", "y"]
    assert ev["params_b"] == ["x", "y", "z"]
    assert ev["pattern"] == "signature_mismatch"


# ---------------------------------------------------------------------------
# Test 2: Direct detector evidence -- override assignment
# ---------------------------------------------------------------------------


def test_override_assignment_evidence():
    """Override assignment produces evidence with target and value summaries."""
    fp = Path("/fake/mod.py")
    func_a = _make_func("setup", ["self"], "self.timeout = 10")
    func_b = _make_func("setup", ["self"], "self.timeout = 30")

    diff = DiffResult(
        seeds_a=[
            FunctionLocation(
                name="setup",
                filepath=fp,
                lineno=1,
                end_lineno=2,
                ast_node=func_a,
                branch="A",
                modified_ranges=[(1, 2)],
            )
        ],
        seeds_b=[
            FunctionLocation(
                name="setup",
                filepath=fp,
                lineno=1,
                end_lineno=2,
                ast_node=func_b,
                branch="B",
                modified_ranges=[(1, 2)],
            )
        ],
    )

    paths = detect_direct_conflicts(diff)
    oa_paths = [
        p for p in paths if p.evidence and p.evidence.get("pattern") == "override_assignment"
    ]
    assert len(oa_paths) >= 1
    ev = oa_paths[0].evidence
    assert ev is not None
    assert ev["target"] == "self.timeout"
    assert "value_a_summary" in ev
    assert "value_b_summary" in ev
    assert ev["pattern"] == "override_assignment"


# ---------------------------------------------------------------------------
# Test 3: JSON output includes evidence
# ---------------------------------------------------------------------------


def test_json_includes_evidence():
    """format_json includes evidence key when present."""
    evidence = {"pattern": "signature_mismatch", "params_a": ["x"], "params_b": ["x", "y"]}
    report = _make_report(evidence=evidence)
    result = format_json([report])
    conflict = result["conflicts"][0]
    assert "evidence" in conflict
    assert conflict["evidence"]["pattern"] == "signature_mismatch"
    assert conflict["evidence"]["params_a"] == ["x"]
    # Verify JSON serializable
    json.dumps(result)


# ---------------------------------------------------------------------------
# Test 4: JSON output omits evidence when None
# ---------------------------------------------------------------------------


def test_json_omits_evidence_when_none():
    """format_json omits evidence key when evidence is None."""
    report = _make_report(evidence=None)
    result = format_json([report])
    conflict = result["conflicts"][0]
    assert "evidence" not in conflict


# ---------------------------------------------------------------------------
# Test 5: CLI output unchanged by evidence
# ---------------------------------------------------------------------------


def test_cli_output_unchanged_by_evidence():
    """format_cli does not include evidence-specific content."""
    evidence = {
        "pattern": "override_assignment",
        "target": "self.timeout",
        "value_a_summary": "Constant(value=10)",
        "value_b_summary": "Constant(value=30)",
        "line_a": 2,
        "line_b": 2,
    }
    report = _make_report(evidence=evidence)
    output = format_cli([report])
    assert "params_a" not in output
    assert "value_a_summary" not in output
    assert "value_b_summary" not in output
    assert "override_assignment" not in output  # pattern key not in CLI


# ---------------------------------------------------------------------------
# Test 6: SDG evidence builder
# ---------------------------------------------------------------------------


def test_build_sdg_evidence():
    """_build_sdg_evidence populates path_labels, source/sink modifications."""
    fp_src = Path("/repo/src.py")
    fp_snk = Path("/repo/snk.py")

    src_nid = make_node_id(fp_src, 10, 0)
    mid_nid = make_node_id(fp_src, 12, 0)
    snk_nid = make_node_id(fp_snk, 20, 0)

    sdg = SDG(
        nodes={
            src_nid: Node(
                id=src_nid,
                filepath=fp_src,
                lineno=10,
                col_offset=0,
                branch="A",
                label="x = compute()",
                enclosing_function="caller",
                is_direct_modification=True,
            ),
            mid_nid: Node(
                id=mid_nid,
                filepath=fp_src,
                lineno=12,
                col_offset=0,
                branch=None,
                label="result = process(x)",
                enclosing_function="caller",
                is_direct_modification=False,
            ),
            snk_nid: Node(
                id=snk_nid,
                filepath=fp_snk,
                lineno=20,
                col_offset=0,
                branch="B",
                label="return val + 1",
                enclosing_function="callee",
                is_direct_modification=True,
            ),
        },
        data_edges={src_nid: {mid_nid}},
        call_edges={mid_nid: {snk_nid}},
    )

    path = InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
        path_nodes=[src_nid, mid_nid, snk_nid],
        source_node=src_nid,
        sink_node=snk_nid,
    )

    ev = _build_sdg_evidence(path, sdg)

    # path_labels
    assert len(ev["path_labels"]) == 3
    assert "x = compute()" in ev["path_labels"][0]
    assert "caller" in ev["path_labels"][0]
    assert "line 10" in ev["path_labels"][0]

    # source_modifications
    assert ev["source_modifications"]["function"] == "caller"
    assert ev["source_modifications"]["branch"] == "A"
    assert 10 in ev["source_modifications"]["modified_lines"]

    # sink_modifications
    assert ev["sink_modifications"]["function"] == "callee"
    assert ev["sink_modifications"]["branch"] == "B"
    assert 20 in ev["sink_modifications"]["modified_lines"]

    # call_interface
    assert "call_interface" in ev
    assert ev["call_interface"]["call_site"] == "result = process(x)"
    assert ev["call_interface"]["callee_entry"] == "return val + 1"
