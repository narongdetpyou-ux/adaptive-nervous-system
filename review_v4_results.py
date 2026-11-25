"""Independently review saved v0.4 validation artifacts without new rollouts.

    python review_v4_results.py --out v4_results/validation

The replay checks the SAME prespecified representative cases already retained
by the workflow; repeating it is verification, not extra independent evidence.
All statistics are recalculated from saved rows. The acceptance-driving paired
bootstrap intervals are independently reconstructed using the declared seed.
No validation tuning or new policy simulation occurs in this script.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import gzip
import hashlib
import json
import math
from pathlib import Path
import platform
from statistics import fmean

import numpy as np

from audit_capacity import audit_source_integrity
from audit_v4 import audit_rows, audit_trace, audit_controller_replay, require, close
from freeze_v4 import verify_frozen, execution_source_hashes
from v4_policy import V4Config
from v4_scenarios import generate_scenarios, scenario_manifest, REGIMES, POLICIES, GROUPS
from v4_simulation import make_controller

ROOT = Path(__file__).resolve().parent


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def artifact(out, descriptor):
    path = (out / descriptor["path"]).resolve()
    require(path.is_relative_to(out) and path.is_file(), "Missing artifact or path escape")
    require(sha(path) == descriptor["sha256"], "Artifact checksum mismatch: " + descriptor["path"])
    return path


def key(row):
    return row["regime"], row["group"], row["scenario_seed"], row["policy"], row["policy_seed"]


def cvar(values):
    ordered = sorted(values, reverse=True)
    mass = .1 * len(ordered)
    whole = int(math.floor(mass + 1e-12))
    fraction = mass - whole
    total = math.fsum(ordered[:whole])
    if fraction > 1e-12:
        total += fraction * ordered[whole]
    return total / mass


def check_csv(out, rows):
    originals = {tuple("" if x is None else str(x) for x in key(row)): row for row in rows}
    with (out / "episodes.csv").open(newline="", encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    require(len(csv_rows) == len(rows), "CSV row count mismatch")
    seen = set()
    for row in csv_rows:
        identity = (row["regime"], row["group"], row["scenario_seed"], row["policy"], row["policy_seed"])
        require(identity in originals and identity not in seen, "CSV episode scope mismatch")
        seen.add(identity)
        original = originals[identity]
        require(set(row) == set(original), "CSV column mismatch")
        for field, value in original.items():
            require(row[field] == ("" if value is None else str(value)), "CSV value mismatch: " + field)
    require(seen == set(originals), "Incomplete CSV scope")


def check_point_statistics(rows, summary):
    """Ordinary scalar calculation independent of the production aggregator."""
    by = {key(row)[:-1]: row for row in rows}
    means = paired = risks = 0
    higher_metrics = {"completed", "tail_completed", "total_completed"}
    for regime, part in summary["regimes"].items():
        identities = {(r["group"], r["scenario_seed"]) for r in rows if r["regime"] == regime}
        slices = [(part, identities)]
        for group, selected in part["by_group"].items():
            slices.append((selected, {i for i in identities if i[0] == group}))
        for source, selected in part["source_slices"].items():
            slices.append((selected, {i for i in identities if by[regime, *i, "static"]["source"] == source}))
        for selected, identities_slice in slices:
            require(selected["scenarios"] == len(identities_slice), "Summary slice count mismatch")
            cases = sorted(identities_slice)
            def values(policy, metric):
                return [float(by[regime, g, s, policy]["drain_censored"]) if metric == "drain_uncleared_fraction"
                        else by[regime, g, s, policy][metric] for g, s in cases]
            for policy in POLICIES:
                for metric in summary["metrics"]:
                    require(close(fmean(values(policy, metric)), selected["overall"][policy][metric]),
                            "Independent aggregate mean mismatch")
                    means += 1
                harm = [max(x - y, 0.) for x, y in zip(values(policy, "mean_deficit"), values("static", "mean_deficit"))]
                risk = selected["static_relative_harm"][policy]
                count = sum(h > 1e-6 for h in harm)
                require(risk["count"] == count, "Static harm count mismatch")
                for field, value in (("frequency", count / len(harm)), ("mean_positive_harm", fmean(harm)),
                                     ("maximum_harm", max(harm)), ("cvar90", cvar(harm))):
                    require(close(value, risk[field]), "Independent risk mismatch: " + field)
                risks += 1
            for candidate, comparisons in selected["comparisons"].items():
                for baseline, evidence in comparisons.items():
                    metrics = {"mean_deficit": evidence, **evidence["secondary"]}
                    for metric, metric_evidence in metrics.items():
                        a, b = values(candidate, metric), values(baseline, metric)
                        d = [(x - y) if metric in higher_metrics else (y - x) for x, y in zip(a, b)]
                        require(close(fmean(a), metric_evidence["candidate_mean"]) and
                                close(fmean(b), metric_evidence["baseline_mean"]) and
                                close(fmean(d), metric_evidence["mean_improvement"]),
                                "Independent paired effect mismatch")
                        counts = (sum(x > 1e-6 for x in d), sum(abs(x) <= 1e-6 for x in d), sum(x < -1e-6 for x in d))
                        require(counts == (metric_evidence["wins"], metric_evidence["ties"], metric_evidence["losses"]),
                                "Paired win/tie/loss mismatch")
                        paired += 1
                    a, b = values(candidate, "mean_deficit"), values(baseline, "mean_deficit")
                    ni = evidence["mean_noninferiority"]
                    require(ni["margin_fraction"] == .05 and
                            close(ni["allowance_slack"], fmean(1.05 * y - x for x, y in zip(a, b))),
                            "Mean noninferiority point slack mismatch")
            for candidate, comparisons in selected["static_harm_comparisons"].items():
                for baseline, evidence in comparisons.items():
                    for metric, item in evidence.items():
                        a = selected["static_relative_harm"][candidate][metric]
                        b = selected["static_relative_harm"][baseline][metric]
                        require(close(item["candidate_risk"], a) and close(item["baseline_policy_risk"], b) and
                                close(item["risk_reduction"], b - a), "Risk comparison mismatch")
    return {"aggregate_point_means_checked": means, "paired_metric_effects_checked": paired,
            "static_relative_risk_values_checked": risks}


def check_acceptance_bootstrap(rows, summary):
    """Rebuild paired family-stratified draws; use partition for empirical tails.

    This does not invoke the production bootstrap, effect or CVaR functions.
    NumPy's documented RNG/percentile primitives are common numerical tools.
    Every policy uses identical resampled case indices; families retain their
    counts. No regime pooling or independent-policy resampling is performed.
    """
    analysis = summary["analysis"]
    draws, seed = analysis["bootstrap_draws"], analysis["bootstrap_seed"]
    require(draws == 2000 and seed == 20261127, "Declared bootstrap specification changed")
    by = {key(row)[:-1]: row for row in rows}
    mean_checks = risk_checks = 0
    for regime in REGIMES:
        keys = sorted({(r["group"], r["scenario_seed"]) for r in rows if r["regime"] == regime},
                      key=lambda x: (GROUPS.index(x[0]), x[1]))
        rng = np.random.default_rng(seed)
        sampled_indices = []
        for group in GROUPS:
            members = [i for i, (g, _) in enumerate(keys) if g == group]
            require(members, "Bootstrap missing family")
            local = rng.integers(0, len(members), size=(draws, len(members)))
            sampled_indices.append(np.asarray(members)[local])
        sampled_indices = np.concatenate(sampled_indices, axis=1)
        values = {p: np.array([by[regime, g, s, p]["mean_deficit"] for g, s in keys]) for p in POLICIES}
        sample_means = {p: values[p][sampled_indices].mean(axis=1) for p in POLICIES}
        part = summary["regimes"][regime]
        for baseline in ("capacity_v2", "capacity_balance"):
            slack = 1.05 * sample_means[baseline] - sample_means["v4_hybrid"]
            ci = np.percentile(slack, [2.5, 97.5])
            recorded = part["comparisons"]["v4_hybrid"][baseline]["mean_noninferiority"]["allowance_slack_ci95"]
            require(all(close(a, b) for a, b in zip(ci, recorded)), "Independent mean-NI CI mismatch")
            mean_checks += 1
        risk_samples = {}
        tail_mass = .1 * len(keys)
        whole = int(math.floor(tail_mass + 1e-12))
        boundary = len(keys) - whole - 1
        fraction = tail_mass - whole
        for policy in ("v4_hybrid", "v3_full", "capacity_v2", "capacity_balance"):
            harms = np.maximum(values[policy] - values["static"], 0.)[sampled_indices]
            partitioned = np.partition(harms, boundary, axis=1)
            numerator = partitioned[:, boundary + 1:].sum(axis=1)
            if fraction > 1e-12:
                numerator += fraction * partitioned[:, boundary]
            risk_samples[policy] = numerator / tail_mass
        for baseline in ("v3_full", "capacity_v2", "capacity_balance"):
            ci = np.percentile(risk_samples[baseline] - risk_samples["v4_hybrid"], [2.5, 97.5])
            recorded = part["static_harm_comparisons"]["v4_hybrid"][baseline]["cvar90"]["risk_reduction_ci95"]
            require(all(close(a, b) for a, b in zip(ci, recorded)), "Independent paired CVaR CI mismatch")
            risk_checks += 1
    return {"mean_noninferiority_intervals_checked": mean_checks,
            "paired_static_relative_cvar_intervals_checked": risk_checks,
            "bootstrap_draws_per_regime": draws, "bootstrap_seed": seed,
            "scope": "All acceptance-driving CIs plus every regime's hybrid-vs-three-comparator CVaR interval; other descriptive CIs not re-estimated."}


def failure_context(rows):
    by = {key(row)[:-1]: row for row in rows}
    result = {}
    for regime in REGIMES:
        candidates = [r for r in rows if r["regime"] == regime and r["policy"] == "v4_hybrid"]
        worst = sorted(candidates, key=lambda r: r["mean_deficit"] - by[regime, r["group"], r["scenario_seed"], "static"]["mean_deficit"], reverse=True)[:10]
        result[regime] = []
        for row in worst:
            case = {"group": row["group"], "scenario_seed": row["scenario_seed"]}
            for policy in ("static", "capacity_v2", "capacity_balance", "v3_full", "v4_hybrid"):
                r = by[regime, row["group"], row["scenario_seed"], policy]
                case[policy] = {m: r[m] for m in ("mean_deficit", "end_deficit", "tail_auc_deficit", "total_dropped", "total_completed", "total_moves")}
            excess = row["mean_deficit"] - case["static"]["mean_deficit"]
            case["hybrid_signed_excess_over_static"] = excess
            denominator = case["static"]["mean_deficit"]
            case["hybrid_excess_over_static_pct"] = 100 * excess / denominator if denominator > 1e-6 else None
            result[regime].append(case)
    return result


def review(out, tests_path=None, test_log_path=None):
    out = Path(out).resolve()
    tests_path = Path(tests_path).resolve() if tests_path else ROOT / "v4_results/unit_tests.json"
    test_log_path = Path(test_log_path).resolve() if test_log_path else ROOT / "v4_results/unit_tests.log"
    # verify_frozen refuses an unfrozen run; reserved arrays are not generated
    # by this review until a recorded freeze is already present and valid.
    frozen = verify_frozen()
    protocol, verification = read_json(ROOT / "v4_protocol.json"), read_json(out / "verification.json")
    require(verification["split"] == "validation" and verification["status"] == "SUCCEEDED", "Completed validation required")
    require(verification["freeze_verification"] == frozen, "Recorded frozen identity mismatch")
    require(verification["execution_source_sha256"] == execution_source_hashes(), "Execution source mismatch")
    require(verification["upstream_sha256"] == audit_source_integrity(ROOT), "Upstream source mismatch")
    require(verification["controller_configuration"] == protocol["controller_configuration"], "Controller configuration mismatch")
    config = V4Config(**protocol["controller_configuration"])
    summary = read_json(artifact(out, {"path": "summary.json", "sha256": verification["summary_sha256"]}))
    receipt = artifact(out, {"path": verification["receipt_path"], "sha256": verification["receipt_sha256"]})
    require(receipt.stat().st_size == verification["receipt_bytes"], "Receipt byte count mismatch")
    manifest = scenario_manifest("validation")
    require(read_json(out / "scenario_manifest.json") == manifest, "Scenario/noise manifest mismatch")
    require(manifest["manifest_sha256"] == verification["scenario_manifest_sha256"] == protocol["validation_manifest_sha256"],
            "Validation manifest identity mismatch")
    require(sha(out / "scenario_manifest.json") == verification["scenario_manifest_file_sha256"], "Manifest file checksum mismatch")
    scenarios = generate_scenarios("validation")
    first = {}
    for case in scenarios:
        first.setdefault(case.group, case)
    index = read_json(out / "representative_traces_index.json")
    descriptors, rows = {}, []
    for regime in REGIMES:
        for policy in POLICIES:
            name = regime + "__" + policy
            descriptor = read_json(out / "indices" / (name + ".json"))
            require((descriptor["split"], descriptor["regime"], descriptor["policy"]) == ("validation", regime, policy),
                    "Policy artifact identity mismatch")
            require(descriptor["rows"]["path"] == "rows/" + name + ".json" and
                    descriptor["traces"]["path"] == "traces/" + name + ".json.gz", "Unexpected artifact path")
            require(descriptor["traces"] == index[name], "Trace index mismatch")
            payload = read_json(artifact(out, descriptor["rows"]))
            require((payload["regime"], payload["policy"]) == (regime, policy) and
                    len(payload["rows"]) == descriptor["rows"]["count"], "Row descriptor mismatch")
            descriptors[name] = descriptor
            rows.extend(payload["rows"])
    require(set(index) == set(descriptors), "Trace-index scope mismatch")
    scope = audit_rows(rows, scenarios)
    require(scope == verification["row_audit"], "Recorded row audit mismatch")
    expected = protocol["expected_validation_scope"]
    for field, value in (("episodes_checked", expected["episodes"]), ("simulated_minutes", expected["total_minutes"]),
                         ("scenarios_checked", expected["scenarios"]), ("scenario_regime_pairs", expected["scenario_regime_pairs"])):
        require(scope[field] == value, "Frozen episode/minute scope mismatch")
    for field, value in (("episodes", expected["episodes"]), ("minutes", expected["total_minutes"]),
                         ("scenarios", expected["scenarios"]), ("scenario_regime_pairs", expected["scenario_regime_pairs"]),
                         ("original_minutes", expected["original_minutes"]), ("followup_minutes", expected["followup_minutes"])):
        require(summary[field] == value, "Summary scope mismatch")
    check_csv(out, rows)
    points = check_point_statistics(rows, summary)
    bootstrap = check_acceptance_bootstrap(rows, summary)
    by = {key(row): row for row in rows}
    count = minutes = 0
    per_policy = {p: {"traces": 0, "decisions": 0} for p in POLICIES}
    pending_loss = startup_loss = 0.
    for name, descriptor in descriptors.items():
        regime, policy = descriptor["regime"], descriptor["policy"]
        with gzip.open(artifact(out, descriptor["traces"]), "rt", encoding="utf-8") as handle:
            representatives = json.load(handle)
        identities = {(regime, group, s.seed, policy, None) for group, s in first.items()}
        require({key(rep["summary"]) for rep in representatives} == identities and len(representatives) == len(identities),
                "Representative scope mismatch")
        require(len(representatives) == descriptor["traces"]["count"], "Trace descriptor count mismatch")
        local_minutes = 0
        for rep in representatives:
            row = rep["summary"]
            require(row == by[key(row)], "Trace summary differs from row artifact")
            physical = audit_trace(rep, first[row["group"]], regime)
            # Constructor receives only declared engineering configuration.
            controller = make_controller(policy, regime, config=config)
            observed = audit_controller_replay(rep, controller)
            require(observed["decisions_replayed"] == physical["minutes_replayed"], "Replay minute mismatch")
            local_minutes += physical["minutes_replayed"]
            count += 1
            pending_loss += physical["observed_pending_resource_minutes"]
            startup_loss += physical["observed_startup_capacity_minutes"]
            per_policy[policy]["traces"] += 1
            per_policy[policy]["decisions"] += observed["decisions_replayed"]
        require(local_minutes == descriptor["traces"]["minutes"], "Trace descriptor minute mismatch")
        minutes += local_minutes
    require(count == 462 and minutes == 51576, "Unexpected representative replay scope")
    require(verification["independent_trace_replay"]["traces"] == count and
            verification["independent_trace_replay"]["minutes"] == minutes, "Workflow replay count differs")
    tests = read_json(tests_path)
    require(tests["passed"] is True and tests["tests_run"] >= 79 and tests["failures"] == tests["errors"] == tests["skipped"] == 0,
            "Required unit tests not passed")
    require(tests["sources_unchanged_during_tests"] is True, "Sources changed during unit tests")
    require(set(tests["modules"]) == {"test_lab", "test_capacity", "test_v3", "test_v4", "test_v4_metrics"}, "Unit module scope mismatch")
    for name, expected_hash in tests["source_hashes"].items():
        require(sha(ROOT / name) == expected_hash, "Unit-tested source changed: " + name)
    underload = [r for r in rows if r["policy"] == "v4_hybrid" and r["group"] == "generated_underload"]
    require(len(underload) == 384, "Underload scope mismatch")
    underload_failures = [{k: r[k] for k in ("regime", "scenario_seed", "total_dropped", "total_moves")}
                         for r in underload if abs(r["total_dropped"]) > 1e-6 or abs(r["total_moves"]) > 1e-6]
    require(verify_frozen() == frozen, "Frozen files changed during review")
    return {
        "version": "0.4.0", "split": "validation", "scope": scope,
        "acceptance_integrity": {
            "tests_passed": True, "frozen_identity_passed": True, "source_and_sensor_boundary_passed": True,
            "full_episode_accounting_passed": True, "paired_scope_passed": True, "independent_replay_passed": True,
        },
        "source_identity": frozen,
        "artifact_evidence": {"summary_sha256": sha(out / "summary.json"), "verification_sha256": sha(out / "verification.json"),
                              "episodes_csv_sha256": sha(out / "episodes.csv"), "policy_artifact_pairs": len(descriptors)},
        "unit_tests": {"tests_run": tests["tests_run"], "report_sha256": sha(tests_path),
                       "log_sha256": sha(test_log_path)},
        "independent_point_statistics": points, "independent_acceptance_bootstrap": bootstrap,
        "physical_replay": {"traces": count, "minutes": minutes, "pending_resource_minutes": pending_loss,
                            "startup_capacity_minutes": startup_loss,
                            "scope": "Repeated verification of the same prespecified representative traces; not additional independent cases."},
        "observation_only_replay": {"traces": count, "decisions": minutes, "by_policy": per_policy,
                                    "actions_and_evidence_match": True,
                                    "boundary": "Only measured pre-queue/arrival, delivered past history, current committed units, and same-source measured flow/end-queue/effective units are supplied. No true rates, future arrays, noise arrays or terminal schedule are supplied."},
        "sensor_delivery": {"source_times": "pre-service t-lag; feedback after t uses source t-lag; latest before decision t is t-1-lag",
                            "same_source_noise_and_effective_denominator_verified": True, "coldstart_verified": True},
        "underload": {"cases": len(underload), "zero_drop_zero_move_failures": underload_failures,
                      "scope": "No zero-deficit requirement: a finite pipeline retains work even when underloaded."},
        "largest_static_relative_harms": failure_context(rows),
        "review_script_sha256": sha(__file__), "python": platform.python_version(), "numpy": np.__version__,
        "limitations": ["All evidence remains synthetic and conditional on the declared model and case families.",
                        "Representative replay does not reconstruct every episode minute; all rows and inline invariants cover the full episode scope.",
                        "Selected confidence intervals were independently reconstructed; remaining descriptive intervals retain the tested frozen implementation."]
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "v4_results/validation"))
    parser.add_argument("--report", default=None)
    parser.add_argument("--tests", default=None, help="Fresh unit-test JSON; defaults to the bundled v4_results/unit_tests.json")
    parser.add_argument("--test-log", default=None, help="Fresh unit-test log; defaults to the bundled v4_results/unit_tests.log")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    report = Path(args.report).resolve() if args.report else out / "independent_review.json"
    result = review(out, tests_path=args.tests, test_log_path=args.test_log)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report), "scope": result["scope"],
                      "acceptance_integrity": result["acceptance_integrity"],
                      "replay": {k: result["physical_replay"][k] for k in ("traces", "minutes")}}, indent=2))


if __name__ == "__main__":
    main()
