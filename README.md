# paxpy

Detects **semantic merge conflicts** in Python repositories. Two branches can each pass their own tests and merge cleanly at the text level, yet still break each other's assumptions at runtime. paxpy finds those conflicts before they reach production.

## How it works

paxpy builds a partial **System Dependence Graph (SDG)** seeded from the git diff of two branches, expands it through the call graph to a bounded depth, and detects interference between the two changesets via **approximate program chopping**.

```
git diff (branch A vs base, branch B vs base)
         ↓
   seed nodes — functions changed by each branch
         ↓
   BFS expansion — call graph up to depth N
         ↓
   SDG — data edges + call edges + control edges
         ↓
   approximate chop — forward from A ∩ backward from B
         ↓
   interference paths → conflict report
```

Detection runs in two tiers:

| Tier | Edges used | Conflict types found |
|------|-----------|----------------------|
| 1 | data + call | DATA_FLOW, CONFLUENCE, OVERRIDE_ASSIGNMENT |
| 2 | data + call + control | CONTROL_DEPENDENCY (new findings only) |

**No external analysis frameworks.** Everything uses Python's `ast` module. Runtime dependencies: Python 3.10+ stdlib and GitPython.

## Installation

```bash
pip install -e .          # core tool
pip install -e ".[bench]" # + benchmark harness (requires rich)
```

Requires Python 3.10+.

## Usage

```bash
paxpy --base main --branch-a feat/payments --branch-b feat/auth --repo /path/to/repo
```

```
Options:
  --repo PATH       Path to git repository (default: current directory)
  --base BRANCH     Common ancestor branch
  --branch-a BRANCH First feature branch
  --branch-b BRANCH Second feature branch
  --depth N         Call-graph expansion depth (default: 5)
  --format          Output format: cli | json | sarif (default: cli)
```

### Example output

```
CONFLICT  DATA_FLOW  tier=1  direction=B_to_A
  path: decode_payload (A) → handle_request (B)
  A changed decode_payload to return a dict.
  B added handle_request which calls .split() on the return value.
```

## Conflict taxonomy

| Type | What it is |
|------|-----------|
| `DATA_FLOW` | A writes a value B reads (producer return type changes, consumer does arithmetic on it) |
| `CONFLUENCE` | A and B both flow into the same downstream sink with incompatible contributions |
| `OVERRIDE_ASSIGNMENT` | B overwrites a variable that A's callee just wrote, discarding the result |
| `CONTROL_DEPENDENCY` | B changes a guard predicate that controls whether A's code executes |

Based on Santos de Jesus et al. ICSE 2024 and Horwitz/Prins/Reps TOPLAS 1989.

## Benchmark

The `paxpy-bench` CLI runs paxpy against a synthetic evaluation harness stored in SQLite.

```bash
paxpy-bench seed                        # populate database (idempotent)
paxpy-bench run --bucket correctness    # run 720 parametric scenarios
paxpy-bench run --bucket adversarial    # run 10 hand-crafted edge cases
paxpy-bench run --bucket performance    # run SDG scalability scenarios
paxpy-bench report --md                 # terminal report + markdown file
paxpy-bench clean --confirm             # reset database
```

### Current results (synthetic benchmark, depth=5)

| Metric | Value |
|--------|-------|
| Precision | **100%** — no false positives, including under name-ambiguity pressure |
| Recall | **65%** |
| F1 | **78.8%** |
| Adversarial | **10/10** (100% F1) |
| Baseline (ICSE 2024, Java) | F1 = 0.50 |

The 35% recall gap has two known causes:

- **CD=7 scenarios (54 FNs):** The conflict is 7 call hops deep; the `depth=5` cap stops expansion before reaching it. Run with `--depth 7` to close this gap.
- **CONTROL_DEPENDENCY at depth ≥ 2 (72 FNs):** Transitive control dependencies through intermediate call chains are not yet detected. A direct guard-on-callee (depth=1) is detected correctly. Fix is in progress in `pdg_builder.py`.

Excluding these two known gaps: **recall = 100%** on all DATA_FLOW, CONFLUENCE, and OVERRIDE_ASSIGNMENT scenarios.

## Development

```bash
# Run tests
pytest

# Lint / format
ruff check src/ tests/
ruff format src/ tests/

# Run integration tests only
pytest tests/integration/ -m integration
```

A `.venv/` is present at the repo root (Python 3.14.3). Activate with `source .venv/bin/activate`.

## Architecture

```
types.py              shared dataclasses and NodeId
diff_parser.py        git diff → seed FunctionLocations
indexer.py            repo → FunctionIndex (name → locations, parsed ASTs)
pdg_builder.py        FunctionDef → PDG (data + control edges)
call_resolver.py      ast.Call × FunctionIndex → resolved targets
sdg_builder.py        DiffResult × FunctionIndex → SDG (BFS expansion)
detector.py           SDG → list[InterferencePath] (approximate chop)
endpoint_analyzer.py  InterferencePath → CompatibilityResult
reporter.py           ConflictReport → CLI / JSON / SARIF
main.py               orchestration
```

## Known limitations

- Python only (no Java, no TypeScript).
- Name-based call resolution: two functions with the same name in different files are treated as the same target (over-approximation). Real imports are not parsed.
- Decorator-chain rebinding, generator exhaustion, comprehension scoping, and protocol drift from duck typing are out of scope for the current taxonomy.
- CONTROL_DEPENDENCY detection is limited to direct guard-on-callee relationships (depth=1). Deeper chains are missed pending a `pdg_builder.py` fix.
