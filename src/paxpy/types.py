from __future__ import annotations

import ast
import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# Type alias for node identifiers: "{filepath}:{lineno}:{col_offset}"
NodeId = str


class ConflictType(enum.Enum):
    DATA_FLOW = "DATA_FLOW"
    CONFLUENCE = "CONFLUENCE"
    OVERRIDE_ASSIGNMENT = "OVERRIDE_ASSIGNMENT"
    CONTROL_DEPENDENCY = "CONTROL_DEPENDENCY"


class Compatibility(enum.Enum):
    INCOMPATIBLE = "incompatible"
    SUSPICIOUS = "suspicious"
    COMPATIBLE = "compatible"
    UNKNOWN = "unknown"


@dataclass
class FunctionLocation:
    """A function definition's location in the repository."""

    name: str
    filepath: Path
    lineno: int
    end_lineno: int | None
    # The AST node — not serializable, but available during analysis
    ast_node: ast.FunctionDef | ast.AsyncFunctionDef | None = field(default=None, repr=False)
    branch: Literal["A", "B"] | None = None
    # Line ranges (1-indexed, inclusive) that were directly modified within this function
    modified_ranges: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class Node:
    """A node in the dependence graph."""

    id: NodeId
    filepath: Path
    lineno: int
    col_offset: int
    ast_node: ast.AST | None = field(default=None, repr=False)
    branch: Literal["A", "B"] | None = None
    # Human-readable label for reporting (e.g., "x = foo()" or "if condition:")
    label: str = ""
    # The function this node belongs to
    enclosing_function: str | None = None
    # True when this node's line falls within a directly modified diff hunk
    is_direct_modification: bool = False


def make_node_id(filepath: Path, lineno: int, col_offset: int) -> NodeId:
    """Create a deterministic, globally unique node identifier."""
    return f"{filepath}:{lineno}:{col_offset}"


@dataclass
class PDG:
    """Program Dependence Graph for a single function."""

    function_name: str
    filepath: Path
    nodes: list[Node] = field(default_factory=list)
    data_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    control_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)


@dataclass
class SDG:
    """Partial System Dependence Graph."""

    nodes: dict[NodeId, Node] = field(default_factory=dict)
    data_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    control_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    call_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    # Reverse adjacency lists (built alongside forward lists)
    reverse_data_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    reverse_control_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    reverse_call_edges: dict[NodeId, set[NodeId]] = field(default_factory=dict)
    # Seeds
    seeds_a: set[NodeId] = field(default_factory=set)
    seeds_b: set[NodeId] = field(default_factory=set)


@dataclass
class DiffResult:
    """Result of parsing git diffs for both branches."""

    seeds_a: list[FunctionLocation] = field(default_factory=list)
    seeds_b: list[FunctionLocation] = field(default_factory=list)


@dataclass
class FunctionIndex:
    """Repository-wide function index for O(1) name lookup.

    The index is built lazily: build_index() does a fast line-scan to populate
    name→location entries without parsing ASTs. ASTs are parsed on demand via
    ensure_parsed() when sdg_builder needs to expand into a function's body.
    """

    # function name → list of locations (multiple when name is ambiguous)
    index: dict[str, list[FunctionLocation]] = field(default_factory=dict)
    # filepath → parsed AST module (with parent pointers added); populated lazily
    parsed_asts: dict[Path, ast.Module] = field(default_factory=dict)

    def lookup(self, name: str) -> list[FunctionLocation]:
        return self.index.get(name, [])

    def ensure_parsed(self, filepath: Path) -> ast.Module | None:
        """Return the parsed AST for filepath, parsing it on demand if needed.

        On first call for a given filepath, reads and parses the file, adds
        parent pointers, caches the result, and populates ast_node on any
        FunctionLocation entries for that file that were created by the fast
        line-scan (i.e. have ast_node=None).
        """
        if filepath in self.parsed_asts:
            return self.parsed_asts[filepath]

        try:
            source = filepath.read_text(encoding="utf-8")
        except OSError:
            return None

        try:
            tree = ast.parse(source, filename=str(filepath))
        except SyntaxError:
            return None

        # Add parent pointers (required by pdg_builder and endpoint_analyzer)
        tree._parent = None  # type: ignore[attr-defined]
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                child._parent = node  # type: ignore[attr-defined]

        self.parsed_asts[filepath] = tree

        # Back-fill ast_node on any locations that were created by the fast scan
        for locs in self.index.values():
            for loc in locs:
                if loc.filepath == filepath and loc.ast_node is None:
                    for node in ast.walk(tree):
                        if (
                            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                            and node.name == loc.name
                            and node.lineno == loc.lineno
                        ):
                            loc.ast_node = node
                            if loc.end_lineno is None:
                                loc.end_lineno = node.end_lineno
                            break

        return tree


@dataclass
class InterferencePath:
    """A detected interference path between two changesets."""

    direction: Literal["A_to_B", "B_to_A"]
    conflict_type: ConflictType
    tier: Literal[1, 2]
    # Ordered list of node IDs from source seed to target seed
    path_nodes: list[NodeId] = field(default_factory=list)
    source_node: NodeId = ""
    sink_node: NodeId = ""


@dataclass
class CompatibilityResult:
    """Result of endpoint compatibility analysis."""

    compatibility: Compatibility
    explanation: str = ""


@dataclass
class ConflictReport:
    """Complete report for a single detected conflict."""

    interference: InterferencePath
    compatibility: CompatibilityResult
    # Resolved details for reporting
    source_function: str = ""
    sink_function: str = ""
    source_file: str = ""
    sink_file: str = ""
