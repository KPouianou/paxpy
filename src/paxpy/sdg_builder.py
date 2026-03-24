"""Build a partial System Dependence Graph (SDG) seeded from git diff results.

Starting from the function seeds in a DiffResult, this module expands the
call graph outward via depth-limited BFS (depth measured in call-graph edges,
not raw dependence edges). For each reachable function it builds the
intraprocedural PDG, then stitches the per-function PDGs together with
inter-procedural call edges — linking actual arguments to formal parameters
and return values back to call sites.

Both forward and reverse adjacency lists are maintained in lockstep so that
downstream modules (detector) can do backward BFS without reversing the graph.

Depends on: types, pdg_builder, call_resolver (and transitively indexer).
"""

from __future__ import annotations

from paxpy.types import SDG, DiffResult, FunctionIndex


def build_sdg(
    diff_result: DiffResult,
    index: FunctionIndex,
    depth: int = 5,
) -> SDG:
    """Build a partial SDG from diff seeds up to `depth` call-graph levels.

    Algorithm:
    1. Seed the BFS queue with all functions in diff_result.seeds_a and
       diff_result.seeds_b (depth 0).
    2. For each function at depth d < `depth`: build its PDG, scan it for
       ast.Call nodes, resolve each call via call_resolver, enqueue resolved
       callees at depth d+1.
    3. Merge all per-function PDGs into the SDG node/edge dicts.
    4. Add inter-procedural call edges (caller call-site node → callee entry
       node) and parameter-binding data edges.
    5. Build reverse adjacency lists from forward lists.
    6. Populate sdg.seeds_a and sdg.seeds_b with NodeIds of the seed nodes.

    Args:
        diff_result: Seeds from diff_parser, tagged with branch A or B.
        index: Repository-wide function index from indexer.
        depth: Maximum number of call-graph edges to traverse. Intraprocedural
            chains do not consume depth. Default 5.

    Returns:
        Fully constructed SDG ready for detector.
    """
    raise NotImplementedError("TODO")


def _merge_pdg_into_sdg(sdg: SDG, pdg: object) -> None:
    """Merge a single PDG's nodes and edges into the SDG.

    Adds PDG nodes to sdg.nodes and merges data/control edges into
    sdg.data_edges / sdg.control_edges. Also updates the corresponding reverse
    adjacency lists.

    Args:
        sdg: The SDG being built (mutated in-place).
        pdg: A PDG instance from pdg_builder.
    """
    raise NotImplementedError("TODO")


def _add_call_edge(sdg: SDG, caller_node_id: str, callee_node_id: str) -> None:
    """Record a call edge (and its reverse) between two nodes in the SDG.

    Both sdg.call_edges and sdg.reverse_call_edges are updated.

    Args:
        sdg: The SDG being built (mutated in-place).
        caller_node_id: NodeId of the call-site node in the caller.
        callee_node_id: NodeId of the entry node in the callee.
    """
    raise NotImplementedError("TODO")


def _build_reverse_edges(sdg: SDG) -> None:
    """Populate reverse_* adjacency dicts from the forward adjacency dicts.

    Rebuilds reverse_data_edges, reverse_control_edges, and reverse_call_edges
    from scratch. Called once after all forward edges are finalized.

    Args:
        sdg: The SDG whose reverse dicts are populated in-place.
    """
    raise NotImplementedError("TODO")
