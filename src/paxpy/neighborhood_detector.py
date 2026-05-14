"""Neighborhood-overlap detector for semantic merge conflicts.

Alternative to the approximate-chop detector. Instead of finding all paths
between A-seeds and B-seeds (which creates spurious connections through hub
functions), this detector:

1. Collects M_A / M_B — nodes that are *directly* within diff hunks
   (is_direct_modification=True) for each branch.
2. Expands radius-bounded neighborhoods N_A / N_B from those nodes using all
   edge types (data, control, call) in both directions.
3. Reports overlap = N_A ∩ N_B, filtered to nodes where both sides actually
   touch modified code (i.e. the overlap node is adjacent to at least one
   M_A node and at least one M_B node within the radius).

Hub nodes such as `completion()` or `logger.log()` are reachable from many
unrelated modifications, so they appear in N_A ∩ N_B. They are suppressed
because they are not in M_A ∪ M_B — no node in the overlap set is a directly
modified node from *both* branches unless both branches actually modified it.
The adjacency filter further ensures that both sides must reach the overlap
node within `radius` hops, not merely be connected to it through long chains.

Depends on: types only.
"""

from __future__ import annotations

from collections import deque

from paxpy.types import (
    SDG,
    ConflictType,
    InterferencePath,
    Node,
    NodeId,
)


def detect_interferences(sdg: SDG, radius: int = 1) -> list[InterferencePath]:
    """Detect semantic merge conflicts via neighborhood overlap.

    Args:
        sdg: Fully constructed partial SDG from sdg_builder.
        radius: Maximum number of hops (any edge type, any direction) to
            expand from directly-modified nodes. Smaller values reduce false
            positives; larger values increase recall. Default is 1.

    Returns:
        List of InterferencePath objects, one per overlapping node. Each path
        is a synthetic 3-node path [source_in_A, overlap_node, sink_in_B]
        (or [source_in_B, overlap_node, sink_in_A] for B_to_A direction).
        When the overlap node itself is directly modified by one side, the
        path collapses to [overlap_node, overlap_node] with that node as both
        source and sink.
    """
    # Collect directly modified nodes per branch
    direct_a: set[NodeId] = set()
    direct_b: set[NodeId] = set()
    for node_id, node in sdg.nodes.items():
        if node.is_direct_modification:
            if node.branch == "A":
                direct_a.add(node_id)
            elif node.branch == "B":
                direct_b.add(node_id)

    if not direct_a or not direct_b:
        return []

    # Expand radius-bounded neighborhoods
    neighborhood_a = _expand_neighborhood(sdg, direct_a, radius)
    neighborhood_b = _expand_neighborhood(sdg, direct_b, radius)

    # Nodes reachable from both sides
    overlap_ids = set(neighborhood_a) & set(neighborhood_b)
    if not overlap_ids:
        return []

    # For each overlap node, build a synthetic InterferencePath
    paths: list[InterferencePath] = []
    # Deduplicate by unordered (source_a, source_b) pair — one path per pair
    seen_pairs: set[frozenset[NodeId]] = set()

    for overlap_id in sorted(overlap_ids):  # sorted for determinism
        # Find the closest directly-modified A node that can reach overlap_id
        source_a = _closest_seed(overlap_id, direct_a, neighborhood_a, sdg)
        # Find the closest directly-modified B node that can reach overlap_id
        source_b = _closest_seed(overlap_id, direct_b, neighborhood_b, sdg)

        if source_a is None or source_b is None:
            continue

        # Deduplicate: one path per unique (source_a, source_b) pair
        pair_key: frozenset[NodeId] = frozenset({source_a, source_b})
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        overlap_node = sdg.nodes.get(overlap_id)
        conflict_type = _infer_conflict_type(overlap_id, overlap_node, sdg, direct_a, direct_b)

        path = InterferencePath(
            direction="A_to_B",
            conflict_type=conflict_type,
            tier=1,
            path_nodes=[source_a, overlap_id, source_b],
            source_node=source_a,
            sink_node=source_b,
        )
        paths.append(path)

    return paths


# ---------------------------------------------------------------------------
# Neighborhood expansion
# ---------------------------------------------------------------------------


def _expand_neighborhood(
    sdg: SDG,
    seeds: set[NodeId],
    radius: int,
) -> dict[NodeId, int]:
    """BFS from seeds over all edge types (both directions) up to radius hops.

    Returns a dict mapping reachable NodeId → minimum hop distance from any
    seed. Seeds themselves have distance 0.

    All six adjacency lists are used: data, control, call edges in both the
    forward and reverse directions. This ensures that regardless of edge
    orientation, any node within `radius` steps of a seed is included.
    """
    dist: dict[NodeId, int] = {}
    queue: deque[tuple[NodeId, int]] = deque()

    for seed in seeds:
        if seed in sdg.nodes:
            dist[seed] = 0
            queue.append((seed, 0))

    while queue:
        node_id, d = queue.popleft()
        if d >= radius:
            continue

        neighbors = (
            sdg.data_edges.get(node_id, set())
            | sdg.reverse_data_edges.get(node_id, set())
            | sdg.control_edges.get(node_id, set())
            | sdg.reverse_control_edges.get(node_id, set())
            | sdg.call_edges.get(node_id, set())
            | sdg.reverse_call_edges.get(node_id, set())
        )
        for nbr in neighbors:
            if nbr not in dist:
                dist[nbr] = d + 1
                queue.append((nbr, d + 1))

    return dist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _closest_seed(
    overlap_id: NodeId,
    direct_seeds: set[NodeId],
    neighborhood: dict[NodeId, int],
    sdg: SDG,
) -> NodeId | None:
    """Return the directly-modified seed node closest to overlap_id.

    Since neighborhood[overlap_id] is the distance from the nearest seed,
    and we need to identify *which* seed that was, we do a second BFS from
    overlap_id back into the seeds set.
    """
    # Fast path: overlap node is itself a direct seed
    if overlap_id in direct_seeds:
        return overlap_id

    # BFS from overlap_id to find the nearest direct seed
    visited: set[NodeId] = {overlap_id}
    queue: deque[tuple[NodeId, int]] = deque([(overlap_id, 0)])
    max_dist = neighborhood.get(overlap_id, 0)

    while queue:
        node_id, d = queue.popleft()
        if d > max_dist:
            break
        if node_id in direct_seeds:
            return node_id

        neighbors = (
            sdg.data_edges.get(node_id, set())
            | sdg.reverse_data_edges.get(node_id, set())
            | sdg.control_edges.get(node_id, set())
            | sdg.reverse_control_edges.get(node_id, set())
            | sdg.call_edges.get(node_id, set())
            | sdg.reverse_call_edges.get(node_id, set())
        )
        for nbr in neighbors:
            if nbr not in visited:
                visited.add(nbr)
                queue.append((nbr, d + 1))

    return None


def _infer_conflict_type(
    overlap_id: NodeId,
    overlap_node: Node | None,
    sdg: SDG,
    direct_a: set[NodeId],
    direct_b: set[NodeId],
) -> ConflictType:
    """Infer the most likely conflict type for an overlap node.

    Heuristics (applied in order):

    1. OVERRIDE_ASSIGNMENT — overlap node is directly modified by *both* sides
       (both branches modified the same statement).
    2. CONTROL_DEPENDENCY — overlap node has incoming control edges from nodes
       in M_A or M_B.
    3. CONFLUENCE — overlap node has incoming data edges from *both* M_A and M_B.
    4. DATA_FLOW — default (one side writes, the other reads through the node).
    """

    if overlap_node is not None and overlap_node.is_direct_modification:
        # The overlap node itself was modified by one side; if both seeds reach
        # it via data edges it's more likely OA than DF.
        in_data = sdg.reverse_data_edges.get(overlap_id, set())
        has_a = bool(in_data & direct_a)
        has_b = bool(in_data & direct_b)
        if has_a and has_b:
            return ConflictType.OVERRIDE_ASSIGNMENT

    # Check for control dependency: a direct seed has a control edge to/from overlap
    in_ctrl = sdg.reverse_control_edges.get(overlap_id, set())
    out_ctrl = sdg.control_edges.get(overlap_id, set())
    if (in_ctrl | out_ctrl) & (direct_a | direct_b):
        return ConflictType.CONTROL_DEPENDENCY

    # Check for confluence: multiple incoming data edges from both sides
    in_data = sdg.reverse_data_edges.get(overlap_id, set())
    has_a = bool(in_data & direct_a)
    has_b = bool(in_data & direct_b)
    if has_a and has_b:
        return ConflictType.CONFLUENCE

    return ConflictType.DATA_FLOW
