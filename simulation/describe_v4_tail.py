"""Describe retained v0.4 tail harm without running or changing any policy.

Family contributions use one GLOBAL static-relative CVaR90 cutoff and tail
mass within each regime. Exact ties at that cutoff share the remaining mass
equally, avoiding an arbitrary dependence on family/file ordering.

This helper is outside the frozen experiment core. It requires completed
workflow evidence and creates a new descriptive artifact; it does not alter
acceptance, select a controller, simulate cases or import future environments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean

ROOT = Path(__file__).resolve().parent
TAIL_FRACTION = 0.10
TOL = 1e-6
PAIRED_POLICIES = ("static", "v3_full", "capacity_v2", "capacity_balance", "v4_hybrid")
CASE_METRICS = (
    "mean_deficit", "dropped", "tail_dropped", "total_dropped",
    "end_deficit", "tail_auc_deficit", "completed", "total_completed",
    "moves", "total_moves",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def finite(value, label):
    require(isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value), "Invalid numeric field: " + label)
    return float(value)


def close_absolute(actual, expected, label):
    require(abs(finite(actual, label) - finite(expected, label)) <= TOL,
            "Saved evidence disagrees with descriptive reconstruction: " + label)


def empirical_tail_weights(harms, tail_fraction=TAIL_FRACTION):
    """Return fractional empirical case weights and an order-invariant cutoff.

    Each case above the cutoff has weight 1. Remaining tail mass is divided
    equally across ALL cases whose stored floating-point harm exactly equals
    the cutoff. Near-equal values are not merged. This also handles a zero
    cutoff when fewer than 10% of cases have positive harm.
    """
    values = [finite(value, "harm") for value in harms]
    require(values and min(values) >= 0, "Harm must be nonnegative and nonempty")
    require(math.isfinite(tail_fraction) and 0 < tail_fraction <= 1, "Invalid tail fraction")
    ordered = sorted(values, reverse=True)
    mass = len(values) * tail_fraction
    cutoff = ordered[max(0, math.ceil(mass - 1e-12) - 1)]
    above = sum(value > cutoff for value in values)
    tied = sum(value == cutoff for value in values)
    remaining = mass - above
    require(tied > 0 and -1e-12 <= remaining <= tied + 1e-12, "Invalid empirical cutoff")
    boundary_weight = remaining / tied
    weights = [
        1.0 if value > cutoff else boundary_weight if value == cutoff else 0.0
        for value in values
    ]
    require(abs(math.fsum(weights) - mass) <= 1e-9, "Empirical tail weights do not sum to tail mass")
    cvar = math.fsum(weight * value for weight, value in zip(weights, values)) / mass
    # A second, ordinary descending-order scalar calculation checks that the
    # tie-symmetric attribution retains the usual fractional-tail statistic.
    whole = int(math.floor(mass + 1e-12))
    fraction = mass - whole
    direct = math.fsum(ordered[:whole])
    if fraction > 1e-12 and whole < len(ordered):
        direct += fraction * ordered[whole]
    close_absolute(cvar, direct / mass, "CVaR90 fractional boundary")
    return weights, {
        "tail_fraction": tail_fraction,
        "case_count": len(values),
        "tail_case_mass": mass,
        "cutoff_harm": cutoff,
        "cases_strictly_above_cutoff": above,
        "cases_exactly_at_cutoff": tied,
        "remaining_case_mass_at_cutoff": remaining,
        "weight_per_boundary_case": boundary_weight,
        "nonzero_weight_case_count": sum(weight > 0 for weight in weights),
        "cvar90": cvar,
    }


def _artifact(directory, descriptor, expected_path):
    require(descriptor["path"] == expected_path, "Unexpected artifact path")
    path = (directory / descriptor["path"]).resolve()
    require(path.is_relative_to(directory) and path.is_file(), "Missing or escaping retained artifact")
    require(sha(path) == descriptor["sha256"], "Retained artifact checksum mismatch")
    return path


def analyze(results):
    results = Path(results).resolve()
    # Check completion BEFORE opening any episode/summary outcome artifact.
    verification_path = results / "verification.json"
    require(verification_path.is_file(), "Completed verification.json is required; partial results are not analyzed")
    verification = read_json(verification_path)
    require(verification.get("version") == "0.4.0" and verification.get("status") == "SUCCEEDED",
            "A completed v0.4 workflow is required")
    split = verification["split"]
    require(split in ("development", "validation"), "Unknown experiment split")
    protocol_path = ROOT / "v4_protocol.json"
    protocol = read_json(protocol_path)
    if split == "validation":
        require(verification["freeze_verification"]["protocol_sha256"] == sha(protocol_path),
                "Validation does not match this frozen protocol")
    require(verification["controller_configuration"] == protocol["controller_configuration"],
            "Completed workflow uses a different selected configuration")
    policies = tuple(protocol["policy_names"])
    regimes = tuple(protocol["regime_configurations"])
    require(set(PAIRED_POLICIES) <= set(policies), "Required comparison policies are missing")
    summary_path = results / "summary.json"
    require(sha(summary_path) == verification["summary_sha256"], "Summary differs from workflow receipt")
    summary = read_json(summary_path)
    require(summary.get("version") == "0.4.0"
            and set(summary["policies"]) == set(policies)
            and set(summary["regimes"]) == set(regimes), "Incomplete summary policy/regime scope")
    manifest_path = results / "scenario_manifest.json"
    require(sha(manifest_path) == verification["scenario_manifest_file_sha256"], "Scenario manifest changed")
    manifest = read_json(manifest_path)
    require(manifest["split"] == split
            and manifest["manifest_sha256"] == verification["scenario_manifest_sha256"], "Manifest identity differs")
    cases = manifest["cases"]
    case_keys = [(case["group"], case["scenario_seed"]) for case in cases]
    require(len(set(case_keys)) == len(cases) == summary["scenarios"], "Duplicate/missing environment cases")
    group_order = tuple(dict.fromkeys(case["group"] for case in cases))
    declared_groups = tuple(protocol["scenario_design"]["legacy_groups"] +
                            protocol["scenario_design"]["generated_groups"])
    require(group_order == declared_groups, "Manifest does not contain the complete declared family order")
    group_index = {group: i for i, group in enumerate(group_order)}
    expected_seeds = set(protocol["split_seeds"][split])
    for group in group_order:
        require({seed for name, seed in case_keys if name == group} == expected_seeds,
                "Family/seed scope differs from the protocol")
    rows, artifacts = {}, {}
    for regime in regimes:
        for policy in policies:
            name = regime + "__" + policy
            index_path = results / "indices" / (name + ".json")
            descriptor = read_json(index_path)
            require((descriptor["split"], descriptor["regime"], descriptor["policy"]) == (split, regime, policy),
                    "Retained index identity differs")
            row_path = _artifact(results, descriptor["rows"], f"rows/{name}.json")
            payload = read_json(row_path)
            require(payload["regime"] == regime and payload["policy"] == policy, "Row wrapper identity differs")
            require(len(payload["rows"]) == descriptor["rows"]["count"] == len(cases), "Incomplete row artifact")
            seen = set()
            for row in payload["rows"]:
                key = (row["group"], row["scenario_seed"])
                require(key in case_keys and key not in seen and row["policy_seed"] is None,
                        "Unexpected, duplicate or stochastic episode")
                require(row["regime"] == regime and row["policy"] == policy, "Row identity differs")
                for metric in CASE_METRICS:
                    finite(row[metric], metric)
                seen.add(key)
                rows[regime, *key, policy] = row
            require(seen == set(case_keys), "Missing paired case")
            artifacts[name] = {
                "index_sha256": sha(index_path),
                "rows_sha256": sha(row_path),
                "row_count": len(payload["rows"]),
            }
    require(len(rows) == summary["episodes"] == len(cases) * len(regimes) * len(policies),
            "Incomplete episode scope")
    parts = {}
    for regime in regimes:
        policies_out, hybrid_weights = {}, None
        for policy in policies:
            values = [rows[regime, *key, policy]["mean_deficit"] for key in case_keys]
            reference = [rows[regime, *key, "static"]["mean_deficit"] for key in case_keys]
            close_absolute(fmean(values), summary["regimes"][regime]["overall"][policy]["mean_deficit"],
                           f"{regime}/{policy}/mean_deficit")
            harms = [max(value - baseline, 0.0) for value, baseline in zip(values, reference)]
            weights, tail = empirical_tail_weights(harms)
            reported = summary["regimes"][regime]["static_relative_harm"][policy]["cvar90"]
            close_absolute(tail["cvar90"], reported, f"{regime}/{policy}/reported_CVaR90")
            contribution_rows = []
            for group in group_order:
                selected = [i for i, case in enumerate(cases) if case["group"] == group]
                contribution = math.fsum(weights[i] * harms[i] for i in selected) / tail["tail_case_mass"]
                contribution_rows.append({
                    "group": group,
                    "source": cases[selected[0]]["source"],
                    "cases": len(selected),
                    "harm_cases_above_tolerance": sum(harms[i] > TOL for i in selected),
                    "tail_case_mass": math.fsum(weights[i] for i in selected),
                    "global_cvar90_contribution": contribution,
                    "share_of_global_cvar90_pct": 100 * contribution / tail["cvar90"] if tail["cvar90"] > 0 else None,
                    "maximum_case_harm": max(harms[i] for i in selected),
                })
            total = math.fsum(item["global_cvar90_contribution"] for item in contribution_rows)
            close_absolute(total, reported, f"{regime}/{policy}/sum_of_family_contributions")
            contribution_rows.sort(key=lambda item: (-item["global_cvar90_contribution"], group_index[item["group"]]))
            sources = []
            for source in dict.fromkeys(case["source"] for case in cases):
                contribution = math.fsum(
                    item["global_cvar90_contribution"] for item in contribution_rows if item["source"] == source)
                sources.append({
                    "source": source, "global_cvar90_contribution": contribution,
                    "share_of_global_cvar90_pct": 100 * contribution / tail["cvar90"] if tail["cvar90"] > 0 else None,
                })
            policies_out[policy] = {
                **tail,
                "reported_cvar90": reported,
                "family_contribution_sum": total,
                "absolute_reconciliation_error": abs(total - reported),
                "families": contribution_rows,
                "sources": sources,
            }
            if policy == "v4_hybrid":
                hybrid_weights = weights
        hybrid_harms = [
            max(rows[regime, *key, "v4_hybrid"]["mean_deficit"] -
                rows[regime, *key, "static"]["mean_deficit"], 0.0)
            for key in case_keys
        ]
        ranking = sorted(range(len(cases)), key=lambda i: (
            -hybrid_harms[i], group_index[cases[i]["group"]], cases[i]["scenario_seed"]))
        largest = []
        for rank, index in enumerate(ranking[:5], start=1):
            case, key = cases[index], case_keys[index]
            paired = {
                policy: {metric: rows[regime, *key, policy][metric] for metric in CASE_METRICS}
                for policy in PAIRED_POLICIES
            }
            largest.append({
                "rank": rank, "group": case["group"], "scenario_seed": case["scenario_seed"],
                "source": case["source"], "original_horizon": case["horizon"],
                "hybrid_positive_harm_vs_static": hybrid_harms[index],
                "hybrid_signed_mean_deficit_excess_vs_static":
                    paired["v4_hybrid"]["mean_deficit"] - paired["static"]["mean_deficit"],
                "hybrid_global_tail_case_weight": hybrid_weights[index],
                "paired_metrics": paired,
                "hybrid_minus_comparator": {
                    policy: {
                        metric: paired["v4_hybrid"][metric] - paired[policy][metric]
                        for metric in ("mean_deficit", "total_dropped", "end_deficit", "tail_auc_deficit")
                    }
                    for policy in PAIRED_POLICIES if policy != "v4_hybrid"
                },
            })
        parts[regime] = {
            "global_static_relative_cvar90": policies_out,
            "five_largest_hybrid_harm_cases": largest,
        }
    return {
        "version": "0.4.0", "analysis_kind": "post-result descriptive tail attribution",
        "split": split,
        "scope": {
            "scenarios": len(cases), "episodes": len(rows), "regimes": list(regimes),
            "policies": list(policies), "families": list(group_order),
        },
        "method": {
            "harm": "max(original-H mean deficit of policy minus paired Static mean deficit, 0)",
            "tail_fraction": TAIL_FRACTION,
            "family_contribution": "sum(case tail weight * case harm within family) / global 0.10*N case mass",
            "cutoff_ties": (
                "Exact stored-value ties at the global cutoff share the remaining tail mass equally. "
                "Near-equal values are not merged. This preserves global CVaR and avoids family-order-dependent attribution."
            ),
            "zero_cutoff": "Zero-harm cases may share remaining mass when fewer than 10% of cases have positive harm; their contribution is zero.",
            "zero_cvar": "Contribution percentages are null when global CVaR is zero; absolute contributions remain defined.",
            "scope_of_denominator": "All family contributions share one regime-wide cutoff and global tail denominator.",
            "largest_case_order": "descending positive hybrid harm; exact ties broken by declared family order then seed",
            "largest_case_limit": "Five cases are shown even if some harms are zero; zero harm is not classified as an adverse event.",
            "metric_windows": {
                "mean_deficit": "original-H mean end-of-minute offered-minus-completed work",
                "dropped": "cumulative dropped work at original H",
                "tail_dropped": "additional dropped work in the fixed 32-minute follow-up",
                "total_dropped": "all dropped work over original H plus 32-minute follow-up",
                "end_deficit": "original-H terminal offered-minus-completed work",
                "tail_auc_deficit": "sum of deficit over the fixed 32-minute follow-up",
            },
            "reconciliation_absolute_tolerance": TOL,
        },
        "evidence": {
            "summary_sha256": sha(summary_path),
            "verification_sha256": sha(verification_path),
            "scenario_manifest_sha256": sha(manifest_path),
            "protocol_sha256": sha(protocol_path),
            "analysis_helper_sha256": sha(Path(__file__)),
            "retained_artifacts": artifacts,
        },
        "regimes": parts,
        "limitations": [
            "Descriptive attribution after outcomes; no simulation, policy selection, acceptance change or tuning.",
            "Family contribution reflects both case frequency and harm magnitude under the original equal-case weighting.",
            "Tail families and largest cases identify observed residual harm, not a proven causal mechanism.",
            "Development cases are known data. Repeated analyses or simulations do not add independent cases.",
            "Any proposed policy change requires a new study and untouched validation data.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True, help="Completed development or validation result directory")
    parser.add_argument("--out", type=Path, required=True, help="New descriptive JSON file; never overwritten")
    args = parser.parse_args()
    require(not args.out.exists(), "Use a new output path; existing evidence is preserved")
    report = analyze(args.results)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    overview = {}
    for regime, part in report["regimes"].items():
        hybrid = part["global_static_relative_cvar90"]["v4_hybrid"]
        overview[regime] = {
            "hybrid_cvar90": hybrid["cvar90"],
            "family_sum_error": hybrid["absolute_reconciliation_error"],
            "largest_family_contributions": hybrid["families"][:3],
            "largest_hybrid_harm_case": {
                key: part["five_largest_hybrid_harm_cases"][0][key]
                for key in ("group", "scenario_seed", "hybrid_positive_harm_vs_static")
            },
        }
    print(json.dumps({"split": report["split"], "path": str(args.out), "regimes": overview}, indent=2))


if __name__ == "__main__":
    main()
