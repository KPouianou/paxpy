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
    """Repository-wide function index for O(1) name lookup."""

    # function name → list of locations (multiple when name is ambiguous)
    index: dict[str, list[FunctionLocation]] = field(default_factory=dict)
    # filepath → parsed AST module (with parent pointers added)
    parsed_asts: dict[Path, ast.Module] = field(default_factory=dict)

    def lookup(self, name: str) -> list[FunctionLocation]:
        return self.index.get(name, [])


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
