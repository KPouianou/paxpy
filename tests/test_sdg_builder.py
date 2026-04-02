"""Tests for sdg_builder.py."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

from paxpy.indexer import build_index
from paxpy.sdg_builder import _add_call_edge, _build_reverse_edges, _merge_pdg_into_sdg, build_sdg
from paxpy.types import PDG, SDG, DiffResult, FunctionLocation, Node, make_node_id

FILEPATH_A = Path("/repo/a.py")
FILEPATH_B = Path("/repo/b.py")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_loc(name: str, filepath: Path, lineno: int, branch: str | None = None) -> FunctionLocation:
    src = f"def {name}():\n    pass\n"
    tree = ast.parse(src)
    func = tree.body[0]
    return FunctionLocation(
        name=name,
        filepath=filepath,
        lineno=lineno,
        end_lineno=lineno + 1,
        ast_node=func,
        branch=branch,  # type: ignore[arg-type]
    )


def write_py(tmp_path: Path, name: str, source: str) -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(source), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _add_call_edge
# ---------------------------------------------------------------------------


def test_add_call_edge_forward():
    sdg = SDG()
    _add_call_edge(sdg, "a:1:0", "b:2:0")
    assert "b:2:0" in sdg.call_edges.get("a:1:0", set())


def test_add_call_edge_reverse():
    sdg = SDG()
    _add_call_edge(sdg, "a:1:0", "b:2:0")
    assert "a:1:0" in sdg.reverse_call_edges.get("b:2:0", set())


def test_add_call_edge_multiple():
    sdg = SDG()
    _add_call_edge(sdg, "caller:1:0", "callee1:5:0")
    _add_call_edge(sdg, "caller:1:0", "callee2:10:0")
    assert len(sdg.call_edges["caller:1:0"]) == 2


# ---------------------------------------------------------------------------
# _build_reverse_edges
# ---------------------------------------------------------------------------


def test_build_reverse_edges_data():
    sdg = SDG()
    sdg.data_edges["a:1:0"] = {"b:2:0", "c:3:0"}
    _build_reverse_edges(sdg)
    assert "a:1:0" in sdg.reverse_data_edges.get("b:2:0", set())
    assert "a:1:0" in sdg.reverse_data_edges.get("c:3:0", set())


def test_build_reverse_edges_control():
    sdg = SDG()
    sdg.control_edges["pred:1:0"] = {"body:2:0"}
    _build_reverse_edges(sdg)
    assert "pred:1:0" in sdg.reverse_control_edges.get("body:2:0", set())


def test_build_reverse_edges_call():
    sdg = SDG()
    sdg.call_edges["caller:1:0"] = {"callee:5:0"}
    _build_reverse_edges(sdg)
    assert "caller:1:0" in sdg.reverse_call_edges.get("callee:5:0", set())


def test_build_reverse_edges_rebuilds_from_scratch():
    sdg = SDG()
    sdg.data_edges["a:1:0"] = {"b:2:0"}
    _build_reverse_edges(sdg)
    # Clear forward and rebuild — reverse should be empty
    sdg.data_edges = {}
    _build_reverse_edges(sdg)
    assert sdg.reverse_data_edges == {}


# ---------------------------------------------------------------------------
# _merge_pdg_into_sdg
# ---------------------------------------------------------------------------


def test_merge_pdg_adds_nodes():
    sdg = SDG()
    pdg = PDG(function_name="foo", filepath=FILEPATH_A)
    nid = make_node_id(FILEPATH_A, 1, 0)
    pdg.nodes.append(Node(id=nid, filepath=FILEPATH_A, lineno=1, col_offset=0))
    _merge_pdg_into_sdg(sdg, pdg)
    assert nid in sdg.nodes


def test_merge_pdg_adds_data_edges():
    sdg = SDG()
    pdg = PDG(function_name="foo", filepath=FILEPATH_A)
    n1 = make_node_id(FILEPATH_A, 1, 0)
    n2 = make_node_id(FILEPATH_A, 2, 0)
    pdg.data_edges[n1] = {n2}
    _merge_pdg_into_sdg(sdg, pdg)
    assert n2 in sdg.data_edges.get(n1, set())
    assert n1 in sdg.reverse_data_edges.get(n2, set())


def test_merge_pdg_adds_control_edges():
    sdg = SDG()
    pdg = PDG(function_name="foo", filepath=FILEPATH_A)
    n1 = make_node_id(FILEPATH_A, 1, 0)
    n2 = make_node_id(FILEPATH_A, 2, 0)
    pdg.control_edges[n1] = {n2}
    _merge_pdg_into_sdg(sdg, pdg)
    assert n2 in sdg.control_edges.get(n1, set())
    assert n1 in sdg.reverse_control_edges.get(n2, set())


# ---------------------------------------------------------------------------
# build_sdg — integration tests
# ---------------------------------------------------------------------------


def test_build_sdg_returns_sdg(tmp_path):
    write_py(tmp_path, "a.py", "def foo():\n    x = 1\n")
    index = build_index(tmp_path)
    loc = FunctionLocation(
        name="foo",
        filepath=tmp_path / "a.py",
        lineno=1,
        end_lineno=2,
        ast_node=None,
        branch="A",
    )
    diff = DiffResult(seeds_a=[loc])
    sdg = build_sdg(diff, index, depth=1)
    assert isinstance(sdg, SDG)


def test_build_sdg_includes_seed_nodes(tmp_path):
    write_py(tmp_path, "a.py", "def foo():\n    x = 1\n    return x\n")
    index = build_index(tmp_path)
    locs = index.lookup("foo")
    assert locs
    locs[0].branch = "A"
    diff = DiffResult(seeds_a=locs)
    sdg = build_sdg(diff, index, depth=0)
    assert len(sdg.nodes) > 0


def test_build_sdg_seeds_a_populated(tmp_path):
    write_py(tmp_path, "a.py", "def foo():\n    return 1\n")
    index = build_index(tmp_path)
    locs = index.lookup("foo")
    locs[0].branch = "A"
    diff = DiffResult(seeds_a=locs)
    sdg = build_sdg(diff, index, depth=0)
    assert len(sdg.seeds_a) > 0


def test_build_sdg_seeds_b_populated(tmp_path):
    write_py(tmp_path, "b.py", "def bar():\n    return 2\n")
    index = build_index(tmp_path)
    locs = index.lookup("bar")
    locs[0].branch = "B"
    diff = DiffResult(seeds_b=locs)
    sdg = build_sdg(diff, index, depth=0)
    assert len(sdg.seeds_b) > 0


def test_build_sdg_expands_callees(tmp_path):
    write_py(
        tmp_path,
        "a.py",
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
    )
    index = build_index(tmp_path)
    locs = index.lookup("caller")
    locs[0].branch = "A"
    diff = DiffResult(seeds_a=locs)
    sdg = build_sdg(diff, index, depth=1)
    # Both caller and helper nodes should be in the graph
    node_funcs = {n.enclosing_function for n in sdg.nodes.values()}
    assert "caller" in node_funcs
    assert "helper" in node_funcs


def test_build_sdg_depth_zero_no_expansion(tmp_path):
    write_py(
        tmp_path,
        "a.py",
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n",
    )
    index = build_index(tmp_path)
    locs = index.lookup("caller")
    locs[0].branch = "A"
    diff = DiffResult(seeds_a=locs)
    sdg = build_sdg(diff, index, depth=0)
    node_funcs = {n.enclosing_function for n in sdg.nodes.values()}
    # helper should NOT be expanded at depth=0
    assert "caller" in node_funcs
    assert "helper" not in node_funcs


def test_build_sdg_no_duplicate_functions(tmp_path):
    """A function reachable via multiple paths is only included once."""
    write_py(
        tmp_path,
        "a.py",
        (
            "def shared():\n    return 0\n\n"
            "def a():\n    return shared()\n\n"
            "def b():\n    return shared()\n"
        ),
    )
    index = build_index(tmp_path)
    a_locs = index.lookup("a")
    b_locs = index.lookup("b")
    a_locs[0].branch = "A"
    b_locs[0].branch = "B"
    diff = DiffResult(seeds_a=a_locs, seeds_b=b_locs)
    sdg = build_sdg(diff, index, depth=1)
    # shared() nodes should appear only once (no duplicates by id)
    shared_nodes = [n for n in sdg.nodes.values() if n.enclosing_function == "shared"]
    ids = [n.id for n in shared_nodes]
    assert len(ids) == len(set(ids))


def test_build_sdg_reverse_edges_populated(tmp_path):
    write_py(tmp_path, "a.py", "def foo():\n    x = 1\n    return x\n")
    index = build_index(tmp_path)
    locs = index.lookup("foo")
    locs[0].branch = "A"
    diff = DiffResult(seeds_a=locs)
    sdg = build_sdg(diff, index, depth=0)
    # Every data edge should have a corresponding reverse entry
    for src, targets in sdg.data_edges.items():
        for tgt in targets:
            assert src in sdg.reverse_data_edges.get(tgt, set())
