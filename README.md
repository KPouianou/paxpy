# paxpy

Detect semantic merge conflicts in Python repositories before they reach production.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/KPouianou/paxpy/actions/workflows/ci.yml/badge.svg)](https://github.com/KPouianou/paxpy/actions/workflows/ci.yml)

## The problem

Two branches can each pass their own tests and merge cleanly at the text level, yet still break each other's assumptions at runtime:

- Branch A changes `get_safe_globals()` to expose `frappe.db.sql` (unrestricted SQL access)
- Branch B adds `commit = frappe.db.commit` to the same function, assuming the existing `read_sql` wrapper is still in place
- Git merges them without conflict. The result: server scripts can now execute arbitrary SQL **and** commit it -- a security regression neither developer intended.

This is a **semantic merge conflict**. Git can't see it because there's no textual overlap. paxpy finds these conflicts statically, before the merge, by analyzing the program's data flow and control dependencies.

## Installation

```bash
git clone https://github.com/KPouianou/paxpy.git
cd paxpy
pip install -e .
```

Requires Python 3.10+ and Git. The only runtime dependency is [GitPython](https://github.com/gitpython-developers/GitPython).

## Quick start

```bash
paxpy --base main --branch-a feat/payments --branch-b feat/auth --repo /path/to/repo
```

Example output:

```
paxpy v0.4.0: 2 conflict(s) detected.

────────────────────────────────────────────────────────────
[!!]   #1  Data Flow  B→A  [Tier 1]  INCOMPATIBLE
  Source : src/payments/processor.py  process_payment
  Sink   : src/auth/validator.py  validate_token
  Path   : 3 node(s)
  Note   : Source returns dict but sink calls .split() on the value.

────────────────────────────────────────────────────────────
[OA]   #2  Override Assignment  A→B  [Tier 1]  INCOMPATIBLE
  Source : src/config.py  setup
  Sink   : src/config.py  setup
  Note   : Both branches assign different values to 'timeout'.
```

Exit codes: **0** = no conflicts, **1** = conflicts detected, **2** = error.

## How it works

paxpy builds a partial **System Dependence Graph (SDG)** seeded from the git diff of two branches against their common ancestor. It expands through the call graph to a bounded depth, then detects interference between the two changesets. The analysis uses only Python's built-in `ast` module -- no external type checkers or analysis frameworks.

For a detailed walkthrough of the architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Detectors

paxpy ships with two detection algorithms:

| Detector | Flag | Best for |
|----------|------|----------|
| **neighborhood** (default) | `--detector neighborhood` | Lower false-positive rate. Checks whether modifications from both branches overlap within a configurable radius in the SDG. |
| **chop** | `--detector chop` | Higher recall. Performs approximate program chopping (forward from A, backward from B, intersect). May produce more false positives on large codebases. |

Use `--radius N` to tune the neighborhood detector (default: 1). Use `--max-call-hops N` to limit how many call boundaries a chop path may cross (default: 2).

## Output formats

| Format | Flag | Description |
|--------|------|-------------|
| CLI | `--format cli` | Human-readable terminal output (default) |
| JSON | `--format json` | Machine-readable JSON array |
| SARIF | `--format sarif` | Static Analysis Results Interchange Format, for integration with GitHub Code Scanning and other tools |

## CLI reference

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--repo PATH` | No | `.` | Path to the git repository |
| `--base BRANCH` | Yes | -- | Common ancestor branch (e.g. `main`) |
| `--branch-a BRANCH` | Yes | -- | First feature branch |
| `--branch-b BRANCH` | Yes | -- | Second feature branch |
| `--depth N` | No | `3` | Call-graph expansion depth (number of call boundaries to cross) |
| `--max-call-hops N` | No | `2` | Suppress paths crossing more than N call boundaries (chop detector) |
| `--format FORMAT` | No | `cli` | Output format: `cli`, `json`, or `sarif` |
| `--detector ALGO` | No | `neighborhood` | Detection algorithm: `neighborhood` or `chop` |
| `--radius N` | No | `1` | Neighborhood radius (neighborhood detector only) |
| `--no-mod-relevance-filter` | No | off | Disable the modification-relevance filter for SDG paths |

## Conflict types

paxpy detects four types of semantic merge conflicts, based on the taxonomy from Santos de Jesus et al. (ICSE 2024) and Horwitz/Prins/Reps (TOPLAS 1989):

| Type | Description |
|------|-------------|
| `DATA_FLOW` | One branch writes a value that the other branch reads. Classic producer-consumer interference. |
| `CONFLUENCE` | Both branches write values that flow into the same downstream computation with incompatible contributions. |
| `OVERRIDE_ASSIGNMENT` | One branch overwrites a variable that the other branch's code just wrote, silently discarding the result. |
| `CONTROL_DEPENDENCY` | One branch changes a guard predicate (if/while/for condition) that controls whether the other branch's code executes. |

## CI integration

paxpy is designed to run in CI pipelines that check pairs of feature branches before merging.

```yaml
# GitHub Actions example
- name: Check for semantic merge conflicts
  run: |
    pip install paxpy
    paxpy --base main \
          --branch-a ${{ github.head_ref }} \
          --branch-b target-branch \
          --format sarif \
          > paxpy-results.sarif

- name: Upload SARIF results
  if: always()
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: paxpy-results.sarif
```

Exit codes:
- **0** -- no conflicts detected
- **1** -- conflicts detected
- **2** -- runtime error (bad arguments, missing repo, etc.)

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/

# Run integration tests only
pytest tests/integration/ -m integration

# Lint and format
ruff check src/ tests/
ruff format src/ tests/
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for more details.

## Known limitations

- Python only (no Java, no TypeScript).
- Name-based call resolution: functions with the same name in different files are treated as the same target (over-approximation). Import paths are not resolved.
- CONTROL_DEPENDENCY detection is limited to direct guard-on-callee relationships. Deeper transitive control chains are not yet detected.
- Decorator chains, generator exhaustion, comprehension scoping, and protocol drift from duck typing are out of scope.

## License

Apache 2.0. See [LICENSE](LICENSE) for the full text.
