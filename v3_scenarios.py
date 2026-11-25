"""Prespecified v0.3 scenario split, transport regimes and drainage follow-up.

The original eleven families are imported unchanged from v0.2. Environment
truth and follow-up metadata are evidence for the harness, never observations
or a terminal-time announcement supplied to a policy.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

from capacity_scenarios import (
    Scenario, LEGACY_GROUPS, GENERATED_GROUPS, GROUPS, GROUP_INDEX,
    _generated, BUDGET, BASE, NOMINAL_PRODUCTIVITY,
)
from simulation import load_trace, scenarios as old_scenarios

ROOT = Path(__file__).resolve().parent
SPLIT_SEEDS = {
    # These cases were part of v0.2 validation and have already been inspected.
    # They are deliberately labeled development in the new study.
    "development": tuple(range(1000, 1004)),
    "validation": tuple(range(2000, 2032)),
}
TAIL_MINUTES = 32
FOLLOWUP_MINUTES = TAIL_MINUTES
REGIME_DELAYS = {"instant": 0, "transit_1": 1, "transit_3": 3}
REGIMES = tuple(REGIME_DELAYS)
ABC_SEEDS = (11, 29, 47)
DEFAULT_POLICIES = (
    "static", "abc_capacity", "capacity_v2",
    "capacity_balance", "v3_signal_only", "v3_full",
)
POLICIES = DEFAULT_POLICIES
DEFAULT_SEEDS_BY_POLICY = {
    policy: ABC_SEEDS if policy == "abc_capacity" else (None,)
    for policy in DEFAULT_POLICIES
}


def policy_seeds(policy, seeds_by_policy=None):
    """Replicate specification can be supplied by the frozen final protocol."""
    mapping = DEFAULT_SEEDS_BY_POLICY if seeds_by_policy is None else seeds_by_policy
    if policy not in mapping:
        raise ValueError(f"Policy has no declared replicate specification: {policy}")
    seeds = tuple(mapping[policy])
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError(f"Empty or duplicate policy seeds: {policy}")
    if any(seed is not None and type(seed) is not int for seed in seeds):
        raise ValueError(f"Invalid policy seed: {policy}")
    if None in seeds and len(seeds) != 1:
        raise ValueError(f"Deterministic and stochastic draws mixed: {policy}")
    return seeds


def generate_scenarios(split="development"):
    """Return original-horizon scenarios; do not append follow-up invisibly."""
    if split not in SPLIT_SEEDS:
        raise ValueError(f"Unknown split: {split}")
    seeds = SPLIT_SEEDS[split]
    trace = load_trace(ROOT / "data/load_test_summary_per_minute.csv")
    result = [
        Scenario(group, seed, arrivals, productivity, "legacy_flash", {
            "generation": "unchanged original Flash Sale generator; v0.3 seed split",
            "trace": "data/load_test_summary_per_minute.csv",
        })
        for group, seed, arrivals, productivity in old_scenarios(trace, seeds)
    ]
    result.extend(_generated(group, seed) for group in GENERATED_GROUPS for seed in seeds)
    return result


def drain_arrays(scenario):
    """Return read-only environment arrays with exactly 32 follow-up minutes.

    Follow-up receives zero external arrivals and retains the last *true*
    productivity row. This is a modeling assumption, not a measured prediction
    or a controller-visible end condition. Original horizon is scenario.horizon.
    """
    arrivals = np.concatenate([scenario.arrivals, np.zeros(TAIL_MINUTES)])
    productivity = np.vstack([
        scenario.productivity,
        np.repeat(scenario.productivity[-1:, :], TAIL_MINUTES, axis=0),
    ])
    arrivals.setflags(write=False)
    productivity.setflags(write=False)
    return arrivals, productivity


def scenario_manifest(split="development", include_arrays=False):
    cases = generate_scenarios(split)
    evidence = []
    for case in cases:
        item = case.manifest(include_arrays=include_arrays)
        extended_arrivals, extended_productivity = drain_arrays(case)
        item.update({
            "original_horizon": case.horizon,
            "followup_minutes": TAIL_MINUTES,
            "total_horizon": case.horizon + TAIL_MINUTES,
            "extended_arrivals_sha256": hashlib.sha256(
                extended_arrivals.astype("<f8").tobytes()).hexdigest(),
            "extended_productivity_sha256": hashlib.sha256(
                extended_productivity.astype("<f8").tobytes()).hexdigest(),
        })
        if include_arrays:
            item["extended_arrivals"] = extended_arrivals.tolist()
            item["extended_productivity"] = extended_productivity.tolist()
        evidence.append(item)
    result = {
        "version": "0.3.0",
        "split": split,
        "scenario_count": len(cases),
        "scenario_regime_pairs": len(cases) * len(REGIMES),
        "seeds": list(SPLIT_SEEDS[split]),
        "regime_delays_minutes": dict(REGIME_DELAYS),
        "followup": {
            "minutes": TAIL_MINUTES,
            "arrivals": "zero",
            "productivity": "constant last true original-horizon row",
            "scope": "modeling assumption; future arrivals, productivity and terminal schedule hidden from policy",
        },
        "cases": evidence,
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    result["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def expected_counts(split="development", policies=None, seeds_by_policy=None):
    """Count episodes/minutes without generating or evaluating validation cases."""
    if split not in SPLIT_SEEDS:
        raise ValueError(f"Unknown split: {split}")
    policies = tuple(DEFAULT_POLICIES if policies is None else policies)
    mapping = DEFAULT_SEEDS_BY_POLICY if seeds_by_policy is None else seeds_by_policy
    draws = sum(len(policy_seeds(policy, mapping)) for policy in policies)
    n = len(SPLIT_SEEDS[split])
    cases = n * len(GROUPS)
    original_minutes_per_draw = n * (len(LEGACY_GROUPS) * 60 + len(GENERATED_GROUPS) * 96)
    return {
        "scenarios": cases,
        "regimes": len(REGIMES),
        "scenario_regime_pairs": cases * len(REGIMES),
        "policy_draws_per_case": draws,
        "episodes": cases * len(REGIMES) * draws,
        "original_minutes": original_minutes_per_draw * len(REGIMES) * draws,
        "followup_minutes": cases * len(REGIMES) * draws * TAIL_MINUTES,
        "total_minutes": (original_minutes_per_draw + cases * TAIL_MINUTES) * len(REGIMES) * draws,
    }
