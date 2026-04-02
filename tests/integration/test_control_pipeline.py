"""Integration tests — CONTROL_DEPENDENCY conflict type.

Scenario
--------
Base has a function check(x) that conditionally calls action() when x > 0,
and a separate action() that returns a fixed value.

Branch A changes action() to return a dict instead of an int.
Branch B changes the predicate in check() from `x > 0` to `x > 10`, making
the condition stricter.

The interference is control-mediated: Branch B changes *when* action is called
(the predicate), while Branch A changes *what* action returns. Any caller
relying on the result of check() may be affected by the combined change.

This conflict is only detectable at Tier 2 (requires control edges).

Expected results
----------------
- At least one InterferencePath detected at tier 2
- ConflictType: CONTROL_DEPENDENCY
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
def action():
    return 42


def check(x):
    if x > 0:
        result = action()
        return result
    return 0
"""

# Branch A: action now returns a dict
BRANCH_A_SOURCE = """\
def action():
    return {"code": 42, "status": "ok"}


def check(x):
    if x > 0:
        result = action()
        return result
    return 0
"""

# Branch B: check tightens the guard condition
BRANCH_B_SOURCE = """\
def action():
    return 42


def check(x):
    if x > 10:
        result = action()
        return result
    return 0
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_control_conflict_detected(git_repo, tmp_path):
    """Full pipeline finds at least one interference path for the control scenario."""
    write_py(tmp_path, "module.py", BASE_SOURCE)
    commit_all(git_repo, "base: add action and check")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_SOURCE)
    commit_all(git_repo, "A: action returns dict")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_SOURCE)
    commit_all(git_repo, "B: check tightens guard")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    assert diff.seeds_a, "branch-a should have changed functions"
    assert diff.seeds_b, "branch-b should have changed functions"

    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths, "expected at least one interference path"


@pytest.mark.integration
def test_control_seeds_attributed(git_repo, tmp_path):
    """action is seeded as A; check is seeded as B."""
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
    assert any(s.name == "action" for s in diff.seeds_a)
    assert any(s.name == "check" for s in diff.seeds_b)


@pytest.mark.integration
def test_control_tier2_path_exists(git_repo, tmp_path):
    """There should be at least one tier-2 path (control + call edges)."""
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

    tier2 = [p for p in paths if p.tier == 2]
    assert tier2, f"expected at least one tier-2 path; tiers found: {[p.tier for p in paths]}"


@pytest.mark.integration
def test_control_conflict_type_control_dependency(git_repo, tmp_path):
    """Tier-2-only paths should be classified as CONTROL_DEPENDENCY."""
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

    # Tier-2-only paths are those that appear at tier 2 but not tier 1
    tier1_pairs = {(p.source_node, p.sink_node) for p in paths if p.tier == 1}
    tier2_only = [
        p for p in paths if p.tier == 2 and (p.source_node, p.sink_node) not in tier1_pairs
    ]

    if tier2_only:
        types = {p.conflict_type for p in tier2_only}
        assert ConflictType.CONTROL_DEPENDENCY in types, (
            f"expected CONTROL_DEPENDENCY in tier-2-only paths, got: {types}"
        )


@pytest.mark.integration
def test_control_direction_b_to_a(git_repo, tmp_path):
    """At least one path should be B_to_A: check (B) calls action (A)."""
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
