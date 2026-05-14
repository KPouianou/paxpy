# Architecture

This document describes paxpy's internal architecture for contributors.

## Overview

paxpy detects semantic merge conflicts in Python. It builds a partial System Dependence Graph (SDG) seeded from git diffs, expands through the call graph to a bounded depth, and detects interference between two branches' changesets.

Two runtime dependencies: Python 3.10+ stdlib and GitPython. No external analysis frameworks (no pytype, pyright, mypy, tree-sitter, etc.).

## Module dependency graph

```
types.py              <- everything depends on this
    |
diff_parser.py        <- depends only on types
indexer.py            <- depends only on types
pdg_builder.py        <- depends only on types
direct_detector.py    <- depends only on types
    |
call_resolver.py      <- depends on types, indexer
    |
sdg_builder.py        <- depends on types, pdg_builder, call_resolver
    |
detector.py           <- depends on types, sdg_builder (uses the graph it builds)
neighborhood_detector.py <- depends on types
endpoint_analyzer.py  <- depends on types (operates on AST nodes from conflict paths)
    |
reporter.py           <- depends on types
    |
main.py               <- orchestrates all modules
```

**Import discipline**: a module must never import from a module that depends on it (no cycles). If you need a reverse import, extract the shared concept into `types.py`.

## Module contracts

### diff_parser.py

- **Input**: repo path (`Path`), base branch (`str`), branch_a (`str`), branch_b (`str`)
- **Output**: `DiffResult` containing `seeds_a: list[FunctionLocation]` and `seeds_b: list[FunctionLocation]`
- Parses git diffs for each branch against base. Maps changed line ranges to `FunctionDef` AST nodes. Tags each with branch attribution (`"A"` or `"B"`).

### indexer.py

- **Input**: repo path (`Path`)
- **Output**: `FunctionIndex` -- a wrapper around `dict[str, list[FunctionLocation]]` mapping function names to locations, plus a `dict[Path, ast.Module]` of parsed ASTs with parent pointers added to all nodes.
- Parses every `.py` file once. Builds inverted index for O(1) name lookup.

### call_resolver.py

- **Input**: an `ast.Call` node, the `FunctionIndex`, the current file path
- **Output**: `list[FunctionLocation]` -- resolved targets. Multiple targets when ambiguous (over-approximation). Empty list only for builtins/unresolvable externals.
- Conservative: unresolvable calls expand to all functions with that name.

### pdg_builder.py

- **Input**: an `ast.FunctionDef` node, the file path
- **Output**: `PDG` dataclass containing:
  - `nodes: list[Node]` -- one per statement/expression of interest
  - `data_edges: dict[NodeId, set[NodeId]]` -- def-use chains (forward adjacency)
  - `control_edges: dict[NodeId, set[NodeId]]` -- control dependencies (forward adjacency)
  - The `Node` objects carry their AST node reference, line number, file path, and an optional branch tag (A/B/None).
- Reaching definitions for def-use. Control deps from if/while/for/try conditions to their body statements.

### sdg_builder.py

- **Input**: `DiffResult`, `FunctionIndex`, depth limit (`int`)
- **Output**: `SDG` dataclass containing:
  - `nodes: dict[NodeId, Node]`
  - `data_edges`, `control_edges`, `call_edges` -- each `dict[NodeId, set[NodeId]]`
  - `reverse_data_edges`, `reverse_control_edges`, `reverse_call_edges` -- same structure, reversed
  - `seeds_a: set[NodeId]`, `seeds_b: set[NodeId]`
- Orchestrates: call graph expansion from seeds (depth-limited BFS measuring depth in call-graph edges, not raw edges), PDG construction per function, cross-procedure linking (actual params to formal params, return to call site).

### detector.py

- **Input**: `SDG`
- **Output**: `list[InterferencePath]` -- each containing the path nodes, conflict type (DF/CF/OA/PDG), tier (1 or 2), direction (A to B or B to A).
- **Tier 1**: multi-source BFS through `data_edges + call_edges` only.
- **Tier 2**: multi-source BFS through all edges. New findings not in Tier 1 are control-mediated.
- Both directions (A to B and B to A).
- Approximate chop: forward BFS from source seeds, backward BFS from target seeds on reversed graph, intersect.

### neighborhood_detector.py

- **Input**: `SDG`, radius (`int`)
- **Output**: `list[InterferencePath]`
- Expands a neighborhood of radius N around each seed node and checks for overlap between A-neighborhoods and B-neighborhoods.

### direct_detector.py

- **Input**: `DiffResult` (containing seeds with populated `ast_node` fields)
- **Output**: `list[InterferencePath]` -- paths for OVERRIDE_ASSIGNMENT and signature/body mismatch patterns
- Compares AST nodes from each branch's seeds directly, without SDG traversal.
- Three checks: same-function signature mismatch, cross-function call signature mismatch, override assignment.
- The OA check includes a control-flow exclusivity filter that suppresses false positives where both branches' assignments are in mutually exclusive branches (opposite arms of the same `if/else`). Uses AST walking, not PDG.
- Paths use synthetic NodeIds (not in any SDG) and bypass endpoint analysis in `main.py`.

### endpoint_analyzer.py

- **Input**: `InterferencePath`, the relevant AST nodes
- **Output**: `CompatibilityResult` -- one of: incompatible, suspicious, compatible, unknown. Plus a human-readable explanation string.
- Examines source return type/value patterns and sink operations on received values.

### reporter.py

- **Input**: `list[ConflictReport]` (which combines InterferencePath + CompatibilityResult)
- **Output**: formatted string (CLI), dict (JSON), or SARIF dict.

## Key design decisions

### 1. NodeId format

Use `str` formatted as `"{filepath}:{lineno}:{col_offset}"`. Globally unique, deterministic, human-readable. Always use `make_node_id(filepath, lineno, col_offset)` from `types.py` -- never construct NodeIds by hand.

### 2. Edge representation

Three separate `dict[NodeId, set[NodeId]]` adjacency lists per graph (data, control, call). Never a single merged adjacency list -- tiered detection requires querying subsets of edges.

### 3. Depth semantics

Depth is measured in call-graph edges (procedure boundaries crossed), not raw dependence edges. Intraprocedural chains do not consume depth. `depth=5` means 5 levels of function calls from the seeds.

### 4. Over-approximation policy

When call targets are unresolvable, expand to all functions with that name. We accept false positives over false negatives.

### 5. Branch tagging

Nodes carry an optional `branch: Literal["A", "B"] | None`. Only nodes corresponding to functions directly in the diff are tagged. Intermediate nodes expanded by the call graph are `None`.

### 6. No external analysis dependencies

Everything uses Python's `ast` module. No pytype, pyright, mypy, or runtime tracing.

## Interference pattern taxonomy

The four conflict types come from Santos de Jesus et al. (ICSE 2024) and Horwitz/Prins/Reps (TOPLAS 1989). The `ConflictType` enum in `types.py` encodes them.

### DATA_FLOW (DF)

One side writes a value that the other side reads. Classic producer-consumer interference. The interference path uses only `data_edges` and `call_edges`. This is a Tier 1 finding.

### CONFLUENCE (CF)

Both sides write values that flow into the same downstream computation. Neither reads the other's output directly, but they collide at a shared sink. Detected when backward reachability from A-seeds and B-seeds share a common descendant with multiple incoming data-edge predecessors.

### OVERRIDE_ASSIGNMENT (OA)

Both sides write to the same state element (variable, attribute, dict key) with no intervening base-version write. One silently overwrites the other's assignment. When ambiguous between OA and DF, prefer OA (more specific and actionable).

### CONTROL_DEPENDENCY (PDG)

One side changes a condition that controls whether the other side's code executes. This is control-mediated interference and only emerges at Tier 2 (when `control_edges` are included in the BFS). Tier-2-only findings -- paths that appear with `use_control_edges=True` but not with `use_control_edges=False` -- are classified as CONTROL_DEPENDENCY.

### Classification logic

1. If any consecutive `(u, v)` pair on the path uses a control edge with no parallel data or call edge: **CONTROL_DEPENDENCY**.
2. If the sink has multiple incoming data-edge predecessors traceable to both A-seeds and B-seeds: **CONFLUENCE**.
3. If both source and sink are assignment nodes writing the same name with no intervening base write: **OVERRIDE_ASSIGNMENT**.
4. Default: **DATA_FLOW**.

**Tier 1 findings** (data + call edges only): can be DF, CF, or OA.
**Tier 2 findings** (adds control edges): new findings are CONTROL_DEPENDENCY.

### Patterns outside this taxonomy

The DF/CF/OA/PDG taxonomy was designed for Java. Known Python-specific interference patterns -- decorator-chain rebinding, comprehension scoping, generator exhaustion, context manager exception semantics, and protocol drift from duck typing -- are out of scope for the current implementation.

## Coding conventions

- **Python 3.10+**. Use `match` statements where they clarify intent.
- **Type hints** on all public functions.
- **Dataclasses** for all data containers. No plain dicts crossing module boundaries.
- `from __future__ import annotations` in every file.
- Tests in `tests/` using pytest. Name test files `test_{module}.py`.
- **No classes where a function suffices.** Classes for stateful objects only.
- Adjacency lists use `defaultdict(set)` internally but are typed as `dict[NodeId, set[NodeId]]` in interfaces.
- Never import from a module that is downstream in the dependency graph.

## Testing strategy

- **Unit tests** per module against synthetic AST fragments and small Python snippets.
- **Integration fixtures** in `tests/fixtures/`: small git repos (created programmatically in test setup) with known conflicts.
- Fixture naming: `test_df_*.py`, `test_cf_*.py`, `test_oa_*.py`, `test_pdg_*.py` for the four conflict types.
- Tests must be deterministic and not require network access.
