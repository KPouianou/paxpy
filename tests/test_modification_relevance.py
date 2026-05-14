"""Tests for _path_has_modification_relevance and its helpers.

Each test constructs a minimal SDG and InterferencePath by hand and verifies
that the filter correctly keeps or suppresses the path.

Test cases:
  1. both_connect — source feeds call args, sink depends on entry → True
  2. only_source_connects — source feeds call, sink mod disconnected → False
  3. only_sink_connects — sink depends on entry, source mod disconnected → False
  4. neither_connects — both mods disconnected → False
  5. source_uses_return — source mod downstream of call boundary → True
  6. no_modified_in_source — tier 1 filter: no mods in source → False
  7. no_call_boundary — path has no call edge → True (conservative)
  8. missing_source_node — source node not in SDG → True (conservative)
"""

from __future__ import annotations

from pathlib import Path

from paxpy.main import _path_has_modification_relevance
from paxpy.types import SDG, ConflictType, InterferencePath, Node, make_node_id


def _node(
    lineno: int,
    *,
    fn: str = "f",
    filepath: str = "/repo/a.py",
    is_mod: bool = False,
    col: int = 0,
) -> Node:
    nid = make_node_id(Path(filepath), lineno, col)
    return Node(
        id=nid,
        filepath=Path(filepath),
        lineno=lineno,
        col_offset=col,
        enclosing_function=fn,
        is_direct_modification=is_mod,
    )


def _sdg(*nodes: Node) -> SDG:
    sdg = SDG()
    for n in nodes:
        sdg.nodes[n.id] = n
    return sdg


def _add_data(sdg: SDG, src: Node, tgt: Node) -> None:
    sdg.data_edges.setdefault(src.id, set()).add(tgt.id)
    sdg.reverse_data_edges.setdefault(tgt.id, set()).add(src.id)


def _add_call(sdg: SDG, src: Node, tgt: Node) -> None:
    sdg.call_edges.setdefault(src.id, set()).add(tgt.id)
    sdg.reverse_call_edges.setdefault(tgt.id, set()).add(src.id)


def _path(source: Node, sink: Node, nodes: list[Node]) -> InterferencePath:
    return InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
        path_nodes=[n.id for n in nodes],
        source_node=source.id,
        sink_node=sink.id,
    )


# ---------------------------------------------------------------------------
# Test 1: Both connect → True
# ---------------------------------------------------------------------------
# Source fn: mod_s --data--> call_node --call--> sink_entry --data--> mod_k
def test_both_connect():
    mod_s = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True)
    call_n = _node(2, fn="caller", filepath="/repo/a.py")
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    mod_k = _node(11, fn="callee", filepath="/repo/b.py", is_mod=True)

    sdg = _sdg(mod_s, call_n, sink_entry, mod_k)
    _add_data(sdg, mod_s, call_n)
    _add_call(sdg, call_n, sink_entry)
    _add_data(sdg, sink_entry, mod_k)

    p = _path(mod_s, sink_entry, [mod_s, call_n, sink_entry, mod_k])
    assert _path_has_modification_relevance(p, sdg) is True


# ---------------------------------------------------------------------------
# Test 2: Only source connects → False
# ---------------------------------------------------------------------------
def test_only_source_connects():
    mod_s = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True)
    call_n = _node(2, fn="caller", filepath="/repo/a.py")
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    # mod_k exists but has NO data edge from sink_entry
    mod_k = _node(11, fn="callee", filepath="/repo/b.py", is_mod=True)

    sdg = _sdg(mod_s, call_n, sink_entry, mod_k)
    _add_data(sdg, mod_s, call_n)
    _add_call(sdg, call_n, sink_entry)
    # No data edge from sink_entry to mod_k

    p = _path(mod_s, sink_entry, [mod_s, call_n, sink_entry])
    assert _path_has_modification_relevance(p, sdg) is False


# ---------------------------------------------------------------------------
# Test 3: Only sink connects → False
# ---------------------------------------------------------------------------
def test_only_sink_connects():
    # mod_s is disconnected from call_n (no data path)
    mod_s = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True)
    other = _node(2, fn="caller", filepath="/repo/a.py")
    call_n = _node(3, fn="caller", filepath="/repo/a.py")
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    mod_k = _node(11, fn="callee", filepath="/repo/b.py", is_mod=True)

    sdg = _sdg(mod_s, other, call_n, sink_entry, mod_k)
    # mod_s -> other (not call_n), no path from mod_s to call_n
    _add_data(sdg, mod_s, other)
    _add_call(sdg, call_n, sink_entry)
    _add_data(sdg, sink_entry, mod_k)

    p = _path(mod_s, sink_entry, [call_n, sink_entry])
    assert _path_has_modification_relevance(p, sdg) is False


# ---------------------------------------------------------------------------
# Test 4: Neither connects → False
# ---------------------------------------------------------------------------
def test_neither_connects():
    mod_s = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True)
    call_n = _node(2, fn="caller", filepath="/repo/a.py")
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    mod_k = _node(11, fn="callee", filepath="/repo/b.py", is_mod=True)

    sdg = _sdg(mod_s, call_n, sink_entry, mod_k)
    # No data edges at all, only call edge
    _add_call(sdg, call_n, sink_entry)

    p = _path(mod_s, sink_entry, [call_n, sink_entry])
    assert _path_has_modification_relevance(p, sdg) is False


# ---------------------------------------------------------------------------
# Test 5: Source uses return value (forward from call boundary) → True
# ---------------------------------------------------------------------------
def test_source_uses_return():
    call_n = _node(1, fn="caller", filepath="/repo/a.py")
    mod_s = _node(2, fn="caller", filepath="/repo/a.py", is_mod=True)
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    mod_k = _node(11, fn="callee", filepath="/repo/b.py", is_mod=True)

    sdg = _sdg(call_n, mod_s, sink_entry, mod_k)
    # Source mod is DOWNSTREAM of call (uses return value)
    _add_data(sdg, call_n, mod_s)
    _add_call(sdg, call_n, sink_entry)
    _add_data(sdg, sink_entry, mod_k)

    p = _path(call_n, sink_entry, [call_n, sink_entry])
    assert _path_has_modification_relevance(p, sdg) is True


# ---------------------------------------------------------------------------
# Test 6: No modified nodes in source → False (tier 1)
# ---------------------------------------------------------------------------
def test_no_modified_in_source():
    src = _node(1, fn="caller", filepath="/repo/a.py", is_mod=False)
    call_n = _node(2, fn="caller", filepath="/repo/a.py", is_mod=False)
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    mod_k = _node(11, fn="callee", filepath="/repo/b.py", is_mod=True)

    sdg = _sdg(src, call_n, sink_entry, mod_k)
    _add_data(sdg, src, call_n)
    _add_call(sdg, call_n, sink_entry)
    _add_data(sdg, sink_entry, mod_k)

    p = _path(src, sink_entry, [src, call_n, sink_entry])
    assert _path_has_modification_relevance(p, sdg) is False


# ---------------------------------------------------------------------------
# Test 7: No call boundary on path → True (conservative keep)
# ---------------------------------------------------------------------------
def test_no_call_boundary():
    mod_s = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True)
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    mod_k = _node(11, fn="callee", filepath="/repo/b.py", is_mod=True)

    sdg = _sdg(mod_s, sink_entry, mod_k)
    # Only data edges, no call edge on path
    _add_data(sdg, mod_s, sink_entry)
    _add_data(sdg, sink_entry, mod_k)

    p = _path(mod_s, sink_entry, [mod_s, sink_entry])
    assert _path_has_modification_relevance(p, sdg) is True


# ---------------------------------------------------------------------------
# Test 8: Missing source/sink nodes → True (conservative keep)
# ---------------------------------------------------------------------------
def test_missing_source_node():
    sink_entry = _node(10, fn="callee", filepath="/repo/b.py")
    sdg = _sdg(sink_entry)

    p = InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
        path_nodes=["nonexistent:1:0", sink_entry.id],
        source_node="nonexistent:1:0",
        sink_node=sink_entry.id,
    )
    assert _path_has_modification_relevance(p, sdg) is True


def test_missing_sink_node():
    source = _node(1, fn="caller", filepath="/repo/a.py", is_mod=True)
    sdg = _sdg(source)

    p = InterferencePath(
        direction="A_to_B",
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
        path_nodes=[source.id, "nonexistent:10:0"],
        source_node=source.id,
        sink_node="nonexistent:10:0",
    )
    assert _path_has_modification_relevance(p, sdg) is True
