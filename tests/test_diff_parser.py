"""Tests for diff_parser.py."""

from __future__ import annotations

import ast
from pathlib import Path

import git

from paxpy.diff_parser import _get_changed_ranges, _map_ranges_to_functions, parse_diffs

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path, initial_branch="main")
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "test")
        cw.set_value("user", "email", "test@test.com")
    return repo


def commit_all(repo: git.Repo, msg: str) -> None:
    repo.git.add(".")
    repo.git.commit("-m", msg)


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# _map_ranges_to_functions
# ---------------------------------------------------------------------------


def test_map_ranges_basic_overlap():
    source = "def foo():\n    x = 1\n    return x\n"
    locs = _map_ranges_to_functions(Path("f.py"), source, [(2, 2)], "A")
    assert len(locs) == 1
    assert locs[0].name == "foo"
    assert locs[0].branch == "A"


def test_map_ranges_no_overlap():
    source = "x = 1\n\ndef foo():\n    return 1\n"
    # Range covers only line 1 (x = 1), not the function (lines 3-4)
    locs = _map_ranges_to_functions(Path("f.py"), source, [(1, 1)], "A")
    assert locs == []


def test_map_ranges_deduplicates():
    source = "def foo():\n    x = 1\n    y = 2\n    return x + y\n"
    # Two ranges both inside foo
    locs = _map_ranges_to_functions(Path("f.py"), source, [(2, 2), (3, 3)], "A")
    assert len(locs) == 1
    assert locs[0].name == "foo"


def test_map_ranges_multiple_functions():
    source = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    locs = _map_ranges_to_functions(Path("f.py"), source, [(2, 2), (5, 5)], "B")
    names = {loc.name for loc in locs}
    assert names == {"foo", "bar"}
    assert all(loc.branch == "B" for loc in locs)


def test_map_ranges_sets_ast_node():
    source = "def foo():\n    pass\n"
    locs = _map_ranges_to_functions(Path("f.py"), source, [(1, 2)], "A")
    assert isinstance(locs[0].ast_node, ast.FunctionDef)


def test_map_ranges_syntax_error_returns_empty():
    locs = _map_ranges_to_functions(Path("f.py"), "def (:\n", [(1, 1)], "A")
    assert locs == []


def test_map_ranges_async_function():
    source = "async def foo():\n    pass\n"
    locs = _map_ranges_to_functions(Path("f.py"), source, [(1, 2)], "A")
    assert len(locs) == 1
    assert isinstance(locs[0].ast_node, ast.AsyncFunctionDef)


def test_map_ranges_function_boundary_start():
    """Range touching only the def line counts as overlapping."""
    source = "def foo():\n    return 1\n"
    locs = _map_ranges_to_functions(Path("f.py"), source, [(1, 1)], "A")
    assert len(locs) == 1


def test_map_ranges_function_boundary_end():
    """Range touching only the last line counts as overlapping."""
    source = "def foo():\n    return 1\n"
    locs = _map_ranges_to_functions(Path("f.py"), source, [(2, 2)], "A")
    assert len(locs) == 1


# ---------------------------------------------------------------------------
# _get_changed_ranges — uses real git repos
# ---------------------------------------------------------------------------


def test_get_changed_ranges_single_line(tmp_path):
    repo = make_repo(tmp_path)
    py = tmp_path / "a.py"
    write(py, "x = 1\ny = 2\nz = 3\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat")
    write(py, "x = 1\ny = 99\nz = 3\n")  # line 2 changed
    commit_all(repo, "change")

    ranges = _get_changed_ranges(tmp_path, "main", "feat")
    assert py in ranges
    assert (2, 2) in ranges[py]


def test_get_changed_ranges_multi_line(tmp_path):
    repo = make_repo(tmp_path)
    py = tmp_path / "a.py"
    write(py, "a = 1\nb = 2\nc = 3\nd = 4\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat")
    write(py, "a = 1\nb = 99\nc = 99\nd = 4\n")  # lines 2-3 changed
    commit_all(repo, "change")

    ranges = _get_changed_ranges(tmp_path, "main", "feat")
    assert py in ranges
    hits = ranges[py]
    # Should cover lines 2 and 3
    assert any(start <= 2 and end >= 3 for start, end in hits)


def test_get_changed_ranges_ignores_non_python(tmp_path):
    repo = make_repo(tmp_path)
    txt = tmp_path / "notes.txt"
    write(txt, "hello\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat")
    write(txt, "world\n")
    commit_all(repo, "change")

    ranges = _get_changed_ranges(tmp_path, "main", "feat")
    assert len(ranges) == 0


def test_get_changed_ranges_new_file(tmp_path):
    repo = make_repo(tmp_path)
    write(tmp_path / "init.py", "x = 1\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat")
    py = tmp_path / "new.py"
    write(py, "def foo():\n    pass\n")
    commit_all(repo, "add new")

    ranges = _get_changed_ranges(tmp_path, "main", "feat")
    assert py in ranges
    assert len(ranges[py]) > 0


# ---------------------------------------------------------------------------
# parse_diffs — integration tests with real git repos
# ---------------------------------------------------------------------------


def test_parse_diffs_single_branch_a(tmp_path):
    repo = make_repo(tmp_path)
    py = tmp_path / "mod.py"
    write(py, "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat-a")
    write(py, "def foo():\n    return 99\n\ndef bar():\n    return 2\n")
    commit_all(repo, "change foo")

    repo.git.checkout("main")
    repo.git.checkout("-b", "feat-b")
    # No changes on B

    result = parse_diffs(tmp_path, "main", "feat-a", "feat-b")
    assert any(s.name == "foo" and s.branch == "A" for s in result.seeds_a)
    assert result.seeds_b == []


def test_parse_diffs_different_functions(tmp_path):
    repo = make_repo(tmp_path)
    py = tmp_path / "mod.py"
    write(py, "def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat-a")
    write(py, "def foo():\n    return 99\n\ndef bar():\n    return 2\n")
    commit_all(repo, "change foo")

    repo.git.checkout("main")
    repo.git.checkout("-b", "feat-b")
    write(py, "def foo():\n    return 1\n\ndef bar():\n    return 99\n")
    commit_all(repo, "change bar")

    result = parse_diffs(tmp_path, "main", "feat-a", "feat-b")
    assert any(s.name == "foo" for s in result.seeds_a)
    assert any(s.name == "bar" for s in result.seeds_b)


def test_parse_diffs_same_function_both_branches(tmp_path):
    repo = make_repo(tmp_path)
    py = tmp_path / "mod.py"
    write(py, "def foo():\n    return 1\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat-a")
    write(py, "def foo():\n    return 2\n")
    commit_all(repo, "change foo a")

    repo.git.checkout("main")
    repo.git.checkout("-b", "feat-b")
    write(py, "def foo():\n    return 3\n")
    commit_all(repo, "change foo b")

    result = parse_diffs(tmp_path, "main", "feat-a", "feat-b")
    assert any(s.name == "foo" and s.branch == "A" for s in result.seeds_a)
    assert any(s.name == "foo" and s.branch == "B" for s in result.seeds_b)


def test_parse_diffs_non_python_ignored(tmp_path):
    repo = make_repo(tmp_path)
    write(tmp_path / "readme.txt", "hello\n")
    write(tmp_path / "placeholder.py", "x = 1\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat-a")
    write(tmp_path / "readme.txt", "world\n")
    commit_all(repo, "change txt")

    repo.git.checkout("main")
    repo.git.checkout("-b", "feat-b")

    result = parse_diffs(tmp_path, "main", "feat-a", "feat-b")
    assert result.seeds_a == []
    assert result.seeds_b == []


def test_parse_diffs_branch_attribution(tmp_path):
    repo = make_repo(tmp_path)
    py = tmp_path / "m.py"
    write(py, "def foo():\n    x = 1\n\ndef bar():\n    y = 2\n")
    commit_all(repo, "base")

    repo.git.checkout("-b", "feat-a")
    write(py, "def foo():\n    x = 99\n\ndef bar():\n    y = 2\n")
    commit_all(repo, "a")

    repo.git.checkout("main")
    repo.git.checkout("-b", "feat-b")
    write(py, "def foo():\n    x = 1\n\ndef bar():\n    y = 99\n")
    commit_all(repo, "b")

    result = parse_diffs(tmp_path, "main", "feat-a", "feat-b")
    for s in result.seeds_a:
        assert s.branch == "A"
    for s in result.seeds_b:
        assert s.branch == "B"
