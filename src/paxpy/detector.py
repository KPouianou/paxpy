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

from paxpy.types import SDG, ConflictType, InterferencePath, NodeId


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
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")


def _infer_conflict_type(path: list[NodeId], sdg: SDG) -> ConflictType:
    """Infer the conflict type from the edge types traversed on a path.

    Checks consecutive (u, v) pairs in the path against sdg.*_edges dicts:
    - If any call edge is traversed and the path is otherwise data-only →
      DATA_FLOW.
    - If any control edge is traversed → CONTROL_DEPENDENCY.
    - If path passes through an assignment that overwrites a value → infer
      OVERRIDE_ASSIGNMENT.
    - Multiple data paths converging → CONFLUENCE.
    Defaults to DATA_FLOW when ambiguous.

    Args:
        path: Ordered list of NodeIds.
        sdg: The SDG (used to query edge dicts).

    Returns:
        The most specific applicable ConflictType.
    """
    raise NotImplementedError("TODO")


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
    raise NotImplementedError("TODO")
