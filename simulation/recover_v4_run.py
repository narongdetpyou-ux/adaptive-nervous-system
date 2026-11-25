"""Resume one interrupted v0.4 workflow through the original Runtime API.

No frozen source, input, configuration or SQLite state is edited manually.
The original runtime records ControllerInterrupted and retries the stranded
attempt within its existing max_attempts. Separate finalization follows.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import threading
import time

from neuron_flow import Runtime
from neuron_flow.model import fingerprint
from run_v4 import agents
from v4_agents import _input, artifact_path, step_name
from v4_scenarios import REGIMES, POLICIES
from audit_v4 import require
from capacity_agents import write_json, sha

INTERRUPTED = "delayed_noisy__v4_no_recovery"
JOB_ID = "v4_validation"


def accepted_files(output, steps):
    records = {}
    for name, step in steps.items():
        if step["status"] != "SUCCEEDED":
            continue
        require(name != "comparison", "This recovery expects comparison to be pending")
        result = step["output"]
        paths = [
            artifact_path(output, result["rows"]),
            artifact_path(output, result["traces"]),
            output / "indices" / (name + ".json"),
        ]
        require(json.loads(paths[-1].read_text()) == result, "Accepted index differs from durable output")
        for path in paths:
            records[str(path.relative_to(output))] = {
                "sha256": sha(path), "bytes": path.stat().st_size,
                "mtime_ns": path.stat().st_mtime_ns,
            }
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    output, evidence = args.out.resolve(), args.evidence_dir.resolve()
    require(output.is_dir() and (output / "run_identity.json").is_file(), "Existing workflow required")
    require(not evidence.exists(), "Use a new recovery evidence directory")
    evidence.mkdir(parents=True)
    started = time.perf_counter()
    helper_hash = sha(Path(__file__))
    names = [step_name(r, p) for r in REGIMES for p in POLICIES]
    with Runtime(output / "workspace", agents=agents(), max_workers=3) as runtime:
        job = runtime.store.job(JOB_ID)
        stored_spec = job["spec"]
        require(fingerprint(stored_spec) == job["fingerprint"], "Stored specification fingerprint differs")
        inputs = stored_spec["input"]
        require(inputs == json.loads((output / "run_identity.json").read_text()), "Stored input differs from run identity")
        require(Path(inputs["out"]).resolve() == output and inputs["split"] == "validation", "Unexpected recovery target")
        _input({"input": inputs})
        before = runtime.status(JOB_ID)
        require(set(before["steps"]) == set(names + ["comparison"]), "Unexpected workflow scope")
        completed = [name for name in names if before["steps"][name]["status"] == "SUCCEEDED"]
        require(len(completed) == 41 and INTERRUPTED not in completed, "Expected exactly 41 accepted evaluations")
        require(before["steps"][INTERRUPTED]["status"] == "RUNNING"
                and before["steps"][INTERRUPTED]["attempts"] == 1, "Unexpected interrupted attempt state")
        require(before["steps"]["comparison"]["status"] == "PENDING"
                and before["steps"]["comparison"]["attempts"] == 0, "Comparison already ran")
        spec_steps = {step["id"]: step for step in stored_spec["steps"]}
        require(spec_steps[INTERRUPTED]["max_attempts"] == 2, "Original retry budget differs")
        files_before = accepted_files(output, before["steps"])
        partial = {}
        for relative in (f"rows/{INTERRUPTED}.json", f"traces/{INTERRUPTED}.json.gz",
                         f"indices/{INTERRUPTED}.json"):
            path = output / relative
            if path.is_file():
                partial[relative] = {"sha256": sha(path), "bytes": path.stat().st_size}
                destination = evidence / "unaccepted_before_resume" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
        start_record = {
            "version": "0.4.0", "job_id": JOB_ID, "output": str(output),
            "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "observation": "Original execution handle was reported unavailable; this process acquired the original OS workspace lock.",
            "interruption_cause": "unknown",
            "before": before, "stored_specification": stored_spec,
            "stored_specification_fingerprint": job["fingerprint"],
            "accepted_artifacts_before": files_before,
            "unaccepted_artifacts_before": partial,
            "monitor_events_before": runtime.synapse.events_for("monitor", JOB_ID),
            "helper_sha256": helper_hash,
            "recovery_method": "Original Runtime.run(job_id); no direct state edits or retry-budget changes.",
        }
        write_json(evidence / "recovery_started.json", start_record)
        print(json.dumps({"phase": "resuming_original_runtime", "accepted_evaluations": 41,
                          "interrupted_step": INTERRUPTED, "previous_attempt": 1,
                          "next_attempt": 2, "evidence": str(evidence)}), flush=True)
        stop = threading.Event()
        def heartbeat():
            while not stop.wait(30):
                print(json.dumps({"phase": "original_runtime_active",
                                  "recovery_elapsed_seconds": round(time.perf_counter() - started, 1)}), flush=True)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            after = runtime.run(JOB_ID)
        finally:
            stop.set()
            thread.join(1)
        write_json(evidence / "runtime_after_resume.json", after)
        events = runtime.synapse.events_for("monitor", JOB_ID)
        write_json(evidence / "monitor_events_after_resume.json", events)
        require(after["status"] == "SUCCEEDED", "Recovered workflow failed; retained evidence records its true state")
        expected = {name: before["steps"][name]["attempts"] for name in completed}
        expected[INTERRUPTED] = 2
        expected["comparison"] = 1
        require({name: after["steps"][name]["attempts"] for name in expected} == expected, "Unexpected attempt counts")
        for name in completed:
            require(after["steps"][name] == before["steps"][name], "Previously accepted step changed: " + name)
        retained_after = accepted_files(output, {name: after["steps"][name] for name in completed})
        require(retained_after == files_before, "Previously accepted artifact content or timestamp changed")
        interrupted_events = [
            event for event in events
            if event["step_id"] == INTERRUPTED
            and (event["payload"].get("error") or {}).get("type") == "ControllerInterrupted"
        ]
        require(len(interrupted_events) == 1, "Expected one built-in ControllerInterrupted event")
        _input({"input": inputs})
        require(sha(Path(__file__)) == helper_hash, "Recovery helper changed during execution")
        result = {
            "status": "SUCCEEDED", "job_id": JOB_ID,
            "interruption_cause": "unknown",
            "accepted_evaluations_preserved": 41,
            "accepted_artifact_files_preserved": len(files_before),
            "completed_step_outputs_and_attempts_preserved": True,
            "accepted_artifact_hashes_sizes_and_mtimes_preserved": True,
            "interrupted_step": INTERRUPTED, "interrupted_attempt": 1, "recovered_attempt": 2,
            "controller_interrupted_events": interrupted_events,
            "actual_attempts": expected,
            "recovery_session_peak_workers": after["peak_workers"],
            "recovery_elapsed_seconds": round(time.perf_counter() - started, 3),
            "timing_scope": "Recovery session only; original total wall time and interruption cause are not inferred.",
            "receipt": after["receipt"], "helper_sha256": helper_hash,
            "finalization_status": "not yet run; finalize_v4_recovery.py will perform truthful post-run checks",
        }
        write_json(evidence / "recovery_runtime_completed.json", result)
        print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
