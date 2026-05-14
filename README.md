# paxpy

**Your merge passed CI. Both branches had green tests. Git merged them without a conflict. And it still broke production.**

paxpy catches the bugs that git merge, your tests, and your code review all miss -- *semantic* merge conflicts where two branches make independently correct changes that break each other's assumptions when combined.

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![CI](https://github.com/KPouianou/paxpy/actions/workflows/ci.yml/badge.svg)](https://github.com/KPouianou/paxpy/actions/workflows/ci.yml)

## A real example

This happened in [frappe/frappe](https://github.com/frappe/frappe) (commit `4e7be5b3`):

- **Branch A** replaced the sandbox's SQL wrapper: `sql = read_sql` (SELECT-only) became `sql = frappe.db.sql` (unrestricted)
- **Branch B** added transaction support to the same sandbox: `commit = frappe.db.commit`
- **Git merged them cleanly.** No textual conflict.
- **Result:** server scripts could now execute arbitrary SQL *and* commit it. A security regression neither developer intended.

paxpy detects this **before the merge** by analyzing data flow between the two branches' changes:

```
paxpy v0.4.0: 1 conflict(s) detected.

────────────────────────────────────────────────────────────
[OA]   #1  Override Assignment  A→B  [Tier 1]  INCOMPATIBLE
  Source : frappe/utils/safe_exec.py  get_safe_globals
  Sink   : frappe/utils/safe_exec.py  get_safe_globals
  Note   : Both branches assign different values to 'sql'.
```

Tested on 15,000 merge commits across 137 open-source Python projects. Found 14 confirmed semantic conflicts -- including security regressions, silent data corruption, and runtime crashes that shipped to production.

## Quick start

```bash
# Install
git clone https://github.com/KPouianou/paxpy.git && cd paxpy && pip install -e .

# Run on your repo
paxpy --base main --branch-a feat/payments --branch-b feat/auth
```

That's it. No configuration files, no setup, no external services. paxpy uses Python's built-in `ast` module to analyze your code statically.

**Exit codes:** 0 = clean, 1 = conflicts found, 2 = error. Designed for CI.

## Use in CI

Add to your GitHub Actions workflow to check every PR:

```yaml
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

Supports three output formats: human-readable CLI (default), JSON, and [SARIF](https://sarifweb.azurewebsites.net/) for GitHub Code Scanning integration.

## What it catches

paxpy detects four types of semantic conflicts:

| Type | What happened | Example |
|------|--------------|---------|
| **Data Flow** | A changes what a function returns, B uses the old return type | A returns `dict`, B calls `.split()` on it |
| **Override Assignment** | Both branches write different values to the same variable | A sets `timeout = 30`, B sets `timeout = 60` |
| **Confluence** | Both branches feed incompatible values into the same computation | A and B both modify constructor parameters that interact |
| **Control Dependency** | A changes a condition that controls whether B's code runs | A tightens `if x > 0` to `if x > 10`, gating B's new code |

## How it works

paxpy builds a partial [System Dependence Graph](https://en.wikipedia.org/wiki/System_dependence_graph) from the git diff, traces data flow across function boundaries, and checks whether the two branches' modifications interfere. No type checkers, no runtime tracing -- pure AST analysis.

For the full architecture and design decisions, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Installation

```bash
# From source (recommended for now)
git clone https://github.com/KPouianou/paxpy.git
cd paxpy
pip install -e .
```

Requires Python 3.10+ and Git. Only runtime dependency: [GitPython](https://github.com/gitpython-developers/GitPython).

## Advanced usage

Most users only need `--base`, `--branch-a`, and `--branch-b`. For tuning:

| Flag | Default | What it does |
|------|---------|-------------|
| `--depth N` | 3 | How many function calls deep to analyze |
| `--detector neighborhood\|chop` | neighborhood | Detection algorithm ([details](ARCHITECTURE.md#detector)) |
| `--radius N` | 1 | How many call boundaries the neighborhood check spans |
| `--format cli\|json\|sarif` | cli | Output format |

## Known limitations

- **Python only.** No Java, TypeScript, or other languages.
- **Name-based call resolution.** Functions with the same name in different modules may be treated as the same target. Import-path resolution is not yet implemented.
- **Experimental.** paxpy is a research tool under active development. False positives are expected. Use it as a signal, not a gate.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

Apache 2.0. See [LICENSE](LICENSE).
