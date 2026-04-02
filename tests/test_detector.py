"""Tests for detector.py."""

from __future__ import annotations

from pathlib import Path

from paxpy.detector import (
    _bfs_backward,
    _bfs_forward,
    _reconstruct_path,
    count_call_hops,
    detect,
    filter_by_call_hops,
)
from paxpy.types import SDG, ConflictType, InterferencePath, Node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sdg(
    edges: dict[str, list[str]] | None = None,
    control_edges: dict[str, list[str]] | None = None,
    call_edges: dict[str, list[str]] | None = None,
    seeds_a: set[str] | None = None,
    seeds_b: set[str] | None = None,
) -> SDG:
    """Build a minimal SDG for testing from string node IDs."""
    sdg = SDG()
    sdg.seeds_a = seeds_a or set()
    sdg.seeds_b = seeds_b or set()

    all_nodes: set[str] = set(sdg.seeds_a) | set(sdg.seeds_b)

    for src, targets in (edges or {}).items():
        sdg.data_edges[src] = set(targets)
        for tgt in targets:
            sdg.reverse_data_edges.setdefault(tgt, set()).add(src)
        all_nodes.add(src)
        all_nodes.update(targets)

    for src, targets in (control_edges or {}).items():
        sdg.control_edges[src] = set(targets)
        for tgt in targets:
            sdg.reverse_control_edges.setdefault(tgt, set()).add(src)
        all_nodes.add(src)
        all_nodes.update(targets)

    for src, targets in (call_edges or {}).items():
        sdg.call_edges[src] = set(targets)
        for tgt in targets:
            sdg.reverse_call_edges.setdefault(tgt, set()).add(src)
        all_nodes.add(src)
        all_nodes.update(targets)

    fp = Path("/repo/test.py")
    for nid in all_nodes:
        parts = nid.split(":")
        lineno = int(parts[1]) if len(parts) > 1 else 1
        col = int(parts[2]) if len(parts) > 2 else 0
        sdg.nodes[nid] = Node(id=nid, filepath=fp, lineno=lineno, col_offset=col)

    return sdg


# ---------------------------------------------------------------------------
# _reconstruct_path
# ---------------------------------------------------------------------------


def test_reconstruct_path_simple():
    preds = {"a": None, "b": "a", "c": "b"}
    path = _reconstruct_path(preds, "c")
    assert path == ["a", "b", "c"]


def test_reconstruct_path_single_node():
    preds = {"a": None}
    path = _reconstruct_path(preds, "a")
    assert path == ["a"]


def test_reconstruct_path_not_reachable():
    preds = {"a": None}
    path = _reconstruct_path(preds, "z")
    assert path == []


# ---------------------------------------------------------------------------
# _bfs_forward
# ---------------------------------------------------------------------------


def test_bfs_forward_single_hop():
    sdg = make_sdg(edges={"A:1:0": ["B:2:0"]})
    preds = _bfs_forward(sdg, {"A:1:0"}, use_control_edges=False)
    assert "B:2:0" in preds
    assert preds["A:1:0"] is None
    assert preds["B:2:0"] == "A:1:0"


def test_bfs_forward_two_hops():
    sdg = make_sdg(edges={"a:1:0": ["b:2:0"], "b:2:0": ["c:3:0"]})
    preds = _bfs_forward(sdg, {"a:1:0"}, use_control_edges=False)
    assert "c:3:0" in preds


def test_bfs_forward_ignores_control_when_flag_false():
    sdg = make_sdg(control_edges={"a:1:0": ["b:2:0"]})
    preds = _bfs_forward(sdg, {"a:1:0"}, use_control_edges=False)
    assert "b:2:0" not in preds


def test_bfs_forward_includes_control_when_flag_true():
    sdg = make_sdg(control_edges={"a:1:0": ["b:2:0"]})
    preds = _bfs_forward(sdg, {"a:1:0"}, use_control_edges=True)
    assert "b:2:0" in preds


def test_bfs_forward_multiple_seeds():
    sdg = make_sdg(edges={"x:1:0": ["z:3:0"], "y:2:0": ["z:3:0"]})
    preds = _bfs_forward(sdg, {"x:1:0", "y:2:0"}, use_control_edges=False)
    assert "z:3:0" in preds


# ---------------------------------------------------------------------------
# _bfs_backward
# ---------------------------------------------------------------------------


def test_bfs_backward_single_hop():
    sdg = make_sdg(edges={"a:1:0": ["b:2:0"]})
    reachable = _bfs_backward(sdg, {"b:2:0"}, use_control_edges=False)
    assert "a:1:0" in reachable
    assert "b:2:0" in reachable


def test_bfs_backward_two_hops():
    sdg = make_sdg(edges={"a:1:0": ["b:2:0"], "b:2:0": ["c:3:0"]})
    reachable = _bfs_backward(sdg, {"c:3:0"}, use_control_edges=False)
    assert "a:1:0" in reachable
    assert "b:2:0" in reachable


def test_bfs_backward_no_control_when_flag_false():
    sdg = make_sdg(control_edges={"a:1:0": ["b:2:0"]})
    reachable = _bfs_backward(sdg, {"b:2:0"}, use_control_edges=False)
    assert "a:1:0" not in reachable


def test_bfs_backward_includes_control_when_flag_true():
    sdg = make_sdg(control_edges={"a:1:0": ["b:2:0"]})
    reachable = _bfs_backward(sdg, {"b:2:0"}, use_control_edges=True)
    assert "a:1:0" in reachable


# ---------------------------------------------------------------------------
# detect — end-to-end
# ---------------------------------------------------------------------------


def test_detect_empty_seeds_returns_empty():
    sdg = make_sdg()
    assert detect(sdg) == []


def test_detect_no_path_returns_empty():
    # A and B exist but no edges connect them
    sdg = make_sdg(seeds_a={"a:1:0"}, seeds_b={"b:2:0"})
    assert detect(sdg) == []


def test_detect_direct_data_path_a_to_b():
    sdg = make_sdg(
        edges={"a:1:0": ["b:2:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    results = detect(sdg)
    assert len(results) >= 1
    ab = [r for r in results if r.direction == "A_to_B"]
    assert len(ab) >= 1
    assert ab[0].tier == 1
    assert ab[0].source_node == "a:1:0"
    assert ab[0].sink_node == "b:2:0"


def test_detect_direct_data_path_b_to_a():
    sdg = make_sdg(
        edges={"b:2:0": ["a:1:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    results = detect(sdg)
    ba = [r for r in results if r.direction == "B_to_A"]
    assert len(ba) >= 1
    assert ba[0].tier == 1


def test_detect_control_path_tier2():
    # No data/call path, only control
    sdg = make_sdg(
        control_edges={"a:1:0": ["b:2:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    results = detect(sdg)
    tier2 = [r for r in results if r.tier == 2]
    assert len(tier2) >= 1
    assert tier2[0].conflict_type == ConflictType.CONTROL_DEPENDENCY


def test_detect_data_path_is_tier1_not_tier2():
    sdg = make_sdg(
        edges={"a:1:0": ["b:2:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    results = detect(sdg)
    # The data path should be tier 1, not additionally reported as tier 2
    tier2_same_pair = [
        r for r in results if r.tier == 2 and r.source_node == "a:1:0" and r.sink_node == "b:2:0"
    ]
    assert len(tier2_same_pair) == 0


def test_detect_multi_hop_path():
    sdg = make_sdg(
        edges={"a:1:0": ["m:5:0"], "m:5:0": ["b:9:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:9:0"},
    )
    results = detect(sdg)
    assert len(results) >= 1
    assert results[0].path_nodes == ["a:1:0", "m:5:0", "b:9:0"]


def test_detect_deduplicates_results():
    sdg = make_sdg(
        edges={"a:1:0": ["b:2:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    results1 = detect(sdg)
    results2 = detect(sdg)
    # Running twice on the same SDG should yield the same count
    assert len(results1) == len(results2)


def test_detect_call_edge_tier1():
    sdg = make_sdg(
        call_edges={"a:1:0": ["b:2:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    results = detect(sdg)
    tier1 = [r for r in results if r.tier == 1]
    assert len(tier1) >= 1


# ---------------------------------------------------------------------------
# count_call_hops
# ---------------------------------------------------------------------------


def test_count_call_hops_no_calls():
    """Pure data-edge path has 0 call hops."""
    sdg = make_sdg(edges={"a:1:0": ["b:2:0"], "b:2:0": ["c:3:0"]})
    assert count_call_hops(["a:1:0", "b:2:0", "c:3:0"], sdg) == 0


def test_count_call_hops_single_call():
    sdg = make_sdg(call_edges={"a:1:0": ["b:2:0"]})
    assert count_call_hops(["a:1:0", "b:2:0"], sdg) == 1


def test_count_call_hops_two_calls():
    sdg = make_sdg(call_edges={"a:1:0": ["b:2:0"], "b:2:0": ["c:3:0"]})
    assert count_call_hops(["a:1:0", "b:2:0", "c:3:0"], sdg) == 2


def test_count_call_hops_mixed_edges():
    """Data edge followed by call edge → 1 call hop."""
    sdg = make_sdg(
        edges={"a:1:0": ["b:2:0"]},
        call_edges={"b:2:0": ["c:3:0"]},
    )
    assert count_call_hops(["a:1:0", "b:2:0", "c:3:0"], sdg) == 1


def test_count_call_hops_single_node():
    sdg = make_sdg()
    assert count_call_hops(["a:1:0"], sdg) == 0


def test_count_call_hops_empty_path():
    sdg = make_sdg()
    assert count_call_hops([], sdg) == 0


# ---------------------------------------------------------------------------
# filter_by_call_hops
# ---------------------------------------------------------------------------


def _make_path(nodes: list[str], direction: str = "A_to_B") -> InterferencePath:
    return InterferencePath(
        direction=direction,  # type: ignore[arg-type]
        conflict_type=ConflictType.DATA_FLOW,
        tier=1,
        path_nodes=nodes,
        source_node=nodes[0] if nodes else "",
        sink_node=nodes[-1] if nodes else "",
    )


def test_filter_keeps_short_paths():
    """Paths at or below max_hops are kept."""
    sdg = make_sdg(call_edges={"a:1:0": ["b:2:0"]})
    paths = [_make_path(["a:1:0", "b:2:0"])]  # 1 call hop
    result = filter_by_call_hops(paths, sdg, max_hops=2)
    assert len(result) == 1


def test_filter_removes_long_paths():
    """Paths exceeding max_hops are suppressed."""
    sdg = make_sdg(call_edges={"a:1:0": ["b:2:0"], "b:2:0": ["c:3:0"], "c:3:0": ["d:4:0"]})
    paths = [_make_path(["a:1:0", "b:2:0", "c:3:0", "d:4:0"])]  # 3 call hops
    result = filter_by_call_hops(paths, sdg, max_hops=2)
    assert len(result) == 0


def test_filter_keeps_exactly_at_threshold():
    sdg = make_sdg(call_edges={"a:1:0": ["b:2:0"], "b:2:0": ["c:3:0"]})
    paths = [_make_path(["a:1:0", "b:2:0", "c:3:0"])]  # exactly 2 call hops
    result = filter_by_call_hops(paths, sdg, max_hops=2)
    assert len(result) == 1


def test_filter_mixed_keeps_short_removes_long():
    sdg = make_sdg(
        edges={"a:1:0": ["b:2:0"]},  # data edge only
        call_edges={"x:1:0": ["y:2:0"], "y:2:0": ["z:3:0"], "z:3:0": ["w:4:0"]},
    )
    short = _make_path(["a:1:0", "b:2:0"])  # 0 call hops
    long_ = _make_path(["x:1:0", "y:2:0", "z:3:0", "w:4:0"])  # 3 call hops
    result = filter_by_call_hops([short, long_], sdg, max_hops=2)
    assert result == [short]


def test_filter_max_hops_zero_keeps_intra_procedural():
    """max_hops=0 keeps only paths with no call edges (purely intra-procedural)."""
    sdg = make_sdg(
        edges={"a:1:0": ["b:2:0"]},
        call_edges={"b:2:0": ["c:3:0"]},
    )
    intra = _make_path(["a:1:0", "b:2:0"])  # 0 call hops
    inter = _make_path(["a:1:0", "b:2:0", "c:3:0"])  # 1 call hop
    result = filter_by_call_hops([intra, inter], sdg, max_hops=0)
    assert result == [intra]


def test_filter_empty_paths_list():
    sdg = make_sdg()
    assert filter_by_call_hops([], sdg, max_hops=2) == []


def test_filter_detect_integration():
    """End-to-end: detect() + filter_by_call_hops keeps direct conflict, drops deep one."""
    # Direct path: A → B (1 call hop) — should survive max_hops=2
    sdg_direct = make_sdg(
        call_edges={"a:1:0": ["b:2:0"]},
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    direct_paths = filter_by_call_hops(detect(sdg_direct), sdg_direct, max_hops=2)
    assert len(direct_paths) >= 1

    # Deep path: A → C → D → E → B (4 call hops) — should be suppressed at max_hops=2
    sdg_deep = make_sdg(
        call_edges={
            "a:1:0": ["c:3:0"],
            "c:3:0": ["d:4:0"],
            "d:4:0": ["e:5:0"],
            "e:5:0": ["b:2:0"],
        },
        seeds_a={"a:1:0"},
        seeds_b={"b:2:0"},
    )
    deep_paths = filter_by_call_hops(detect(sdg_deep), sdg_deep, max_hops=2)
    assert len(deep_paths) == 0
