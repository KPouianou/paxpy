# Contributing to paxpy

## Setting up a dev environment

```bash
git clone https://github.com/KPouianou/paxpy.git
cd paxpy
pip install -e ".[dev]"
```

This installs paxpy in editable mode along with pytest and ruff.

## Running tests

```bash
# All tests
pytest tests/

# Single test file
pytest tests/test_types.py

# Integration tests only
pytest tests/integration/ -m integration

# Stop on first failure
pytest -x
```

## Linting and formatting

```bash
# Check for lint errors
ruff check src/ tests/

# Auto-fix lint issues
ruff check --fix src/ tests/

# Format code
ruff format src/ tests/
```

## Code style

See [ARCHITECTURE.md](ARCHITECTURE.md) for full coding conventions. The essentials:

- Python 3.10+. Use `match` statements where they clarify intent.
- Type hints on all public functions.
- Dataclasses for all data containers. No plain dicts crossing module boundaries.
- `from __future__ import annotations` in every file.
- No classes where a function suffices.

## Adding a new conflict type

1. Add the new variant to the `ConflictType` enum in `src/paxpy/types.py`.
2. Implement the classification logic in `src/paxpy/detector.py` (SDG-based) or `src/paxpy/direct_detector.py` (AST-based).
3. Update the classification cascade in the "Classification logic" section of [ARCHITECTURE.md](ARCHITECTURE.md).
4. Add CLI output formatting in `src/paxpy/reporter.py`.
5. Write tests: unit tests in `tests/` and at least one integration test with a synthetic git fixture.

## Adding a new detector

1. Create `src/paxpy/{name}_detector.py`. It should accept an `SDG` and return `list[InterferencePath]`.
2. Register the detector as a `--detector` choice in `src/paxpy/main.py` (see `build_parser()`).
3. Wire it into the detection step in `main()`.
4. Add unit tests in `tests/test_{name}_detector.py`.

## Pull requests

- One logical change per PR.
- Include tests for new functionality.
- Ensure `ruff check` and `pytest` pass before submitting.
- Follow the module dependency graph in ARCHITECTURE.md -- never introduce import cycles.
