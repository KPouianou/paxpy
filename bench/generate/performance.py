"""Large-SDG scenario generators for the performance bucket.

These scenarios are not designed to test correctness — they exist to measure
how paxpy's runtime and memory scale with SDG size. Each scenario has a
controlled call graph size (node count approximation) produced by combining
fan-out and call depth.

Approximate SDG node count for a chain of depth D with fan-out F:
    nodes ≈ D * F + D   (each level adds F aux functions + 1 chain function)

Target sizes: 50, 150, 500, 1500, 5000, 15000 nodes.
"""

from __future__ import annotations

from bench.generate.parametric import ScenarioSpec, _fn, _src

# (call_depth, fan_out) pairs chosen to hit approximate node count targets
# nodes ≈ depth * (fan_out + 1) + 2  (rough estimate)
_SIZE_CONFIGS = [
    ("xs", 50, 4, 10),  # depth=4,  fan_out=10  → ~44 chain+aux nodes
    ("sm", 150, 6, 20),  # depth=6,  fan_out=20  → ~126
    ("md", 500, 8, 60),  # depth=8,  fan_out=60  → ~488
    ("lg", 1500, 10, 145),  # depth=10, fan_out=145 → ~1460
    ("xl", 5000, 12, 400),  # depth=12, fan_out=400 → ~4812
    ("xxl", 15000, 15, 990),  # depth=15, fan_out=990 → ~14865
]

_SEEDS = [42, 137, 999, 256, 512]


def all_performance() -> list[ScenarioSpec]:
    specs = []
    for size_label, approx_nodes, depth, fan_out in _SIZE_CONFIGS:
        for seed in _SEEDS:
            specs.append(_make_perf_scenario(size_label, approx_nodes, depth, fan_out, seed))
    return specs


def _make_perf_scenario(
    size_label: str,
    approx_nodes: int,
    call_depth: int,
    fan_out: int,
    seed: int,
) -> ScenarioSpec:
    """Generate a single-file scenario with a controlled large call graph.

    The scenario has a real DF conflict (positive=True) so the detector
    actually runs the full chop algorithm, not just returns early.
    """
    name = f"performance_{size_label}_d{call_depth}_fo{fan_out}_s{seed}"

    # Build chain
    chain_len = call_depth + 1
    prefixes = ["lead"] + [f"mid{i}" for i in range(1, chain_len - 1)] + ["tail"]
    chain = [_fn(p, seed, i) for i, p in enumerate(prefixes)]
    aux = [_fn("aux", seed, 100 + i) for i in range(fan_out)]

    producer = chain[0]
    consumer = chain[-1]

    # Build source blocks
    def aux_fns() -> list[str]:
        return [f"def {a}(x):\n    return x + 1" for a in aux]

    def chain_fns(producer_body: str) -> list[str]:
        blocks = []
        # Producer
        blocks.append(f"def {producer}(x):\n    {producer_body}")
        # Intermediates
        for i, fn in enumerate(chain[1:-1]):
            prev = chain[i]
            extra = "\n    ".join(f"_ = {a}(x)" for a in aux[:3])  # call a few aux fns
            blocks.append(f"def {fn}(x):\n    result = {prev}(x)\n    {extra}\n    return result")
        # Consumer
        prev = chain[-2] if len(chain) > 1 else producer
        blocks.append(f"def {consumer}(data):\n    result = {prev}(data)\n    return result")
        return blocks

    base_prod = "return x * 2"
    a_prod = 'return {"value": x * 2, "raw": x}'
    base_cons = "    return result"
    b_cons_extra = "    return result + 1"

    def make_source(prod_body: str, cons_extra: str) -> str:
        blocks = aux_fns() + chain_fns(prod_body)
        src = _src(*blocks)
        if cons_extra != base_cons:
            src = src.replace(
                f"def {consumer}(data):\n    result = {chain[-2] if len(chain) > 1 else producer}(data)\n    return result",
                f"def {consumer}(data):\n    result = {chain[-2] if len(chain) > 1 else producer}(data)\n{cons_extra}",
            )
        return src

    base_source = make_source(base_prod, base_cons)
    a_source = make_source(a_prod, base_cons)
    b_source = make_source(base_prod, b_cons_extra)

    return ScenarioSpec(
        name=name,
        bucket="performance",
        conflict_type="DATA_FLOW",
        call_depth=call_depth,
        fan_out=fan_out,
        file_count=1,
        name_ambiguity=0,
        positive=True,
        random_seed=seed,
        complexity_tier="performance",
        base_source={"module.py": base_source},
        branch_a_source={"module.py": a_source},
        branch_b_source={"module.py": b_source},
        expected_conflict=True,
        expected_direction="B_to_A",
        expected_tier=1,
        label_rationale=(
            f"Performance scenario: ~{approx_nodes} SDG nodes "
            f"(depth={call_depth}, fan_out={fan_out}). "
            "Standard DF conflict to exercise full detection pipeline."
        ),
        mutation_a="return_scalar_to_dict",
        mutation_b="arithmetic_on_return",
        hypothesis=(
            f"Target SDG size ≈{approx_nodes} nodes. "
            "Measures sdg_build_ms, detection_ms, and actual node count. "
            "Expected: conflict detected; primary interest is runtime, not correctness."
        ),
    )
