"""Paired, case-level analysis for the capacity-aware benchmark.

Policy draws are averaged within a scenario before any inference. The bootstrap
resamples scenario seeds within each family, retaining paired policy outcomes.
Intervals describe this synthetic generator, not deployment uncertainty.
"""
from collections import defaultdict
import math

import numpy as np

from capacity_scenarios import GROUPS, LEGACY_GROUPS, GENERATED_GROUPS, POLICIES, policy_seeds

METRICS = ("mean_backlog", "peak_backlog", "end_backlog", "completed", "moves")
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261125
TOL = 1e-6


def _bootstrap_indices(groups, draws, seed):
    rng = np.random.default_rng(seed)
    pieces = []
    for group in GROUPS:
        indices = np.flatnonzero(np.array(groups) == group)
        if len(indices):
            pieces.append(indices[rng.integers(0, len(indices), (draws, len(indices)))])
    return np.concatenate(pieces, axis=1)


def _paired(candidate, baseline, bootstrap):
    differences = baseline - candidate
    boot_candidate = candidate[bootstrap].mean(axis=1)
    boot_baseline = baseline[bootstrap].mean(axis=1)
    boot_difference = boot_baseline - boot_candidate
    baseline_mean = float(np.mean(baseline))
    candidate_mean = float(np.mean(candidate))
    positive_denominator = boot_baseline > TOL
    ratio_interval = None
    if np.all(positive_denominator):
        reductions = 100 * (boot_baseline - boot_candidate) / boot_baseline
        ratio_interval = np.percentile(reductions, [2.5, 97.5]).tolist()
    return {
        "scenarios": len(candidate),
        "candidate_mean_backlog": candidate_mean,
        "baseline_mean_backlog": baseline_mean,
        "mean_backlog_improvement": float(np.mean(differences)),
        "improvement_ci95": np.percentile(boot_difference, [2.5, 97.5]).tolist(),
        "reduction_pct": 100 * (baseline_mean - candidate_mean) / baseline_mean if baseline_mean > TOL else None,
        "reduction_pct_ci95": ratio_interval,
        "wins": int(np.sum(differences > TOL)),
        "ties": int(np.sum(np.abs(differences) <= TOL)),
        "losses": int(np.sum(differences < -TOL)),
        "bootstrap_draws": len(bootstrap),
        "bootstrap_draws_with_positive_denominator": int(positive_denominator.sum()),
    }


def _slice(cases, draws, seed):
    data = np.array([[list(case[policy][metric] for metric in METRICS) for policy in POLICIES] for case in cases])
    group_labels = [case["group"] for case in cases]
    boot = _bootstrap_indices(group_labels, draws, seed)
    overall = {
        policy: {metric: float(data[:, pi, mi].mean()) for mi, metric in enumerate(METRICS)}
        for pi, policy in enumerate(POLICIES)
    }
    comparisons = {}
    for candidate in ("capacity_aware", "abc_capacity"):
        ci = POLICIES.index(candidate)
        comparisons[candidate] = {
            baseline: _paired(data[:, ci, 0], data[:, bi, 0], boot)
            for bi, baseline in enumerate(POLICIES) if baseline != candidate
        }
    return {"scenarios": len(cases), "overall": overall, "comparisons": comparisons}


def aggregate(rows, bootstrap_draws=BOOTSTRAP_DRAWS, bootstrap_seed=BOOTSTRAP_SEED):
    """Aggregate six complete policies for each case.

    Required row fields: group, scenario_seed, policy, policy_seed and all
    METRICS. The standard simulator's arrivals, max_mass_error,
    max_budget_error and horizon fields are preserved in validation summaries
    when present. This function refuses missing/duplicate policy draws.
    """
    if type(bootstrap_draws) is not int or bootstrap_draws < 1:
        raise ValueError("bootstrap_draws must be a positive integer")
    if not rows:
        raise ValueError("No episode rows")
    by = defaultdict(list)
    seen = set()
    case_keys = set()
    for row in rows:
        group, seed, policy, ps = row["group"], row["scenario_seed"], row["policy"], row["policy_seed"]
        if group not in GROUPS or policy not in POLICIES:
            raise ValueError("Unrecognized group or policy")
        key = (group, seed, policy, ps)
        if key in seen:
            raise ValueError(f"Duplicate episode: {key}")
        seen.add(key)
        for metric in METRICS:
            if not math.isfinite(row[metric]) or row[metric] < -TOL:
                raise ValueError(f"Invalid metric {metric}")
        by[group, seed, policy].append(row)
        case_keys.add((group, seed))
    cases = []
    for group, seed in sorted(case_keys, key=lambda key: (GROUPS.index(key[0]), key[1])):
        case = {"group": group, "scenario_seed": seed, "source": "legacy_flash" if group in LEGACY_GROUPS else "generated_synthetic"}
        for policy in POLICIES:
            replicates = by[group, seed, policy]
            if {r["policy_seed"] for r in replicates} != set(policy_seeds(policy)) or len(replicates) != len(policy_seeds(policy)):
                raise ValueError(f"Incomplete draws for {(group, seed, policy)}")
            case[policy] = {metric: float(np.mean([r[metric] for r in replicates])) for metric in METRICS}
        cases.append(case)
    # Never mix rows from splits with an accidental unequal number of seeds per
    # family. Balanced counts make case weighting identical to family weighting.
    counts = defaultdict(int)
    for case in cases:
        counts[case["group"]] += 1
    if len(set(counts.values())) != 1:
        raise ValueError("Groups have unequal case counts; aggregation weighting would change")
    full = _slice(cases, bootstrap_draws, bootstrap_seed)
    slices = {}
    for source in ("legacy_flash", "generated_synthetic"):
        selected = [case for case in cases if case["source"] == source]
        if selected:
            slices[source] = _slice(selected, bootstrap_draws, bootstrap_seed)
    by_group = {}
    for group in GROUPS:
        selected = [case for case in cases if case["group"] == group]
        if selected:
            by_group[group] = _slice(selected, bootstrap_draws, bootstrap_seed)
    horizon = lambda row: int(row.get("horizon", 60 if row["group"] in LEGACY_GROUPS else 96))
    return {
        "scenarios": len(cases),
        "episodes": len(rows),
        "minutes": sum(horizon(row) for row in rows),
        "overall": full["overall"],
        "comparisons": full["comparisons"],
        "capacity_aware_vs": full["comparisons"]["capacity_aware"],
        "abc_capacity_vs": full["comparisons"]["abc_capacity"],
        "by_group": by_group,
        "source_slices": slices,
        "case_results": cases,
        "max_mass_error": max(float(row.get("max_mass_error", 0.0)) for row in rows),
        "max_budget_error": max(float(row.get("max_budget_error", 0.0)) for row in rows),
        "analysis": {
            "primary_metric": "mean total end-of-minute backlog",
            "weighting": "average three policy draws within each stochastic policy/scenario; then equal scenario weight; equal seed counts per family",
            "paired_units": "scenario family and seed, not individual policy draws",
            "bootstrap": "percentile 95% paired bootstrap, resample seeds within each family, retain the observed number of cases per family",
            "bootstrap_draws": bootstrap_draws,
            "bootstrap_seed": bootstrap_seed,
            "percentage_definition": "100 * (mean baseline - mean candidate) / mean baseline; positive favors candidate",
            "interval_scope": "Monte Carlo scenario variation conditional on these synthetic families, not uncertainty over real services or alternative models; unadjusted exploratory comparisons",
            "zero_denominator": "relative reduction and its interval are null when the relevant mean baseline is zero; absolute differences remain defined",
            "combined_scope": "descriptive mixture of legacy and generated synthetic families with 60/96-minute horizons; inspect source slices and every family",
        },
    }


summarize = aggregate

