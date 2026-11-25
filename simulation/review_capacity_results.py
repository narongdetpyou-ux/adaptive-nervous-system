"""Reproduce the independent audit of an existing v0.2 validation result.

This is a post-validation reviewer, not a new policy experiment. It checks the
frozen experiment, every episode row, statistical aggregation, physical traces,
and the new controllers' decisions using recorded observations only.

Usage:
    python review_capacity_results.py
    python review_capacity_results.py --out fresh_results/validation \
        --report fresh_results/independent_review.json

Without --report the complete report is printed and no file is written. No
frozen file or result artifact is modified. A failed check raises ValueError
and prevents a successful report from being written.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import gzip
import hashlib
import json
from pathlib import Path
import platform

import numpy as np

from audit_capacity import (
    audit_rows, audit_source_integrity, audit_trace, close,
    independent_policy_means, require,
)
from capacity_metrics import aggregate
from capacity_policy import CapacityAwarePolicy, CapacityConfig
from capacity_scenarios import (
    GROUP_INDEX, POLICIES, generate_scenarios, policy_seeds, scenario_manifest,
)
from freeze_capacity import verify_frozen

ROOT = Path(__file__).resolve().parent
NEW_POLICIES = ("capacity_aware", "abc_capacity")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def result_path(directory, relative):
    path = (directory / relative).resolve()
    require(path.is_relative_to(directory), "Result reference leaves its directory")
    require(path.is_file(), "Missing result artifact: " + str(relative))
    return path


def episode_key(row):
    return row["group"], row["scenario_seed"], row["policy"], row["policy_seed"]


def replay_observed_controller(representative, configuration):
    """Reconstruct actions and complete evidence without a productivity input.

    Environment labels/seeds are used outside the controller solely to recreate
    its original random stream. The controller receives current observed queue,
    current arrival, prior end queues and allocation. Completed flow/end queue
    become observations only AFTER the recorded decision for that minute.
    No current/future productivity or future arrival is supplied to any method.
    """
    row = representative["summary"]
    minutes = representative["minutes"]
    require(row["policy"] in NEW_POLICIES, "Not a capacity policy trace")
    uniform = None
    if row["policy_seed"] is not None:
        stream = np.random.SeedSequence([
            row["policy_seed"], row["scenario_seed"], GROUP_INDEX[row["group"]],
        ])
        uniform = np.random.default_rng(stream).random(len(minutes))
    mode = "catalog" if row["policy"] == "capacity_aware" else "abc_only"
    controller = CapacityAwarePolicy(mode, uniform, CapacityConfig(**configuration))
    staff = np.full(3, 69.0)
    history = []
    for minute in minutes:
        t = minute["t"]
        delta, evidence = controller.decide(
            t, np.array(minute["observed"]), history,
            staff.copy(), minute["arrival"],
        )
        identity = f"{row['policy']}/{row['group']}/{row['scenario_seed']}/minute {t}"
        require(np.array_equal(delta, np.array(minute["delta"])),
                "Observation-only action replay mismatch: " + identity)
        require(evidence == minute["decision_evidence"],
                "Observation-only evidence replay mismatch: " + identity)
        staff += delta
        controller.observe(
            t, staff.copy(), np.array(minute["flow"]), np.array(minute["queue"]),
        )
        history = (history + [np.array(minute["queue"])])[-5:]
    return len(minutes)


def check_csv(out, rows):
    with (out / "episodes.csv").open(newline="", encoding="utf-8") as handle:
        csv_rows = list(csv.DictReader(handle))
    require(len(csv_rows) == len(rows), "CSV episode count differs from JSON")
    key = lambda r: (r["group"], str(r["scenario_seed"]), r["policy"],
                     "" if r["policy_seed"] is None else str(r["policy_seed"]))
    originals = {key(row): row for row in rows}
    seen = set()
    for row in csv_rows:
        identity = row["group"], row["scenario_seed"], row["policy"], row["policy_seed"]
        require(identity not in seen and identity in originals,
                "Duplicate or unexpected CSV episode")
        seen.add(identity)
        original = originals[identity]
        require(set(row) == set(original), "CSV columns differ from JSON")
        for field, value in original.items():
            expected = "" if value is None else str(value)
            require(row[field] == expected, "CSV value differs from JSON: " + field)
    require(seen == set(originals), "CSV is missing JSON episodes")


def analyze_failures(summary):
    harms, terminal_harms = [], []
    for case in summary["case_results"]:
        base, candidate = case["static"], case["capacity_aware"]
        increase = candidate["mean_backlog"] - base["mean_backlog"]
        if increase > 1e-6:
            harms.append({
                "group": case["group"], "scenario_seed": case["scenario_seed"],
                "static_mean": base["mean_backlog"],
                "capacity_aware_mean": candidate["mean_backlog"],
                "absolute_increase": increase,
                "increase_pct": 100 * increase / base["mean_backlog"] if base["mean_backlog"] else None,
                "static_end": base["end_backlog"],
                "capacity_aware_end": candidate["end_backlog"],
                "static_peak": base["peak_backlog"],
                "capacity_aware_peak": candidate["peak_backlog"],
                "capacity_aware_moves": candidate["moves"],
            })
        extra_end = candidate["end_backlog"] - base["end_backlog"]
        if extra_end > 1e-6:
            terminal_harms.append({
                "group": case["group"], "seed": case["scenario_seed"],
                "extra_end_backlog": extra_end,
                "static_end": base["end_backlog"],
                "capacity_aware_end": candidate["end_backlog"],
                "mean_backlog_improvement": -increase,
            })
    harms.sort(key=lambda item: -item["absolute_increase"])
    terminal_harms.sort(key=lambda item: -item["extra_end_backlog"])
    groups = {
        group: {
            "static_mean": data["overall"]["static"]["mean_backlog"],
            "capacity_aware_mean": data["overall"]["capacity_aware"]["mean_backlog"],
            "static_end": data["overall"]["static"]["end_backlog"],
            "capacity_aware_end": data["overall"]["capacity_aware"]["end_backlog"],
            "static_reduction_pct": data["comparisons"]["capacity_aware"]["static"]["reduction_pct"],
            "static_wins_ties_losses": [
                data["comparisons"]["capacity_aware"]["static"][field]
                for field in ("wins", "ties", "losses")
            ],
        }
        for group, data in summary["by_group"].items()
    }
    overall = summary["overall"]
    terminal_denominator = overall["static"]["end_backlog"]
    completion_denominator = overall["static"]["completed"]
    return {
        "harms_vs_static_count": len(harms),
        "harms_vs_static_by_group": dict(Counter(item["group"] for item in harms)),
        "largest_absolute_harms_vs_static": harms[:5],
        "largest_relative_harms_vs_static": sorted(
            harms, key=lambda item: -(item["increase_pct"] or 0),
        )[:5],
        "by_group": groups,
        "terminal_harms_vs_static": terminal_harms,
        "mean_completed_increase_pct": (
            100 * (overall["capacity_aware"]["completed"] - completion_denominator) / completion_denominator
            if completion_denominator else None
        ),
        "mean_terminal_reduction_pct": (
            100 * (terminal_denominator - overall["capacity_aware"]["end_backlog"]) / terminal_denominator
            if terminal_denominator else None
        ),
        "abc_only_gate_tradeoff": {
            "vs_static": summary["abc_capacity_vs"]["static"],
            "vs_abc": summary["abc_capacity_vs"]["abc"],
            "vs_catalog": summary["abc_capacity_vs"]["capacity_aware"],
            "mean_moves": overall["abc_capacity"]["moves"],
            "catalog_mean_moves": overall["capacity_aware"]["moves"],
            "terminal_backlog": overall["abc_capacity"]["end_backlog"],
            "catalog_terminal_backlog": overall["capacity_aware"]["end_backlog"],
        },
    }


def review(out):
    out = Path(out).resolve()
    protocol = read_json(ROOT / "capacity_protocol.json")
    frozen = verify_frozen()
    summary = read_json(out / "summary.json")
    verification = read_json(out / "verification.json")
    require(verification["split"] == "validation", "This reviewer expects a validation result")
    require(verification["status"] == "SUCCEEDED", "Validation workflow did not succeed")
    require(verification["freeze_verification"] == frozen, "Result belongs to another frozen experiment")
    require(verification["capacity_config"] == protocol["controller_configuration"], "Result configuration differs")

    cases = generate_scenarios("validation")
    cases_by = {(case.group, case.seed): case for case in cases}
    first_cases = {}
    for case in cases:
        first_cases.setdefault(case.group, case)
    upstream = audit_source_integrity(ROOT)
    rows, evaluations = [], {}
    for policy in POLICIES:
        result = read_json(out / "rows" / f"{policy}.json")
        require(result["policy"] == policy and result["split"] == "validation", "Wrong policy-result identity")
        require(result["upstream_hashes"] == upstream, "Policy result has different upstream identity")
        evaluations[policy] = result
        rows.extend(result["rows"])
    scope = audit_rows(rows, cases, {policy: policy_seeds(policy) for policy in POLICIES})
    expected_scope = {
        "scenarios_checked": protocol["splits"]["validation"]["scenarios"],
        "episodes_checked": protocol["splits"]["validation"]["episodes"],
        "simulated_minutes": sum(case.horizon for case in cases) * sum(len(policy_seeds(p)) for p in POLICIES),
    }
    require(scope == expected_scope == verification["row_audit"], "Validation scope differs from protocol")
    require(aggregate(rows) == summary, "Full statistical aggregation differs")
    for policy, metrics in independent_policy_means(rows).items():
        for metric, value in metrics.items():
            require(close(summary["overall"][policy][metric], value), "Independent policy mean differs")
    require(upstream == protocol["upstream_sha256"] == verification["upstream_hashes"], "Source hash evidence differs")
    manifest = scenario_manifest("validation")
    require(read_json(out / "scenario_manifest.json") == manifest, "Saved scenario manifest differs")
    require(manifest["manifest_sha256"] == protocol["validation_manifest_sha256"] == verification["scenario_manifest_sha256"],
            "Scenario-manifest identity differs")
    require(sha(out / "summary.json") == verification["summary_sha256"], "Summary checksum mismatch")
    receipt = result_path(out, verification["receipt_path"])
    require(sha(receipt) == verification["receipt_sha256"], "Workflow receipt checksum mismatch")
    require(receipt.stat().st_size == verification["receipt_bytes"], "Workflow receipt size mismatch")
    check_csv(out, rows)

    row_by = {episode_key(row): row for row in rows}
    trace_index = read_json(out / "representative_traces_index.json")
    require(set(trace_index) == set(POLICIES), "Trace index has wrong policy scope")
    traces_checked = minutes_checked = controlled_traces = decisions_replayed = 0
    for policy in POLICIES:
        descriptor = trace_index[policy]
        require(descriptor == evaluations[policy]["representative_traces"], "Trace descriptors disagree")
        require(descriptor["path"] == f"traces/{policy}.json.gz", "Unexpected trace artifact name")
        path = result_path(out, descriptor["path"])
        require(sha(path) == descriptor["sha256"], "Trace artifact checksum mismatch")
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            representatives = json.load(handle)
        expected = {(group, case.seed, policy, policy_seeds(policy)[0]) for group, case in first_cases.items()}
        actual = {episode_key(rep["summary"]) for rep in representatives}
        require(actual == expected and len(representatives) == len(expected) == descriptor["count"],
                "Representative trace scope differs")
        policy_minutes = 0
        for representative in representatives:
            row = representative["summary"]
            require(row == row_by[episode_key(row)], "Trace summary differs from episode row")
            env = cases_by[row["group"], row["scenario_seed"]]
            check = audit_trace(representative, env.arrivals, env.productivity)
            traces_checked += 1
            minutes_checked += check["minutes_replayed"]
            policy_minutes += check["minutes_replayed"]
            if policy in NEW_POLICIES:
                # This separate function accepts observations, not env/rates.
                decisions_replayed += replay_observed_controller(representative, protocol["controller_configuration"])
                controlled_traces += 1
        require(policy_minutes == descriptor["minutes"], "Trace minute count differs")
    require(traces_checked == len(first_cases) * len(POLICIES), "Wrong replay trace total")
    require(minutes_checked == sum(case.horizon for case in first_cases.values()) * len(POLICIES), "Wrong replay minute total")
    require(verification["independent_trace_replay"]["traces"] == traces_checked and
            verification["independent_trace_replay"]["minutes"] == minutes_checked,
            "Saved physical replay count differs")

    underload = [row for row in rows if row["policy"] == "capacity_aware" and row["group"] == "generated_underload"]
    require(len(underload) == len(protocol["splits"]["validation"]["scenario_seeds"]), "Wrong underload scope")
    underload_passes = all(
        row["moves"] == 0 and row["mean_backlog"] == row["peak_backlog"] == row["end_backlog"] == 0
        for row in underload
    )
    primary = summary["capacity_aware_vs"]["static"]
    advancement = {
        source: {
            baseline: {
                "absolute_improvement_ci95": data["comparisons"]["capacity_aware"][baseline]["improvement_ci95"],
                "reduction_pct": data["comparisons"]["capacity_aware"][baseline]["reduction_pct"],
                "passes": data["comparisons"]["capacity_aware"][baseline]["improvement_ci95"][0] > 0,
            }
            for baseline in ("static", "abc_guard")
        }
        for source, data in summary["source_slices"].items()
    }
    require(set(advancement) == {"legacy_flash", "generated_synthetic"}, "Advancement source slices differ")
    require(verify_frozen() == frozen, "Frozen files changed during review")
    return {
        "review_metadata": {
            "schema": "capacity-results-independent-review-v1",
            "reviewer_script": Path(__file__).name,
            "reviewer_script_sha256": sha(__file__),
            "result_directory": str(out),
            "protocol_sha256": frozen["protocol_sha256"],
            "summary_sha256": sha(out / "summary.json"),
            "python": platform.python_version(), "numpy": np.__version__,
            "scope": "Audit of saved validation evidence; no new policy/environment experiment",
        },
        "scope": scope,
        "checks": {
            "full_aggregation_equal": True,
            "independent_policy_means_agree": True,
            "all_11_upstream_hashes_match": True,
            "protocol_implementation_and_environment_freeze_verified": True,
            "summary_receipt_trace_hashes_match": True,
            "csv_rows_match_json": True,
            "representative_scalar_replay": {"traces": traces_checked, "minutes": minutes_checked},
            "causal_decision_replay_from_observations_only": {
                "traces": controlled_traces, "decisions": decisions_replayed,
                "all_recorded_actions_and_full_evidence_equal": True,
            },
            "generated_underload_zero_moves_and_backlog": underload_passes,
        },
        "primary_vs_static": primary,
        "primary_passes": primary["improvement_ci95"][0] > 0,
        "advancement_checks": advancement,
        "advancement_passes": underload_passes and all(
            value["passes"] for data in advancement.values() for value in data.values()
        ),
        **analyze_failures(summary),
        "limitations": [
            "Confidence intervals reflect Monte Carlo scenario seeds within specified synthetic families, conditional on fixed policy draws and simulator; no model/deployment uncertainty.",
            "Only the complete-mixture capacity_aware vs static interval is the primary claim; secondary intervals are unadjusted exploratory comparisons.",
            "Positive average effects can coexist with worse cases and severe relative regression when baseline backlog is small; inspect the reported case failures.",
            "The forecast ensemble does not guarantee actual non-regression: hidden abrupt faults and demand shifts remain possible.",
            "Scalar replay verifies prespecified representative traces; other episodes have row accounting plus simulator inline invariants, not independent complete replay.",
            "Observation-only replay checks reproducibility of recorded decisions with the frozen controller; it is not a proof of optimality or a formal security analysis.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "capacity_results" / "validation",
                        help="Existing validation result directory")
    parser.add_argument("--report", type=Path, help="Optional JSON report destination; otherwise print only")
    args = parser.parse_args()
    result = review(args.out)
    encoded = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if args.report is not None:
        target = args.report.resolve()
        # Avoid accidental replacement of frozen source or input evidence.
        protected = {ROOT / name for name in read_json(ROOT / "capacity_protocol.json")["implementation_sha256"]}
        protected.update((ROOT / "capacity_protocol.json", ROOT / "capacity_protocol.sha256"))
        require(target not in protected, "Report path would overwrite a frozen file")
        if target.exists():
            prior = read_json(target)
            require(isinstance(prior, dict) and "primary_vs_static" in prior and "checks" in prior,
                    "Report path already contains a different artifact")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
