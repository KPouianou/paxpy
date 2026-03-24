"""Tests verifying that all types.py dataclasses and enums are well-formed."""

from __future__ import annotations

from pathlib import Path

from paxpy.types import (
    PDG,
    SDG,
    Compatibility,
    CompatibilityResult,
    ConflictReport,
    ConflictType,
    DiffResult,
    FunctionIndex,
    FunctionLocation,
    InterferencePath,
    Node,
    make_node_id,
)

# ---------------------------------------------------------------------------
# make_node_id
# ---------------------------------------------------------------------------


def test_make_node_id_format():
    nid = make_node_id(Path("/repo/src/foo.py"), 42, 8)
    assert nid == "/repo/src/foo.py:42:8"


def test_make_node_id_zero_offsets():
    nid = make_node_id(Path("bar.py"), 1, 0)
    assert nid == "bar.py:1:0"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_conflict_type_members():
    assert ConflictType.DATA_FLOW.value == "DATA_FLOW"
    assert ConflictType.CONFLUENCE.value == "CONFLUENCE"
    assert ConflictType.OVERRIDE_ASSIGNMENT.value == "OVERRIDE_ASSIGNMENT"
    assert ConflictType.CONTROL_DEPENDENCY.value == "CONTROL_DEPENDENCY"
    assert len(ConflictType) == 4


def test_compatibility_members():
    assert Compatibility.INCOMPATIBLE.value == "incompatible"
    assert Compatibility.SUSPICIOUS.value == "suspicious"
    assert Compatibility.COMPATIBLE.value == "compatible"
    assert Compatibility.UNKNOWN.value == "unknown"
    assert len(Compatibility) == 4


# ---------------------------------------------------------------------------
# FunctionLocation
# ---------------------------------------------------------------------------


def test_function_location_defaults():
    loc = FunctionLocation(
        name="my_func",
        filepath=Path("/repo/a.py"),
        lineno=10,
        end_lineno=20,
    )
    assert loc.name == "my_func"
    assert loc.filepath == Path("/repo/a.py")
    assert loc.lineno == 10
    assert loc.end_lineno == 20
    assert loc.ast_node is None
    assert loc.branch is None


def test_function_location_with_branch():
    loc = FunctionLocation(
        name="f",
        filepath=Path("x.py"),
        lineno=1,
        end_lineno=None,
        branch="A",
    )
    assert loc.branch == "A"


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------


def test_node_defaults():
    nid = make_node_id(Path("f.py"), 5, 0)
    node = Node(id=nid, filepath=Path("f.py"), lineno=5, col_offset=0)
    assert node.id == nid
    assert node.ast_node is None
    assert node.branch is None
    assert node.label == ""
    assert node.enclosing_function is None


# ---------------------------------------------------------------------------
# PDG
# ---------------------------------------------------------------------------


def test_pdg_defaults():
    pdg = PDG(function_name="foo", filepath=Path("foo.py"))
    assert pdg.function_name == "foo"
    assert pdg.nodes == []
    assert pdg.data_edges == {}
    assert pdg.control_edges == {}


def test_pdg_nodes_mutable():
    pdg1 = PDG(function_name="a", filepath=Path("a.py"))
    pdg2 = PDG(function_name="b", filepath=Path("b.py"))
    nid = make_node_id(Path("a.py"), 1, 0)
    node = Node(id=nid, filepath=Path("a.py"), lineno=1, col_offset=0)
    pdg1.nodes.append(node)
    # Verify default_factory gives independent lists
    assert pdg2.nodes == []


# ---------------------------------------------------------------------------
# SDG
# ---------------------------------------------------------------------------


def test_sdg_defaults():
    sdg = SDG()
    assert sdg.nodes == {}
    assert sdg.data_edges == {}
    assert sdg.control_edges == {}
    assert sdg.call_edges == {}
    assert sdg.reverse_data_edges == {}
    assert sdg.reverse_control_edges == {}
    assert sdg.reverse_call_edges == {}
    assert sdg.seeds_a == set()
    assert sdg.seeds_b == set()


def test_sdg_can_add_nodes_and_edges():
    sdg = SDG()
    nid_a = make_node_id(Path("a.py"), 1, 0)
    nid_b = make_node_id(Path("b.py"), 2, 4)
    node_a = Node(id=nid_a, filepath=Path("a.py"), lineno=1, col_offset=0, branch="A")
    node_b = Node(id=nid_b, filepath=Path("b.py"), lineno=2, col_offset=4, branch="B")

    sdg.nodes[nid_a] = node_a
    sdg.nodes[nid_b] = node_b
    sdg.data_edges[nid_a] = {nid_b}
    sdg.reverse_data_edges[nid_b] = {nid_a}
    sdg.seeds_a.add(nid_a)
    sdg.seeds_b.add(nid_b)

    assert len(sdg.nodes) == 2
    assert nid_b in sdg.data_edges[nid_a]
    assert nid_a in sdg.reverse_data_edges[nid_b]
    assert nid_a in sdg.seeds_a
    assert nid_b in sdg.seeds_b


# ---------------------------------------------------------------------------
# DiffResult
# ---------------------------------------------------------------------------


def test_diff_result_defaults():
    dr = DiffResult()
    assert dr.seeds_a == []
    assert dr.seeds_b == []


def test_diff_result_seeds_independent():
    dr1 = DiffResult()
    dr2 = DiffResult()
    dr1.seeds_a.append(FunctionLocation(name="x", filepath=Path("x.py"), lineno=1, end_lineno=5))
    assert dr2.seeds_a == []


# ---------------------------------------------------------------------------
# FunctionIndex
# ---------------------------------------------------------------------------


def test_function_index_defaults():
    fi = FunctionIndex()
    assert fi.index == {}
    assert fi.parsed_asts == {}


def test_function_index_lookup_missing():
    fi = FunctionIndex()
    result = fi.lookup("nonexistent")
    assert result == []


def test_function_index_lookup_found():
    fi = FunctionIndex()
    loc = FunctionLocation(name="foo", filepath=Path("a.py"), lineno=1, end_lineno=10)
    fi.index["foo"] = [loc]
    assert fi.lookup("foo") == [loc]


# ---------------------------------------------------------------------------
# InterferencePath
# ---------------------------------------------------------------------------


def test_interference_path_defaults():
    ip = InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
    )
    assert ip.path_nodes == []
    assert ip.source_node == ""
    assert ip.sink_node == ""


# ---------------------------------------------------------------------------
# CompatibilityResult
# ---------------------------------------------------------------------------


def test_compatibility_result():
    cr = CompatibilityResult(compatibility=Compatibility.UNKNOWN)
    assert cr.explanation == ""


def test_compatibility_result_with_explanation():
    cr = CompatibilityResult(
        compatibility=Compatibility.INCOMPATIBLE,
        explanation="Return type mismatch: int vs str",
    )
    assert "int" in cr.explanation


# ---------------------------------------------------------------------------
# ConflictReport
# ---------------------------------------------------------------------------


def test_conflict_report_defaults():
    ip = InterferencePath(
        direction="B_to_A",
        conflict_type=ConflictType.CONTROL_DEPENDENCY,
        tier=2,
    )
    cr = CompatibilityResult(compatibility=Compatibility.SUSPICIOUS)
    report = ConflictReport(interference=ip, compatibility=cr)
    assert report.source_function == ""
    assert report.sink_function == ""
    assert report.source_file == ""
    assert report.sink_file == ""
