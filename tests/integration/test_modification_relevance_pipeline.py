"""Integration tests for the modification-relevance filter.

These tests create real git repos and verify that the filter correctly
suppresses FP paths where modifications don't participate in inter-procedural
data flow, while preserving TP paths where they do.
"""

from __future__ import annotations

import pytest

from paxpy.diff_parser import parse_diffs
from paxpy.indexer import build_index
from paxpy.main import _path_has_modification_relevance
from paxpy.sdg_builder import build_sdg

from .conftest import commit_all, write_py

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Integration Test 1: FP suppressed — unrelated modification in caller
# ---------------------------------------------------------------------------
# caller() calls helper() and also has a standalone debug assignment.
# Branch A only changes the debug assignment (unrelated to helper call).
# Branch B changes helper's body.
# Paths whose call boundary is the helper() call should be suppressed
# because A's modification doesn't feed into or consume from that call.

BASE_FP_CALLER = """\
from helper_mod import helper

def caller():
    debug_msg = "info"
    x = helper(10)
    return x
"""

BASE_FP_HELPER = """\
def helper(x):
    return x * 2
"""

BRANCH_A_FP_CALLER = """\
from helper_mod import helper

def caller():
    debug_msg = "debug: verbose"
    x = helper(10)
    return x
"""

BRANCH_B_FP_HELPER = """\
def helper(x):
    return x * 3
"""


@pytest.mark.integration
def test_fp_unrelated_modification_suppressed(git_repo, tmp_path):
    """A's debug_msg change doesn't feed into helper() call; should be suppressed."""
    write_py(tmp_path, "caller_mod.py", BASE_FP_CALLER)
    write_py(tmp_path, "helper_mod.py", BASE_FP_HELPER)
    commit_all(git_repo, "base: caller and helper")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "caller_mod.py", BRANCH_A_FP_CALLER)
    commit_all(git_repo, "A: change debug_msg assignment")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "helper_mod.py", BRANCH_B_FP_HELPER)
    commit_all(git_repo, "B: change helper body")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)

    from paxpy.neighborhood_detector import detect_interferences

    paths = detect_interferences(sdg, radius=2)

    # For any path between caller and helper that has a call boundary,
    # A's modification (debug_msg) doesn't participate in data flow
    # through the call to helper, so the filter should suppress it.
    for path in paths:
        src = sdg.nodes.get(path.source_node)
        snk = sdg.nodes.get(path.sink_node)
        if src and snk:
            src_fn = src.enclosing_function or ""
            snk_fn = snk.enclosing_function or ""
            if {src_fn, snk_fn} == {"caller", "helper"}:
                result = _path_has_modification_relevance(path, sdg)
                assert result is False, (
                    f"Expected FP suppression for {src_fn} -> {snk_fn}, "
                    f"path_nodes={path.path_nodes}"
                )


# ---------------------------------------------------------------------------
# Integration Test 2: TP preserved — return type change
# ---------------------------------------------------------------------------
# Branch A changes producer's return value (the modification IS the data flow).
# Branch B changes consumer's use of the return value.
# The modification-relevance filter should keep this path.

BASE_TP_SOURCE = """\
def producer():
    return 42


def consumer():
    x = producer()
    return x + 1
"""

BRANCH_A_TP_SOURCE = """\
def producer():
    return "forty-two"


def consumer():
    x = producer()
    return x + 1
"""

BRANCH_B_TP_SOURCE = """\
def producer():
    return 42


def consumer():
    x = producer()
    return x * 2
"""


@pytest.mark.integration
def test_tp_return_type_change_preserved(git_repo, tmp_path):
    """A's return change flows directly to B's use; should be kept."""
    write_py(tmp_path, "mod.py", BASE_TP_SOURCE)
    commit_all(git_repo, "base: producer and consumer")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "mod.py", BRANCH_A_TP_SOURCE)
    commit_all(git_repo, "A: change return type")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "mod.py", BRANCH_B_TP_SOURCE)
    commit_all(git_repo, "B: change arithmetic operation")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)

    from paxpy.neighborhood_detector import detect_interferences

    paths = detect_interferences(sdg, radius=2)

    # There should be at least one path between producer and consumer
    # and the filter should keep it.
    kept = []
    for path in paths:
        src = sdg.nodes.get(path.source_node)
        snk = sdg.nodes.get(path.sink_node)
        if src and snk:
            src_fn = src.enclosing_function or ""
            snk_fn = snk.enclosing_function or ""
            if {src_fn, snk_fn} == {"producer", "consumer"} and _path_has_modification_relevance(
                path, sdg
            ):
                kept.append(path)

    assert len(kept) >= 1, (
        f"Expected at least 1 kept path between producer/consumer, "
        f"got {len(kept)} out of {len(paths)} total paths"
    )
