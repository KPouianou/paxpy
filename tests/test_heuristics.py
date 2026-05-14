"""Tests for cheap structural heuristics in bench/run/heuristics.py."""

from __future__ import annotations

from bench.run.heuristics import (
    HEURISTICS,
    _changed_files,
    _changed_function_names,
    _changed_lines,
    _extract_call_names,
    _imported_modules,
    any_shared_file,
    call_graph_1hop,
    diff_proximity,
    import_overlap,
    same_function,
)

# ---------------------------------------------------------------------------
# Helper: _changed_files
# ---------------------------------------------------------------------------


def test_changed_files_identical():
    base = {"a.py": "x = 1"}
    branch = {"a.py": "x = 1"}
    assert _changed_files(base, branch) == set()


def test_changed_files_modified():
    base = {"a.py": "x = 1"}
    branch = {"a.py": "x = 2"}
    assert _changed_files(base, branch) == {"a.py"}


def test_changed_files_added():
    base = {}
    branch = {"a.py": "x = 1"}
    assert _changed_files(base, branch) == {"a.py"}


def test_changed_files_deleted():
    base = {"a.py": "x = 1"}
    branch = {}
    assert _changed_files(base, branch) == {"a.py"}


# ---------------------------------------------------------------------------
# Helper: _changed_function_names
# ---------------------------------------------------------------------------


def test_changed_function_names_no_change():
    src = "def foo():\n    return 1\n"
    assert _changed_function_names(src, src) == set()


def test_changed_function_names_body_change():
    base = "def foo():\n    return 1\n"
    branch = "def foo():\n    return 2\n"
    assert _changed_function_names(base, branch) == {"foo"}


def test_changed_function_names_new_function():
    base = "def foo():\n    return 1\n"
    branch = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    assert _changed_function_names(base, branch) == {"bar"}


def test_changed_function_names_removed_function():
    base = "def foo():\n    return 1\ndef bar():\n    return 2\n"
    branch = "def foo():\n    return 1\n"
    assert _changed_function_names(base, branch) == {"bar"}


def test_changed_function_names_multi():
    base = "def foo():\n    return 1\ndef bar():\n    return 2\n"
    branch = "def foo():\n    return 9\ndef bar():\n    return 2\n"
    assert _changed_function_names(base, branch) == {"foo"}


# ---------------------------------------------------------------------------
# Helper: _changed_lines
# ---------------------------------------------------------------------------


def test_changed_lines_identical():
    src = "a\nb\nc\n"
    assert _changed_lines(src, src) == set()


def test_changed_lines_one_changed():
    base = "a\nb\nc\n"
    branch = "a\nX\nc\n"
    assert _changed_lines(base, branch) == {2}


def test_changed_lines_appended():
    base = "a\n"
    branch = "a\nb\n"
    assert 2 in _changed_lines(base, branch)


# ---------------------------------------------------------------------------
# Helper: _imported_modules
# ---------------------------------------------------------------------------


def test_imported_modules_import():
    src = "import os\nimport sys\n"
    assert _imported_modules(src) == {"os", "sys"}


def test_imported_modules_from():
    src = "from pathlib import Path\nfrom os.path import join\n"
    assert _imported_modules(src) == {"pathlib", "os"}


def test_imported_modules_mixed():
    src = "import json\nfrom collections import defaultdict\n"
    assert _imported_modules(src) == {"json", "collections"}


def test_imported_modules_none():
    src = "x = 1\n"
    assert _imported_modules(src) == set()


# ---------------------------------------------------------------------------
# H4: any_shared_file
# ---------------------------------------------------------------------------


def test_h4_both_touch_same_file():
    base = {"m.py": "x = 1"}
    a = {"m.py": "x = 2"}
    b = {"m.py": "x = 3"}
    assert any_shared_file(base, a, b) is True


def test_h4_different_files():
    base = {"a.py": "x = 1", "b.py": "y = 1"}
    a = {"a.py": "x = 2", "b.py": "y = 1"}
    b = {"a.py": "x = 1", "b.py": "y = 2"}
    assert any_shared_file(base, a, b) is False


def test_h4_only_a_changes():
    base = {"m.py": "x = 1"}
    a = {"m.py": "x = 2"}
    b = {"m.py": "x = 1"}  # no change from base
    assert any_shared_file(base, a, b) is False


# ---------------------------------------------------------------------------
# H1: same_function
# ---------------------------------------------------------------------------


def test_h1_both_touch_same_function():
    base = {"m.py": "def foo():\n    return 1\ndef bar():\n    return 2\n"}
    a = {"m.py": "def foo():\n    return 99\ndef bar():\n    return 2\n"}
    b = {"m.py": "def foo():\n    return 42\ndef bar():\n    return 2\n"}
    assert same_function(base, a, b) is True


def test_h1_different_functions():
    base = {"m.py": "def foo():\n    return 1\ndef bar():\n    return 2\n"}
    a = {"m.py": "def foo():\n    return 99\ndef bar():\n    return 2\n"}
    b = {"m.py": "def foo():\n    return 1\ndef bar():\n    return 42\n"}
    assert same_function(base, a, b) is False


def test_h1_different_files():
    base = {"a.py": "def foo():\n    return 1\n", "b.py": "def foo():\n    return 1\n"}
    a = {"a.py": "def foo():\n    return 99\n", "b.py": "def foo():\n    return 1\n"}
    b = {"a.py": "def foo():\n    return 1\n", "b.py": "def foo():\n    return 99\n"}
    # Both touch a function named 'foo', but in different files → no shared file conflict
    assert same_function(base, a, b) is False


def test_h1_no_conflict_no_change():
    base = {"m.py": "def foo():\n    return 1\n"}
    # b makes no change
    assert (
        same_function(
            base, {"m.py": "def foo():\n    return 2\n"}, {"m.py": "def foo():\n    return 1\n"}
        )
        is False
    )


# ---------------------------------------------------------------------------
# H2: diff_proximity
# ---------------------------------------------------------------------------


def test_h2_within_5():
    base = {"m.py": "\n".join(f"x{i} = {i}" for i in range(20))}
    # A changes line 5, B changes line 7 → distance 2
    a_lines = base["m.py"].splitlines()
    b_lines = base["m.py"].splitlines()
    a_lines[4] = "x4 = 99"  # line 5
    b_lines[6] = "x6 = 99"  # line 7
    a = {"m.py": "\n".join(a_lines)}
    b = {"m.py": "\n".join(b_lines)}
    assert diff_proximity(base, a, b, 5) is True


def test_h2_outside_5_inside_25():
    base_lines = [f"x{i} = {i}" for i in range(50)]
    base = {"m.py": "\n".join(base_lines)}
    a_lines = base_lines[:]
    b_lines = base_lines[:]
    a_lines[0] = "x0 = 99"  # line 1
    b_lines[10] = "x10 = 99"  # line 11 → distance 10
    a = {"m.py": "\n".join(a_lines)}
    b = {"m.py": "\n".join(b_lines)}
    assert diff_proximity(base, a, b, 5) is False
    assert diff_proximity(base, a, b, 10) is True


def test_h2_different_files():
    base = {"a.py": "x = 1", "b.py": "y = 1"}
    a = {"a.py": "x = 2", "b.py": "y = 1"}
    b = {"a.py": "x = 1", "b.py": "y = 2"}
    # Changes are in different files, so no proximity hit
    assert diff_proximity(base, a, b, 50) is False


# ---------------------------------------------------------------------------
# H3: import_overlap
# ---------------------------------------------------------------------------


def test_h3_a_imports_b_changed_module():
    base = {
        "module_a.py": "import module_b\n\ndef foo():\n    return 1\n",
        "module_b.py": "def helper():\n    return 1\n",
    }
    a = {
        "module_a.py": "import module_b\n\ndef foo():\n    return 2\n",
        "module_b.py": "def helper():\n    return 1\n",
    }
    b = {
        "module_a.py": "import module_b\n\ndef foo():\n    return 1\n",
        "module_b.py": "def helper():\n    return 99\n",
    }
    # A changes module_a.py which imports module_b; B changes module_b → overlap
    assert import_overlap(base, a, b) is True


def test_h3_no_import_relation():
    base = {
        "module_a.py": "def foo():\n    return 1\n",
        "module_b.py": "def bar():\n    return 1\n",
    }
    a = {"module_a.py": "def foo():\n    return 2\n", "module_b.py": "def bar():\n    return 1\n"}
    b = {"module_a.py": "def foo():\n    return 1\n", "module_b.py": "def bar():\n    return 2\n"}
    assert import_overlap(base, a, b) is False


def test_h3_single_file_always_false():
    base = {"m.py": "def foo():\n    return 1\n"}
    a = {"m.py": "def foo():\n    return 2\n"}
    b = {"m.py": "def foo():\n    return 3\n"}
    assert import_overlap(base, a, b) is False


# ---------------------------------------------------------------------------
# HEURISTICS registry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helper: _extract_call_names
# ---------------------------------------------------------------------------


def test_extract_call_names_basic():
    body = "def foo():\n    return bar()\n"
    assert "bar" in _extract_call_names(body)


def test_extract_call_names_excludes_keywords():
    body = "def foo():\n    if x > 0:\n        return 1\n"
    names = _extract_call_names(body)
    assert "if" not in names
    assert "return" not in names


def test_extract_call_names_multi():
    body = "def foo():\n    x = bar(baz())\n    return qux(x)\n"
    names = _extract_call_names(body)
    assert {"bar", "baz", "qux"}.issubset(names)


# ---------------------------------------------------------------------------
# H5: call_graph_1hop
# ---------------------------------------------------------------------------


def test_h5_direct_caller_callee():
    """B changes a consumer that directly calls A's changed producer → H5 fires."""
    base = {
        "m.py": "def producer():\n    return 1\n\ndef consumer():\n    x = producer()\n    return x\n"
    }
    a = {
        "m.py": "def producer():\n    return {'value': 1}\n\ndef consumer():\n    x = producer()\n    return x\n"
    }
    b = {
        "m.py": "def producer():\n    return 1\n\ndef consumer():\n    x = producer()\n    return x + 1\n"
    }
    assert call_graph_1hop(base, a, b) is True


def test_h5_deep_chain_misses():
    """Deep chain: A changes level3, B changes consumer. No direct call → H5 misses."""
    assert call_graph_1hop(_DEEP_BASE, _DEEP_A, _DEEP_B) is False


def test_h5_no_shared_functions():
    """Both branches change unrelated functions with no call relationship."""
    base = {"m.py": "def foo():\n    return 1\n\ndef bar():\n    return 2\n"}
    a = {"m.py": "def foo():\n    return 99\n\ndef bar():\n    return 2\n"}
    b = {"m.py": "def foo():\n    return 1\n\ndef bar():\n    return 99\n"}
    assert call_graph_1hop(base, a, b) is False


def test_h5_cross_file():
    """A changes module_b.helper, B changes module_a.caller which calls helper → H5 fires."""
    base = {
        "module_a.py": "def caller():\n    return helper()\n",
        "module_b.py": "def helper():\n    return 1\n",
    }
    a = {
        "module_a.py": "def caller():\n    return helper()\n",
        "module_b.py": "def helper():\n    return {'v': 1}\n",
    }
    b = {
        "module_a.py": "def caller():\n    return helper() + 1\n",
        "module_b.py": "def helper():\n    return 1\n",
    }
    assert call_graph_1hop(base, a, b) is True


def test_heuristics_registry_complete():
    expected = {
        "same_function",
        "diff_proximity_5",
        "diff_proximity_10",
        "diff_proximity_25",
        "diff_proximity_50",
        "import_overlap",
        "any_shared_file",
        "call_graph_1hop",
    }
    assert set(HEURISTICS.keys()) == expected


def test_heuristics_registry_callable():
    base = {"m.py": "def foo():\n    return 1\n"}
    a = {"m.py": "def foo():\n    return 2\n"}
    b = {"m.py": "def foo():\n    return 1\n"}
    for name, fn in HEURISTICS.items():
        result = fn(base, a, b)
        assert isinstance(result, bool), f"{name} should return bool"


# ---------------------------------------------------------------------------
# Regression: deep-chain scenario should NOT trigger shallow heuristics
# ---------------------------------------------------------------------------

_DEEP_BASE = {
    "module.py": (
        "def level3():\n    return 1\n\n"
        "def level2():\n    return level3()\n\n"
        "def level1():\n    return level2()\n\n"
        "def consumer():\n    x = level1()\n    return x\n"
    )
}

# A changes level3 (the deep producer), B changes consumer (the deep consumer)
_DEEP_A = {
    "module.py": (
        "def level3():\n    return {'value': 1}\n\n"  # changed: returns dict
        "def level2():\n    return level3()\n\n"
        "def level1():\n    return level2()\n\n"
        "def consumer():\n    x = level1()\n    return x\n"
    )
}
_DEEP_B = {
    "module.py": (
        "def level3():\n    return 1\n\n"
        "def level2():\n    return level3()\n\n"
        "def level1():\n    return level2()\n\n"
        "def consumer():\n    x = level1()\n    return x + 1\n"  # changed: arithmetic on result
    )
}


def test_deep_chain_h1_misses():
    """H1 should NOT flag deep-chain conflict — different functions changed."""
    assert same_function(_DEEP_BASE, _DEEP_A, _DEEP_B) is False


def test_deep_chain_h2_misses():
    """H2 with a tight window (N=5) should NOT flag the deep-chain conflict.
    level3 (changed by A) is at line 2; consumer (changed by B) is at line 12.
    Distance = 10 > 5, so the small proximity window misses the semantic connection.
    With N=50 it WOULD flag (same-file, within 50 lines) — that's an expected FP
    for large-window H2, and is documented behaviour, not a bug.
    """
    assert diff_proximity(_DEEP_BASE, _DEEP_A, _DEEP_B, 5) is False


def test_deep_chain_h4_still_flags():
    """H4 flags anything in the same file — even deep-chain (expected FP)."""
    assert any_shared_file(_DEEP_BASE, _DEEP_A, _DEEP_B) is True
