"""Regression tests for chop depth monotonicity.

If detect() + filter_by_call_hops() flags a candidate at SDG depth N, it must
also flag that candidate at depth N+k for any k > 0. Expanding the SDG (more
call-graph depth) can only add nodes and edges — it never removes them — so any
path found at depth N must still be present and still pass the hop filter at
depth N+k.

These tests construct synthetic SDGs that simulate the two-level scenario:
  - sdg_shallow: the graph reachable with a smaller depth limit
  - sdg_deep: sdg_shallow plus extra nodes from deeper expansion

The tests assert that every (source, sink) pair flagged in sdg_shallow is also
flagged in sdg_deep at the same or lower call-hop count.
"""

from __future__ import annotations

from pathlib import Path

from paxpy.detector import count_call_hops, detect, filter_by_call_hops
from paxpy.types import SDG, Node

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_sdg(
    data_edges: dict[str, list[str]] | None = None,
    call_edges: dict[str, list[str]] | None = None,
    control_edges: dict[str, list[str]] | None = None,
    seeds_a: set[str] | None = None,
    seeds_b: set[str] | None = None,
) -> SDG:
    """Build a minimal SDG for testing from string node IDs."""
    sdg = SDG()
    sdg.seeds_a = seeds_a or set()
    sdg.seeds_b = seeds_b or set()

    all_nodes: set[str] = set(sdg.seeds_a) | set(sdg.seeds_b)

    for src, targets in (data_edges or {}).items():
        sdg.data_edges.setdefault(src, set()).update(targets)
        for tgt in targets:
            sdg.reverse_data_edges.setdefault(tgt, set()).add(src)
        all_nodes.add(src)
        all_nodes.update(targets)

    for src, targets in (call_edges or {}).items():
        sdg.call_edges.setdefault(src, set()).update(targets)
        for tgt in targets:
            sdg.reverse_call_edges.setdefault(tgt, set()).add(src)
        all_nodes.add(src)
        all_nodes.update(targets)

    for src, targets in (control_edges or {}).items():
        sdg.control_edges.setdefault(src, set()).update(targets)
        for tgt in targets:
            sdg.reverse_control_edges.setdefault(tgt, set()).add(src)
        all_nodes.add(src)
        all_nodes.update(targets)

    fp = Path("/repo/test.py")
    for nid in all_nodes:
        parts = nid.split(":")
        lineno = int(parts[1]) if len(parts) > 1 else 1
        col = int(parts[2]) if len(parts) > 2 else 0
        sdg.nodes[nid] = Node(id=nid, filepath=fp, lineno=lineno, col_offset=col)

    return sdg


def _flagged_pairs(paths: list) -> set[tuple[str, str]]:
    """Return the set of (source_node, sink_node) pairs from detected paths."""
    return {(p.source_node, p.sink_node) for p in paths}


# ---------------------------------------------------------------------------
# Core monotonicity tests
# ---------------------------------------------------------------------------


def test_monotonicity_direct_call_path():
    """A direct call path found at shallow depth is also found at deep depth.

    Shallow SDG: A -call-> B  (1 call hop)
    Deep SDG:    A -call-> B, plus extra unreachable nodes C, D
    Both should detect the A→B path at max_hops=2.
    """
    shallow = _build_sdg(
        call_edges={"A:1:0": ["B:2:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )
    deep = _build_sdg(
        call_edges={"A:1:0": ["B:2:0"], "C:3:0": ["D:4:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )

    shallow_paths = filter_by_call_hops(detect(shallow), shallow, max_hops=2)
    deep_paths = filter_by_call_hops(detect(deep), deep, max_hops=2)

    assert len(shallow_paths) >= 1, "Shallow SDG must flag A→B"
    assert _flagged_pairs(shallow_paths) <= _flagged_pairs(deep_paths), (
        "Every pair flagged at shallow depth must also be flagged at deep depth"
    )


def test_monotonicity_intermediate_node_added():
    """Adding an intermediate node to the SDG does not remove existing detections.

    Shallow SDG: A -data-> M -call-> B  (1 call hop, found via BFS)
    Deep SDG:    same + extra node X reachable from A but not from M or B
    """
    shallow = _build_sdg(
        data_edges={"A:1:0": ["M:5:0"]},
        call_edges={"M:5:0": ["B:9:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:9:0"},
    )
    deep = _build_sdg(
        data_edges={"A:1:0": ["M:5:0", "X:20:0"]},
        call_edges={"M:5:0": ["B:9:0"], "X:20:0": ["Y:21:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:9:0"},
    )

    shallow_paths = filter_by_call_hops(detect(shallow), shallow, max_hops=2)
    deep_paths = filter_by_call_hops(detect(deep), deep, max_hops=2)

    assert len(shallow_paths) >= 1, "Shallow SDG must detect A→B via M"
    assert _flagged_pairs(shallow_paths) <= _flagged_pairs(deep_paths), (
        "Path A→B via M must still be found after extra nodes are added"
    )


def test_monotonicity_call_hop_count_stable_with_extra_nodes():
    """The call-hop count for a path found in the shallow SDG does not increase
    when the SDG is expanded, because BFS finds the shortest-raw-edge path and
    the existing shorter path is never evicted.

    Shallow: A -call-> B  (1 call hop)
    Deep: same + longer alternative path A -call-> C -call-> D -call-> B
          BFS still finds A→B directly (shorter in raw hops) → still 1 call hop.
    """
    shallow = _build_sdg(
        call_edges={"A:1:0": ["B:2:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )
    deep = _build_sdg(
        call_edges={
            "A:1:0": ["B:2:0", "C:3:0"],
            "C:3:0": ["D:4:0"],
            "D:4:0": ["B:2:0"],
        },
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )

    # Confirm shallow detects A→B
    shallow_paths = filter_by_call_hops(detect(shallow), shallow, max_hops=2)
    assert len(shallow_paths) >= 1

    # Confirm deep also detects A→B (BFS finds the direct edge first)
    deep_paths = filter_by_call_hops(detect(deep), deep, max_hops=2)
    assert len(deep_paths) >= 1

    # The call-hop count on the A→B path must be 1 (direct call) in both SDGs
    ab_shallow = [p for p in shallow_paths if p.source_node == "A:1:0" and p.sink_node == "B:2:0"]
    ab_deep = [p for p in deep_paths if p.source_node == "A:1:0" and p.sink_node == "B:2:0"]
    assert ab_shallow, "Shallow must have A→B path"
    assert ab_deep, "Deep must have A→B path"
    assert count_call_hops(ab_shallow[0].path_nodes, shallow) == 1
    assert count_call_hops(ab_deep[0].path_nodes, deep) == 1


def test_monotonicity_control_path_preserved():
    """A control-dependency path found at shallow depth persists at deeper depth.

    Simulates a tier-2 (control-edge) finding that must survive SDG expansion.
    """
    shallow = _build_sdg(
        control_edges={"A:1:0": ["B:2:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )
    deep = _build_sdg(
        control_edges={"A:1:0": ["B:2:0"], "C:3:0": ["D:4:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )

    shallow_paths = filter_by_call_hops(detect(shallow), shallow, max_hops=5)
    deep_paths = filter_by_call_hops(detect(deep), deep, max_hops=5)

    assert len(shallow_paths) >= 1, "Shallow must detect control-dep A→B"
    assert _flagged_pairs(shallow_paths) <= _flagged_pairs(deep_paths), (
        "Control-dep path A→B must persist when SDG is expanded"
    )


def test_monotonicity_both_directions():
    """Monotonicity holds for both A→B and B→A paths."""
    shallow = _build_sdg(
        call_edges={"A:1:0": ["B:2:0"], "B:2:0": ["A:1:0"]},
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )
    deep = _build_sdg(
        call_edges={
            "A:1:0": ["B:2:0"],
            "B:2:0": ["A:1:0"],
            "X:10:0": ["Y:11:0"],
        },
        seeds_a={"A:1:0"},
        seeds_b={"B:2:0"},
    )

    shallow_paths = filter_by_call_hops(detect(shallow), shallow, max_hops=3)
    deep_paths = filter_by_call_hops(detect(deep), deep, max_hops=3)

    assert len(shallow_paths) >= 1
    assert _flagged_pairs(shallow_paths) <= _flagged_pairs(deep_paths), (
        "All pairs flagged at shallow depth must be flagged at deep depth"
    )


def test_monotonicity_multi_hop_chain_preserved():
    """A multi-hop chain at depth N stays detectable at depth N+k.

    Simulates a 3-function call chain:  A → M1 → M2 → B  (3 call hops)
    At depth 3 this chain is fully included. At depth 5, extra functions are
    reachable from B but do not affect the A→B path.
    """
    base_call_edges = {
        "A:1:0": ["M1:5:0"],
        "M1:5:0": ["M2:10:0"],
        "M2:10:0": ["B:15:0"],
    }
    shallow = _build_sdg(
        call_edges=base_call_edges,
        seeds_a={"A:1:0"},
        seeds_b={"B:15:0"},
    )

    # Deep adds callees of B — doesn't affect the A→B chain
    deep_call_edges = dict(base_call_edges)
    deep_call_edges["B:15:0"] = ["E1:20:0"]
    deep_call_edges["E1:20:0"] = ["E2:25:0"]
    deep = _build_sdg(
        call_edges=deep_call_edges,
        seeds_a={"A:1:0"},
        seeds_b={"B:15:0"},
    )

    shallow_paths = filter_by_call_hops(detect(shallow), shallow, max_hops=5)
    deep_paths = filter_by_call_hops(detect(deep), deep, max_hops=5)

    assert len(shallow_paths) >= 1, "3-hop chain must be detected at shallow depth"
    assert _flagged_pairs(shallow_paths) <= _flagged_pairs(deep_paths), (
        "3-hop chain A→B must still be detected when deeper nodes are added"
    )
