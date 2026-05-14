"""Integration tests for direct_detector through the full pipeline.

These tests create real git repos with branch structures and verify that
the direct detector finds (or correctly suppresses) conflicts when run
through the diff_parser -> direct_detector pipeline.
"""

from __future__ import annotations

import pytest

from paxpy.diff_parser import parse_diffs
from paxpy.direct_detector import detect_direct_conflicts
from paxpy.types import ConflictType

from .conftest import commit_all, write_py

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Test 1: Signature mismatch end-to-end
# ---------------------------------------------------------------------------

BASE_SIG_SOURCE = """\
class Handler:
    def process(self, data):
        return data

    def caller(self):
        return self.process("raw")
"""

BRANCH_A_SIG_SOURCE = """\
class Handler:
    def process(self, data, fmt):
        return format_data(data, fmt)

    def caller(self):
        return self.process("raw")
"""

BRANCH_B_SIG_SOURCE = """\
class Handler:
    def process(self, data):
        return data

    def caller(self):
        return self.process("raw_data")
"""


@pytest.mark.integration
def test_signature_mismatch_end_to_end(git_repo, tmp_path):
    """Branch A adds a param to process(); B calls with old arg count."""
    write_py(tmp_path, "handler.py", BASE_SIG_SOURCE)
    commit_all(git_repo, "base: Handler with process and caller")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "handler.py", BRANCH_A_SIG_SOURCE)
    commit_all(git_repo, "A: process takes fmt param")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "handler.py", BRANCH_B_SIG_SOURCE)
    commit_all(git_repo, "B: caller uses old process signature")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    assert diff.seeds_a, "branch-a should have changed functions"
    assert diff.seeds_b, "branch-b should have changed functions"

    paths = detect_direct_conflicts(diff)
    assert len(paths) >= 1, f"expected at least 1 path, got: {paths}"


# ---------------------------------------------------------------------------
# Test 2: Same-function OA end-to-end
# ---------------------------------------------------------------------------

BASE_OA_SOURCE = """\
class Config:
    def configure(self):
        self.mode = "default"
"""

BRANCH_A_OA_SOURCE = """\
class Config:
    def configure(self):
        self.mode = "fast"
"""

BRANCH_B_OA_SOURCE = """\
class Config:
    def configure(self):
        self.mode = "safe"
"""


@pytest.mark.integration
def test_oa_same_function_end_to_end(git_repo, tmp_path):
    """Both branches change self.mode in configure() to different values."""
    write_py(tmp_path, "config.py", BASE_OA_SOURCE)
    commit_all(git_repo, "base: Config.configure with default mode")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "config.py", BRANCH_A_OA_SOURCE)
    commit_all(git_repo, "A: mode = fast")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "config.py", BRANCH_B_OA_SOURCE)
    commit_all(git_repo, "B: mode = safe")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    paths = detect_direct_conflicts(diff)

    oa = [p for p in paths if p.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
    assert len(oa) >= 1, f"expected OA detection, got: {paths}"


# ---------------------------------------------------------------------------
# Test 3: Direct detector paths bypass self-referential filter
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_direct_paths_bypass_self_referential_filter(git_repo, tmp_path):
    """OA paths from direct detector survive main.py's self-referential filter.

    main.py suppresses SDG paths where src_fn == snk_fn in the same file,
    but direct_detector paths are exempt (step 5a in main.py).
    This test verifies that by checking the direct_detector output directly:
    both endpoints are in the same function/file and the path is still present.
    """
    write_py(tmp_path, "config.py", BASE_OA_SOURCE)
    commit_all(git_repo, "base")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "config.py", BRANCH_A_OA_SOURCE)
    commit_all(git_repo, "A")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "config.py", BRANCH_B_OA_SOURCE)
    commit_all(git_repo, "B")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    paths = detect_direct_conflicts(diff)

    oa = [p for p in paths if p.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
    assert len(oa) >= 1, "OA path must survive even though both endpoints are in same function"

    # Verify both endpoints reference the same file (the self-referential pattern)
    for path in oa:
        src_file = path.source_node.rsplit(":", 2)[0]
        snk_file = path.sink_node.rsplit(":", 2)[0]
        assert src_file == snk_file, "OA endpoints should be in the same file"


# ---------------------------------------------------------------------------
# Test 4: No SDG regression — direct detector doesn't add spurious paths
# ---------------------------------------------------------------------------

BASE_DF_SOURCE = """\
def producer():
    return 1


def consumer():
    val = producer()
    return val * 2
"""

BRANCH_A_DF_SOURCE = """\
def producer():
    return {"value": 1}


def consumer():
    val = producer()
    return val * 2
"""

BRANCH_B_DF_SOURCE = """\
def producer():
    return 1


def consumer():
    val = producer()
    return val + 10
"""


@pytest.mark.integration
def test_no_spurious_direct_paths_on_df_scenario(git_repo, tmp_path):
    """Simple DF scenario: direct detector should not add spurious paths.

    A modifies producer(), B modifies consumer() which calls producer().
    SDG detectors should handle this. Direct detector should find 0 paths
    (different functions, no OA, no sig mismatch).
    """
    write_py(tmp_path, "module.py", BASE_DF_SOURCE)
    commit_all(git_repo, "base: producer and consumer")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "module.py", BRANCH_A_DF_SOURCE)
    commit_all(git_repo, "A: producer returns dict")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "module.py", BRANCH_B_DF_SOURCE)
    commit_all(git_repo, "B: consumer adds 10 instead of doubling")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    direct_paths = detect_direct_conflicts(diff)

    # Direct detector should find 0 paths — different functions, no overlap
    assert len(direct_paths) == 0, (
        f"direct detector should not flag DF scenario, got: {direct_paths}"
    )

    # Verify SDG detectors DO find paths (the scenario IS a real conflict)
    from paxpy.detector import detect
    from paxpy.indexer import build_index
    from paxpy.sdg_builder import build_sdg

    index = build_index(tmp_path)
    sdg = build_sdg(diff, index, depth=3)
    sdg_paths = detect(sdg)
    assert len(sdg_paths) >= 1, "SDG detectors should find the DF conflict"


# ---------------------------------------------------------------------------
# Test 5: OA suppressed for mutually exclusive branches
# ---------------------------------------------------------------------------

BASE_EXCLUSIVE_SOURCE = """\
class Handler:
    def handle(self, error):
        result = "unknown"
        return result
"""

BRANCH_A_EXCLUSIVE_SOURCE = """\
class Handler:
    def handle(self, error):
        if error:
            result = "failed"
        return result
"""

BRANCH_B_EXCLUSIVE_SOURCE = """\
class Handler:
    def handle(self, error):
        if not error:
            result = "success"
        return result
"""


@pytest.mark.integration
def test_oa_suppressed_mutually_exclusive_branches(git_repo, tmp_path):
    """OA should be suppressed when assignments are in complement if-branches."""
    write_py(tmp_path, "handler.py", BASE_EXCLUSIVE_SOURCE)
    commit_all(git_repo, "base: Handler with default result")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "handler.py", BRANCH_A_EXCLUSIVE_SOURCE)
    commit_all(git_repo, "A: result=failed in if error")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "handler.py", BRANCH_B_EXCLUSIVE_SOURCE)
    commit_all(git_repo, "B: result=success in if not error")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    paths = detect_direct_conflicts(diff)

    oa = [p for p in paths if p.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
    assert len(oa) == 0, f"OA should be suppressed for mutually exclusive branches, got: {oa}"


# ---------------------------------------------------------------------------
# Test 6: OA still detected for same-arm changes
# ---------------------------------------------------------------------------

BRANCH_A_SAME_ARM_SOURCE = """\
class Handler:
    def handle(self, error):
        if error:
            result = "failed"
        return result
"""

BRANCH_B_SAME_ARM_SOURCE = """\
class Handler:
    def handle(self, error):
        if error:
            result = "error_occurred"
        return result
"""


@pytest.mark.integration
def test_oa_detected_same_arm(git_repo, tmp_path):
    """OA should still be detected when both branches assign in the same if-arm."""
    write_py(tmp_path, "handler.py", BASE_EXCLUSIVE_SOURCE)
    commit_all(git_repo, "base: Handler with default result")

    git_repo.git.checkout("-b", "branch-a")
    write_py(tmp_path, "handler.py", BRANCH_A_SAME_ARM_SOURCE)
    commit_all(git_repo, "A: result=failed in if error")

    git_repo.git.checkout("main")
    git_repo.git.checkout("-b", "branch-b")
    write_py(tmp_path, "handler.py", BRANCH_B_SAME_ARM_SOURCE)
    commit_all(git_repo, "B: result=error_occurred in if error")

    diff = parse_diffs(tmp_path, "main", "branch-a", "branch-b")
    paths = detect_direct_conflicts(diff)

    oa = [p for p in paths if p.conflict_type == ConflictType.OVERRIDE_ASSIGNMENT]
    assert len(oa) >= 1, f"OA should be detected for same-arm changes, got: {paths}"
