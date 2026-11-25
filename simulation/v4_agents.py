"""Artifact-backed Neuron Flow agents for the six-regime v0.4 experiment.

Only small descriptors and a bounded overview cross the worker message
boundary.  Complete rows, traces and statistical summaries are atomic local
artifacts checked by path, identity, checksum and independent validators.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
from pathlib import Path
from statistics import fmean

from neuron_flow import TransientAgentError
from capacity_agents import write_json, write_gzip_json, sha
from audit_capacity import audit_source_integrity
from audit_v4 import require, close
from v4_policy import V4Config
from v4_scenarios import (
    generate_scenarios, REGIMES, REGIME_CONFIGS, POLICIES, policy_seeds,
    LEGACY_GROUPS, GROUPS, expected_counts,
)
from v4_simulation import simulate_case, make_controller
from v4_metrics import aggregate, METRICS, HIGHER_IS_BETTER

ROOT = Path(__file__).resolve().parent


def canonical_sha(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(payload).hexdigest()


def step_name(regime, policy):
    require(regime in REGIMES and policy in POLICIES, "Unknown regime/policy")
    return regime + "__" + policy


def _input(message):
    from freeze_v4 import (
        execution_source_hashes, verify_predeclared_acceptance, verify_parent_frozen,
    )
    data = message["input"]
    require(data["split"] in ("development", "validation"), "Unknown split")
    output = Path(data["out"]).resolve()
    require(output.is_dir(), "Missing output directory")
    config = V4Config(**data["controller_configuration"])
    require(canonical_sha(config.as_dict()) == data["controller_configuration_sha256"],
            "Input configuration identity changed")
    require(audit_source_integrity(ROOT) == data["upstream_sha256"], "Original sources changed")
    require(execution_source_hashes() == data["execution_source_sha256"], "Execution sources changed")
    verify_predeclared_acceptance()
    require(verify_parent_frozen() == data["parent_v3_verification"], "Frozen parent identity changed")
    manifest_path = output / "scenario_manifest.json"
    require(sha(manifest_path) == data["scenario_manifest_file_sha256"], "Scenario artifact changed")
    manifest = json.loads(manifest_path.read_text())
    require(manifest["split"] == data["split"], "Scenario manifest split mismatch")
    recorded_identity = manifest.pop("manifest_sha256")
    require(recorded_identity == data["scenario_manifest_sha256"] == canonical_sha(manifest),
            "Scenario manifest identity mismatch")
    require(manifest["scenario_count"] == expected_counts(data["split"])["scenarios"],
            "Scenario manifest count mismatch")
    if data["split"] == "validation":
        from freeze_v4 import verify_frozen
        frozen = verify_frozen()
        require(frozen == data["freeze_verification"], "Frozen experiment identity changed")
        require(frozen["controller_configuration"] == config.as_dict(),
                "Configuration differs from frozen protocol")
        require(frozen["validation_manifest_sha256"] == recorded_identity,
                "Run does not use the frozen held-out environment manifest")
    return data["split"], output, config


def _integrity_after(message):
    # Rechecking also binds development executions to the exact configuration
    # and source snapshot submitted to the runtime.
    _input(message)


def _cases(output, split):
    cases = generate_scenarios(split)
    manifest = json.loads((output / "scenario_manifest.json").read_text())
    retained = {(item["group"], item["scenario_seed"]): item for item in manifest["cases"]}
    require(len(retained) == len(cases), "Scenario evidence has duplicate/missing cases")
    for case in cases:
        item = retained[case.group, case.seed]
        require(all(item.get(key) == value for key, value in case.manifest().items()),
                "Generated physical/noise arrays differ from submitted manifest")
    return cases


def artifact_path(output, descriptor):
    output = Path(output).resolve()
    path = (output / descriptor["path"]).resolve()
    require(path.is_relative_to(output), "Artifact path leaves the output directory")
    require(path.is_file(), "Missing experiment artifact")
    require(sha(path) == descriptor["sha256"], "Experiment artifact checksum mismatch")
    return path


def load_rows(output, result):
    payload = json.loads(artifact_path(output, result["rows"]).read_text())
    require(payload["regime"] == result["regime"] and payload["policy"] == result["policy"],
            "Row artifact identity mismatch")
    require(len(payload["rows"]) == result["rows"]["count"], "Row artifact count mismatch")
    return payload["rows"]


def evaluate_agent(message):
    split, output, config = _input(message)
    regime, policy = message["params"]["regime"], message["params"]["policy"]
    name = step_name(regime, policy)
    if message["params"].get("inject_retry") and message["attempt"] == 1:
        raise TransientAgentError("Controlled first-attempt failure before v0.4 policy computation")
    if message["params"].get("reuse_original"):
        original = json.loads((output / "indices" / f"{name}.json").read_text())
        rows = load_rows(output, original)
        rows[0]["completed"] += 1
        relative = f"negative/{name}.json"
        write_json(output / relative, {"regime": regime, "policy": policy, "rows": rows})
        result = {
            **original,
            "rows": {"path": relative, "sha256": sha(output / relative), "count": len(rows)},
        }
    else:
        cases = _cases(output, split)
        rows, representatives, seen_groups = [], [], set()
        for scenario in cases:
            detailed = scenario.group not in seen_groups
            seen_groups.add(scenario.group)
            for policy_seed in policy_seeds(policy):
                row, minutes = simulate_case(
                    policy, scenario, policy_seed, regime=regime,
                    detailed=detailed, config=config,
                )
                rows.append(row)
                if detailed:
                    representatives.append({"summary": row, "minutes": minutes})
        row_relative = f"rows/{name}.json"
        trace_relative = f"traces/{name}.json.gz"
        write_json(output / row_relative, {"regime": regime, "policy": policy, "rows": rows})
        write_gzip_json(output / trace_relative, representatives)
        result = {
            "split": split, "regime": regime, "policy": policy,
            "rows": {"path": row_relative, "sha256": sha(output / row_relative), "count": len(rows)},
            "traces": {
                "path": trace_relative, "sha256": sha(output / trace_relative),
                "count": len(representatives),
                "minutes": sum(len(rep["minutes"]) for rep in representatives),
                "selection": "first scenario seed in each family; every deterministic policy/regime; fixed before execution",
            },
        }
        write_json(output / "indices" / f"{name}.json", result)
    _integrity_after(message)
    return result


def validate_evaluation(message, result):
    from audit_v4 import audit_rows, audit_trace, audit_controller_replay
    split, output, config = _input(message)
    regime, policy = message["params"]["regime"], message["params"]["policy"]
    name = step_name(regime, policy)
    require((result["split"], result["regime"], result["policy"]) == (split, regime, policy),
            "Evaluation identity mismatch")
    expected_row = f"negative/{name}.json" if message["params"].get("reuse_original") else f"rows/{name}.json"
    require(result["rows"]["path"] == expected_row, "Unexpected row artifact path")
    require(result["traces"]["path"] == f"traces/{name}.json.gz", "Unexpected trace artifact path")
    rows = load_rows(output, result)
    cases = _cases(output, split)
    audit_rows(rows, cases, {policy: policy_seeds(policy)}, regimes=(regime,))
    with gzip.open(artifact_path(output, result["traces"]), "rt", encoding="utf-8") as handle:
        representatives = json.load(handle)
    first = {}
    for case in cases:
        first.setdefault(case.group, case)
    expected = {(group, case.seed, None) for group, case in first.items()}
    actual = {
        (rep["summary"]["group"], rep["summary"]["scenario_seed"], rep["summary"]["policy_seed"])
        for rep in representatives
    }
    require(actual == expected and len(representatives) == len(expected), "Representative trace scope mismatch")
    require(result["traces"]["count"] == len(representatives), "Representative trace count mismatch")
    row_map = {(r["group"], r["scenario_seed"], r["policy_seed"]): r for r in rows}
    minutes = 0
    for rep in representatives:
        row = rep["summary"]
        require(row == row_map[row["group"], row["scenario_seed"], row["policy_seed"]],
                "Representative summary differs from row artifact")
        audit_trace(rep, first[row["group"]], regime)
        controller = make_controller(policy, REGIME_CONFIGS[regime], config=config)
        audit_controller_replay(rep, controller)
        minutes += len(rep["minutes"])
    require(minutes == result["traces"]["minutes"], "Representative minute count mismatch")
    _integrity_after(message)


def all_rows(output, dependencies):
    expected = {step_name(regime, policy) for regime in REGIMES for policy in POLICIES}
    require(set(dependencies) == expected, "Missing regime/policy dependency")
    return [
        row for regime in REGIMES for policy in POLICIES
        for row in load_rows(output, dependencies[step_name(regime, policy)])
    ]


def overview(summary):
    """Small human-readable overview; the full summary remains on disk."""
    result = {key: summary[key] for key in (
        "scenarios", "regime_count", "scenario_regime_pairs", "episodes",
        "original_minutes", "followup_minutes", "minutes", "max_mass_error", "max_budget_error",
    )}
    result["regimes"] = {}
    for regime, part in summary["regimes"].items():
        result["regimes"][regime] = {
            "overall": {
                policy: {metric: values[metric] for metric in (
                    "mean_deficit", "end_deficit", "tail_auc_deficit", "total_dropped",
                    "total_completed", "total_moves",
                )}
                for policy, values in part["overall"].items()
            },
            "v4_hybrid_vs": {
                baseline: {key: comparison[key] for key in (
                    "reduction_pct", "reduction_pct_ci95", "improvement_ci95", "wins", "ties", "losses",
                )}
                for baseline, comparison in part["comparisons"]["v4_hybrid"].items()
            },
            "static_relative_harm": {
                policy: {key: risk[key] for key in (
                    "count", "frequency", "mean_positive_harm", "cvar90", "maximum_harm",
                )}
                for policy, risk in part["static_relative_harm"].items()
            },
            "v4_hybrid_static_cvar_vs_v3_full":
                part["static_harm_comparisons"]["v4_hybrid"]["v3_full"]["cvar90"],
        }
    return result


def compare_agent(message):
    from audit_v4 import audit_rows
    split, output, _ = _input(message)
    rows = all_rows(output, message["dependencies"])
    audit_rows(rows, _cases(output, split), {p: policy_seeds(p) for p in POLICIES}, regimes=REGIMES)
    summary = aggregate(rows)
    write_json(output / "summary.json", summary)
    _integrity_after(message)
    return {
        "summary": {"path": "summary.json", "sha256": sha(output / "summary.json")},
        "overview": overview(summary),
    }


def _scalar_cvar(harms):
    ordered = sorted(harms, reverse=True)
    mass = len(ordered) * 0.1
    whole = int(math.floor(mass + 1e-12))
    fraction = mass - whole
    total = math.fsum(ordered[:whole])
    if fraction > 1e-12 and whole < len(ordered):
        total += fraction * ordered[whole]
    return total / mass


def _check_effect(evidence, candidate, baseline, higher=False):
    differences = [(a - b) if higher else (b - a) for a, b in zip(candidate, baseline)]
    for key, expected in (
        ("candidate_mean", fmean(candidate)), ("baseline_mean", fmean(baseline)),
        ("mean_improvement", fmean(differences)),
    ):
        require(close(evidence[key], expected), "Independent paired point statistic disagrees: " + key)
    wins = sum(value > 1e-6 for value in differences)
    losses = sum(value < -1e-6 for value in differences)
    require((evidence["wins"], evidence["ties"], evidence["losses"]) ==
            (wins, len(differences) - wins - losses, losses), "Paired win/tie/loss counts disagree")


def _check_part(part, cases, values, regime):
    require(part["scenarios"] == len(cases), "Statistical slice case count differs")
    for policy in POLICIES:
        for metric in METRICS:
            expected = fmean(values[regime, c.group, c.seed, policy][metric] for c in cases)
            require(close(expected, part["overall"][policy][metric]), "Independent aggregate mean disagrees")
        harms = [
            max(values[regime, c.group, c.seed, policy]["mean_deficit"] -
                values[regime, c.group, c.seed, "static"]["mean_deficit"], 0.0)
            for c in cases
        ]
        risk = part["static_relative_harm"][policy]
        count = sum(h > 1e-6 for h in harms)
        require(risk["count"] == count, "Static-relative harm count differs")
        for key, expected in (
            ("frequency", count / len(harms)), ("mean_positive_harm", fmean(harms)),
            ("maximum_harm", max(harms)), ("cvar90", _scalar_cvar(harms)),
        ):
            require(close(risk[key], expected), "Independent static-relative harm statistic differs: " + key)
    for candidate, comparisons in part["comparisons"].items():
        for baseline, comparison in comparisons.items():
            new = [values[regime, c.group, c.seed, candidate]["mean_deficit"] for c in cases]
            old = [values[regime, c.group, c.seed, baseline]["mean_deficit"] for c in cases]
            _check_effect(comparison, new, old)
            allowance = comparison["mean_noninferiority"]
            require(allowance["margin_fraction"] == 0.05, "Mean noninferiority margin changed")
            require(close(allowance["allowance_slack"], fmean(1.05*b-a for a, b in zip(new, old))),
                    "Independent noninferiority point slack disagrees")
            for metric, evidence in comparison["secondary"].items():
                x = [values[regime, c.group, c.seed, candidate][metric] for c in cases]
                y = [values[regime, c.group, c.seed, baseline][metric] for c in cases]
                _check_effect(evidence, x, y, higher=metric in HIGHER_IS_BETTER)
    for candidate, comparisons in part["static_harm_comparisons"].items():
        for baseline, risks in comparisons.items():
            for metric, evidence in risks.items():
                new = part["static_relative_harm"][candidate][metric]
                old = part["static_relative_harm"][baseline][metric]
                require(close(evidence["candidate_risk"], new) and
                        close(evidence["baseline_policy_risk"], old) and
                        close(evidence["risk_reduction"], old-new), "Paired static-relative risk difference disagrees")


def validate_comparison(message, result):
    """Check every overall/source/family point statistic without a second bootstrap."""
    from audit_v4 import audit_rows
    split, output, _ = _input(message)
    require(result["summary"]["path"] == "summary.json", "Unexpected summary artifact path")
    summary = json.loads(artifact_path(output, result["summary"]).read_text())
    require(result["overview"] == overview(summary), "Overview differs from statistical artifact")
    require(summary["metrics"] == list(METRICS), "Summary metric specification differs")
    rows = all_rows(output, message["dependencies"])
    cases = _cases(output, split)
    audit_rows(rows, cases, {p: policy_seeds(p) for p in POLICIES}, regimes=REGIMES)
    scope = expected_counts(split)
    for key, expected in (
        ("episodes", scope["episodes"]), ("scenarios", scope["scenarios"]),
        ("regime_count", scope["regimes"]), ("scenario_regime_pairs", scope["scenario_regime_pairs"]),
        ("original_minutes", scope["original_minutes"]), ("followup_minutes", scope["followup_minutes"]),
        ("minutes", scope["total_minutes"]),
    ):
        require(summary[key] == expected, "Aggregate scope mismatch: " + key)
    values = {}
    for row in rows:
        key = (row["regime"], row["group"], row["scenario_seed"], row["policy"])
        require(key not in values, "Duplicate deterministic episode")
        values[key] = {
            metric: float(row["drain_censored"]) if metric == "drain_uncleared_fraction" else row[metric]
            for metric in METRICS
        }
    require(set(summary["regimes"]) == set(REGIMES), "Missing statistical regime")
    for regime in REGIMES:
        part = summary["regimes"][regime]
        _check_part(part, cases, values, regime)
        require(set(part["source_slices"]) == {"legacy_flash", "generated_synthetic"}, "Missing source slice")
        for source in ("legacy_flash", "generated_synthetic"):
            subset = [c for c in cases if c.source == source]
            _check_part(part["source_slices"][source], subset, values, regime)
        require(set(part["by_group"]) == set(GROUPS), "Missing family slice")
        for group in GROUPS:
            _check_part(part["by_group"][group], [c for c in cases if c.group == group], values, regime)
    _integrity_after(message)


def noop_agent(message):
    return {"ok": True}


def validate_noop(message, result):
    require(result == {"ok": True}, "Invalid no-op result")
