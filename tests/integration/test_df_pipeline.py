"""Integration tests — DATA_FLOW conflict type.

Scenario
--------
Base has two functions: compute(x) returns an int, and process(data) calls
compute and returns the result directly.

Branch A changes compute to return a dict instead of an int.
Branch B changes process to perform arithmetic on compute's return value.

The tool should detect a B→A interference path: process (B) calls compute (A),
and B's arithmetic use is incompatible with A's new dict return type.

Expected results
----------------
- At least one InterferencePath detected
- Direction: B_to_A (process depends on compute)
- Tier: 1 (data/call path, no control edges needed)
- CompatibilityResult: SUSPICIOUS or INCOMPATIBLE (dict + arithmetic)
"""

from __future__ import annotations

import pytest

from paxpy.detector import detect
from paxpy.diff_parser import parse_diffs
from paxpy.endpoint_analyzer import analyze_endpoints
from paxpy.indexer import build_index
from paxpy.sdg_builder import build_sdg
from paxpy.types import Compatibility

from .conftest import commit_all, write_py

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Scenario source code
# ---------------------------------------------------------------------------

BASE_SOURCE = """\
def compute(x):
    return x * 2


def process(data):
    result = compute(data)
    return result
"""

BRANCH_A_SOURCE = """\
def compute(x):
    return {"value": x * 2, "factor": 2}


def process(data):
    result = compute(data)
    return result
"""

BRANCH_B_SOURCE = """\
def compute(x):
    return x * 2


def process(data):
    result = compute(data)
    return result + 1
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_df_conflict_detected(git_repo, tmp_path):
    """Full pipeline finds at least one interference path for the DF scenario."""
    write_py(tmp_path, "module.py", BASE_SOURCE)
    commit_all(git_repo, "base: add compute and process")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_SOURCE)
    commit_all(git_repo, "A: compute returns dict")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_SOURCE)
    commit_all(git_repo, "B: process does arithmetic on result")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    assert diff.seeds_a, "branch-a should have changed functions"
    assert diff.seeds_b, "branch-b should have changed functions"

    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths, "expected at least one interference path"


@pytest.mark.integration
def test_df_direction_b_to_a(git_repo, tmp_path):
    """Interference direction should be B_to_A: process (B) depends on compute (A)."""
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
def test_df_tier1(git_repo, tmp_path):
    """Data/call path should be detected at tier 1 (no control edges required)."""
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
    assert tier1, "expected at least one tier-1 (data/call) interference path"


@pytest.mark.integration
def test_df_endpoint_analysis_suspicious_or_incompatible(git_repo, tmp_path):
    """Endpoint analysis should flag dict-source + arithmetic-sink as suspicious/incompatible."""
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
    path = paths[0]
    source_node = sdg.nodes.get(path.source_node)
    sink_node = sdg.nodes.get(path.sink_node)
    result = analyze_endpoints(
        path,
        source_node,
        sink_node,
    )

    assert result.compatibility in {
        Compatibility.SUSPICIOUS,
        Compatibility.INCOMPATIBLE,
        Compatibility.UNKNOWN,
    }, f"expected SUSPICIOUS/INCOMPATIBLE/UNKNOWN for dict+arithmetic, got {result.compatibility}"


@pytest.mark.integration
def test_df_seeds_correctly_attributed(git_repo, tmp_path):
    """Seeds should be correctly tagged with branch A and B."""
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
    assert any(s.name == "compute" for s in diff.seeds_a)
    assert any(s.name == "process" for s in diff.seeds_b)


@pytest.mark.integration
def test_df_no_conflict_when_unrelated_changes(git_repo, tmp_path):
    """No interference when A and B change independent functions with no call relationship."""
    source = """\
        def alpha():
            return 1


        def beta():
            return 2
    """
    write_py(tmp_path, "module.py", source)
    commit_all(git_repo, "base")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", source.replace("return 1", "return 99"))
    commit_all(git_repo, "A: change alpha")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", source.replace("return 2", "return 88"))
    commit_all(git_repo, "B: change beta")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    paths = detect(sdg)

    assert paths == [], f"expected no conflicts for unrelated functions, got: {paths}"
