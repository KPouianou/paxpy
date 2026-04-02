"""Detect interference paths between two branches' changesets in the SDG.

Given a fully constructed SDG whose nodes are tagged with branch affiliation
(A or B), this module performs approximate program chopping to find all paths
from A-seeded nodes to B-seeded nodes (and vice versa). Detection proceeds in
two tiers:

  Tier 1 — Data/call paths: multi-source BFS through data_edges ∪ call_edges.
    Forward BFS from source seeds; backward BFS from target seeds on reversed
    graph; intersection = confirmed interference.

  Tier 2 — Control-mediated paths: repeat with all edges (data ∪ control ∪
    call). Findings present in Tier 2 but absent in Tier 1 are classified as
    CONTROL_DEPENDENCY conflicts.

Both A→B and B→A directions are checked. Conflict type is inferred from the
edge types traversed on the witnessing path.

Depends on: types, sdg (the SDG dataclass).
"""

from __future__ import annotations

from collections import deque

from paxpy.types import SDG, ConflictType, InterferencePath, NodeId


def count_call_hops(path_nodes: list[NodeId], sdg: SDG) -> int:
    """Count inter-procedural (call edge) boundaries crossed in a path.

    Walks consecutive (u, v) pairs in path_nodes and counts how many are
    connected via sdg.call_edges. This gives the number of function-call
    boundaries the interference path crosses, which is a better discriminator
    of genuine conflicts vs. incidental graph connectivity than raw node-hop
    count.

    Args:
        path_nodes: Ordered list of NodeIds from source to sink.
        sdg: The SDG whose call_edges are queried.

    Returns:
        Number of call edges in the path (0 = purely intra-procedural).
    """
    return sum(
        1
        for u, v in zip(path_nodes, path_nodes[1:], strict=False)
        if v in sdg.call_edges.get(u, set())
    )


def filter_by_call_hops(
    paths: list[InterferencePath],
    sdg: SDG,
    max_hops: int,
) -> list[InterferencePath]:
    """Suppress interference paths that cross too many call-graph boundaries.

    Paths with many call hops represent incidental graph connectivity through
    shared infrastructure (e.g. ORM core, framework utilities) rather than
    genuine semantic interference between the two branches. Keeping only short
    paths dramatically reduces false positives while retaining direct
    caller/callee relationships where real conflicts occur.

    Args:
        paths: Detected interference paths from detect().
        sdg: The SDG (needed to query call_edges).
        max_hops: Maximum number of call edges allowed. Paths with more than
            this many call-edge crossings are suppressed.

    Returns:
        Filtered list containing only paths with <= max_hops call edges.
    """
    return [p for p in paths if count_call_hops(p.path_nodes, sdg) <= max_hops]


def detect(sdg: SDG) -> list[InterferencePath]:
    """Find all interference paths between the A and B seed sets.

    Runs Tier 1 then Tier 2 in both directions (A→B and B→A). Returns the
    union of all found InterferencePath instances, deduplicated by
    (source_node, sink_node, conflict_type, tier).

    Args:
        sdg: Fully constructed SDG with seeds_a and seeds_b populated.

    Returns:
        List of InterferencePath, each describing one detected conflict path.
        Empty list if no interference is found.
    """
    if not sdg.seeds_a or not sdg.seeds_b:
        return []

    results: list[InterferencePath] = []
    seen: set[tuple[NodeId, NodeId, str, int]] = set()

    def add(path: InterferencePath) -> None:
        key = (path.source_node, path.sink_node, path.conflict_type.value, path.tier)
        if key not in seen:
            seen.add(key)
            results.append(path)

    # Tier 1 — data + call edges only
    tier1_ab = _approximate_chop(sdg, sdg.seeds_a, sdg.seeds_b, use_control_edges=False)
    tier1_ba = _approximate_chop(sdg, sdg.seeds_b, sdg.seeds_a, use_control_edges=False)

    tier1_paths_ab: set[tuple[NodeId, NodeId]] = set()
    tier1_paths_ba: set[tuple[NodeId, NodeId]] = set()

    for path in tier1_ab:
        if len(path) >= 2:
            ip = InterferencePath(
                direction="A_to_B",
                conflict_type=_infer_conflict_type(path, sdg),
                tier=1,
                path_nodes=path,
                source_node=path[0],
                sink_node=path[-1],
            )
            tier1_paths_ab.add((path[0], path[-1]))
            add(ip)

    for path in tier1_ba:
        if len(path) >= 2:
            ip = InterferencePath(
                direction="B_to_A",
                conflict_type=_infer_conflict_type(path, sdg),
                tier=1,
                path_nodes=path,
                source_node=path[0],
                sink_node=path[-1],
            )
            tier1_paths_ba.add((path[0], path[-1]))
            add(ip)

    # Tier 2 — all edges (data + control + call)
    tier2_ab = _approximate_chop(sdg, sdg.seeds_a, sdg.seeds_b, use_control_edges=True)
    tier2_ba = _approximate_chop(sdg, sdg.seeds_b, sdg.seeds_a, use_control_edges=True)

    for path in tier2_ab:
        if len(path) >= 2 and (path[0], path[-1]) not in tier1_paths_ab:
            ip = InterferencePath(
                direction="A_to_B",
                conflict_type=ConflictType.CONTROL_DEPENDENCY,
                tier=2,
                path_nodes=path,
                source_node=path[0],
                sink_node=path[-1],
            )
            add(ip)

    for path in tier2_ba:
        if len(path) >= 2 and (path[0], path[-1]) not in tier1_paths_ba:
            ip = InterferencePath(
                direction="B_to_A",
                conflict_type=ConflictType.CONTROL_DEPENDENCY,
                tier=2,
                path_nodes=path,
                source_node=path[0],
                sink_node=path[-1],
            )
            add(ip)

    return results


def _approximate_chop(
    sdg: SDG,
    sources: set[NodeId],
    targets: set[NodeId],
    use_control_edges: bool,
) -> list[list[NodeId]]:
    """Find interference paths from sources to targets via approximate chopping.

    Forward BFS from `sources` using data_edges (+ call_edges, + optionally
    control_edges). Backward BFS from `targets` using the corresponding
    reverse_* edges. Nodes in both reachable sets form the chop — the
    intersection. Reconstruct one representative path per (source, target) pair
    in the intersection.

    Args:
        sdg: The SDG to search.
        sources: Seed NodeIds for the forward BFS (source branch seeds).
        targets: Seed NodeIds for the backward BFS (target branch seeds).
        use_control_edges: If True, include control_edges and
            reverse_control_edges in the traversal.

    Returns:
        List of paths. Each path is an ordered list of NodeIds from a source
        seed to a target seed.
    """
    # Forward BFS: predecessors map for path reconstruction
    predecessors = _bfs_forward(sdg, sources, use_control_edges)

    # Backward BFS: set of nodes that can reach a target
    backward_reachable = _bfs_backward(sdg, targets, use_control_edges)

    # Chop = intersection of forward-reachable and backward-reachable
    chop = set(predecessors.keys()) & backward_reachable

    if not chop:
        return []

    # Find target nodes that are in the chop and reconstruct paths to them
    paths: list[list[NodeId]] = []
    reached_targets = targets & chop

    for target in reached_targets:
        if target in predecessors:
            path = _reconstruct_path(predecessors, target)
            if path:
                paths.append(path)

    return paths


def _bfs_forward(
    sdg: SDG,
    seeds: set[NodeId],
    use_control_edges: bool,
) -> dict[NodeId, NodeId | None]:
    """BFS forward from `seeds`, returning a predecessor map for path reconstruction.

    Args:
        sdg: The SDG.
        seeds: Starting nodes (predecessor = None).
        use_control_edges: Include control edges in traversal.

    Returns:
        Dict mapping each reachable NodeId to its predecessor (None for seeds).
    """
    predecessors: dict[NodeId, NodeId | None] = {s: None for s in seeds}
    queue: deque[NodeId] = deque(seeds)

    while queue:
        node = queue.popleft()

        neighbors: set[NodeId] = set()
        neighbors.update(sdg.data_edges.get(node, set()))
        neighbors.update(sdg.call_edges.get(node, set()))
        if use_control_edges:
            neighbors.update(sdg.control_edges.get(node, set()))

        for neighbor in neighbors:
            if neighbor not in predecessors:
                predecessors[neighbor] = node
                queue.append(neighbor)

    return predecessors


def _bfs_backward(
    sdg: SDG,
    seeds: set[NodeId],
    use_control_edges: bool,
) -> set[NodeId]:
    """BFS backward from `seeds` using reverse adjacency lists.

    Args:
        sdg: The SDG.
        seeds: Starting nodes for the backward traversal.
        use_control_edges: Include reverse_control_edges.

    Returns:
        Set of all NodeIds reachable by backward traversal from seeds.
    """
    reachable: set[NodeId] = set(seeds)
    queue: deque[NodeId] = deque(seeds)

    while queue:
        node = queue.popleft()

        neighbors: set[NodeId] = set()
        neighbors.update(sdg.reverse_data_edges.get(node, set()))
        neighbors.update(sdg.reverse_call_edges.get(node, set()))
        if use_control_edges:
            neighbors.update(sdg.reverse_control_edges.get(node, set()))

        for neighbor in neighbors:
            if neighbor not in reachable:
                reachable.add(neighbor)
                queue.append(neighbor)

    return reachable


def _infer_conflict_type(path: list[NodeId], sdg: SDG) -> ConflictType:
    """Infer the conflict type from the edge types traversed on a path.

    Checks consecutive (u, v) pairs in the path against sdg.*_edges dicts:
    - If any control edge is traversed → CONTROL_DEPENDENCY.
    - If multiple data sources converge at a node → CONFLUENCE.
    - If path passes through an assignment that overwrites a value →
      OVERRIDE_ASSIGNMENT.
    - Otherwise → DATA_FLOW.
    Defaults to DATA_FLOW when ambiguous.

    Args:
        path: Ordered list of NodeIds.
        sdg: The SDG (used to query edge dicts).

    Returns:
        The most specific applicable ConflictType.
    """
    has_control = False

    for u, v in zip(path, path[1:], strict=False):
        # Only count as a control edge when v is NOT also reachable via a data
        # or call edge from u.  For-loop iteration defines the loop variable
        # (data edge) AND controls the body (control edge); prefer the data
        # interpretation so that simple for-loop paths aren't mis-classified as
        # CONTROL_DEPENDENCY.
        is_data_or_call = v in sdg.data_edges.get(u, set()) or v in sdg.call_edges.get(u, set())
        if v in sdg.control_edges.get(u, set()) and not is_data_or_call:
            has_control = True

    if has_control:
        return ConflictType.CONTROL_DEPENDENCY

    # Check if any sink node has multiple incoming data edges (confluence)
    if len(path) >= 2:
        sink = path[-1]
        incoming_data = sdg.reverse_data_edges.get(sink, set())
        if len(incoming_data) > 1:
            return ConflictType.CONFLUENCE

    return ConflictType.DATA_FLOW


def _reconstruct_path(
    predecessors: dict[NodeId, NodeId | None],
    target: NodeId,
) -> list[NodeId]:
    """Walk the predecessor map backwards from `target` to reconstruct the path.

    Args:
        predecessors: Map from node to its BFS predecessor.
        target: The end node of the path.

    Returns:
        Ordered list [source, ..., target]. Empty if target is not reachable.
    """
    if target not in predecessors:
        return []

    path: list[NodeId] = []
    current: NodeId | None = target

    while current is not None:
        path.append(current)
        current = predecessors.get(current)

    path.reverse()
    return path
