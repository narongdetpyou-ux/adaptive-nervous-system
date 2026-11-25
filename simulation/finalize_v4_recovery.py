"""Truthfully finalize a resumed v0.4 repetition, without altering its freeze.

Run only after recover_v4_run.py succeeds. Completed evaluation outputs are
reused. This performs duplicate/restart, negative control, complete row and
physical/sensor trace audits and exports, retaining the actual extra attempt.
"""
import argparse
import csv
from datetime import datetime, timezone
import gzip
import io
import json
from pathlib import Path
import platform
import time

import numpy as np

from neuron_flow import Runtime
from neuron_flow.model import fingerprint
from run_v4 import agents
from recover_v4_run import accepted_files, INTERRUPTED, JOB_ID
from v4_agents import ROOT, _input, artifact_path, all_rows, step_name
from v4_scenarios import REGIMES, POLICIES, GROUPS, generate_scenarios, policy_seeds, expected_counts
from audit_v4 import require, audit_rows, audit_trace
from audit_capacity import audit_source_integrity
from freeze_v4 import verify_frozen, execution_source_hashes, verify_parent_frozen
from capacity_agents import atomic_bytes, write_json, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output, evidence = args.out.resolve(), args.evidence_dir.resolve()
    require(not (output / "verification.json").exists(), "Existing verification must not be overwritten")
    started = time.perf_counter()
    helper_hash = sha(Path(__file__))
    recovery = json.loads((evidence / "recovery_runtime_completed.json").read_text())
    start = json.loads((evidence / "recovery_started.json").read_text())
    recovered_state = json.loads((evidence / "runtime_after_resume.json").read_text())
    require(recovery["status"] == recovered_state["status"] == "SUCCEEDED", "Runtime recovery has not succeeded")
    require(sha(ROOT / "recover_v4_run.py") == recovery["helper_sha256"] == start["helper_sha256"],
            "The executed recovery helper changed")
    spec = start["stored_specification"]
    inputs = spec["input"]
    split, checked_output, config = _input({"input": inputs})
    require(split == "validation" and checked_output == output, "Unexpected finalization target")
    require(inputs == json.loads((output / "run_identity.json").read_text()), "Original run identity changed")
    names = [step_name(r, p) for r in REGIMES for p in POLICIES]
    completed = [name for name in names if start["before"]["steps"][name]["status"] == "SUCCEEDED"]
    injected = "legacy_instant__v4_hybrid"
    require(start["before"]["steps"][injected]["attempts"] == 2, "Original controlled retry count differs")
    require(all(start["before"]["steps"][name]["attempts"] == 1 for name in completed if name != injected),
            "Unexpected original accepted attempt count")
    expected_attempts = {name: 2 if name in (injected, INTERRUPTED) else 1 for name in names}
    expected_attempts["comparison"] = 1
    public_spec = {key: spec[key] for key in ("job_id", "input", "steps")}
    print(json.dumps({"phase": "finalizing_recovered_workflow", "expected_attempts": expected_attempts}), flush=True)
    with Runtime(output / "workspace", agents=agents(), max_workers=3) as runtime:
        stored = runtime.store.job(JOB_ID)
        require(stored["spec"] == spec and stored["fingerprint"] == fingerprint(spec),
                "Original stored specification changed")
        state = runtime.status(JOB_ID)
        require(state["status"] == "SUCCEEDED" and state["steps"] == recovered_state["steps"],
                "Recovered durable state changed before finalization")
        attempts = {name: state["steps"][name]["attempts"] for name in expected_attempts}
        require(attempts == expected_attempts, "Actual recovered attempts differ from expected history")
        report_path = Path(state["receipt"]["path"])
        receipt_before = (sha(report_path), report_path.stat().st_mtime_ns)
        duplicate = runtime.submit(public_spec)
        require(duplicate["created"] is False, "Duplicate submission unexpectedly created a job")
        reused = runtime.run(JOB_ID)
        require(reused["status"] == "SUCCEEDED" and reused["steps"] == state["steps"],
                "Restart reran or changed a completed recovered step")
        require((sha(report_path), report_path.stat().st_mtime_ns) == receipt_before,
                "Restart altered the recovered receipt")
        timeout = max(step["timeout_s"] for step in spec["steps"])
        negative = {
            "job_id": "v4_negative_validation", "input": inputs,
            "steps": [
                {"id": "bad", "agent": "v4_evaluate",
                 "params": {"regime": "legacy_instant", "policy": "static", "reuse_original": True},
                 "timeout_s": timeout, "max_attempts": 1},
                {"id": "blocked", "agent": "v4_noop", "depends_on": ["bad"]},
                {"id": "independent", "agent": "v4_noop"},
            ],
        }
        negative_submission = runtime.submit(negative)
        neg = runtime.run(negative["job_id"])
        neg_status = {name: item["status"] for name, item in neg["steps"].items()}
        require(neg["status"] == "FAILED" and neg_status == {
            "bad": "FAILED", "blocked": "BLOCKED", "independent": "SUCCEEDED"},
            "Negative-control rejection/isolation failed")
        require(neg["steps"]["bad"]["attempts"] == 1, "Corrupted output was retried")
        events = runtime.synapse.events_for("monitor", JOB_ID)
        negative_events = runtime.synapse.events_for("monitor", negative["job_id"])
    current_preserved = accepted_files(output, {name: state["steps"][name] for name in completed})
    require(current_preserved == start["accepted_artifacts_before"], "Previously accepted artifacts changed")
    dependencies = {name: state["steps"][name]["output"] for name in names}
    rows = all_rows(output, dependencies)
    cases = generate_scenarios("validation")
    row_audit = audit_rows(rows, cases, {p: policy_seeds(p) for p in POLICIES}, regimes=REGIMES)
    print(json.dumps({"phase": "row_audit_complete", "episodes": len(rows)}), flush=True)
    first = {}
    for case in cases:
        first.setdefault(case.group, case)
    row_map = {(r["regime"], r["group"], r["scenario_seed"], r["policy"]): r for r in rows}
    trace_index, trace_audits = {}, {}
    for name in names:
        result = dependencies[name]
        trace_index[name] = result["traces"]
        with gzip.open(artifact_path(output, result["traces"]), "rt", encoding="utf-8") as handle:
            representatives = json.load(handle)
        expected = {(group, case.seed) for group, case in first.items()}
        observed = {(rep["summary"]["group"], rep["summary"]["scenario_seed"]) for rep in representatives}
        require(observed == expected and len(representatives) == len(expected), "Representative scope differs")
        results = []
        for rep in representatives:
            row = rep["summary"]
            require(row == row_map[row["regime"], row["group"], row["scenario_seed"], row["policy"]],
                    "Representative summary differs from retained rows")
            results.append(audit_trace(rep, first[row["group"]], result["regime"]))
        minutes = sum(item["minutes_replayed"] for item in results)
        require(result["traces"]["count"] == len(results) and result["traces"]["minutes"] == minutes,
                "Representative descriptor counts differ")
        trace_audits[name] = {"traces": len(results), "minutes": minutes,
                             "physics_and_sensor_checks_passed": True}
        print(json.dumps({"phase": "physical_sensor_replay", "step": name,
                          "completed_artifact_pairs": len(trace_audits)}), flush=True)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    for relative in ("episodes.csv", "representative_traces_index.json", "workflow_events.json"):
        require(not (output / relative).exists(), "Final export already exists; preserve it: " + relative)
    atomic_bytes(output / "episodes.csv", buffer.getvalue().encode("utf-8"))
    write_json(output / "representative_traces_index.json", trace_index)
    write_json(output / "workflow_events.json", events)
    write_json(evidence / "negative_control_state.json", neg)
    write_json(evidence / "negative_control_events.json", negative_events)
    write_json(evidence / "fresh_physical_sensor_replay.json", trace_audits)
    comparison = state["steps"]["comparison"]["output"]
    require(sha(output / "summary.json") == comparison["summary"]["sha256"], "Final summary differs")
    require(audit_source_integrity(ROOT) == inputs["upstream_sha256"], "Original sources changed")
    require(execution_source_hashes() == inputs["execution_source_sha256"], "Frozen execution sources changed")
    require(verify_parent_frozen() == inputs["parent_v3_verification"], "Parent freeze changed")
    require(verify_frozen() == inputs["freeze_verification"], "Frozen v0.4 identity changed")
    require(sha(output / "scenario_manifest.json") == inputs["scenario_manifest_file_sha256"],
            "Original scenario manifest changed")
    require(sha(Path(__file__)) == helper_hash, "Finalizer changed during execution")
    trace_count = sum(item["traces"] for item in trace_audits.values())
    trace_minutes = sum(item["minutes"] for item in trace_audits.values())
    require(trace_count == 462 and trace_minutes == 51576, "Independent replay scope differs")
    current_preserved = accepted_files(output, {name: state["steps"][name] for name in completed})
    require(current_preserved == start["accepted_artifacts_before"], "Original accepted artifacts changed during audits")
    recovery_elapsed = (datetime.now(timezone.utc) -
                        datetime.fromisoformat(start["started_at_utc"])).total_seconds()
    recovery_record = {
        "interruption_observed": True,
        "interruption_cause": "unknown",
        "observations": start["observation"],
        "method": "Original Runtime.run on the unchanged stored job; ControllerInterrupted retry used its original budget.",
        "original_accepted_evaluations_preserved": 41,
        "original_accepted_artifact_files_preserved": len(current_preserved),
        "original_accepted_states_hashes_sizes_mtimes_preserved": True,
        "interrupted_step": INTERRUPTED,
        "interrupted_attempt": 1,
        "recovered_attempt": 2,
        "controller_interrupted_events": recovery["controller_interrupted_events"],
        "stored_specification_fingerprint": start["stored_specification_fingerprint"],
        "runtime_recovery_helper_sha256": recovery["helper_sha256"],
        "finalization_helper_sha256": helper_hash,
        "original_runtime_session_peak_workers": None,
        "recovery_runtime_session_peak_workers": recovery["recovery_session_peak_workers"],
        "original_full_run_wall_seconds": None,
        "runtime_recovery_elapsed_seconds": recovery["recovery_elapsed_seconds"],
        "finalization_elapsed_seconds": round(time.perf_counter() - started, 3),
        "recovery_wall_seconds_including_interphase_gap": round(recovery_elapsed, 3),
        "counting_scope": (
            "29,568 accepted deterministic episode rows and their declared simulated minutes. "
            "Uncommitted work from the interrupted first attempt is unknown and adds no independent cases."
        ),
        "fresh_physical_sensor_replay": {"traces": trace_count, "minutes": trace_minutes},
        "accepted_controller_replay_provenance": {
            "retained_validated_evaluations": 41,
            "evaluation_validated_after_retry": 1,
            "scope": (
                "All accepted evaluation outputs passed the unchanged frozen validator, including exact "
                "observation-only controller replay. The finalizer freshly replays physics/sensors; "
                "it does not claim an additional full controller replay."
            ),
        },
        "evidence_directory": str(evidence),
        "negative_control_submission_created": negative_submission["created"],
    }
    verification = {
        "version": "0.4.0", "split": "validation", "status": state["status"],
        "steps": attempts,
        "peak_workers": recovery["recovery_session_peak_workers"],
        "peak_workers_scope": "Main-workflow recovery controller session only; original session peak is not durably available.",
        "receipt_path": str(report_path.relative_to(output)), "receipt_sha256": receipt_before[0],
        "receipt_bytes": report_path.stat().st_size,
        "summary_sha256": comparison["summary"]["sha256"],
        "artifact_exchange": "descriptors only; rows/traces/summary checksum-verified from atomic local artifacts",
        "duplicate_submission_created": duplicate["created"],
        "restart_replay_preserved_attempts": True,
        "restart_replay_preserved_report": True,
        "negative_control": {
            "status": neg["status"], "steps": neg_status, "bad_error": neg["steps"]["bad"]["error"],
            "scope": "completed+1 in a separate static legacy_instant row copy; no additional rollout",
        },
        "row_audit": row_audit,
        "independent_trace_replay": {
            "traces": trace_count, "minutes": trace_minutes,
            "selection": "first scenario seed per family, every deterministic policy/regime; fixed before outcomes",
            "scope": "Fresh independent physical and sensor replay during recovery finalization; all rows audited.",
        },
        "observation_only_controller_replay": {
            "traces": trace_count, "decisions": trace_minutes, "actions_and_evidence_match": True,
            "scope": (
                "Frozen evaluation validators: 41 durably accepted evaluations retained unchanged and "
                "one accepted evaluation validated after recovery; only recorded measured inputs."
            ),
        },
        "source_and_sensor_boundary": {
            "passed": True, "traces": trace_count, "minutes": trace_minutes,
            "checks": "Fresh source timestamp, coldstart, common-noise, exact-ledger and same-source effective-staff checks.",
        },
        "independent_statistics_check": (
            "The comparison worker and frozen comparison validator ran after recovery: all overall/source/family "
            "point means, paired effects/counts, NI slack and static-relative harm/CVaR checked; "
            "intervals use the unchanged frozen statistical module."
        ),
        "expected_scope": expected_counts("validation"),
        "upstream_sha256": inputs["upstream_sha256"],
        "parent_v3_verification": inputs["parent_v3_verification"],
        "execution_source_sha256": inputs["execution_source_sha256"],
        "freeze_verification": inputs["freeze_verification"],
        "scenario_manifest_sha256": inputs["scenario_manifest_sha256"],
        "scenario_manifest_file_sha256": inputs["scenario_manifest_file_sha256"],
        "controller_configuration": config.as_dict(),
        "controller_configuration_sha256": inputs["controller_configuration_sha256"],
        "configuration_source": inputs["configuration_source"],
        "python": platform.python_version(), "numpy": np.__version__,
        "elapsed_seconds": round(recovery_elapsed, 3),
        "timing_scope": "Recovery start through finalization, including the interphase gap; excludes unknown original-run duration.",
        "inference_scope": "A resumed repetition of the same frozen cases; no additional independent scenarios.",
        "recovery": recovery_record,
    }
    write_json(evidence / "recovery_finalization_completed.json", recovery_record)
    write_json(output / "recovery.json", recovery_record)
    write_json(output / "verification.json", verification)
    print(json.dumps({
        "status": verification["status"], "actual_attempts": attempts,
        "completed_evaluations_preserved": 41, "physical_sensor_traces_replayed": trace_count,
        "replay_minutes": trace_minutes, "verification": str(output / "verification.json"),
        "interruption_cause": "unknown",
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
