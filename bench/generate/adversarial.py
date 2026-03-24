"""Hand-crafted adversarial scenarios that target specific known failure modes.

Each case states an explicit hypothesis about how paxpy should behave.
These are not parametrically generated — they are designed to probe edge
cases, known limitations, and boundary conditions of the implementation.

Adversarial cases are expected to fail in documented ways: a "pass" for an
adversarial scenario means the failure mode is what the hypothesis predicted
(e.g., a clean miss with no crash), not necessarily that paxpy detected
anything.
"""

from __future__ import annotations

from bench.generate.parametric import ScenarioSpec


def all_adversarial() -> list[ScenarioSpec]:
    return [
        _depth_limit_boundary(),
        _diamond_two_paths(),
        _high_fan_out_stress(),
        _high_name_ambiguity(),
        _cross_file_deep_chain(),
        _oa_inside_try_block(),
        _cf_fan_in_many_callers(),
        _df_through_augassign(),
        _compatible_change_no_conflict(),
        _both_branches_change_same_function(),
    ]


def _spec(name: str, base: str, a: str, b: str, expected: bool,
          rationale: str, hypothesis: str,
          direction: str | None = None, tier: int | None = None,
          mut_a: str = "", mut_b: str = "") -> ScenarioSpec:
    """Helper to build an adversarial ScenarioSpec from single-file sources."""
    return ScenarioSpec(
        name=f"adversarial_{name}",
        bucket="adversarial",
        conflict_type=None,
        call_depth=-1,
        fan_out=-1,
        file_count=1,
        name_ambiguity=0,
        positive=expected,
        random_seed=-1,
        complexity_tier="adversarial",
        base_source={"module.py": base},
        branch_a_source={"module.py": a},
        branch_b_source={"module.py": b},
        expected_conflict=expected,
        expected_direction=direction,
        expected_tier=tier,
        label_rationale=rationale,
        mutation_a=mut_a,
        mutation_b=mut_b,
        hypothesis=hypothesis,
    )


def _depth_limit_boundary() -> ScenarioSpec:
    """Conflict at call depth 6 with default depth=5 — should be missed cleanly."""
    base = """\
def fn_a():
    return 42


def mid1():
    return fn_a()


def mid2():
    return mid1()


def mid3():
    return mid2()


def mid4():
    return mid3()


def mid5():
    return mid4()


def fn_b():
    result = mid5()
    return result
"""
    branch_a = base.replace("return 42", 'return {"code": 42}')
    branch_b = base.replace("return result\n", "return result + 1\n")
    return _spec(
        "depth_limit_boundary",
        base, branch_a, branch_b,
        expected=False,   # paxpy at depth=5 cannot reach fn_a from fn_b
        rationale="Conflict exists at call depth 6, beyond default depth=5.",
        hypothesis=(
            "At depth=5, paxpy should miss this conflict (FN). "
            "Expected: no paths found, no crash. "
            "Validates depth-limit behaviour is a graceful under-approximation."
        ),
        direction="B_to_A", tier=1,
        mut_a="return_scalar_to_dict", mut_b="arithmetic_on_return",
    )


def _diamond_two_paths() -> ScenarioSpec:
    """Two independent paths from producer to consumer — deduplication should report once."""
    base = """\
def producer():
    return 42


def path_left():
    return producer()


def path_right():
    return producer()


def consumer():
    a = path_left()
    b = path_right()
    return a + b
"""
    branch_a = base.replace("return 42", 'return {"v": 42}')
    branch_b = base.replace("return a + b", "return a + b + 1")
    return _spec(
        "diamond_two_paths",
        base, branch_a, branch_b,
        expected=True,
        rationale="Two call paths (via path_left and path_right) both connect producer→consumer.",
        hypothesis=(
            "paxpy should detect interference. Both paths reach the same endpoints "
            "so deduplication should yield at most 2 paths (one per route). "
            "No crash. Tests whether the chop intersection handles diamond graphs."
        ),
        direction="B_to_A", tier=1,
        mut_a="return_scalar_to_dict", mut_b="arithmetic_on_return",
    )


def _high_fan_out_stress() -> ScenarioSpec:
    """One hub function called by 15 consumers — tests BFS fan-in scalability."""
    hub_body = "def hub():\n    return 1\n\n"
    consumers = "".join(
        f"def consumer_{i}():\n    return hub()\n\n" for i in range(15)
    )
    # Branch B changes consumer_0 which calls hub (changed by A)
    base = hub_body + consumers + "def entry():\n    return consumer_0()\n"
    branch_a = base.replace("return 1", 'return {"value": 1}')
    branch_b = base.replace(
        "def consumer_0():\n    return hub()\n",
        "def consumer_0():\n    result = hub()\n    return result + 1\n",
    )
    return _spec(
        "high_fan_out_stress",
        base, branch_a, branch_b,
        expected=True,
        rationale="hub() is called by 15 consumers; only consumer_0 is changed by B.",
        hypothesis=(
            "paxpy should detect interference through consumer_0. "
            "High fan-in increases reverse-BFS cost. "
            "Tests whether detection degrades or times out under fan-in pressure."
        ),
        direction="B_to_A", tier=1,
        mut_a="return_scalar_to_dict", mut_b="arithmetic_on_return",
    )


def _high_name_ambiguity() -> ScenarioSpec:
    """Many functions share the name 'compute' — over-approximation FP risk."""
    # All these are in the same file, so they shadow each other in Python
    # but paxpy's index (which does name lookup) treats them as separate entries.
    # Actually, Python would only keep the last definition — this tests whether
    # paxpy handles duplicate names in the same file gracefully.
    base = """\
def compute(x):
    return x * 2


def consumer(data):
    result = compute(data)
    return result


def unrelated_a():
    return 1


def unrelated_b():
    return 2
"""
    branch_a = base.replace("return x * 2", 'return {"value": x * 2}')
    branch_b = base.replace("return result\n", "return result + 1\n")
    return _spec(
        "high_name_ambiguity",
        base, branch_a, branch_b,
        expected=True,
        rationale="Standard DF scenario; name ambiguity is single-file (last def wins in Python).",
        hypothesis=(
            "paxpy should detect interference. Tests whether the indexer correctly "
            "handles a single-file scenario with the canonical DF pattern. "
            "Baseline for the multi-file name-ambiguity stress tests."
        ),
        direction="B_to_A", tier=1,
        mut_a="return_scalar_to_dict", mut_b="arithmetic_on_return",
    )


def _cross_file_deep_chain() -> ScenarioSpec:
    """Cross-file call chain at depth 3 — exercises the multi-file indexer path."""
    module_a = """\
def producer(x):
    return x * 2


def mid(x):
    return producer(x)
"""
    module_b = """\
def consumer(data):
    result = mid(data)
    return result
"""
    base      = {"module_a.py": module_a, "module_b.py": module_b}
    a_src     = {
        "module_a.py": module_a.replace("return x * 2", 'return {"value": x * 2}'),
        "module_b.py": module_b,
    }
    b_src     = {
        "module_a.py": module_a,
        "module_b.py": module_b.replace("return result\n", "return result + 1\n"),
    }
    return ScenarioSpec(
        name="adversarial_cross_file_deep",
        bucket="adversarial",
        conflict_type=None,
        call_depth=2,
        fan_out=1,
        file_count=2,
        name_ambiguity=0,
        positive=True,
        random_seed=-1,
        complexity_tier="adversarial",
        base_source=base,
        branch_a_source=a_src,
        branch_b_source=b_src,
        expected_conflict=True,
        expected_direction="B_to_A",
        expected_tier=1,
        label_rationale="Cross-file DF: producer in module_a, consumer in module_b, depth=2.",
        mutation_a="return_scalar_to_dict",
        mutation_b="arithmetic_on_return",
        hypothesis=(
            "paxpy should detect interference across file boundaries via name-based "
            "call resolution. Tests the indexer's cross-file lookup and the diff_parser's "
            "handling of changes in two different files."
        ),
    )


def _oa_inside_try_block() -> ScenarioSpec:
    """Override assignment inside a try block — control + data mixed."""
    base = """\
def transform(x):
    return x + 1


def pipeline(x):
    try:
        config = transform(x)
    except Exception:
        config = 0
    return config
"""
    branch_a = base.replace("return x + 1", 'return {"value": x + 1}')
    branch_b = base.replace(
        "        config = transform(x)\n    except Exception:\n        config = 0",
        "        config = transform(x)\n        config = 0\n    except Exception:\n        config = -1",
    )
    return _spec(
        "oa_inside_try",
        base, branch_a, branch_b,
        expected=True,
        rationale="A changes transform return type; B overrides config inside try body.",
        hypothesis=(
            "The override happens inside a try block, adding control edges. "
            "paxpy may classify as CONTROL_DEPENDENCY instead of OVERRIDE_ASSIGNMENT. "
            "Tests mixed control+data edge classification in the detector."
        ),
        direction="B_to_A", tier=None,
        mut_a="return_scalar_to_dict", mut_b="override_inside_try",
    )


def _cf_fan_in_many_callers() -> ScenarioSpec:
    """Confluence at an accumulator with 10 callers — reverse-BFS stress."""
    helper_base = "def helper(v):\n    return v * 10\n\n"
    callers = "".join(
        f"def caller_{i}(vals):\n"
        f"    return sum(helper(v) for v in vals)\n\n"
        for i in range(10)
    )
    base = helper_base + callers
    branch_a = base.replace("return v * 10", 'return {"result": v * 10}')
    branch_b = base.replace(
        "def caller_0(vals):\n    return sum(helper(v) for v in vals)\n",
        "def caller_0(vals):\n    total = 0\n    for v in vals:\n        total += helper(v)\n    return total\n",
    )
    return _spec(
        "cf_fan_in_many_callers",
        base, branch_a, branch_b,
        expected=True,
        rationale="helper() changed by A; caller_0 changed accumulation by B. 10 callers total.",
        hypothesis=(
            "Reverse-BFS from B's seed (caller_0) should find helper via call edge. "
            "The 9 unchanged callers should not create false paths. "
            "Tests whether high fan-in (10 callers) degrades detection or precision."
        ),
        direction="B_to_A", tier=1,
        mut_a="return_scalar_to_dict", mut_b="change_accumulation_pattern",
    )


def _df_through_augassign() -> ScenarioSpec:
    """DF path passes through an AugAssign (+=) — tests AugAssign data edge handling."""
    base = """\
def source():
    return 10


def middle():
    total = 0
    total += source()
    return total


def sink():
    result = middle()
    return result
"""
    branch_a = base.replace("return 10", 'return {"value": 10}')
    branch_b = base.replace("return result\n", "return result + 1\n")
    return _spec(
        "df_through_augassign",
        base, branch_a, branch_b,
        expected=True,
        rationale="A changes source() return type; B does arithmetic on sink's result. AugAssign in middle.",
        hypothesis=(
            "The data edge through total += source() requires AugAssign to be "
            "treated as both read and write. If AugAssign data edges are missing, "
            "the chain breaks and paxpy produces a FN. Tests the AugAssign fix in pdg_builder."
        ),
        direction="B_to_A", tier=1,
        mut_a="return_scalar_to_dict", mut_b="arithmetic_on_return",
    )


def _compatible_change_no_conflict() -> ScenarioSpec:
    """A changes return type compatibly; B uses value in a type-safe way — should be TN."""
    base = """\
def produce(x):
    return x * 2


def consume(data):
    result = produce(data)
    return result
"""
    # A changes produce to still return a number (different formula, same type)
    branch_a = base.replace("return x * 2", "return x * 3 + 1")
    # B adds a completely unrelated function
    branch_b = base + "\ndef unrelated():\n    return 99\n"
    return _spec(
        "compatible_change_no_conflict",
        base, branch_a, branch_b,
        expected=False,
        rationale="A changes scalar→scalar (same type); B changes an unrelated function.",
        hypothesis=(
            "paxpy should not flag this (TN). A's change is type-compatible. "
            "B's change has no call path to A. Tests false-positive rate for "
            "same-type mutations and independent additions."
        ),
        direction=None, tier=None,
        mut_a="change_scalar_value", mut_b="add_unrelated_function",
    )


def _both_branches_change_same_function() -> ScenarioSpec:
    """Both A and B change the same function — edge case for seed attribution."""
    base = """\
def shared(x):
    return x * 2


def caller(data):
    return shared(data)
"""
    # Both branches change `shared` differently
    branch_a = base.replace("return x * 2", 'return {"value": x * 2}')
    branch_b = base.replace("return x * 2", "return x * 3")
    return _spec(
        "both_change_same_function",
        base, branch_a, branch_b,
        expected=False,
        rationale="Both A and B change the same function — diff_parser seeds both as A and B.",
        hypothesis=(
            "When the same function appears in both diff_result.seeds_a and seeds_b, "
            "seed attribution conflicts. paxpy should not crash. The result "
            "(conflict or not) is a documentation of actual behaviour, not an assertion."
        ),
        direction=None, tier=None,
        mut_a="return_scalar_to_dict", mut_b="change_scalar_multiplier",
    )
