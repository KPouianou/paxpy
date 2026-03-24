"""Integration tests — OVERRIDE_ASSIGNMENT conflict type.

Scenario
--------
Base has a function pipeline() that calls transform(x) and stores the result
in a shared variable `config`, then calls apply(config).

Branch A changes transform() to return a new-style object (a dict).
Branch B changes pipeline() to also assign to `config` later with an integer,
overriding the value that flows from transform().

The two branches interfere because A's new transform output feeds into a slot
that B then overrides — an override-assignment conflict on the data-flow path.

Expected results
----------------
- At least one InterferencePath detected
- Direction: B_to_A (pipeline in B overrides result from transform in A)
"""

from __future__ import annotations

import pytest

from paxpy.detector import detect
from paxpy.diff_parser import parse_diffs
from paxpy.indexer import build_index
from paxpy.sdg_builder import build_sdg

from .conftest import commit_all, write_py

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Scenario source code
# ---------------------------------------------------------------------------

BASE_SOURCE = """\
def transform(x):
    return x + 1


def apply(config):
    return config * 2


def pipeline(x):
    config = transform(x)
    return apply(config)
"""

# Branch A: transform now returns a dict
BRANCH_A_SOURCE = """\
def transform(x):
    return {"value": x + 1, "scale": 1}


def apply(config):
    return config * 2


def pipeline(x):
    config = transform(x)
    return apply(config)
"""

# Branch B: pipeline reassigns config after the call, overriding transform's result
BRANCH_B_SOURCE = """\
def transform(x):
    return x + 1


def apply(config):
    return config * 2


def pipeline(x):
    config = transform(x)
    config = 0
    return apply(config)
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_oa_conflict_detected(git_repo, tmp_path):
    """Full pipeline finds at least one interference path for the OA scenario."""
    write_py(tmp_path, "module.py", BASE_SOURCE)
    commit_all(git_repo, "base: add transform, apply, pipeline")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_SOURCE)
    commit_all(git_repo, "A: transform returns dict")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_SOURCE)
    commit_all(git_repo, "B: pipeline overrides config")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    assert diff.seeds_a, "branch-a should have changed functions"
    assert diff.seeds_b, "branch-b should have changed functions"

    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths, "expected at least one interference path"


@pytest.mark.integration
def test_oa_seeds_attributed(git_repo, tmp_path):
    """transform is seeded as A; pipeline is seeded as B."""
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
    assert any(s.name == "transform" for s in diff.seeds_a)
    assert any(s.name == "pipeline" for s in diff.seeds_b)


@pytest.mark.integration
def test_oa_direction_b_to_a(git_repo, tmp_path):
    """Direction should be B_to_A: pipeline (B) depends on transform (A)."""
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
def test_oa_tier1_detected(git_repo, tmp_path):
    """Override-assignment path should be detected at tier 1 (data/call edges only)."""
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

    tier1 = [p for p in paths if p.tier == 1]
    assert tier1, "expected at least one tier-1 interference path"


@pytest.mark.integration
def test_oa_no_conflict_when_no_shared_callees(git_repo, tmp_path):
    """No interference when A and B change functions that share no callee."""
    source = """\
        def func_a():
            return 1


        def func_b():
            return 2
    """
    write_py(tmp_path, "module.py", source)
    commit_all(git_repo, "base")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", source.replace("return 1", "return 10"))
    commit_all(git_repo, "A: change func_a")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", source.replace("return 2", "return 20"))
    commit_all(git_repo, "B: change func_b")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths == [], f"expected no conflicts, got: {paths}"
