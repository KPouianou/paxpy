# paxpy — Agent Guide

This file is read automatically by every Claude Code agent working on this project. It encodes the architectural constraints that all modules must respect so that independently-implemented modules produce compatible code.

---

## Quick Reference

```bash
# Install (dev)
pip install -e .
pip install ruff pytest        # or: pip install -e ".[dev]"

# Run tests
pytest                         # all tests
pytest tests/test_types.py     # single file
pytest -x                      # stop on first failure

# Lint / format
ruff check src/ tests/         # lint
ruff format src/ tests/        # format
ruff check --fix src/ tests/   # auto-fix lint issues

# Run CLI
paxpy --base main --branch-a feat/a --branch-b feat/b --repo /path/to/repo
```

A `.venv/` is present at the repo root (Python 3.14.3). Activate with `source .venv/bin/activate` or prefix commands with `.venv/bin/`.

## Branch & PR Strategy

Each module is implemented on its own branch and merged via PR:

| Branch | Module(s) |
|--------|-----------|
| `impl/diff-parser` | `diff_parser.py` + `test_diff_parser.py` |
| `impl/indexer` | `indexer.py` + `test_indexer.py` |
| `impl/pdg-builder` | `pdg_builder.py` + `test_pdg_builder.py` |
| `impl/call-resolver` | `call_resolver.py` + `test_call_resolver.py` |
| `impl/sdg-builder` | `sdg_builder.py` + `test_sdg_builder.py` |
| `impl/detector` | `detector.py` + `test_detector.py` |
| `impl/endpoint-analyzer` | `endpoint_analyzer.py` + `test_endpoint_analyzer.py` |
| `impl/reporter` | `reporter.py` + `test_reporter.py` |
| `impl/wire-up` | `main.py` final wiring + integration tests |

Branch off `main`. PRs merge into `main`. Tier-1 branches (`diff-parser`, `indexer`, `pdg-builder`) can be opened in parallel. `impl/call-resolver` branches off after `impl/indexer` is merged. `impl/sdg-builder` branches off after all tier-1 PRs are merged.

---

## Project Overview

paxpy detects **semantic merge conflicts** in Python. It builds a partial System Dependence Graph (SDG) seeded from git diffs, expands through the call graph to a bounded depth, and detects interference between two branches' changesets via **approximate program chopping**.

Two runtime dependencies: Python 3.10+ stdlib and GitPython. **No external analysis frameworks** (no pytype, pyright, mypy, tree-sitter, etc.).

---

## Architecture — Module Dependency Graph

```
types.py          ← everything depends on this
    ↑
diff_parser.py    ← depends only on types
indexer.py        ← depends only on types
pdg_builder.py    ← depends only on types
    ↑
call_resolver.py  ← depends on types, indexer
    ↑
sdg_builder.py    ← depends on types, pdg_builder, call_resolver
    ↑
detector.py       ← depends on types, sdg_builder (uses the graph it builds)
endpoint_analyzer.py ← depends on types (operates on AST nodes from conflict paths)
    ↑
reporter.py       ← depends on types
    ↑
main.py           ← orchestrates all modules
```

**Import discipline**: a module must never import from a module that depends on it (no cycles). If you think you need a reverse import, extract the shared concept into types.py.

---

## Module Contracts — What Each Module Takes and Returns

### diff_parser.py

- **Input**: repo path (`Path`), base branch (`str`), branch_a (`str`), branch_b (`str`)
- **Output**: `DiffResult` containing `seeds_a: list[FunctionLocation]` and `seeds_b: list[FunctionLocation]`
- Parses git diffs for each branch against base. Maps changed line ranges to FunctionDef AST nodes. Tags each with branch attribution (`"A"` or `"B"`).

### indexer.py

- **Input**: repo path (`Path`)
- **Output**: `FunctionIndex` — a wrapper around `dict[str, list[FunctionLocation]]` mapping function names to locations, plus a `dict[Path, ast.Module]` of parsed ASTs **with parent pointers added to all nodes**.
- Parses every `.py` file once. Builds inverted index for O(1) name lookup.

### call_resolver.py

- **Input**: an `ast.Call` node, the `FunctionIndex`, the current file path
- **Output**: `list[FunctionLocation]` — resolved targets. Multiple targets when ambiguous (over-approximation). Empty list only for builtins/unresolvable externals.
- Conservative: unresolvable calls expand to all functions with that name.

### pdg_builder.py

- **Input**: an `ast.FunctionDef` node, the file path
- **Output**: `PDG` dataclass containing:
  - `nodes: list[Node]` — one per statement/expression of interest
  - `data_edges: dict[NodeId, set[NodeId]]` — def-use chains (forward adjacency)
  - `control_edges: dict[NodeId, set[NodeId]]` — control dependencies (forward adjacency)
  - The `Node` objects carry their AST node reference, line number, file path, and an optional branch tag (A/B/None).
- Reaching definitions for def-use. Control deps from if/while/for/try conditions to their body statements.

### sdg_builder.py

- **Input**: `DiffResult`, `FunctionIndex`, depth limit (`int`)
- **Output**: `SDG` dataclass containing:
  - `nodes: dict[NodeId, Node]`
  - `data_edges: dict[NodeId, set[NodeId]]`
  - `control_edges: dict[NodeId, set[NodeId]]`
  - `call_edges: dict[NodeId, set[NodeId]]`
  - `reverse_data_edges`, `reverse_control_edges`, `reverse_call_edges` — same structure, reversed
  - `seeds_a: set[NodeId]`, `seeds_b: set[NodeId]`
- Orchestrates: call graph expansion from seeds (depth-limited BFS measuring depth in **call-graph edges**, not raw edges), PDG construction per function, cross-procedure linking (actual params → formal params, return → call site).

### detector.py

- **Input**: `SDG`
- **Output**: `list[InterferencePath]` — each containing the path nodes, conflict type (DF/CF/OA/PDG), tier (1 or 2), direction (A→B or B→A).
- **Tier 1**: multi-source BFS through `data_edges ∪ call_edges` only.
- **Tier 2**: multi-source BFS through all edges. New findings not in Tier 1 are control-mediated.
- Both directions (A→B and B→A).
- Approximate chop: forward BFS from source seeds, backward BFS from target seeds on reversed graph, intersect.

### endpoint_analyzer.py

- **Input**: `InterferencePath`, the relevant AST nodes
- **Output**: `CompatibilityResult` — one of: incompatible, suspicious, compatible, unknown. Plus a human-readable explanation string.
- Examines source return type/value patterns and sink operations on received values.

### reporter.py

- **Input**: `list[ConflictReport]` (which combines InterferencePath + CompatibilityResult)
- **Output**: formatted string (CLI), dict (JSON), or SARIF dict.

---

## Key Design Decisions (for all agents)

### 1. NodeId format

Use `str` formatted as `"{filepath}:{lineno}:{col_offset}"`. Globally unique, deterministic, human-readable. Always use `make_node_id(filepath, lineno, col_offset)` from `types.py` — never construct NodeIds by hand.

### 2. Edge representation

Three separate `dict[NodeId, set[NodeId]]` adjacency lists per graph (data, control, call). **Never a single merged adjacency list** — tiered detection requires querying subsets of edges.

### 3. Depth semantics

Depth is measured in **call-graph edges** (procedure boundaries crossed), not raw dependence edges. Intraprocedural chains don't consume depth. `depth=5` means 5 levels of function calls from the seeds.

### 4. Over-approximation policy

When call targets are unresolvable, expand to **all functions with that name**. We accept false positives, not false negatives.

### 5. Branch tagging

Nodes carry an optional `branch: Literal["A", "B"] | None`. Only nodes corresponding to functions **directly in the diff** are tagged. Intermediate nodes expanded by the call graph are `None`.

### 6. No external analysis deps

Everything uses Python's `ast` module. No pytype, pyright, mypy, or runtime tracing.

---

## Coding Conventions

- **Python 3.10+**. Use `match` statements where they clarify intent.
- **Type hints** on all public functions.
- **Dataclasses** for all data containers. No plain dicts crossing module boundaries.
- `from __future__ import annotations` in **every file**.
- Tests in `tests/` using pytest. Name test files `test_{module}.py`.
- **No classes where a function suffices.** Classes for stateful objects only.
- Adjacency lists use `defaultdict(set)` internally but are typed as `dict[NodeId, set[NodeId]]` in interfaces.
- Never import from a module that is downstream in the dependency graph.

---

## Testing Strategy

- **Unit tests** per module against synthetic AST fragments / small Python snippets.
- **Integration fixtures** in `tests/fixtures/`: small git repos (created programmatically in test setup) with known conflicts.
- Fixture naming: `test_df_*.py`, `test_cf_*.py`, `test_oa_*.py`, `test_pdg_*.py` for the four conflict types.
- Tests must be deterministic and not require network access.

---

## Module Implementation Order

Modules can be implemented in parallel within the same tier:

| Tier | Modules | Blocking on |
|------|---------|-------------|
| 0 | `types.py` | — |
| 1 (parallel) | `diff_parser.py`, `indexer.py`, `pdg_builder.py` | types |
| 1 (parallel) | `call_resolver.py` | types, indexer |
| 2 | `sdg_builder.py` | all tier 1 |
| 3 | `detector.py`, `endpoint_analyzer.py` | sdg_builder |
| 4 | `reporter.py`, `main.py` wiring | all above |

When implementing a module, read this file first, then read `types.py`, then read the target module's docstring and signatures before writing any logic.
