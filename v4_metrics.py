"""Paired analysis of v0.4 demand deficit, loss and adverse-case risk.

The primary per-case value is offered-minus-completed work averaged over the
original horizon. Dropped work remains in that deficit. Finite queues cannot
improve this metric merely by discarding jobs. Physical drainage is separate.
Six realism regimes are never pooled into a headline performance estimate.
"""
from collections import defaultdict
import math

import numpy as np

from v4_scenarios import (
    GROUPS, LEGACY_GROUPS, REGIMES, REGIME_DELAYS, FOLLOWUP_MINUTES,
    POLICIES, policy_seeds,
)

BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261127
TOL = 1e-6
TAIL_FRACTION = 0.10
MEAN_ALLOWANCE = 0.05
PRIMARY_METRIC = "mean_deficit"
CORE_METRICS = (
    "mean_deficit", "peak_deficit", "end_deficit",
    "tail_mean_deficit", "tail_auc_deficit", "tail_end_deficit",
    "completed", "tail_completed", "total_completed", "offered",
    "dropped", "tail_dropped", "total_dropped",
    "mean_queue", "end_queue", "tail_end_queue",
    "moves", "tail_moves", "total_moves",
    "resource_minutes_lost", "tail_resource_minutes_lost",
    "startup_capacity_minutes_lost", "tail_startup_capacity_minutes_lost",
    "drain_time_capped", "peak_service_queue_max_over_full_run",
    "end_inflight_work", "tail_end_inflight_work",
)
DERIVED_METRICS = ("drain_uncleared_fraction",)
METRICS = CORE_METRICS + DERIVED_METRICS
SECONDARY_COMPARISON_METRICS = (
    "end_deficit", "tail_auc_deficit", "tail_end_deficit", "total_dropped",
    "total_completed", "end_queue", "tail_end_queue", "moves", "total_moves",
    "resource_minutes_lost", "tail_resource_minutes_lost",
    "startup_capacity_minutes_lost", "tail_startup_capacity_minutes_lost",
    "drain_time_capped", "drain_uncleared_fraction",
)
HIGHER_IS_BETTER = {"completed", "tail_completed", "total_completed"}
DEFAULT_CANDIDATES = ("v4_hybrid", "v4_no_risk", "v4_no_recovery")
DEFAULT_BASELINES = (
    "static", "capacity_v2", "capacity_balance", "v3_full",
    "v4_no_risk", "v4_no_recovery",
)


def _number(value, name):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        raise ValueError(f"Expected a numeric metric: {name}")
    value = float(value)
    if not math.isfinite(value) or value < -TOL:
        raise ValueError(f"Invalid nonnegative metric: {name}")
    return value


def _close(a, b):
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=TOL)


def _cvar_last_axis(values, tail_fraction=TAIL_FRACTION):
    ordered = np.sort(values, axis=-1)[..., ::-1]
    mass = ordered.shape[-1] * tail_fraction
    whole = int(math.floor(mass + 1e-12))
    fraction = mass - whole
    total = ordered[..., :whole].sum(axis=-1)
    if fraction > 1e-12 and whole < ordered.shape[-1]:
        total = total + fraction * ordered[..., whole]
    return total / mass


def empirical_cvar(values, tail_fraction=TAIL_FRACTION):
    """Exact upper-tail empirical average, including fractional boundary mass."""
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("CVaR requires a nonempty finite vector")
    if isinstance(tail_fraction, bool) or not math.isfinite(tail_fraction) or not 0 < tail_fraction <= 1:
        raise ValueError("Invalid CVaR tail fraction")
    return float(_cvar_last_axis(values[None, :], tail_fraction)[0])


def _ci(values):
    if not len(values):
        return None
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


def _effect(candidate, baseline, candidate_draws, baseline_draws, *, higher=False):
    sign = -1.0 if higher else 1.0
    improvement = sign * (baseline-candidate)
    draw_improvement = sign * (baseline_draws-candidate_draws)
    denominator_ok = baseline_draws > TOL
    relative_ci = _ci(100*draw_improvement/baseline_draws) if np.all(denominator_ok) else None
    baseline_mean = float(baseline.mean())
    result = {
        "candidate_mean": float(candidate.mean()), "baseline_mean": baseline_mean,
        "mean_improvement": float(improvement.mean()), "improvement_ci95": _ci(draw_improvement),
        "relative_improvement_pct": 100*float(improvement.mean())/baseline_mean if baseline_mean > TOL else None,
        "relative_improvement_pct_ci95": relative_ci,
        "wins": int(np.sum(improvement > TOL)),
        "ties": int(np.sum(np.abs(improvement) <= TOL)),
        "losses": int(np.sum(improvement < -TOL)),
        "direction": "higher_is_better" if higher else "lower_is_better",
        "bootstrap_positive_denominator_draws": int(denominator_ok.sum()),
    }
    if not higher:
        result["reduction_pct"] = result["relative_improvement_pct"]
        result["reduction_pct_ci95"] = relative_ci
    return result


def _row_metric_values(row):
    values = {metric: _number(row[metric], metric) for metric in CORE_METRICS}
    if type(row["drain_censored"]) is not bool:
        raise ValueError("drain_censored must be a boolean")
    values["drain_uncleared_fraction"] = float(row["drain_censored"])
    for name in ("moves", "tail_moves", "total_moves"):
        if not _close(values[name], round(values[name])):
            raise ValueError(f"Move count is not integral: {name}")
    identities = (
        ("total_completed", values["completed"]+values["tail_completed"]),
        ("total_dropped", values["dropped"]+values["tail_dropped"]),
        ("total_moves", values["moves"]+values["tail_moves"]),
        ("end_deficit", values["offered"]-values["completed"]),
        ("tail_end_deficit", values["offered"]-values["total_completed"]),
        ("end_deficit", values["end_queue"]+values["dropped"]),
        ("tail_end_deficit", values["tail_end_queue"]+values["total_dropped"]),
        ("tail_auc_deficit", FOLLOWUP_MINUTES*values["tail_mean_deficit"]),
    )
    for name, expected in identities:
        if not _close(values[name], expected):
            raise ValueError(f"Demand/loss accounting identity failed: {name}")
    for small, large in (
        ("mean_queue", "mean_deficit"), ("end_queue", "end_deficit"),
        ("tail_end_queue", "tail_end_deficit"), ("mean_deficit", "peak_deficit"),
        ("end_deficit", "peak_deficit"), ("end_inflight_work", "end_queue"),
        ("tail_end_inflight_work", "tail_end_queue"),
        ("total_completed", "offered"), ("total_dropped", "offered"),
    ):
        if values[small] > values[large]+TOL:
            raise ValueError(f"Metric ordering failed: {small} exceeds {large}")
    capped = values["drain_time_capped"]
    if not -TOL <= capped <= FOLLOWUP_MINUTES+TOL:
        raise ValueError("Drain time exceeds the fixed follow-up")
    if row["drain_censored"] != (values["tail_end_queue"] > TOL):
        raise ValueError("Drain censoring must use retained work, not deficit")
    if row["drain_censored"] and not _close(capped, FOLLOWUP_MINUTES):
        raise ValueError("Censored physical drainage must use the follow-up cap")
    if values["end_queue"] <= TOL:
        if capped > TOL or row["drain_censored"]:
            raise ValueError("Already empty retained work must have drain time zero")
    elif not row["drain_censored"] and capped < 1-TOL:
        raise ValueError("Initially nonempty retained work cannot clear before follow-up")
    for name in ("max_mass_error", "max_budget_error"):
        if _number(row[name], name) > TOL:
            raise ValueError(f"Episode invariant exceeds tolerance: {name}")
    return values


def _comparisons(cases, policies, candidates, baselines, draws, seed, detailed=True):
    array = np.asarray([[[case[p][m] for m in METRICS] for p in policies] for case in cases], dtype=float)
    pi, mi = {p:i for i,p in enumerate(policies)}, {m:i for i,m in enumerate(METRICS)}
    boot = _bootstrap_indices([case["group"] for case in cases], draws, seed)
    ci_metrics = (PRIMARY_METRIC,)+SECONDARY_COMPARISON_METRICS if detailed else (PRIMARY_METRIC,)
    # Cache only bootstrap means, not a giant draws/cases/policies/metrics tensor.
    sampled = {p:{m:array[:, pi[p], mi[m]][boot].mean(axis=1) for m in ci_metrics} for p in policies}
    overall = {p:{m:float(array[:,pi[p],mi[m]].mean()) for m in METRICS} for p in policies}
    result = {}
    for candidate in candidates:
        result[candidate] = {}
        for baseline in baselines:
            if candidate == baseline:
                continue
            a, b = array[:,pi[candidate],mi[PRIMARY_METRIC]], array[:,pi[baseline],mi[PRIMARY_METRIC]]
            comparison = _effect(a,b,sampled[candidate][PRIMARY_METRIC],sampled[baseline][PRIMARY_METRIC])
            slack = (1+MEAN_ALLOWANCE)*b-a
            slack_draws = (1+MEAN_ALLOWANCE)*sampled[baseline][PRIMARY_METRIC]-sampled[candidate][PRIMARY_METRIC]
            comparison.update({
                "scenarios":len(cases), "bootstrap_draws":draws,
                "candidate_mean_deficit":comparison["candidate_mean"],
                "baseline_mean_deficit":comparison["baseline_mean"],
                "mean_deficit_improvement":comparison["mean_improvement"],
                "mean_noninferiority":{
                    "margin_fraction":MEAN_ALLOWANCE,
                    "statistic":"1.05*mean(baseline)-mean(candidate)",
                    "allowance_slack":float(slack.mean()), "allowance_slack_ci95":_ci(slack_draws),
                },
            })
            worst = int(np.argmax(a-b))
            comparison["worst_mean_deficit_case"] = {
                "group":cases[worst]["group"], "scenario_seed":cases[worst]["scenario_seed"],
                "candidate":float(a[worst]), "baseline":float(b[worst]), "signed_excess":float(a[worst]-b[worst]),
            }
            secondary = {}
            for metric in SECONDARY_COMPARISON_METRICS:
                x, y = array[:,pi[candidate],mi[metric]], array[:,pi[baseline],mi[metric]]
                higher = metric in HIGHER_IS_BETTER
                if detailed:
                    secondary[metric] = _effect(x,y,sampled[candidate][metric],sampled[baseline][metric],higher=higher)
                else:
                    gain = (x-y) if higher else (y-x)
                    secondary[metric] = {
                        "candidate_mean":float(x.mean()),"baseline_mean":float(y.mean()),
                        "mean_improvement":float(gain.mean()),"wins":int(np.sum(gain>TOL)),
                        "ties":int(np.sum(np.abs(gain)<=TOL)),"losses":int(np.sum(gain < -TOL)),
                        "direction":"higher_is_better" if higher else "lower_is_better",
                    }
            comparison["secondary"] = secondary
            result[candidate][baseline] = comparison
    static_harms, static_risk_comparisons, bootstrap_risks = {}, {}, {}
    if "static" in pi:
        reference = array[:,pi["static"],mi[PRIMARY_METRIC]]
        for policy in policies:
            harm = np.maximum(array[:,pi[policy],mi[PRIMARY_METRIC]]-reference,0.)
            samples = harm[boot]
            indicators = (harm>TOL).astype(float)
            bootstrap_risks[policy] = {
                "frequency":indicators[boot].mean(axis=1),
                "mean_positive_harm":samples.mean(axis=1),
                "cvar90":_cvar_last_axis(samples),
            }
            static_harms[policy] = {
                "count":int(indicators.sum()),"frequency":float(indicators.mean()),
                "frequency_ci95":_ci(bootstrap_risks[policy]["frequency"]),
                "mean_positive_harm":float(harm.mean()),
                "mean_positive_harm_ci95":_ci(bootstrap_risks[policy]["mean_positive_harm"]),
                "cvar90":empirical_cvar(harm),"cvar90_ci95":_ci(bootstrap_risks[policy]["cvar90"]),
                "maximum_harm":float(harm.max()),"tail_fraction":TAIL_FRACTION,
            }
        for candidate in candidates:
            static_risk_comparisons[candidate] = {}
            for baseline in baselines:
                if baseline == candidate:
                    continue
                risks = {}
                for metric in ("frequency","mean_positive_harm","cvar90"):
                    new, old = static_harms[candidate][metric], static_harms[baseline][metric]
                    risks[metric] = {
                        "candidate_risk":new,"baseline_policy_risk":old,"risk_reduction":old-new,
                        "risk_reduction_ci95":_ci(bootstrap_risks[baseline][metric]-bootstrap_risks[candidate][metric]),
                        "relative_risk_reduction_pct":100*(old-new)/old if old>TOL else None,
                    }
                static_risk_comparisons[candidate][baseline] = risks
    return {"scenarios":len(cases),"overall":overall,"comparisons":result,
            "static_relative_harm":static_harms,"static_harm_comparisons":static_risk_comparisons}


def aggregate(rows, policies=None, candidates=None, baselines=None,
              bootstrap_draws=BOOTSTRAP_DRAWS, bootstrap_seed=BOOTSTRAP_SEED,
              include_cases=False):
    """Validate and analyze complete paired rows; preserve six separate regimes.

    Every v0.4 policy is deterministic. A partial-regime dataset is supported
    for a worker or test, but all supplied regimes must contain exactly the
    same family/seed cases. Full protocol coverage is also checked by the
    independent audit and acceptance evaluator. Raw rows remain separate files
    by default instead of being duplicated in the summary.
    """
    if not rows:
        raise ValueError("No episode rows")
    if type(bootstrap_draws) is not int or bootstrap_draws<0:
        raise ValueError("bootstrap_draws must be a nonnegative integer; zero means point-only development")
    if type(bootstrap_seed) is not int:
        raise ValueError("bootstrap_seed must be an integer")
    policies = tuple(POLICIES if policies is None else policies)
    candidates = tuple(DEFAULT_CANDIDATES if candidates is None else candidates)
    baselines = tuple(DEFAULT_BASELINES if baselines is None else baselines)
    for label,values in (("policies",policies),("candidates",candidates),("baselines",baselines)):
        if not values or len(set(values))!=len(values):
            raise ValueError(f"{label} must be unique and nonempty")
    if not set(candidates).union(baselines)<=set(policies):
        raise ValueError("A comparison policy is missing from the evaluated policies")
    expected = {p:tuple(policy_seeds(p)) for p in policies}
    if any(seeds!=(None,) for seeds in expected.values()):
        raise ValueError("v0.4 declares deterministic policies only")
    by, case_keys, original_minutes = {}, defaultdict(set), 0
    for row in rows:
        regime,group,seed,policy = row["regime"],row["group"],row["scenario_seed"],row["policy"]
        if regime not in REGIMES or group not in GROUPS or policy not in policies:
            raise ValueError("Unexpected regime, group or policy")
        if type(seed) is not int or row["policy_seed"] is not None:
            raise ValueError("Unexpected scenario seed or stochastic policy draw")
        source = "legacy_flash" if group in LEGACY_GROUPS else "generated_synthetic"
        if row["source"]!=source:
            raise ValueError("Source label disagrees with the fixed family")
        horizon = 60 if group in LEGACY_GROUPS else 96
        if type(row["horizon"]) is not int or row["horizon"]!=horizon:
            raise ValueError("horizon must be the original 60/96-minute horizon")
        if type(row["followup_minutes"]) is not int or row["followup_minutes"]!=FOLLOWUP_MINUTES:
            raise ValueError("Unexpected follow-up horizon")
        key = (regime,group,seed,policy)
        if key in by:
            raise ValueError(f"Duplicate episode: {key}")
        by[key] = _row_metric_values(row)
        case_keys[regime].add((group,seed))
        original_minutes += horizon
    sets = list(case_keys.values())
    if any(keys!=sets[0] for keys in sets[1:]):
        raise ValueError("Regimes must contain identical paired environment cases")
    summaries, all_cases = {}, []
    for regime in REGIMES:
        if regime not in case_keys:
            continue
        cases=[]
        for group,seed in sorted(case_keys[regime],key=lambda k:(GROUPS.index(k[0]),k[1])):
            case={"regime":regime,"group":group,"scenario_seed":seed,
                  "source":"legacy_flash" if group in LEGACY_GROUPS else "generated_synthetic"}
            for policy in policies:
                key=(regime,group,seed,policy)
                if key not in by:
                    raise ValueError(f"Missing paired policy episode: {key}")
                case[policy]=by[key]
            # Environment offered work must not depend on the policy's losses.
            if any(not _close(case[p]["offered"],case[policies[0]]["offered"]) for p in policies):
                raise ValueError("Offered workload differs between paired policies")
            cases.append(case)
        counts=defaultdict(int)
        for case in cases:counts[case["group"]]+=1
        if len(set(counts.values()))!=1:
            raise ValueError("Unequal family case counts change intended weighting")
        full=_comparisons(cases,policies,candidates,baselines,bootstrap_draws,bootstrap_seed)
        source_slices={}
        for source in ("legacy_flash","generated_synthetic"):
            selected=[c for c in cases if c["source"]==source]
            if selected:source_slices[source]=_comparisons(selected,policies,candidates,baselines,bootstrap_draws,bootstrap_seed)
        by_group={}
        for group in GROUPS:
            selected=[c for c in cases if c["group"]==group]
            if selected:by_group[group]=_comparisons(selected,policies,candidates,baselines,bootstrap_draws,bootstrap_seed,detailed=False)
        full.update({"migration_delay_minutes":REGIME_DELAYS[regime],"source_slices":source_slices,
                     "by_group":by_group,"episodes":len(cases)*len(policies)})
        if "v4_hybrid" in full["comparisons"]:full["v4_hybrid_vs"]=full["comparisons"]["v4_hybrid"]
        if include_cases:full["case_results"]=cases
        summaries[regime]=full;all_cases.extend(cases)
    return {
        "version":"0.4.0","scenarios":len(sets[0]),"regime_count":len(summaries),
        "scenario_regime_pairs":len(all_cases),"episodes":len(rows),"original_minutes":original_minutes,
        "followup_minutes":len(rows)*FOLLOWUP_MINUTES,"minutes":original_minutes+len(rows)*FOLLOWUP_MINUTES,
        "policies":list(policies),"policy_seeds":{p:list(expected[p]) for p in policies},"metrics":list(METRICS),
        "regimes":summaries,
        "max_mass_error":max(float(r["max_mass_error"]) for r in rows),
        "max_budget_error":max(float(r["max_budget_error"]) for r in rows),
        "analysis":{
            "primary_metric":"Mean original-H end-of-minute offered-minus-completed deficit, including cumulative dropped work.",
            "deficit_identity":"offered-completed = retained service queues + in-flight work + cumulative dropped work",
            "regime_handling":"Six nested mechanics regimes analyzed separately; no pooled headline or independent-regime claim.",
            "weighting":"Equal case weights and equal family counts; workload scales are not normalized.",
            "paired_unit":"One family/seed within one regime; deterministic policies share the same offered workload/noise stream.",
            "bootstrap":"Paired percentile resamples of cases within each family; retain all policy pairing.",
            "bootstrap_draws":bootstrap_draws,"bootstrap_seed":bootstrap_seed,
            "interval_mode":"point_only_development" if bootstrap_draws==0 else "paired_percentile_bootstrap",
            "effect_sign":"Positive improvement favors candidate. Completion metrics use higher-is-better; deficit/loss metrics use lower-is-better.",
            "harm_definition":"Positive part of candidate case mean deficit minus the corresponding static case mean deficit.",
            "cvar90_definition":"Exact mean over the worst 10% empirical positive harms, using fractional boundary mass.",
            "static_risk_comparison":"Compare the two policies' static-relative risk functionals with the same paired bootstrap; do not replace this with candidate-minus-comparator harm.",
            "mean_noninferiority":"95% percentile CI of 1.05*bootstrap mean(baseline)-bootstrap mean(candidate), with a fixed 5% allowance.",
            "zero_denominator":"Relative effect/ratio CIs are null for zero baseline means. Linear noninferiority slack and absolute risks remain defined.",
            "loss_guard":"Cumulative drops remain in demand deficit forever; report total drops and completed separately.",
            "drainage":"Physical drainage uses retained queues plus pipeline, capped at 32 minutes and accompanied by censoring. Drops are not completed work.",
            "tail":"32 zero-arrival minutes with constant last original productivity; this artificial continuation is not announced to the controller.",
            "resource_loss":"Pending-resource minutes and active-minus-effective startup minutes are distinct; original and follow-up totals stay separate.",
            "interval_scope":"Conditional synthetic-scenario Monte Carlo uncertainty, not uncertainty about deployment, model correctness or future faults.",
            "multiplicity":"Primary final-regime risk comparison and predeclared engineering intersection are fixed in the protocol; other intervals are descriptive and unadjusted.",
            "known_development":"2000-2007 are known v0.3 cases reused openly; 4000-4063 are reserved v0.4 validation seeds.",
            "raw_case_results":"Raw episode rows are the authoritative retained evidence. Include_cases optionally duplicates paired values in this summary.",
        },
    }


summarize=aggregate
