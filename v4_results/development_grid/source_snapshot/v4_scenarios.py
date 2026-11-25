"""Prespecified v0.4 cases, nested physical regimes and common sensor noise.

The eleven arrival/productivity families are unchanged.  Known v0.3 cases are
development data; new validation seeds must remain unexamined until the v0.4
protocol and implementation are frozen.  This module contains environment
truth and evidence metadata, not a controller observation interface.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

import numpy as np

from capacity_scenarios import (
    Scenario as OriginalScenario, LEGACY_GROUPS, GENERATED_GROUPS, GROUPS,
    GROUP_INDEX, _generated, BUDGET, BASE, NOMINAL_PRODUCTIVITY,
)
from simulation import load_trace, scenarios as old_scenarios
from v4_environment import EnvironmentConfig, REGIMES, REGIME_CONFIGS, REGIME_DELAYS

ROOT = Path(__file__).resolve().parent
FOLLOWUP_MINUTES = 32
TAIL_MINUTES = FOLLOWUP_MINUTES
SPLIT_SEEDS = {
    "development": tuple(range(2000, 2008)),
    "validation": tuple(range(4000, 4064)),
}
POLICIES = (
    "static", "capacity_v2", "capacity_balance", "v3_full",
    "v4_hybrid", "v4_no_risk", "v4_no_recovery",
)
DEFAULT_POLICIES = POLICIES
SENSOR_NOISE_SIGMA = 0.05
SENSOR_CHANNELS = ("arrival", "pre_queue", "end_queue", "flow")
DEFAULT_SEEDS_BY_POLICY = {policy: (None,) for policy in POLICIES}


def policy_seeds(policy, seeds_by_policy=None):
    """All seven policies are deterministic in v0.4."""
    if policy not in POLICIES:
        raise ValueError(f"Unknown v0.4 policy: {policy}")
    if seeds_by_policy is not None and tuple(seeds_by_policy.get(policy, ())) != (None,):
        raise ValueError("v0.4 has one deterministic rollout per policy/case/regime")
    return (None,)


def _hash_array(array):
    return hashlib.sha256(np.asarray(array).astype("<f8").tobytes()).hexdigest()


def generate_sensor_noise(group, seed, total_horizon):
    """Draw common, source-indexed mean-one lognormal sensor multipliers.

    Channels have separate RNG streams.  The log-space standard deviation is
    0.05; the mean parameter -sigma**2/2 makes the multiplier mean exactly one
    in distribution.  These draws do not change physical arrival or service
    arrays and are used only in the delayed_noisy observation adapter.
    """
    if type(seed) is not int or seed < 0 or type(total_horizon) is not int or total_horizon <= 0:
        raise ValueError("Noise requires a nonnegative seed and positive horizon")
    # Unknown labels are supported for deterministic hand-built audit cases;
    # registered study families always use their fixed original group index.
    group_index = GROUP_INDEX.get(group)
    if group_index is None:
        group_index = int.from_bytes(hashlib.sha256(str(group).encode()).digest()[:4], "little")
    result = {}
    for channel_index, channel in enumerate(SENSOR_CHANNELS):
        rng = np.random.default_rng(np.random.SeedSequence(
            [20261125, 4, int(group_index), seed, channel_index]))
        shape = (total_horizon,) if channel == "arrival" else (total_horizon, 3)
        values = rng.lognormal(
            mean=-0.5 * SENSOR_NOISE_SIGMA**2,
            sigma=SENSOR_NOISE_SIGMA,
            size=shape,
        )
        values.setflags(write=False)
        result[channel] = values
    return MappingProxyType(result)


@dataclass(frozen=True)
class Scenario(OriginalScenario):
    """Original physical case plus independent, common observation errors."""
    sensor_noise: dict = field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
        object.__setattr__(self, "metadata", deepcopy(self.metadata))
        given = self.sensor_noise
        if not given:
            given = generate_sensor_noise(self.group, self.seed, self.horizon + FOLLOWUP_MINUTES)
        if set(given) != set(SENSOR_CHANNELS):
            raise ValueError("Sensor noise must contain the four declared channels")
        copied = {}
        for channel in SENSOR_CHANNELS:
            values = np.array(given[channel], dtype=float, copy=True)
            shape = (self.horizon + FOLLOWUP_MINUTES,)
            if channel != "arrival":
                shape += (3,)
            if values.shape != shape or not np.isfinite(values).all() or np.any(values <= 0):
                raise ValueError(f"Invalid {channel} sensor noise array")
            values.setflags(write=False)
            copied[channel] = values
        object.__setattr__(self, "sensor_noise", MappingProxyType(copied))

    def manifest(self, include_arrays=False):
        result = super().manifest(include_arrays=include_arrays)
        result["sensor_noise_shapes"] = {
            channel: list(values.shape) for channel, values in self.sensor_noise.items()
        }
        result["sensor_noise_sha256"] = {
            channel: _hash_array(values) for channel, values in self.sensor_noise.items()
        }
        if include_arrays:
            result["sensor_noise"] = {
                channel: values.tolist() for channel, values in self.sensor_noise.items()
            }
        return result


def generate_scenarios(split="development"):
    """Generate unchanged original-horizon cases and fixed sensor multipliers.

    Do not call this on validation before freezing the new implementation.
    expected_counts() describes the held-out split without generating it.
    """
    if split not in SPLIT_SEEDS:
        raise ValueError(f"Unknown split: {split}")
    seeds = SPLIT_SEEDS[split]
    trace = load_trace(ROOT / "data/load_test_summary_per_minute.csv")
    result = [
        Scenario(group, seed, arrivals, productivity, "legacy_flash", {
            "generation": "unchanged original Flash Sale generator; v0.4 seed split",
            "trace": "data/load_test_summary_per_minute.csv",
        })
        for group, seed, arrivals, productivity in old_scenarios(trace, seeds)
    ]
    for group in GENERATED_GROUPS:
        for seed in seeds:
            original = _generated(group, seed)
            result.append(Scenario(
                original.group, original.seed, original.arrivals,
                original.productivity, original.source, original.metadata,
            ))
    return result


def drain_arrays(scenario):
    """Return original truth plus 32 zero-arrival drainage minutes.

    Last true original-horizon productivity is held constant throughout the
    follow-up.  This is a modeling assumption.  Resources in transit, startup
    and noisy/lagged observation delivery continue normally.  Controllers are
    never told the original horizon, future arrivals or this end condition.
    """
    arrivals = np.concatenate([scenario.arrivals, np.zeros(FOLLOWUP_MINUTES)])
    productivity = np.vstack([
        scenario.productivity,
        np.repeat(scenario.productivity[-1:, :], FOLLOWUP_MINUTES, axis=0),
    ])
    arrivals.setflags(write=False)
    productivity.setflags(write=False)
    return arrivals, productivity


def scenario_manifest(split="development", include_arrays=False):
    """Serializable environment evidence, including common-noise hashes."""
    cases = generate_scenarios(split)
    evidence = []
    for case in cases:
        item = case.manifest(include_arrays=include_arrays)
        arrivals, productivity = drain_arrays(case)
        item.update({
            "original_horizon": case.horizon,
            "followup_minutes": FOLLOWUP_MINUTES,
            "total_horizon": case.horizon + FOLLOWUP_MINUTES,
            "extended_arrivals_sha256": _hash_array(arrivals),
            "extended_productivity_sha256": _hash_array(productivity),
        })
        if include_arrays:
            item["extended_arrivals"] = arrivals.tolist()
            item["extended_productivity"] = productivity.tolist()
        evidence.append(item)
    result = {
        "version": "0.4.0",
        "split": split,
        "scenario_count": len(cases),
        "scenario_regime_pairs": len(cases) * len(REGIMES),
        "seeds": list(SPLIT_SEEDS[split]),
        "regimes": {name: config.as_dict() for name, config in REGIME_CONFIGS.items()},
        "policies": list(POLICIES),
        "policy_replicates": {policy: list(policy_seeds(policy)) for policy in POLICIES},
        "followup": {
            "minutes": FOLLOWUP_MINUTES,
            "arrivals": "zero",
            "productivity": "constant last true original-horizon row",
            "scope": "modeling assumption; future truth and terminal schedule hidden from policy",
            "state": "resources, startup, pipeline and observation delivery continue",
        },
        "sensor_noise": {
            "distribution": "independent mean-one multiplicative lognormal",
            "log_mean": -0.5 * SENSOR_NOISE_SIGMA**2,
            "log_sigma": SENSOR_NOISE_SIGMA,
            "channels": list(SENSOR_CHANNELS),
            "index": "source minute, shared across every policy",
            "resource_ledger": "exact; never noised",
            "decision_at_t": "start queue and arrival from t-lag; negative source is zero coldstart",
            "feedback_at_end_t": "end queue and flow from t-lag; paired with same-source effective staff",
            "latest_feedback_at_decision_t": "source t-1-lag",
            "timestamp": "actual source and age recorded; legacy API clock may be retimestamped",
        },
        "cases": evidence,
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    result["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def expected_counts(split="development", policies=None, seeds_by_policy=None, regimes=None):
    """Count episodes/minutes without touching held-out environment arrays."""
    if split not in SPLIT_SEEDS:
        raise ValueError(f"Unknown split: {split}")
    policies = tuple(POLICIES if policies is None else policies)
    regimes = tuple(REGIMES if regimes is None else regimes)
    if not policies or len(set(policies)) != len(policies):
        raise ValueError("Empty or duplicate policy selection")
    if not regimes or len(set(regimes)) != len(regimes) or any(r not in REGIMES for r in regimes):
        raise ValueError("Invalid regime selection")
    draws = sum(len(policy_seeds(policy, seeds_by_policy)) for policy in policies)
    seeds_per_group = len(SPLIT_SEEDS[split])
    cases = seeds_per_group * len(GROUPS)
    original_per_draw = seeds_per_group * (
        len(LEGACY_GROUPS) * 60 + len(GENERATED_GROUPS) * 96)
    episodes = cases * len(regimes) * draws
    original_minutes = original_per_draw * len(regimes) * draws
    followup_minutes = episodes * FOLLOWUP_MINUTES
    return {
        "scenarios": cases,
        "groups": len(GROUPS),
        "seeds_per_group": seeds_per_group,
        "regimes": len(regimes),
        "scenario_regime_pairs": cases * len(regimes),
        "policy_draws_per_case": draws,
        "episodes": episodes,
        "original_minutes": original_minutes,
        "followup_minutes": followup_minutes,
        "total_minutes": original_minutes + followup_minutes,
    }
