# paxpy

A static analysis tool that detects semantic merge conflicts in Python codebases.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/KPouianou/paxpy/actions/workflows/ci.yml/badge.svg)](https://github.com/KPouianou/paxpy/actions/workflows/ci.yml)

## What this is

A **semantic merge conflict** occurs when two branches each make valid changes that git merges without complaint, but the combined result is incorrect -- a function returns a different type than a new caller expects, two branches assign conflicting values to the same config key, or a parameter gets renamed on one side while the other side adds code that uses the old name.

These bugs are hard to catch because each branch's tests pass individually and git reports no merge conflict. They surface at runtime, often in production.

paxpy attempts to detect these conflicts statically by analyzing the data flow and control dependencies between two branches' changes using Python's `ast` module.

## Status

**Experimental / research stage.** paxpy is under active development and has significant limitations:

- High false-positive rate (~25-35% of merge commits get flagged, but very few flags are real conflicts)
- Name-based call resolution means functions with the same name in different modules are conflated
- No import-path resolution, no type inference, no runtime analysis
- Python only

In an evaluation across 15,000 merge commits from 137 open-source Python projects, paxpy detected 13 out of 14 manually confirmed semantic conflicts. The confirmed conflicts include security regressions, runtime crashes (TypeError/NameError), and silent data corruption. However, precision remains low -- the tool is best used as a triage filter to surface candidates for human review, not as an automated gate.

## An example from the evaluation

In [frappe/frappe](https://github.com/frappe/frappe) (commit `4e7be5b3`), two branches independently modified `get_safe_globals()` in the server-script sandbox:

- One branch replaced `sql = read_sql` (a SELECT-only wrapper) with `sql = frappe.db.sql` (unrestricted access)
- The other branch added `commit = frappe.db.commit` to the same function

Git merged both changes cleanly. The result: server scripts could execute arbitrary SQL and commit it -- a privilege escalation that neither developer intended. paxpy flags this as an Override Assignment conflict on the `sql` key.

## Installation

```bash
git clone https://github.com/KPouianou/paxpy.git
cd paxpy
pip install -e .
```

Requires Python 3.10+ and Git. Only runtime dependency: [GitPython](https://github.com/gitpython-developers/GitPython).

## Usage

```bash
paxpy --base main --branch-a feature-x --branch-b feature-y --repo /path/to/repo
```

paxpy compares two branches against their common ancestor and reports any detected interference between their changes.

**Exit codes:** 0 = no conflicts detected, 1 = conflicts detected, 2 = error.

**Output formats:** Human-readable CLI (default), JSON (`--format json`), or SARIF (`--format sarif`) for integration with GitHub Code Scanning.

### CI integration

```yaml
# GitHub Actions example
- name: Check for semantic merge conflicts
  run: |
    pip install paxpy
    paxpy --base main \
          --branch-a ${{ github.head_ref }} \
          --branch-b main \
          --format sarif \
          > paxpy-results.sarif

- name: Upload results
  if: always()
  uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: paxpy-results.sarif
```

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--base BRANCH` | (required) | Common ancestor branch |
| `--branch-a BRANCH` | (required) | First feature branch |
| `--branch-b BRANCH` | (required) | Second feature branch |
| `--repo PATH` | `.` | Path to the git repository |
| `--format cli\|json\|sarif` | `cli` | Output format |
| `--depth N` | `3` | Call-graph expansion depth |
| `--detector neighborhood\|chop` | `neighborhood` | Detection algorithm (see [ARCHITECTURE.md](ARCHITECTURE.md)) |
| `--radius N` | `1` | Neighborhood detector radius |

## What it detects

paxpy looks for four interference patterns between branches, based on the taxonomy from [Santos de Jesus et al. (ICSE 2024)](https://doi.org/10.1145/3597503.3639074) and [Horwitz, Prins & Reps (TOPLAS 1989)](https://doi.org/10.1145/73141.73153):

| Pattern | Description |
|---------|-------------|
| **Data Flow** | One branch changes a value that the other branch reads (e.g., return type changed, but a caller still assumes the old type) |
| **Override Assignment** | Both branches assign different values to the same variable in the same function |
| **Confluence** | Both branches write values that flow into a shared downstream computation |
| **Control Dependency** | One branch changes a predicate that determines whether the other branch's code executes |

## How it works

paxpy builds a partial [System Dependence Graph](https://en.wikipedia.org/wiki/System_dependence_graph) (SDG) seeded from the git diff of each branch against the merge base. It expands through the call graph to a bounded depth, then checks for interference between the two changesets using either neighborhood overlap or approximate program chopping.

A separate direct-comparison detector handles patterns that don't produce SDG interference paths, such as both branches assigning different values to the same variable (Override Assignment) or one branch changing a function's signature while the other branch's code calls it with the old signature.

The analysis uses only Python's built-in `ast` module. No external type checkers, analysis frameworks, or runtime instrumentation.

For implementation details, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Limitations and known issues

- **High false-positive rate.** Many flagged merges are not actual conflicts. The tool over-approximates by design -- it prefers false positives over missed real conflicts.
- **Python only.** No support for other languages.
- **Name-based call resolution.** `process()` in `module_a.py` and `process()` in `module_b.py` are treated as the same function. Import paths are not resolved.
- **No type inference.** The analysis operates on syntactic structure, not types. It cannot determine that a value's type changed.
- **Large repositories are slow.** Indexing all `.py` files in a monorepo can take minutes. Repos like saltstack/salt may time out at the default 300s limit.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

Apache 2.0. See [LICENSE](LICENSE).
