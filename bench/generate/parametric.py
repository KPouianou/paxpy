"""Parametric scenario generator for the paxpy benchmark.

Generates deterministic synthetic Python scenarios covering all four conflict
types across configurable complexity dimensions:

  call_depth     — number of call-graph edges between the two seed functions
  fan_out        — extra callees per intermediate (increases BFS breadth)
  file_count     — 1=single file, 2=producer and consumer in separate files
  name_ambiguity — extra functions with the same name as the producer in
                   additional files (over-approximation pressure; requires
                   file_count >= 2)
  positive       — True=conflict expected, False=independent changes, no path

Each scenario is identified by a name derived from its parameters, making it
idempotent: seeding twice with the same parameters produces the same name and
is handled by INSERT OR IGNORE in the database.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Scenario specification
# ---------------------------------------------------------------------------


@dataclass
class ScenarioSpec:
    name: str
    bucket: str
    conflict_type: str | None       # None for negatives
    call_depth: int
    fan_out: int
    file_count: int
    name_ambiguity: int
    positive: bool
    random_seed: int
    complexity_tier: str
    base_source: dict[str, str]     # filename → Python source
    branch_a_source: dict[str, str]
    branch_b_source: dict[str, str]
    expected_conflict: bool
    expected_direction: str | None  # "B_to_A" | "A_to_B" | None
    expected_tier: int | None       # 1 | 2 | None
    label_rationale: str
    mutation_a: str
    mutation_b: str
    hypothesis: str = ""


# ---------------------------------------------------------------------------
# Parameter grid
# ---------------------------------------------------------------------------

CALL_DEPTHS     = [1, 2, 3, 5, 7]
FAN_OUTS        = [1, 2]
FILE_COUNTS     = [1, 2]
NAME_AMBIGUITIES = [0, 2]          # only used when file_count >= 2
SEEDS           = [42, 137, 999]
CONFLICT_TYPES  = ["DATA_FLOW", "CONFLUENCE", "OVERRIDE_ASSIGNMENT", "CONTROL_DEPENDENCY"]


def all_correctness_params() -> list[dict]:
    """Return all (conflict_type, call_depth, fan_out, file_count, name_ambiguity,
    positive, seed) combinations for the correctness bucket."""
    params = []
    for ct in CONFLICT_TYPES:
        for cd in CALL_DEPTHS:
            for fo in FAN_OUTS:
                for fc in FILE_COUNTS:
                    ambigs = NAME_AMBIGUITIES if fc >= 2 else [0]
                    for na in ambigs:
                        for pos in [True, False]:
                            for s in SEEDS:
                                params.append(dict(
                                    conflict_type=ct, call_depth=cd, fan_out=fo,
                                    file_count=fc, name_ambiguity=na,
                                    positive=pos, random_seed=s,
                                ))
    return params


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _complexity_tier(call_depth: int, file_count: int, name_ambiguity: int, fan_out: int) -> str:
    if call_depth <= 2 and file_count == 1 and name_ambiguity == 0 and fan_out == 1:
        return "simple"
    if call_depth <= 4 and file_count <= 2 and name_ambiguity <= 2 and fan_out <= 2:
        return "moderate"
    return "complex"


def _fn(prefix: str, seed: int, idx: int = 0) -> str:
    """Deterministic short function name."""
    h = abs(hash(f"{seed}|{idx}|{prefix}")) % 9000 + 1000
    return f"{prefix}_{h}"


def _src(*blocks: str) -> str:
    """Join source blocks with double newlines, strip leading/trailing blank lines."""
    return "\n\n".join(b.strip() for b in blocks if b.strip()) + "\n"


def _assign_files(
    names: list[str],
    file_count: int,
    ambig_names: list[str],
    seed: int,
) -> dict[str, list[str]]:
    """Return {filename: [function_names]} assignment.

    Producer (names[0]) goes in module_a.py; consumer (names[-1]) goes in
    module_b.py (or module_a.py if file_count==1). Intermediates are split
    evenly. Ambiguous copies of the producer name go in their own files.
    """
    if file_count == 1:
        return {"module.py": names}

    # Split chain: first half in a, second half in b
    mid = max(1, len(names) // 2)
    assignment: dict[str, list[str]] = {
        "module_a.py": names[:mid],
        "module_b.py": names[mid:],
    }
    for i, aname in enumerate(ambig_names):
        assignment[f"module_ambig_{i}.py"] = [aname]
    return assignment


# ---------------------------------------------------------------------------
# Source builders per conflict type
# ---------------------------------------------------------------------------


def _build_df(
    chain: list[str],
    aux: list[str],
    positive: bool,
    file_assign: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """DATA_FLOW: producer return type changes; consumer does arithmetic on it."""
    producer = chain[0]
    consumer = chain[-1]

    # ---- base function bodies ----
    def producer_base() -> str:
        return f"def {producer}(x):\n    return x * 2"

    def producer_a() -> str:       # branch A: returns dict
        return f"def {producer}(x):\n    return {{\"value\": x * 2, \"raw\": x}}"

    def consumer_base(prev: str) -> str:
        return f"def {consumer}(data):\n    result = {prev}(data)\n    return result"

    def consumer_b(prev: str) -> str:  # branch B: arithmetic (incompatible with dict)
        return f"def {consumer}(data):\n    result = {prev}(data)\n    return result + 1"

    def aux_fns() -> list[str]:
        return [f"def {a}(x):\n    return x" for a in aux]

    # chain[1:-1] are intermediates (empty when call_depth==1)
    mids = chain[1:-1]
    prev_of_consumer = chain[-2] if len(chain) > 1 else producer

    def make_sources(prod_fn, cons_fn) -> dict[str, str]:
        all_blocks: dict[str, list[str]] = {fn: [] for fn in file_assign}
        # map function name → file
        fn_to_file: dict[str, str] = {}
        for fname, fns in file_assign.items():
            for fn in fns:
                fn_to_file[fn] = fname

        # aux functions go in same file as producer
        prod_file = fn_to_file.get(producer, list(file_assign.keys())[0])
        for block in aux_fns():
            all_blocks[prod_file].append(block)

        # producer
        all_blocks[prod_file].append(prod_fn())

        # intermediates — one block per intermediate, each calling the previous in chain
        for j, mid in enumerate(mids):
            mid_file = fn_to_file.get(mid, prod_file)
            prev = chain[j]  # j=0 → producer, j=1 → first mid, etc.
            extra_lines = "".join(f"\n    _ = {a}(x)" for a in aux)
            body = f"    result = {prev}(x){extra_lines}\n    return result"
            all_blocks[mid_file].append(f"def {mid}(x):\n{body}")

        # consumer
        cons_file = fn_to_file.get(consumer, list(file_assign.keys())[-1])
        all_blocks[cons_file].append(cons_fn(prev_of_consumer))

        return {f: _src(*blocks) for f, blocks in all_blocks.items() if blocks}

    if positive:
        base   = make_sources(producer_base, consumer_base)
        a_src  = make_sources(producer_a,    consumer_base)
        b_src  = make_sources(producer_base, consumer_b)
    else:
        # Negative: A and B change independent functions, no shared call path
        solo_a = chain[0].replace(chain[0].split("_")[0], "isola")
        solo_b = consumer.replace(consumer.split("_")[0], "isolb")

        def make_neg_sources(a_changes: bool, b_changes: bool) -> dict[str, str]:
            sources = make_sources(producer_base, consumer_base)
            # Add independent functions to the first file
            first_file = list(file_assign.keys())[0]
            base_extra = (
                f"def {solo_a}():\n    return 1\n\ndef {solo_b}():\n    return 2"
            )
            a_extra = (
                f"def {solo_a}():\n    return 99\n\ndef {solo_b}():\n    return 2"
            )
            b_extra = (
                f"def {solo_a}():\n    return 1\n\ndef {solo_b}():\n    return 88"
            )
            extra = base_extra
            if a_changes and not b_changes:
                extra = a_extra
            elif b_changes and not a_changes:
                extra = b_extra
            sources = dict(sources)
            sources[first_file] = sources.get(first_file, "") + "\n\n" + extra + "\n"
            return sources

        base  = make_neg_sources(False, False)
        a_src = make_neg_sources(True,  False)
        b_src = make_neg_sources(False, True)

    return base, a_src, b_src


def _build_cf(
    chain: list[str],
    aux: list[str],
    positive: bool,
    file_assign: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """CONFLUENCE: helper return type changes; accumulator changes how it aggregates."""
    helper   = chain[0]
    accum    = chain[-1]
    prev_acc = chain[-2] if len(chain) > 1 else helper

    def helper_base() -> str:
        return f"def {helper}(v):\n    return v * 10"

    def helper_a() -> str:
        return f"def {helper}(v):\n    return {{\"result\": v * 10, \"raw\": v}}"

    def accum_base(prev: str) -> str:
        lines = [
            f"def {accum}(values):",
            "    total = 0",
            "    for v in values:",
            f"        total = total + {prev}(v)",
            "    return total",
        ]
        return "\n".join(lines)

    def accum_b(prev: str) -> str:   # different accumulation pattern
        lines = [
            f"def {accum}(values):",
            "    total = 0",
            "    for v in values:",
            f"        total += {prev}(v)",
            "    return total",
        ]
        return "\n".join(lines)

    def aux_fns() -> list[str]:
        return [f"def {a}(x):\n    return x * 5" for a in aux]

    def intermediates() -> list[str]:
        blocks = []
        for i, fn in enumerate(chain[1:-1]):
            prev = chain[i]
            blocks.append(f"def {fn}(v):\n    r = {prev}(v)\n    return r")
        return blocks

    def make_sources(h_fn, a_fn) -> dict[str, str]:
        fn_to_file: dict[str, str] = {}
        for fname, fns in file_assign.items():
            for fn in fns:
                fn_to_file[fn] = fname
        all_blocks: dict[str, list[str]] = {f: [] for f in file_assign}
        hfile = fn_to_file.get(helper, list(file_assign.keys())[0])
        afile = fn_to_file.get(accum, list(file_assign.keys())[-1])
        for b in aux_fns():
            all_blocks[hfile].append(b)
        all_blocks[hfile].append(h_fn())
        for b in intermediates():
            all_blocks[hfile].append(b)
        all_blocks[afile].append(a_fn(prev_acc))
        return {f: _src(*blocks) for f, blocks in all_blocks.items() if blocks}

    if positive:
        base  = make_sources(helper_base, accum_base)
        a_src = make_sources(helper_a,    accum_base)
        b_src = make_sources(helper_base, accum_b)
    else:
        solo_a = helper.replace(helper.split("_")[0], "isola")
        solo_b = accum.replace(accum.split("_")[0], "isolb")

        def make_neg(a_changes: bool, b_changes: bool) -> dict[str, str]:
            s = make_sources(helper_base, accum_base)
            ff = list(file_assign.keys())[0]
            bk = (
                f"def {solo_a}():\n    return {'9' if a_changes else '1'}\n\n"
                f"def {solo_b}():\n    return {'8' if b_changes else '2'}"
            )
            s = dict(s)
            s[ff] = s.get(ff, "") + "\n\n" + bk + "\n"
            return s

        base  = make_neg(False, False)
        a_src = make_neg(True,  False)
        b_src = make_neg(False, True)

    return base, a_src, b_src


def _build_oa(
    chain: list[str],
    aux: list[str],
    positive: bool,
    file_assign: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """OVERRIDE_ASSIGNMENT: A changes what transform() returns; B overrides config."""
    transform = chain[0]
    pipeline  = chain[-1]
    prev_pipe = chain[-2] if len(chain) > 1 else transform

    def transform_base() -> str:
        return f"def {transform}(x):\n    return x + 1"

    def transform_a() -> str:
        return f"def {transform}(x):\n    return {{\"value\": x + 1, \"scale\": 1}}"

    def apply_fn(seed: int) -> tuple[str, str]:
        name = _fn("apply", seed, 50)
        return name, f"def {name}(cfg):\n    return cfg"

    def pipeline_base(prev: str, apply: str) -> str:
        lines = [
            f"def {pipeline}(x):",
            f"    config = {prev}(x)",
            f"    return {apply}(config)",
        ]
        return "\n".join(lines)

    def pipeline_b(prev: str, apply: str) -> str:  # overrides config
        lines = [
            f"def {pipeline}(x):",
            f"    config = {prev}(x)",
            "    config = 0",               # override assignment
            f"    return {apply}(config)",
        ]
        return "\n".join(lines)

    def intermediates() -> list[str]:
        blocks = []
        for i, fn in enumerate(chain[1:-1]):
            prev = chain[i]
            blocks.append(f"def {fn}(x):\n    r = {prev}(x)\n    return r")
        return blocks

    def make_sources(t_fn, p_fn, apply_name: str) -> dict[str, str]:
        fn_to_file: dict[str, str] = {}
        for fname, fns in file_assign.items():
            for fn in fns:
                fn_to_file[fn] = fname
        all_blocks: dict[str, list[str]] = {f: [] for f in file_assign}
        tfile = fn_to_file.get(transform, list(file_assign.keys())[0])
        pfile = fn_to_file.get(pipeline,  list(file_assign.keys())[-1])
        all_blocks[tfile].append(t_fn())
        for b in intermediates():
            all_blocks[tfile].append(b)
        _, apply_body = apply_fn(0)
        all_blocks[pfile].append(apply_body)
        all_blocks[pfile].append(p_fn(prev_pipe, apply_name))
        return {f: _src(*blocks) for f, blocks in all_blocks.items() if blocks}

    apply_name, _ = apply_fn(0)

    if positive:
        base  = make_sources(transform_base, pipeline_base, apply_name)
        a_src = make_sources(transform_a,    pipeline_base, apply_name)
        b_src = make_sources(transform_base, pipeline_b,    apply_name)
    else:
        solo_a = transform.replace(transform.split("_")[0], "isola")
        solo_b = pipeline.replace(pipeline.split("_")[0], "isolb")

        def make_neg(a_ch: bool, b_ch: bool) -> dict[str, str]:
            s = make_sources(transform_base, pipeline_base, apply_name)
            ff = list(file_assign.keys())[0]
            bk = (
                f"def {solo_a}():\n    return {'10' if a_ch else '1'}\n\n"
                f"def {solo_b}():\n    return {'20' if b_ch else '2'}"
            )
            s = dict(s)
            s[ff] = s.get(ff, "") + "\n\n" + bk + "\n"
            return s

        base  = make_neg(False, False)
        a_src = make_neg(True,  False)
        b_src = make_neg(False, True)

    return base, a_src, b_src


def _build_pdg(
    chain: list[str],
    aux: list[str],
    positive: bool,
    file_assign: dict[str, list[str]],
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """CONTROL_DEPENDENCY: A changes callee return type; B changes the guard predicate."""
    action = chain[0]
    check  = chain[-1]
    prev_check = chain[-2] if len(chain) > 1 else action

    def action_base() -> str:
        return f"def {action}():\n    return 42"

    def action_a() -> str:
        return f"def {action}():\n    return {{\"code\": 42, \"status\": \"ok\"}}"

    def check_base(prev: str) -> str:
        lines = [
            f"def {check}(x):",
            "    if x > 0:",
            f"        result = {prev}()",
            "        return result",
            "    return 0",
        ]
        return "\n".join(lines)

    def check_b(prev: str) -> str:   # tighter predicate
        lines = [
            f"def {check}(x):",
            "    if x > 10:",
            f"        result = {prev}()",
            "        return result",
            "    return 0",
        ]
        return "\n".join(lines)

    def intermediates() -> list[str]:
        blocks = []
        for i, fn in enumerate(chain[1:-1]):
            prev = chain[i]
            # intermediates just pass through
            blocks.append(f"def {fn}(x):\n    r = {prev}()\n    return r")
        return blocks

    def make_sources(act_fn, chk_fn) -> dict[str, str]:
        fn_to_file: dict[str, str] = {}
        for fname, fns in file_assign.items():
            for fn in fns:
                fn_to_file[fn] = fname
        all_blocks: dict[str, list[str]] = {f: [] for f in file_assign}
        afile = fn_to_file.get(action, list(file_assign.keys())[0])
        cfile = fn_to_file.get(check,  list(file_assign.keys())[-1])
        all_blocks[afile].append(act_fn())
        for b in intermediates():
            all_blocks[afile].append(b)
        all_blocks[cfile].append(chk_fn(prev_check))
        return {f: _src(*blocks) for f, blocks in all_blocks.items() if blocks}

    if positive:
        base  = make_sources(action_base, check_base)
        a_src = make_sources(action_a,    check_base)
        b_src = make_sources(action_base, check_b)
    else:
        solo_a = action.replace(action.split("_")[0], "isola")
        solo_b = check.replace(check.split("_")[0], "isolb")

        def make_neg(a_ch: bool, b_ch: bool) -> dict[str, str]:
            s = make_sources(action_base, check_base)
            ff = list(file_assign.keys())[0]
            bk = (
                f"def {solo_a}():\n    return {'99' if a_ch else '1'}\n\n"
                f"def {solo_b}():\n    return {'88' if b_ch else '2'}"
            )
            s = dict(s)
            s[ff] = s.get(ff, "") + "\n\n" + bk + "\n"
            return s

        base  = make_neg(False, False)
        a_src = make_neg(True,  False)
        b_src = make_neg(False, True)

    return base, a_src, b_src


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_BUILDERS = {
    "DATA_FLOW":           _build_df,
    "CONFLUENCE":          _build_cf,
    "OVERRIDE_ASSIGNMENT": _build_oa,
    "CONTROL_DEPENDENCY":  _build_pdg,
}

_RATIONALES = {
    ("DATA_FLOW", True):
        "A changes producer return type (scalar→dict); B does arithmetic on consumer result.",
    ("DATA_FLOW", False):
        "A and B change independent functions with no shared call path.",
    ("CONFLUENCE", True):
        "A changes helper return type; B changes accumulation pattern. Both flow to same sink.",
    ("CONFLUENCE", False):
        "A and B change independent sink functions with no shared callee.",
    ("OVERRIDE_ASSIGNMENT", True):
        "A changes transform() return type; B overrides config=0, discarding transform's output.",
    ("OVERRIDE_ASSIGNMENT", False):
        "A and B write to different variables in unrelated functions.",
    ("CONTROL_DEPENDENCY", True):
        "A changes action() return type; B tightens the guard predicate controlling action().",
    ("CONTROL_DEPENDENCY", False):
        "A and B change unrelated functions with no control or data path between them.",
}

_MUTATIONS = {
    "DATA_FLOW":           ("return_scalar_to_dict", "arithmetic_on_return"),
    "CONFLUENCE":          ("return_scalar_to_dict", "change_accumulation_pattern"),
    "OVERRIDE_ASSIGNMENT": ("return_scalar_to_dict", "override_assignment_to_zero"),
    "CONTROL_DEPENDENCY":  ("return_scalar_to_dict", "tighten_guard_predicate"),
}

_DIRECTIONS = {
    "DATA_FLOW":           "B_to_A",
    "CONFLUENCE":          "B_to_A",
    "OVERRIDE_ASSIGNMENT": "B_to_A",
    "CONTROL_DEPENDENCY":  "B_to_A",
}

_TIERS = {
    "DATA_FLOW":           1,
    "CONFLUENCE":          1,
    "OVERRIDE_ASSIGNMENT": 1,
    "CONTROL_DEPENDENCY":  2,
}


def generate_scenario(
    conflict_type: str,
    call_depth: int,
    fan_out: int,
    file_count: int,
    name_ambiguity: int,
    positive: bool,
    random_seed: int,
) -> ScenarioSpec:
    """Generate a fully-specified benchmark scenario from parameters.

    All outputs are deterministic: the same arguments always produce the same
    scenario name and source code.
    """
    random.Random(random_seed)

    ct_slug = conflict_type.lower().replace("_", "")
    sign    = "pos" if positive else "neg"
    name = (
        f"correctness_{ct_slug}_cd{call_depth}_fo{fan_out}"
        f"_fc{file_count}_na{name_ambiguity}_{sign}_s{random_seed}"
    )

    tier = _complexity_tier(call_depth, file_count, name_ambiguity, fan_out)

    # Build function name chain: chain[0]=producer/source, chain[-1]=consumer/sink
    chain_len = call_depth + 1
    prefixes  = ["lead"] + [f"mid{i}" for i in range(1, chain_len - 1)] + ["tail"]
    chain     = [_fn(p, random_seed, i) for i, p in enumerate(prefixes)]

    # Extra fan-out callees (not on the conflict path)
    aux = [_fn("aux", random_seed, 100 + i) for i in range(max(0, fan_out - 1))]

    # Ambiguous: extra functions sharing producer's base name, in extra files
    # (only meaningful for file_count >= 2, forces over-approximation)
    ambig_names = [
        _fn(chain[0].split("_")[0], random_seed + i + 1, 200 + i)
        for i in range(name_ambiguity if file_count >= 2 else 0)
    ]

    file_assign = _assign_files(chain, file_count, ambig_names, random_seed)

    builder = _BUILDERS[conflict_type]
    base_src, a_src, b_src = builder(chain, aux, positive, file_assign)

    # Add ambiguous-name clones to their own files (for multi-file na>0 scenarios)
    for i, _aname in enumerate(ambig_names):
        fname = f"module_ambig_{i}.py"
        # Clone with same name as producer to stress over-approximation
        clone_body = f"def {chain[0]}(x):\n    return x * 3\n"
        for src_dict in (base_src, a_src, b_src):
            if fname not in src_dict:
                src_dict[fname] = clone_body

    mut_a, mut_b = _MUTATIONS[conflict_type]
    rationale    = _RATIONALES[(conflict_type, positive)]
    direction    = _DIRECTIONS[conflict_type] if positive else None
    exp_tier     = _TIERS[conflict_type]      if positive else None

    return ScenarioSpec(
        name=name,
        bucket="correctness",
        conflict_type=conflict_type if positive else None,
        call_depth=call_depth,
        fan_out=fan_out,
        file_count=file_count,
        name_ambiguity=name_ambiguity,
        positive=positive,
        random_seed=random_seed,
        complexity_tier=tier,
        base_source=base_src,
        branch_a_source=a_src,
        branch_b_source=b_src,
        expected_conflict=positive,
        expected_direction=direction,
        expected_tier=exp_tier,
        label_rationale=rationale,
        mutation_a=mut_a if positive else "independent_change",
        mutation_b=mut_b if positive else "independent_change",
    )
