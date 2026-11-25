"""Frozen-in-advance synthetic scenario families for the capacity gate benchmark.

The five legacy families reuse the original generator unchanged, on fresh seeds.
The six generated families never use the Dropbox arrival trace. Productivity is
simulation truth for the environment and MUST NOT be passed to a controller.
"""
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

import numpy as np

from simulation import GROUPS as OLD_GROUPS, load_trace, scenarios as old_scenarios

ROOT = Path(__file__).resolve().parent
LEGACY_GROUPS = tuple(OLD_GROUPS)
GENERATED_GROUPS = (
    "generated_underload",
    "generated_sustained_processing_fault",
    "generated_switching_fault",
    "generated_pulsed_short_fault",
    "generated_asymmetric_recovery",
    "generated_sustained_overload",
)
GROUPS = LEGACY_GROUPS + GENERATED_GROUPS
GROUP_INDEX = {group: i for i, group in enumerate(GROUPS)}
SPLIT_SEEDS = {
    "development": tuple(range(300, 304)),
    "validation": tuple(range(1000, 1016)),
}
ABC_SEEDS = (11, 29, 47)
POLICIES = ("static", "maxweight", "abc", "abc_guard", "abc_capacity", "capacity_aware")
STOCHASTIC_POLICIES = ("abc", "abc_guard", "abc_capacity")
HORIZON_GENERATED = 96
NOMINAL_PRODUCTIVITY = 200.0
BUDGET = 207.0
BASE = np.array([69.0, 69.0, 69.0])


@dataclass(frozen=True)
class Scenario:
    group: str
    seed: int
    arrivals: np.ndarray
    productivity: np.ndarray
    source: str = "generated_synthetic"
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        arrivals = np.array(self.arrivals, dtype=float, copy=True)
        productivity = np.array(self.productivity, dtype=float, copy=True)
        if arrivals.ndim != 1 or not len(arrivals):
            raise ValueError("Arrivals must be a nonempty one-dimensional vector")
        if productivity.shape != (len(arrivals), 3):
            raise ValueError("Productivity must have horizon by three shape")
        if not np.isfinite(arrivals).all() or np.any(arrivals < 0):
            raise ValueError("Invalid arrivals")
        if not np.isfinite(productivity).all() or np.any(productivity <= 0):
            raise ValueError("Invalid productivity")
        arrivals.setflags(write=False)
        productivity.setflags(write=False)
        object.__setattr__(self, "arrivals", arrivals)
        object.__setattr__(self, "productivity", productivity)

    @property
    def horizon(self):
        return len(self.arrivals)

    @property
    def case_id(self):
        return f"{self.group}:{self.seed}"

    def manifest(self, include_arrays=False):
        """Evidence manifest, not a controller observation."""
        payload = {
            "group": self.group,
            "scenario_seed": self.seed,
            "source": self.source,
            "horizon": self.horizon,
            "arrivals_total": float(self.arrivals.sum()),
            "arrivals_sha256": hashlib.sha256(self.arrivals.astype("<f8").tobytes()).hexdigest(),
            "productivity_sha256": hashlib.sha256(self.productivity.astype("<f8").tobytes()).hexdigest(),
            "metadata": self.metadata,
        }
        if include_arrays:
            payload["arrivals"] = self.arrivals.tolist()
            payload["productivity"] = self.productivity.tolist()
        return payload


def _generated(group, seed):
    """Generate all disturbances before any policy is evaluated.

    Arrival and productivity RNG streams are isolated. No group is selected or
    parameterized using a controller's outcomes. Each group is intentionally a
    modeling assumption rather than a fit to measured service data.
    """
    index = GENERATED_GROUPS.index(group)
    arr_rng = np.random.default_rng(np.random.SeedSequence([20261125, index, seed, 0]))
    prod_rng = np.random.default_rng(np.random.SeedSequence([20261125, index, seed, 1]))
    t = np.arange(HORIZON_GENERATED)
    arrival_scale = float(arr_rng.uniform(.9, 1.1))
    productivity_scale = prod_rng.uniform(.92, 1.08, 3)
    # Slowly varying, bounded productivity noise is a different family from the
    # legacy generator's independent minute noise.
    innovation = prod_rng.normal(0.0, .035, (HORIZON_GENERATED, 3))
    correlated_noise = np.zeros_like(innovation)
    for minute in range(HORIZON_GENERATED):
        previous = correlated_noise[minute - 1] if minute else 0.0
        correlated_noise[minute] = .65 * previous + innovation[minute]
    rate = NOMINAL_PRODUCTIVITY * productivity_scale * np.clip(1 + correlated_noise, .82, 1.18)
    phase = float(arr_rng.uniform(0, 2 * np.pi))
    fault_factor = float(prod_rng.uniform(.42, .62))
    metadata = {
        "generation": "independent synthetic arrivals and latent productivity",
        "arrival_scale": arrival_scale,
        "stage_productivity_scale": productivity_scale.tolist(),
        "fault_factor": fault_factor,
    }
    if group == "generated_underload":
        # No bottleneck, including at the low stage-rate bound. Censored
        # throughput must not be mistaken for a measured service-rate collapse.
        demand = 4800 + 1400 * np.sin(2 * np.pi * t / 31 + phase)
        rate[24:44, 1] *= .75
        demand[64:72] = 0.0
        metadata["events"] = "moderate stage-1 slowdown [24,44), zero arrivals [64,72); ample capacity"
    elif group == "generated_sustained_processing_fault":
        demand = np.full(HORIZON_GENERATED, 10500.0)
        demand[:12] = 4500
        demand[72:] = 5500
        rate[16:64, 1] *= fault_factor
        metadata["events"] = "stage-1 fault [16,64); high load [12,72), low-load recovery"
    elif group == "generated_switching_fault":
        demand = 10000 + 1500 * np.sin(2 * np.pi * t / 37 + phase)
        demand[:10] = 4000
        demand[80:] = 5000
        # Unequal and sub/dwell-length changes include stale-estimate risk.
        for stage, start, end in [(0, 14, 31), (2, 31, 39), (1, 39, 59), (0, 59, 71)]:
            rate[start:end, stage] *= fault_factor
        metadata["events"] = "fault stages 0 [14,31), 2 [31,39), 1 [39,59), 0 [59,71)"
    elif group == "generated_pulsed_short_fault":
        demand = np.full(HORIZON_GENERATED, 4500.0)
        for start, length, height in [(12, 5, 23500), (31, 3, 28000), (51, 7, 20500), (74, 4, 25000)]:
            demand[start:start + length] = height
        for stage, start in [(1, 13), (0, 32), (2, 53), (1, 75)]:
            rate[start:start + 3, stage] *= fault_factor
        metadata["events"] = "3-7 minute arrival pulses; 3-minute faults shorter than the 8-minute dwell"
    elif group == "generated_asymmetric_recovery":
        demand = np.full(HORIZON_GENERATED, 9500.0)
        demand[:12] = 4500
        demand[80:] = 4500
        # A persistent nonuniform service mix and two changes force capacity
        # inference to distinguish recovery from mere lack of upstream work.
        rate *= np.array([1.35, .72, 1.18])
        rate[20:48, 2] *= .58
        rate[48:72, 1] *= 1.55
        metadata["events"] = "base multipliers [1.35,.72,1.18]; stage-2 fault [20,48), stage-1 speedup [48,72)"
    elif group == "generated_sustained_overload":
        demand = 18000 + 1200 * np.sin(2 * np.pi * t / 29 + phase)
        demand[:10] = 5000
        demand[80:] = 6000
        rate[32:56, 2] *= .75
        metadata["events"] = "arrival overload [10,80), moderate stage-2 slowdown [32,56), finite recovery tail"
    else:
        raise ValueError(f"Unknown generated group: {group}")
    # Count-valued arrivals with overdispersion from a shared minute factor;
    # independent of service rates and of every policy.
    minute_factor = arr_rng.lognormal(mean=-.5 * .08**2, sigma=.08, size=HORIZON_GENERATED)
    lam = np.maximum(0, demand * arrival_scale * minute_factor)
    arrivals = arr_rng.poisson(lam).astype(float)
    return Scenario(group, seed, arrivals, rate, "generated_synthetic", metadata)


def generate_scenarios(split="development"):
    """Return the complete, consistently ordered split; never policy-specific."""
    if split not in SPLIT_SEEDS:
        raise ValueError(f"Unknown split: {split}")
    seeds = SPLIT_SEEDS[split]
    trace = load_trace(ROOT / "data/load_test_summary_per_minute.csv")
    output = [
        Scenario(group, seed, arrivals, productivity, "legacy_flash", {
            "generation": "original v0.1.0 flash generator, unchanged; fresh seed",
            "trace": "data/load_test_summary_per_minute.csv",
        })
        for group, seed, arrivals, productivity in old_scenarios(trace, seeds)
    ]
    output += [_generated(group, seed) for group in GENERATED_GROUPS for seed in seeds]
    return output


def scenario_manifest(split="development", include_arrays=False):
    cases = generate_scenarios(split)
    result = {
        "split": split,
        "scenario_count": len(cases),
        "seeds": list(SPLIT_SEEDS[split]),
        "cases": [case.manifest(include_arrays) for case in cases],
    }
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    result["manifest_sha256"] = hashlib.sha256(canonical).hexdigest()
    return result


def policy_seeds(policy):
    if policy not in POLICIES:
        raise ValueError(f"Unknown policy: {policy}")
    return ABC_SEEDS if policy in STOCHASTIC_POLICIES else (None,)

