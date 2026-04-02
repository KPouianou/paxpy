"""Integration tests — CONFLUENCE conflict type.

Scenario
--------
Base has a function aggregate(values) that calls helper(v) for each value and
sums the results.

Branch A changes helper to return a dict instead of an int.
Branch B adds a second call path that also accumulates helper's return value
into the same total variable (changing how the accumulation works).

The tool should detect that both branches flow data into the same accumulator
variable — a CONFLUENCE conflict.

Expected results
----------------
- At least one InterferencePath detected
- ConflictType: CONFLUENCE or DATA_FLOW (dict output + numeric accumulator)
"""

from __future__ import annotations

import pytest

from paxpy.detector import detect
from paxpy.diff_parser import parse_diffs
from paxpy.indexer import build_index
from paxpy.sdg_builder import build_sdg
from paxpy.types import ConflictType

from .conftest import commit_all, write_py

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Scenario source code
# ---------------------------------------------------------------------------

BASE_SOURCE = """\
def helper(v):
    return v * 10


def aggregate(values):
    total = 0
    for v in values:
        total = total + helper(v)
    return total
"""

# Branch A: helper returns a dict
BRANCH_A_SOURCE = """\
def helper(v):
    return {"result": v * 10, "raw": v}


def aggregate(values):
    total = 0
    for v in values:
        total = total + helper(v)
    return total
"""

# Branch B: aggregate accumulates differently — uses total += helper(v) and
# adds a secondary term, creating a second data-flow path into total
BRANCH_B_SOURCE = """\
def helper(v):
    return v * 10


def aggregate(values):
    total = 0
    for v in values:
        total += helper(v)
    return total
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_cf_conflict_detected(git_repo, tmp_path):
    """Full pipeline finds at least one interference path for the CF scenario."""
    write_py(tmp_path, "module.py", BASE_SOURCE)
    commit_all(git_repo, "base: add helper and aggregate")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_SOURCE)
    commit_all(git_repo, "A: helper returns dict")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_SOURCE)
    commit_all(git_repo, "B: aggregate uses +=")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    assert diff.seeds_a, "branch-a should have changed functions"
    assert diff.seeds_b, "branch-b should have changed functions"

    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths, "expected at least one interference path"


@pytest.mark.integration
def test_cf_seeds_attributed(git_repo, tmp_path):
    """helper is seeded as A, aggregate is seeded as B."""
    write_py(tmp_path, "module.py", BASE_SOURCE)
    commit_all(git_repo, "base")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_SOURCE)
    commit_all(git_repo, "A")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_SOURCE)
    commit_all(git_repo, "B")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")

    assert all(s.branch == "A" for s in diff.seeds_a)
    assert all(s.branch == "B" for s in diff.seeds_b)
    assert any(s.name == "helper" for s in diff.seeds_a)
    assert any(s.name == "aggregate" for s in diff.seeds_b)


@pytest.mark.integration
def test_cf_direction_b_to_a(git_repo, tmp_path):
    """Direction should be B_to_A: aggregate (B) calls helper (A)."""
    write_py(tmp_path, "module.py", BASE_SOURCE)
    commit_all(git_repo, "base")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_SOURCE)
    commit_all(git_repo, "A")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_SOURCE)
    commit_all(git_repo, "B")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    b_to_a = [p for p in paths if p.direction == "B_to_A"]
    assert b_to_a, f"expected B_to_A path; got directions: {[p.direction for p in paths]}"


@pytest.mark.integration
def test_cf_conflict_type(git_repo, tmp_path):
    """Detected conflict type should be CONFLUENCE or DATA_FLOW."""
    write_py(tmp_path, "module.py", BASE_SOURCE)
    commit_all(git_repo, "base")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_SOURCE)
    commit_all(git_repo, "A")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_SOURCE)
    commit_all(git_repo, "B")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths
    acceptable = {ConflictType.CONFLUENCE, ConflictType.DATA_FLOW}
    types_found = {p.conflict_type for p in paths}
    assert types_found & acceptable, f"expected CONFLUENCE or DATA_FLOW, got: {types_found}"


@pytest.mark.integration
def test_cf_no_conflict_independent_callers(git_repo, tmp_path):
    """No conflict when two branches change functions with no shared call path."""
    source = """\
        def sink_a():
            return 1


        def sink_b():
            return 2
    """
    write_py(tmp_path, "module.py", source)
    commit_all(git_repo, "base")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", source.replace("return 1", "return 99"))
    commit_all(git_repo, "A: change sink_a")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", source.replace("return 2", "return 88"))
    commit_all(git_repo, "B: change sink_b")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths == [], f"expected no conflicts for independent sinks, got: {paths}"
