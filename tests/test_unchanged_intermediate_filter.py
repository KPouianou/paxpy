"""Tests for _all_intermediates_unchanged filter.

Each test constructs a minimal SDG and InterferencePath by hand and verifies
that the filter correctly keeps or suppresses the path.

Test cases:
  1. overlap_in_seed_function — intermediate in a modified function → kept
  2. overlap_in_unchanged_utility — intermediate in unchanged function → suppressed
  3. overlap_function_unknown — enclosing_function is None → kept (conservative)
  4. path_no_intermediates — path_length == 2 → kept
  5. mixed_intermediates — one in seed, one not → kept
  6. all_unchanged_multiple — multiple intermediates, all unchanged → suppressed
"""

from __future__ import annotations

from pathlib import Path

from paxpy.main import _all_intermediates_unchanged
from paxpy.types import SDG, ConflictType, InterferencePath, Node, make_node_id


def _node(
    lineno: int,
    *,
    fn: str | None = "f",
    filepath: str = "/repo/a.py",
    is_mod: bool = False,
    col: int = 0,
    branch: str | None = None,
) -> Node:
    nid = make_node_id(Path(filepath), lineno, col)
    return Node(
        id=nid,
        filepath=Path(filepath),
        lineno=lineno,
        col_offset=col,
        enclosing_function=fn,
        is_direct_modification=is_mod,
        branch=branch,
    )


def _sdg(*nodes: Node) -> SDG:
    sdg = SDG()
    for n in nodes:
        sdg.nodes[n.id] = n
    return sdg


def _path(nodes: list[Node]) -> InterferencePath:
    return InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
        path_nodes=[n.id for n in nodes],
        source_node=nodes[0].id,
        sink_node=nodes[-1].id,
    )


# ---------------------------------------------------------------------------
# Test 1: Overlap in seed function → finding kept
# ---------------------------------------------------------------------------
def test_overlap_in_seed_function():
    """Intermediate node is in a function modified by a branch → keep."""
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True, branch="A")
    overlap = _node(10, fn="caller", filepath="/repo/a.py")  # same function as source
    sink = _node(20, fn="callee", filepath="/repo/b.py", is_mod=True, branch="B")

    sdg = _sdg(source, overlap, sink)
    p = _path([source, overlap, sink])

    seed_functions = {("caller", "/repo/a.py"), ("callee", "/repo/b.py")}
    assert _all_intermediates_unchanged(p, sdg, seed_functions) is False


# ---------------------------------------------------------------------------
# Test 2: Overlap in unchanged utility → finding suppressed
# ---------------------------------------------------------------------------
def test_overlap_in_unchanged_utility():
    """Intermediate node is in an unchanged function → suppress."""
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True, branch="A")
    overlap = _node(10, fn="utility", filepath="/repo/utils.py")  # unchanged function
    sink = _node(20, fn="callee", filepath="/repo/b.py", is_mod=True, branch="B")

    sdg = _sdg(source, overlap, sink)
    p = _path([source, overlap, sink])

    seed_functions = {("caller", "/repo/a.py"), ("callee", "/repo/b.py")}
    assert _all_intermediates_unchanged(p, sdg, seed_functions) is True


# ---------------------------------------------------------------------------
# Test 3: Overlap function unknown → finding kept (conservative)
# ---------------------------------------------------------------------------
def test_overlap_function_unknown():
    """Intermediate node has no enclosing_function → conservative keep."""
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True, branch="A")
    overlap = _node(10, fn=None, filepath="/repo/utils.py")
    sink = _node(20, fn="callee", filepath="/repo/b.py", is_mod=True, branch="B")

    sdg = _sdg(source, overlap, sink)
    p = _path([source, overlap, sink])

    seed_functions = {("caller", "/repo/a.py"), ("callee", "/repo/b.py")}
    assert _all_intermediates_unchanged(p, sdg, seed_functions) is False


# ---------------------------------------------------------------------------
# Test 4: Path with no intermediates (length 2) → finding kept
# ---------------------------------------------------------------------------
def test_path_no_intermediates():
    """Path with only source and sink (no intermediates) → keep."""
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True, branch="A")
    sink = _node(20, fn="callee", filepath="/repo/b.py", is_mod=True, branch="B")

    sdg = _sdg(source, sink)
    p = _path([source, sink])

    seed_functions = {("caller", "/repo/a.py"), ("callee", "/repo/b.py")}
    assert _all_intermediates_unchanged(p, sdg, seed_functions) is False


# ---------------------------------------------------------------------------
# Test 5: Mixed intermediates — one in seed, one not → kept
# ---------------------------------------------------------------------------
def test_mixed_intermediates():
    """One intermediate in seed function, one in unchanged → keep (not ALL unchanged)."""
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True, branch="A")
    inter1 = _node(10, fn="utility", filepath="/repo/utils.py")  # unchanged
    inter2 = _node(15, fn="callee", filepath="/repo/b.py")  # in seed function
    sink = _node(20, fn="callee", filepath="/repo/b.py", is_mod=True, branch="B")

    sdg = _sdg(source, inter1, inter2, sink)
    p = _path([source, inter1, inter2, sink])

    seed_functions = {("caller", "/repo/a.py"), ("callee", "/repo/b.py")}
    assert _all_intermediates_unchanged(p, sdg, seed_functions) is False


# ---------------------------------------------------------------------------
# Test 6: All unchanged multiple intermediates → suppressed
# ---------------------------------------------------------------------------
def test_all_unchanged_multiple_intermediates():
    """Multiple intermediates all in unchanged functions → suppress."""
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True, branch="A")
    inter1 = _node(10, fn="utility_a", filepath="/repo/utils.py")
    inter2 = _node(15, fn="utility_b", filepath="/repo/helpers.py")
    sink = _node(20, fn="callee", filepath="/repo/b.py", is_mod=True, branch="B")

    sdg = _sdg(source, inter1, inter2, sink)
    p = _path([source, inter1, inter2, sink])

    seed_functions = {("caller", "/repo/a.py"), ("callee", "/repo/b.py")}
    assert _all_intermediates_unchanged(p, sdg, seed_functions) is True


# ---------------------------------------------------------------------------
# Test 7: Intermediate node not in SDG → conservative keep
# ---------------------------------------------------------------------------
def test_intermediate_not_in_sdg():
    """Intermediate node ID not found in SDG → conservative keep."""
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True, branch="A")
    sink = _node(20, fn="callee", filepath="/repo/b.py", is_mod=True, branch="B")

    sdg = _sdg(source, sink)
    # Path references a node not in the SDG
    p = InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
        path_nodes=[source.id, "nonexistent:10:0", sink.id],
        source_node=source.id,
        sink_node=sink.id,
    )

    seed_functions = {("caller", "/repo/a.py"), ("callee", "/repo/b.py")}
    assert _all_intermediates_unchanged(p, sdg, seed_functions) is False
