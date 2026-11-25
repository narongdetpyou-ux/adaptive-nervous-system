"""Paired v0.3 analysis with transport regimes, drainage and adverse-case tails.

The experiment unit is one family/seed within one transport regime. Stochastic
policy draws are averaged first. The three regimes are never pooled into a
headline effect. All intervals are conditional synthetic-scenario uncertainty.
"""
from collections import defaultdict
import math

import numpy as np

from v3_scenarios import (
    GROUPS, LEGACY_GROUPS, REGIMES, REGIME_DELAYS, FOLLOWUP_MINUTES,
    DEFAULT_POLICIES, DEFAULT_SEEDS_BY_POLICY, policy_seeds,
)

BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261126
TOL = 1e-6
TAIL_FRACTION = 0.10
CORE_METRICS = (
    "mean_backlog", "peak_backlog", "end_backlog", "completed", "moves",
    "tail_mean_backlog", "tail_auc", "tail_end_backlog", "drain_time_capped",
    "resource_minutes_lost", "tail_resource_minutes_lost",
)
DERIVED_METRICS = ("drain_uncleared_fraction",)
OPTIONAL_METRICS = ("tail_moves", "total_moves", "tail_completed")
METRICS = CORE_METRICS + DERIVED_METRICS
SECONDARY_COMPARISON_METRICS = (
    "end_backlog", "tail_auc", "tail_end_backlog", "drain_time_capped",
    "drain_uncleared_fraction", "moves", "resource_minutes_lost",
    "tail_resource_minutes_lost",
)
DEFAULT_CANDIDATES = ("v3_full", "v3_signal_only")
DEFAULT_BASELINES = ("static", "capacity_v2", "capacity_balance", "abc_capacity")


def empirical_cvar(values, tail_fraction=TAIL_FRACTION):
    """Average the largest tail_fraction of the empirical distribution.

    Fractional mass at the boundary keeps the tail exactly 10% even when the
    number of cases is not divisible by ten. Pass positive-part harms for the
    reported harm-CVaR; this primitive also accepts any finite real vector.
    """
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or not len(x) or not np.isfinite(x).all():
        raise ValueError("CVaR requires a nonempty finite vector")
    if not math.isfinite(tail_fraction) or not 0 < tail_fraction <= 1:
        raise ValueError("Invalid CVaR tail fraction")
    return float(_cvar_last_axis(x[None, :], tail_fraction)[0])


def _cvar_last_axis(values, tail_fraction=TAIL_FRACTION):
    ordered = np.sort(values, axis=-1)[..., ::-1]
    mass = ordered.shape[-1] * tail_fraction
    whole = int(math.floor(mass + 1e-12))
    fraction = mass - whole
    total = ordered[..., :whole].sum(axis=-1)
    if fraction > 1e-12 and whole < ordered.shape[-1]:
        total = total + fraction * ordered[..., whole]
    return total / mass


def _ci(values):
    return np.percentile(values, [2.5, 97.5]).tolist()


def _bootstrap_indices(group_labels, draws, seed):
    rng = np.random.default_rng(seed)
    labels = np.asarray(group_labels)
    pieces = []
    for group in GROUPS:
        indices = np.flatnonzero(labels == group)
        if len(indices):
            pieces.append(indices[rng.integers(0, len(indices), (draws, len(indices)))])
    if not pieces:
        raise ValueError("Empty bootstrap population")
    return np.concatenate(pieces, axis=1)


def _effect(candidate, baseline, bootstrap, *, harms=False):
    """Positive improvement favors the candidate for a lower-is-better metric."""
    improvement = baseline - candidate
    sampled_improvement = improvement[bootstrap].mean(axis=1)
    sampled_baseline = baseline[bootstrap].mean(axis=1)
    baseline_mean = float(baseline.mean())
    candidate_mean = float(candidate.mean())
    denominator_ok = sampled_baseline > TOL
    relative_ci = None
    if np.all(denominator_ok):
        relative_ci = _ci(100 * sampled_improvement / sampled_baseline)
    result = {
        "candidate_mean": candidate_mean,
        "baseline_mean": baseline_mean,
        "mean_improvement": float(improvement.mean()),
        "improvement_ci95": _ci(sampled_improvement),
        "reduction_pct": 100 * float(improvement.mean()) / baseline_mean if baseline_mean > TOL else None,
        "reduction_pct_ci95": relative_ci,
        "wins": int(np.sum(improvement > TOL)),
        "ties": int(np.sum(np.abs(improvement) <= TOL)),
        "losses": int(np.sum(improvement < -TOL)),
        "bootstrap_positive_denominator_draws": int(denominator_ok.sum()),
    }
    if harms:
        positive_harm = np.maximum(-improvement, 0.0)
        sampled_harm = positive_harm[bootstrap]
        indicators = positive_harm > TOL
        result["harm"] = {
            "definition": "max(candidate minus baseline, zero); not the absolute value of a signed difference",
            "frequency": float(indicators.mean()),
            "frequency_ci95": _ci(indicators[bootstrap].mean(axis=1)),
            "count": int(indicators.sum()),
            "mean_positive_harm": float(positive_harm.mean()),
            "mean_positive_harm_ci95": _ci(sampled_harm.mean(axis=1)),
            "cvar90": empirical_cvar(positive_harm),
            "cvar90_ci95": _ci(_cvar_last_axis(sampled_harm)),
            "maximum_harm": float(positive_harm.max()),
            "tail_fraction": TAIL_FRACTION,
        }
    return result


def _comparisons(cases, policies, metrics, candidates, baselines, draws, seed, detailed=True):
    array = np.array([
        [[case[policy][metric] for metric in metrics] for policy in policies]
        for case in cases
    ], dtype=float)
    pi = {p: i for i, p in enumerate(policies)}
    mi = {m: i for i, m in enumerate(metrics)}
    boot = _bootstrap_indices([case["group"] for case in cases], draws, seed)
    result = {}
    for candidate in candidates:
        if candidate not in pi:
            raise ValueError(f"Candidate not evaluated: {candidate}")
        result[candidate] = {}
        for baseline in baselines:
            if baseline not in pi:
                raise ValueError(f"Baseline not evaluated: {baseline}")
            if baseline == candidate:
                continue
            a = array[:, pi[candidate], mi["mean_backlog"]]
            b = array[:, pi[baseline], mi["mean_backlog"]]
            comparison = _effect(a, b, boot, harms=True)
            comparison.update({
                "scenarios": len(cases),
                "candidate_mean_backlog": comparison["candidate_mean"],
                "baseline_mean_backlog": comparison["baseline_mean"],
                "mean_backlog_improvement": comparison["mean_improvement"],
                "bootstrap_draws": draws,
            })
            worst_index = int(np.argmax(a-b))
            comparison["worst_mean_backlog_case"] = {
                "group": cases[worst_index]["group"],
                "scenario_seed": cases[worst_index]["scenario_seed"],
                "candidate": float(a[worst_index]),
                "baseline": float(b[worst_index]),
                "signed_excess": float(a[worst_index]-b[worst_index]),
            }
            # Main/source slices have full secondary intervals. Family slices
            # retain point effects and paired counts plus terminal harms to
            # keep the output readable and the orchestration payload bounded.
            secondary = {}
            for metric in SECONDARY_COMPARISON_METRICS:
                x = array[:, pi[candidate], mi[metric]]
                y = array[:, pi[baseline], mi[metric]]
                if detailed:
                    secondary[metric] = _effect(x, y, boot, harms=metric in ("end_backlog", "tail_end_backlog"))
                else:
                    d = y-x
                    secondary[metric] = {
                        "candidate_mean": float(x.mean()),
                        "baseline_mean": float(y.mean()),
                        "mean_improvement": float(d.mean()),
                        "wins": int(np.sum(d > TOL)),
                        "ties": int(np.sum(np.abs(d) <= TOL)),
                        "losses": int(np.sum(d < -TOL)),
                    }
            comparison["secondary"] = secondary
            result[candidate][baseline] = comparison
    overall = {
        policy: {metric: float(array[:, pi[policy], mi[metric]].mean()) for metric in metrics}
        for policy in policies
    }
    static_harms = {}
    static_risk_comparisons = {}
    if "static" in pi:
        reference = array[:, pi["static"], mi["mean_backlog"]]
        bootstrap_risks = {}
        for policy in policies:
            harm = np.maximum(array[:, pi[policy], mi["mean_backlog"]] - reference, 0.0)
            samples = harm[boot]
            frequency = (harm > TOL).astype(float)
            bootstrap_risks[policy] = {
                "frequency": frequency[boot].mean(axis=1),
                "mean_positive_harm": samples.mean(axis=1),
                "cvar90": _cvar_last_axis(samples),
            }
            static_harms[policy] = {
                "count": int(frequency.sum()),
                "frequency": float(frequency.mean()),
                "frequency_ci95": _ci(bootstrap_risks[policy]["frequency"]),
                "mean_positive_harm": float(harm.mean()),
                "mean_positive_harm_ci95": _ci(bootstrap_risks[policy]["mean_positive_harm"]),
                "cvar90": empirical_cvar(harm),
                "cvar90_ci95": _ci(bootstrap_risks[policy]["cvar90"]),
                "maximum_harm": float(harm.max()),
            }
        for candidate in candidates:
            static_risk_comparisons[candidate] = {}
            for baseline in baselines:
                if baseline == candidate:
                    continue
                risks = {}
                for metric in ("frequency", "mean_positive_harm", "cvar90"):
                    new = static_harms[candidate][metric]
                    old = static_harms[baseline][metric]
                    risks[metric] = {
                        "candidate_risk": new,
                        "baseline_policy_risk": old,
                        "risk_reduction": old-new,
                        "risk_reduction_ci95": _ci(bootstrap_risks[baseline][metric] - bootstrap_risks[candidate][metric]),
                        "relative_risk_reduction_pct": 100*(old-new)/old if old > TOL else None,
                    }
                static_risk_comparisons[candidate][baseline] = risks
    return {
        "scenarios": len(cases), "overall": overall, "comparisons": result,
        "static_relative_harm": static_harms,
        "static_harm_comparisons": static_risk_comparisons,
    }


def _row_metric_values(row, metrics):
    values = {}
    for metric in metrics:
        if metric == "drain_uncleared_fraction":
            if type(row["drain_censored"]) is not bool:
                raise ValueError("drain_censored must be a boolean")
            value = float(row["drain_censored"])
        else:
            value = row[metric]
        if isinstance(value, bool) or not isinstance(value, (float, int, np.floating, np.integer)):
            raise ValueError(f"Metric must be numeric: {metric}")
        value = float(value)
        if not math.isfinite(value) or value < -TOL:
            raise ValueError(f"Invalid metric: {metric}")
        values[metric] = value
    if not -TOL <= values["drain_time_capped"] <= FOLLOWUP_MINUTES + TOL:
        raise ValueError("Drain time must be capped at 32 follow-up minutes")
    if row["drain_censored"] and abs(values["drain_time_capped"]-FOLLOWUP_MINUTES) > TOL:
        raise ValueError("A censored drain time must equal the follow-up cap")
    if row["drain_censored"] != (values["tail_end_backlog"] > TOL):
        raise ValueError("Drain censoring disagrees with final backlog")
    if not math.isclose(values["tail_auc"], FOLLOWUP_MINUTES*values["tail_mean_backlog"], rel_tol=1e-9, abs_tol=TOL):
        raise ValueError("Tail AUC and tail mean disagree")
    if values["end_backlog"] <= TOL:
        if values["drain_time_capped"] > TOL or row["drain_censored"]:
            raise ValueError("An already-empty original horizon must have drain time zero")
    return values


def aggregate(rows, policies=None, seeds_by_policy=None, candidates=None, baselines=None,
              bootstrap_draws=BOOTSTRAP_DRAWS, bootstrap_seed=BOOTSTRAP_SEED,
              include_cases=True):
    """Analyze complete paired episode rows; each transport regime stays separate.

    Required identifiers: regime, group, scenario_seed, policy, policy_seed.
    Required values: CORE_METRICS plus boolean drain_censored. horizon means
    original 60/96-minute horizon; resource_minutes_lost and moves cover that
    original horizon. tail_resource_minutes_lost is separate. tail_moves,
    total_moves and tail_completed are included when present on every row.
    """
    if not rows:
        raise ValueError("No episode rows")
    if type(bootstrap_draws) is not int or bootstrap_draws < 1:
        raise ValueError("bootstrap_draws must be a positive integer")
    policies = tuple(DEFAULT_POLICIES if policies is None else policies)
    mapping = DEFAULT_SEEDS_BY_POLICY if seeds_by_policy is None else seeds_by_policy
    candidates = tuple(DEFAULT_CANDIDATES if candidates is None else candidates)
    baselines = tuple(DEFAULT_BASELINES if baselines is None else baselines)
    if len(set(policies)) != len(policies) or not policies:
        raise ValueError("Policies must be unique and nonempty")
    if not candidates or len(set(candidates)) != len(candidates) or len(set(baselines)) != len(baselines):
        raise ValueError("Candidates/baselines must be unique")
    expected_draws = {policy: policy_seeds(policy, mapping) for policy in policies}
    optional = []
    for metric in OPTIONAL_METRICS:
        present = [metric in row for row in rows]
        if any(present) and not all(present):
            raise ValueError(f"Optional metric is inconsistently recorded: {metric}")
        if all(present):
            optional.append(metric)
    metrics = CORE_METRICS + DERIVED_METRICS + tuple(optional)
    by = defaultdict(list)
    case_keys = defaultdict(set)
    observed = set()
    original_minutes = 0
    for row in rows:
        regime, group, seed = row["regime"], row["group"], row["scenario_seed"]
        policy, ps = row["policy"], row["policy_seed"]
        if regime not in REGIMES or group not in GROUPS or policy not in policies:
            raise ValueError("Unexpected regime, group or policy")
        if type(seed) is not int:
            raise ValueError("Scenario seeds must be integers")
        key = (regime, group, seed, policy, ps)
        if key in observed:
            raise ValueError(f"Duplicate episode: {key}")
        observed.add(key)
        horizon = 60 if group in LEGACY_GROUPS else 96
        if int(row.get("horizon", horizon)) != horizon:
            raise ValueError("horizon must describe the original scenario, not the follow-up")
        if int(row.get("followup_minutes", FOLLOWUP_MINUTES)) != FOLLOWUP_MINUTES:
            raise ValueError("Unexpected follow-up horizon")
        values = _row_metric_values(row, metrics)
        by[regime, group, seed, policy].append((ps, values))
        case_keys[regime].add((group, seed))
        original_minutes += horizon
    # A single-regime call is supported, but whenever multiple regimes are
    # supplied they must contain precisely the same paired environment cases.
    key_sets = list(case_keys.values())
    if any(keys != key_sets[0] for keys in key_sets[1:]):
        raise ValueError("Regimes do not contain identical scenario cases")
    summaries = {}
    all_cases = []
    for regime in REGIMES:
        if regime not in case_keys:
            continue
        cases = []
        for group, seed in sorted(case_keys[regime], key=lambda k:(GROUPS.index(k[0]), k[1])):
            case = {
                "regime": regime, "group": group, "scenario_seed": seed,
                "source": "legacy_flash" if group in LEGACY_GROUPS else "generated_synthetic",
            }
            for policy in policies:
                replicas = by[regime, group, seed, policy]
                if {ps for ps, _ in replicas} != set(expected_draws[policy]) or len(replicas) != len(expected_draws[policy]):
                    raise ValueError(f"Missing or unexpected policy draws: {(regime, group, seed, policy)}")
                positions = {ps:i for i,ps in enumerate(expected_draws[policy])}
                replicas = sorted(replicas, key=lambda item:positions[item[0]])
                case[policy] = {
                    metric: float(np.mean([values[metric] for _, values in replicas]))
                    for metric in metrics
                }
            cases.append(case)
        counts = defaultdict(int)
        for case in cases:
            counts[case["group"]] += 1
        if len(set(counts.values())) != 1:
            raise ValueError("Unequal case counts across families change the intended weighting")
        full = _comparisons(cases, policies, metrics, candidates, baselines,
                            bootstrap_draws, bootstrap_seed, detailed=True)
        source_slices = {}
        for source in ("legacy_flash", "generated_synthetic"):
            selected = [case for case in cases if case["source"] == source]
            if selected:
                source_slices[source] = _comparisons(selected, policies, metrics, candidates, baselines,
                                                    bootstrap_draws, bootstrap_seed, detailed=True)
        by_group = {}
        for group in GROUPS:
            selected = [case for case in cases if case["group"] == group]
            if selected:
                by_group[group] = _comparisons(selected, policies, metrics, candidates, baselines,
                                               bootstrap_draws, bootstrap_seed, detailed=False)
        full.update({
            "migration_delay_minutes": REGIME_DELAYS[regime],
            "source_slices": source_slices,
            "by_group": by_group,
            "episodes": sum(row["regime"] == regime for row in rows),
        })
        if "v3_full" in full["comparisons"]:
            full["v3_full_vs"] = full["comparisons"]["v3_full"]
        if include_cases:
            full["case_results"] = cases
        summaries[regime] = full
        all_cases.extend(cases)
    return {
        "version": "0.3.0",
        "scenarios": len(key_sets[0]),
        "regime_count": len(summaries),
        "scenario_regime_pairs": len(all_cases),
        "episodes": len(rows),
        "original_minutes": original_minutes,
        "followup_minutes": len(rows) * FOLLOWUP_MINUTES,
        "minutes": original_minutes + len(rows) * FOLLOWUP_MINUTES,
        "policies": list(policies),
        "policy_seeds": {p:list(expected_draws[p]) for p in policies},
        "metrics": list(metrics),
        "regimes": summaries,
        "max_mass_error": max(float(row.get("max_mass_error", 0.0)) for row in rows),
        "max_budget_error": max(float(row.get("max_budget_error", 0.0)) for row in rows),
        "analysis": {
            "primary_metric": "mean end-of-minute total backlog over original H only",
            "regime_handling": "instant, one-minute and three-minute transport regimes analyzed separately; no pooled headline",
            "weighting": "average policy draws within each case; equal case weight and equal family counts within each regime",
            "paired_unit": "family and scenario seed; policy draws are not independent scenario observations",
            "bootstrap": "2000 default paired percentile resamples of scenario seeds within each family; preserve policy pairing",
            "bootstrap_draws": bootstrap_draws,
            "bootstrap_seed": bootstrap_seed,
            "effect_sign": "positive mean_improvement/reduction_pct favors candidate; harms are positive excess backlog",
            "harm_definition": "max(candidate case mean backlog - baseline case mean backlog, 0); frequency uses tolerance 1e-6",
            "static_risk_comparison": "static_relative_harm profiles each policy against static; static_harm_comparisons contrasts those risk profiles using the same paired bootstrap, which differs from directly taking candidate-minus-comparator positive parts",
            "cvar90_definition": "average of the worst 10% empirical positive-part case harms, using fractional mass at boundary; zero means no positive case harm in this sample",
            "drain_time": "first zero-backlog time after original H, with already empty set to 0; censored at 32; report capped time and uncleared fraction together",
            "censoring_scope": "capped mean is a restricted follow-up measure, not an estimate of uncensored mean time to clearance",
            "tail_definition": "32 minutes of zero new arrivals and constant last true original-horizon productivity; model assumption unknown in advance to policy",
            "resource_minutes_lost": "resources unavailable in transit during original H; follow-up losses reported separately; no monetary cost",
            "zero_denominator": "relative effects and ratio intervals are null for zero baseline; absolute harms remain meaningful",
            "interval_scope": "Monte Carlo variation within specified synthetic families conditional on fixed policy draws and queue mechanics; not model/deployment uncertainty",
            "multiplicity": "secondary/source/family intervals are descriptive and unadjusted; the frozen acceptance rule identifies predeclared decision comparisons",
            "known_development": "development cases were already observed in v0.2; only new 2000-2031 seeds form v0.3 validation",
            "family_secondary_scope": "per-family primary effects/harm intervals are provided; per-family secondary metrics retain point effects and paired counts",
        },
    }


summarize = aggregate
