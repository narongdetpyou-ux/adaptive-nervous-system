"""Run six v0.4 realism regimes through the original Neuron Flow runtime.

Validation always uses the selected, frozen controller configuration.
Development can use --config, an existing selected configuration, or the
constructor defaults for an early documented experiment. Output directories
must be new/empty; a completed job's resume behavior is checked internally.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import platform
import time

import numpy as np

from neuron_flow import Runtime, AgentDefinition
from capacity_agents import atomic_bytes, write_json, sha
from audit_capacity import audit_source_integrity
from audit_v4 import require
from v4_policy import V4Config
from v4_scenarios import (
    REGIMES, POLICIES, GROUPS, policy_seeds, scenario_manifest,
    generate_scenarios, expected_counts,
)
from v4_agents import (
    ROOT, step_name, all_rows, canonical_sha,
    validate_evaluation, validate_comparison, validate_noop,
)
from freeze_v4 import (
    execution_source_hashes, verify_predeclared_acceptance,
    verify_parent_frozen, load_selected_config, SELECTED_CONFIG,
)


def agents():
    return [
        AgentDefinition("v4_evaluate", "v4_agents:evaluate_agent", validate_evaluation, version="4"),
        AgentDefinition("v4_compare", "v4_agents:compare_agent", validate_comparison, version="4"),
        AgentDefinition("v4_noop", "v4_agents:noop_agent", validate_noop, version="4"),
    ]


def _configuration(split, supplied):
    if split == "validation":
        if supplied is not None:
            raise ValueError("Validation uses the frozen selected configuration; --config is development-only")
        from freeze_v4 import verify_frozen
        frozen = verify_frozen()
        config = V4Config(**frozen["controller_configuration"])
        return config, frozen, {
            "kind": "frozen_selected", "path": "v4_selected_config.json",
            "sha256": sha(SELECTED_CONFIG),
        }
    if supplied is not None:
        path = supplied.resolve()
        config = V4Config(**json.loads(path.read_text()))
        return config, None, {"kind": "explicit_development_file", "path": str(path), "sha256": sha(path)}
    if SELECTED_CONFIG.is_file():
        return load_selected_config(), None, {
            "kind": "selected_development", "path": "v4_selected_config.json",
            "sha256": sha(SELECTED_CONFIG),
        }
    return V4Config(), None, {"kind": "development_constructor_defaults"}


def main():
    from audit_v4 import audit_rows
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "validation"), default="development")
    parser.add_argument("--out", type=Path, required=True, help="New or empty output directory")
    parser.add_argument("--config", type=Path, help="Development-only JSON object of V4Config fields")
    parser.add_argument("--step-timeout", type=int, default=1800,
                        help="Per-worker timeout in seconds; scheduling only, not a policy parameter")
    args = parser.parse_args()
    if args.step_timeout <= 0:
        raise ValueError("Worker timeout must be positive")
    output = args.out.resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Use an empty output directory; existing experiment evidence is preserved")
    config, frozen, configuration_source = _configuration(args.split, args.config)
    verify_predeclared_acceptance()
    upstream = audit_source_integrity(ROOT)
    parent = verify_parent_frozen()
    execution_sources = execution_source_hashes()
    output.mkdir(parents=True, exist_ok=True)
    manifest = scenario_manifest(args.split)
    if frozen is not None:
        require(manifest["manifest_sha256"] == frozen["validation_manifest_sha256"],
                "Validation manifest differs from frozen protocol")
    write_json(output / "scenario_manifest.json", manifest)
    write_json(output / "configuration.json", config.as_dict())
    inputs = {
        "split": args.split,
        "out": str(output),
        "controller_configuration": config.as_dict(),
        "controller_configuration_sha256": canonical_sha(config.as_dict()),
        "configuration_source": configuration_source,
        "freeze_verification": frozen,
        "parent_v3_verification": parent,
        "upstream_sha256": upstream,
        "execution_source_sha256": execution_sources,
        "scenario_manifest_sha256": manifest["manifest_sha256"],
        "scenario_manifest_file_sha256": sha(output / "scenario_manifest.json"),
    }
    write_json(output / "run_identity.json", inputs)
    names = [step_name(regime, policy) for regime in REGIMES for policy in POLICIES]
    retry_name = step_name("legacy_instant", "v4_hybrid")
    spec = {
        "job_id": f"v4_{args.split}",
        "input": inputs,
        "steps": [
            {
                "id": step_name(regime, policy), "agent": "v4_evaluate",
                "params": {
                    "regime": regime, "policy": policy,
                    "inject_retry": step_name(regime, policy) == retry_name,
                },
                "timeout_s": args.step_timeout, "max_attempts": 2,
            }
            for regime in REGIMES for policy in POLICIES
        ] + [{
            "id": "comparison", "agent": "v4_compare", "depends_on": names,
            "timeout_s": args.step_timeout, "max_attempts": 1,
        }],
    }
    workspace = output / "workspace"
    expected_attempts = {name: 2 if name == retry_name else 1 for name in names}
    expected_attempts["comparison"] = 1
    started = time.perf_counter()
    print(json.dumps({
        "split": args.split, "scope": expected_counts(args.split), "workers": 3,
        "configuration_source": configuration_source,
    }), flush=True)
    with Runtime(workspace, agents=agents(), max_workers=3) as runtime:
        submitted = runtime.submit(spec)
        require(submitted["created"] is True, "Fresh workspace unexpectedly reused a job")
        state = runtime.run(spec["job_id"])
        if state["status"] != "SUCCEEDED":
            write_json(output / "failed_workflow.json", state)
            raise RuntimeError("v0.4 workflow failed; inspect failed_workflow.json")
        attempts = {key: state["steps"][key]["attempts"] for key in expected_attempts}
        require(attempts == expected_attempts, "Unexpected evaluation/comparison attempts")
        require(1 <= state["peak_workers"] <= 3, "Worker concurrency exceeded the configured bound")
        report = Path(state["receipt"]["path"])
        before = (sha(report), report.stat().st_mtime_ns)
        events = runtime.synapse.events_for("monitor", spec["job_id"])
    with Runtime(workspace, agents=agents(), max_workers=3) as runtime:
        duplicate = runtime.submit(spec)
        require(duplicate["created"] is False, "Duplicate submission created a new job")
        reused = runtime.run(spec["job_id"])
        require(reused["status"] == "SUCCEEDED", "Completed workflow failed to resume")
        require({key: reused["steps"][key]["attempts"] for key in attempts} == attempts,
                "Restart reran a completed step")
        require((sha(report), report.stat().st_mtime_ns) == before, "Restart altered the report")
        negative = {
            "job_id": f"v4_negative_{args.split}", "input": inputs,
            "steps": [
                {
                    "id": "bad", "agent": "v4_evaluate",
                    "params": {"regime": "legacy_instant", "policy": "static", "reuse_original": True},
                    "timeout_s": args.step_timeout, "max_attempts": 1,
                },
                {"id": "blocked", "agent": "v4_noop", "depends_on": ["bad"]},
                {"id": "independent", "agent": "v4_noop"},
            ],
        }
        runtime.submit(negative)
        neg = runtime.run(negative["job_id"])
        neg_status = {name: step["status"] for name, step in neg["steps"].items()}
        require(neg["status"] == "FAILED" and neg_status == {
            "bad": "FAILED", "blocked": "BLOCKED", "independent": "SUCCEEDED",
        }, "Negative-control rejection/isolation failed")
        require(neg["steps"]["bad"]["attempts"] == 1, "Invalid result was unexpectedly retried")
    dependencies = {name: state["steps"][name]["output"] for name in names}
    rows = all_rows(output, dependencies)
    cases = generate_scenarios(args.split)
    row_audit = audit_rows(rows, cases, {p: policy_seeds(p) for p in POLICIES}, regimes=REGIMES)
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic_bytes(output / "episodes.csv", buffer.getvalue().encode("utf-8"))
    trace_index = {name: result["traces"] for name, result in dependencies.items()}
    write_json(output / "representative_traces_index.json", trace_index)
    write_json(output / "workflow_events.json", events)
    comparison = state["steps"]["comparison"]["output"]
    require(sha(output / "summary.json") == comparison["summary"]["sha256"], "Final summary changed")
    require(audit_source_integrity(ROOT) == upstream, "Original sources changed during the experiment")
    require(execution_source_hashes() == execution_sources, "Execution sources changed during the experiment")
    require(verify_parent_frozen() == parent, "Parent freeze changed during the experiment")
    require(sha(output / "scenario_manifest.json") == inputs["scenario_manifest_file_sha256"],
            "Scenario manifest changed during the experiment")
    if args.split == "validation":
        from freeze_v4 import verify_frozen
        require(verify_frozen() == frozen, "Frozen implementation changed during validation")
    trace_count = sum(info["count"] for info in trace_index.values())
    trace_minutes = sum(info["minutes"] for info in trace_index.values())
    require(trace_count == len(GROUPS) * len(POLICIES) * len(REGIMES) == 462,
            "Unexpected representative coverage")
    require(trace_minutes == 51576, "Unexpected representative replay minutes")
    verification = {
        "version": "0.4.0", "split": args.split, "status": state["status"],
        "steps": attempts, "peak_workers": state["peak_workers"],
        "receipt_path": str(report.relative_to(output)), "receipt_sha256": before[0],
        "receipt_bytes": report.stat().st_size, "summary_sha256": comparison["summary"]["sha256"],
        "artifact_exchange": "descriptors only; complete rows/traces/summary checksum-verified from atomic local files",
        "duplicate_submission_created": duplicate["created"],
        "restart_replay_preserved_attempts": True,
        "restart_replay_preserved_report": True,
        "negative_control": {
            "status": neg["status"], "steps": neg_status, "bad_error": neg["steps"]["bad"]["error"],
            "scope": "completed count increased by one in a separate static legacy_instant row copy; no additional rollout",
        },
        "row_audit": row_audit,
        "independent_trace_replay": {
            "traces": trace_count, "minutes": trace_minutes,
            "selection": "first scenario seed in each family, every deterministic policy and regime; before outcomes",
            "scope": "independent scalar resource, buffer, pipeline, startup and work replay; all episodes have inline/row invariants",
        },
        "observation_only_controller_replay": {
            "traces": trace_count, "decisions": trace_minutes, "actions_and_evidence_match": True,
            "scope": "only recorded measured inputs, historical delivered feedback and exact committed allocations",
        },
        "source_and_sensor_boundary": {
            "passed": True, "traces": trace_count, "minutes": trace_minutes,
            "checks": "source timestamps, coldstart, common source-indexed factors, exact staff ledger, historical effective denominator",
        },
        "independent_statistics_check": (
            "Every overall/source/family point mean, paired difference/win/tie/loss, mean-NI point slack, "
            "static-relative harm count/frequency/mean/maximum/CVaR90 and risk difference; "
            "bootstrap intervals use the separately tested frozen statistical module."
        ),
        "expected_scope": expected_counts(args.split),
        "upstream_sha256": upstream, "parent_v3_verification": parent,
        "execution_source_sha256": execution_sources, "freeze_verification": frozen,
        "scenario_manifest_sha256": manifest["manifest_sha256"],
        "scenario_manifest_file_sha256": inputs["scenario_manifest_file_sha256"],
        "controller_configuration": config.as_dict(),
        "controller_configuration_sha256": inputs["controller_configuration_sha256"],
        "configuration_source": configuration_source,
        "python": platform.python_version(), "numpy": np.__version__,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "timing_scope": "local experiment wall time, not a runtime performance benchmark",
        "inference_scope": "Six regimes remain separate; successful workflow is not an advancement decision.",
    }
    write_json(output / "verification.json", verification)
    print(json.dumps({
        "status": state["status"], "split": args.split, **comparison["overview"],
        "independent_trace_replay": verification["independent_trace_replay"],
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
