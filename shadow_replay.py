"""Read-only replay of recorded observations through the v0.3 policy.

Recommendations are never applied. Allocation, transport and dwell state follow
only recorded ACTUAL actions. No environment productivity or future data is
accepted as a policy input, and no external service is contacted.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import numpy as np

from abc_service.frozen import catalog
from v3_policy import V3Config, V3Policy

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = "ans-shadow-observations/0.3.0"
TOL = 1e-6
NO_PREVIOUS_MOVE = -10**9
RECORD_FIELDS = {
    "t", "arrival", "observed_queue", "staff_before", "active_staff",
    "pending_staff", "committed_staff", "flow", "end_queue",
    "actual_delta", "known_transit_delay",
}
TOP_FIELDS = {
    "schema_version", "source_kind", "description", "units",
    "model_assumptions", "initial_state", "records",
}
ASSUMPTIONS = {
    "topology": "three_stage_serial_fluid",
    "budget": 207,
    "same_minute_forwarding": True,
    "action_catalog": "ABC_Causal_Service_0.2.0",
    "warmup_minutes": 5,
    "dwell_minutes": 8,
    "minimum_stage_reserve": 10,
    "nominal_productivity_prior": 200,
    "no_pending_resources_at_start": True,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def number(value, name, nonnegative=True):
    require(type(value) in (int, float) and np.isfinite(value), f"{name}: finite number required")
    if nonnegative:
        require(value >= 0, f"{name}: must be nonnegative")
    return float(value)


def vector(value, name, nonnegative=True):
    require(type(value) is list and len(value) == 3, f"{name}: three-element list required")
    return np.array([number(x, name, nonnegative) for x in value], dtype=float)


def close(actual, expected, message):
    require(np.allclose(actual, expected, rtol=1e-10, atol=TOL), message)


def validate_observations(document):
    """Validate this model's observation contract without any latent rate."""
    require(type(document) is dict and set(document) == TOP_FIELDS, "Unexpected/missing document fields")
    require(document["schema_version"] == SCHEMA_VERSION, "Unsupported schema version")
    require(document["source_kind"] in ("synthetic", "recorded"), "Label source_kind explicitly")
    require(type(document["description"]) is str, "description must be text")
    require(document["model_assumptions"] == ASSUMPTIONS, "Unsupported model assumptions")
    units = document["units"]
    require(type(units) is dict and set(units) == {"time", "work", "resource", "work_unit_definition"}, "Invalid unit declaration")
    require(units["time"] == "minute" and units["work"] == "work_unit" and units["resource"] == "resource_unit",
            "This adapter supports minute/work_unit/resource_unit only")
    require(type(units["work_unit_definition"]) is str and bool(units["work_unit_definition"].strip()),
            "Describe how the work unit was mapped")
    initial = document["initial_state"]
    require(type(initial) is dict and set(initial) == {"committed_staff", "end_queue", "last_actual_change"},
            "Invalid initial state")
    committed = vector(initial["committed_staff"], "initial committed staff")
    require(abs(float(committed.sum())-207) <= TOL and min(committed) >= 10-TOL,
            "Initial committed budget/reserve violation")
    previous_queue = vector(initial["end_queue"], "initial queue")
    last_change = initial["last_actual_change"]
    require(type(last_change) is int and last_change < 0, "Initial last_actual_change must precede t=0")
    records = document["records"]
    require(type(records) is list and len(records) > 0, "At least one observation required")
    require(type(records[0]) is dict and "known_transit_delay" in records[0], "Missing delay")
    delay = records[0]["known_transit_delay"]
    require(type(delay) is int and delay in (0, 1, 3), "Known transport delay must be 0, 1 or 3")
    require(last_change + delay <= 0, "Initial state claims no pending resources before a previous transfer can have arrived")
    active = committed.copy()
    pending = np.zeros(3)
    ready_at = None
    moves = 0
    total_arrivals = 0.0
    total_completed = 0.0
    actions = [np.asarray(item["delta"], dtype=float) for item in catalog()["actions"]]
    for expected_t, record in enumerate(records):
        require(type(record) is dict and set(record) == RECORD_FIELDS,
                f"t={expected_t}: unexpected/missing observation fields; latent productivity/evidence are not accepted")
        t = record["t"]
        require(type(t) is int and t == expected_t, "Records must start at zero and be consecutive")
        require(type(record["known_transit_delay"]) is int and record["known_transit_delay"] == delay,
                "Transport delay cannot change within a replay")
        if ready_at is not None and t >= ready_at:
            active += pending
            pending[:] = 0
            ready_at = None
        arrival = number(record["arrival"], "arrival")
        before = vector(record["staff_before"], "staff_before")
        close(before, committed, f"t={t}: staff_before does not follow recorded actions")
        observed = vector(record["observed_queue"], "observed_queue")
        expected_queue = previous_queue.copy()
        expected_queue[0] += arrival
        close(observed, expected_queue, f"t={t}: arrival/queue continuity mismatch")
        delta = vector(record["actual_delta"], "actual_delta", nonnegative=False)
        require(abs(float(delta.sum())) <= TOL, f"t={t}: actual transfer creates resources")
        if np.any(np.abs(delta) > TOL):
            require(any(np.allclose(delta, a, rtol=0, atol=TOL) for a in actions), f"t={t}: actual transfer is outside the declared catalog")
            require(t >= 5 and t-last_change >= 8, f"t={t}: actual warmup/dwell violation")
            require(ready_at is None and not np.any(pending), f"t={t}: overlapping actual transfers")
            committed += delta
            require(min(committed) >= 10-TOL, f"t={t}: actual transfer violates reserve")
            if delay == 0:
                active += delta
            else:
                active += np.minimum(delta, 0)
                pending = np.maximum(delta, 0)
                ready_at = t+delay
            last_change = t
            moves += 1
        close(vector(record["committed_staff"], "committed_staff"), committed,
              f"t={t}: committed allocation mismatch")
        close(vector(record["active_staff"], "active_staff"), active, f"t={t}: active resources mismatch")
        close(vector(record["pending_staff"], "pending_staff"), pending, f"t={t}: transit ledger mismatch")
        close(active+pending, committed, f"t={t}: resource accounting mismatch")
        require(abs(float(active.sum()+pending.sum())-207) <= TOL, f"t={t}: budget violation")
        flow = vector(record["flow"], "flow")
        require(not np.any((active <= TOL) & (flow > TOL)), f"t={t}: positive flow without active resources")
        q = observed.copy()
        for stage in range(3):
            require(flow[stage] <= q[stage]+TOL, f"t={t}: flow exceeds available work")
            q[stage] -= flow[stage]
            if stage < 2:
                q[stage+1] += flow[stage]
        previous_queue = vector(record["end_queue"], "end_queue")
        close(previous_queue, q, f"t={t}: flow/queue conservation mismatch")
        total_arrivals += arrival
        total_completed += flow[2]
    return {
        "records": len(records), "recorded_moves": moves, "known_transit_delay": delay,
        "total_recorded_arrivals": total_arrivals, "total_recorded_completions": total_completed,
        "last_actual_change": last_change,
        "scope": "observed resource ledger and serial-flow consistency; service capacity cannot be verified without independent telemetry",
    }


def default_config_binding(require_frozen=True):
    """Use the default controller only when it matches the packaged freeze."""
    config = V3Config()
    protocol_path = ROOT / "v3_protocol.json"
    hash_path = ROOT / "v3_protocol.sha256"
    if not protocol_path.exists() and not hash_path.exists():
        require(not require_frozen, "Frozen v3 protocol is required; use the completed v0.3 package, not a development checkout")
        return config, {"status": "development_default_for_self_test_only"}
    require(protocol_path.is_file() and hash_path.is_file(), "Incomplete v3 protocol freeze")
    protocol_hash = sha(protocol_path)
    require(protocol_hash == hash_path.read_text().split()[0], "Frozen protocol hash mismatch")
    protocol = json.loads(protocol_path.read_text())
    require(protocol["controller_configuration"] == config.as_dict(), "Default controller differs from frozen configuration")
    for filename, expected in protocol["implementation_sha256"].items():
        path = (ROOT / filename).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), "Invalid frozen source path")
        require(sha(path) == expected, f"Frozen source mismatch: {filename}")
    parent = protocol["parent_v2_verification"]
    require(sha(ROOT/"capacity_protocol.json") == parent["protocol_sha256"], "Parent protocol mismatch")
    for hashes_name in ("implementation_sha256", "upstream_sha256"):
        for filename, expected in parent.get(hashes_name, {}).items():
            path = (ROOT / filename).resolve()
            require(path.is_relative_to(ROOT) and path.is_file(), "Invalid parent source path")
            require(sha(path) == expected, f"Frozen parent source mismatch: {filename}")
    return config, {"status": "default_matches_frozen_configuration", "protocol_sha256": protocol_hash}


def replay(document, *, require_frozen=True):
    """Recommend from history, observe the actual record, never apply a proposal."""
    original_digest = hashlib.sha256(canonical(document)).hexdigest()
    validation = validate_observations(document)
    config, binding = default_config_binding(require_frozen)
    policy = V3Policy("full", transit_delay=validation["known_transit_delay"], config=config)
    policy.last_change = document["initial_state"]["last_actual_change"]
    history = []
    results = []
    for record in document["records"]:
        t = record["t"]
        actual_last_change_before = policy.last_change
        # decide advances the observation clock and can speculatively update
        # last_change. Preserve the observation clock but ALWAYS discard that
        # hypothetical action clock before consuming the actual observation.
        try:
            recommendation, evidence = policy.decide(
                t, np.array(record["observed_queue"], dtype=float),
                [x.copy() for x in history], np.array(record["staff_before"], dtype=float),
                float(record["arrival"]),
            )
        finally:
            policy.last_change = actual_last_change_before
        actual_delta = np.array(record["actual_delta"], dtype=float)
        actual_moved = bool(np.any(np.abs(actual_delta) > TOL))
        if actual_moved:
            policy.last_change = t
        policy.observe(t, np.array(record["active_staff"], dtype=float),
                       np.array(record["flow"], dtype=float), np.array(record["end_queue"], dtype=float))
        history = (history+[np.array(record["end_queue"], dtype=float)])[-5:]
        results.append({
            "t": t,
            "recommended_delta": recommendation.tolist(),
            "reason": evidence["branch"],
            "recorded_actual_delta": actual_delta.tolist(),
            "recommendation_applied_by_adapter": False,
            "recommendation_matches_recorded_action": bool(np.allclose(recommendation, actual_delta, rtol=0, atol=TOL)),
            "last_actual_change_before": actual_last_change_before,
            "last_actual_change_after": policy.last_change,
            "model_evidence": {key:value for key,value in evidence.items()
                               if key not in ("last_change_before", "last_change_after")},
        })
    require(hashlib.sha256(canonical(document)).hexdigest() == original_digest, "Input observations were mutated")
    return {
        "schema_version": "ans-shadow-recommendations/0.3.0",
        "source_kind": document["source_kind"],
        "mode": "read_only_shadow_replay",
        "input_canonical_sha256": original_digest,
        "configuration_binding": binding,
        "validation": validation,
        "recommendations": results,
        "recommended_move_count": sum(any(abs(x)>TOL for x in row["recommended_delta"]) for row in results),
        "recorded_actual_move_count": validation["recorded_moves"],
        "scope": "Recommendations on the recorded trajectory only. They were not applied; no counterfactual queue, throughput, cost saving or benefit is measured.",
    }


def self_test():
    """Check the ignored-recommendation clock and an independent actual move."""
    example = json.loads((ROOT / "synthetic_example.json").read_text(encoding="utf-8"))
    before = copy.deepcopy(example)
    result = replay(example, require_frozen=False)
    require(example == before, "Self-test: input mutated")
    require(result["recommended_move_count"] > 0, "Self-test fixture must exercise an unapplied recommendation")
    require(result["recorded_actual_move_count"] == 0, "Self-test fixture must be a recorded static trajectory")
    require(all(row["last_actual_change_after"] == example["initial_state"]["last_actual_change"]
                for row in result["recommendations"]), "Unapplied recommendation advanced the dwell timer")
    selected = [row["t"] for row in result["recommendations"] if any(abs(x)>TOL for x in row["recommended_delta"])]
    require(any(b-a < 8 for a,b in zip(selected, selected[1:])), "Self-test did not exercise repeated shadow proposals within dwell")
    fixture = copy.deepcopy(example)
    fixture["description"] = "Synthetic self-test of recorded actual transfer; no environment-rate inputs"
    fixture["records"] = []
    delay = 1
    committed = np.full(3,69.)
    active = committed.copy()
    pending = np.zeros(3)
    chosen = np.asarray(catalog()["actions"][0]["delta"], dtype=float)
    require(min(committed+chosen) >= 10, "Self-test action reserve")
    for t in range(16):
        if t == 6:
            active += pending
            pending[:] = 0
        staff_before = committed.copy()
        delta = chosen.copy() if t == 5 else np.zeros(3)
        if t == 5:
            committed += delta
            active += np.minimum(delta,0)
            pending = np.maximum(delta,0)
        fixture["records"].append({
            "t": t, "arrival":500., "observed_queue":[500.,0.,0.],
            "staff_before":staff_before.tolist(), "active_staff":active.tolist(),
            "pending_staff":pending.tolist(), "committed_staff":committed.tolist(),
            "flow":[500.,500.,500.], "end_queue":[0.,0.,0.],
            "actual_delta":delta.tolist(), "known_transit_delay":delay,
        })
    actual = replay(fixture, require_frozen=False)
    require(actual["recorded_actual_move_count"] == 1, "Recorded transfer was not counted")
    require(actual["recommendations"][5]["last_actual_change_after"] == 5, "Actual transfer failed to advance timer")
    require(all(row["reason"] == "locked" for row in actual["recommendations"][6:13]), "Actual dwell lock not respected")
    require(actual["recommendations"][13]["reason"] != "locked", "Actual dwell lock did not expire")
    return {
        "status":"PASS", "input_unchanged":True, "recommendations_never_applied":True,
        "unapplied_recommendations_did_not_advance_clock":True,
        "actual_transfer_advanced_clock_and_locked_until_minute_13":True,
        "synthetic_example_recommendations":result["recommended_move_count"],
        "external_connections":0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--input", type=Path, help="Local observation JSON")
    choice.add_argument("--self-test", action="store_true")
    parser.add_argument("--out", type=Path, help="New output JSON; input is never overwritten")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        require(not args.out and not args.validate_only, "Self-test does not write an output file")
        print(json.dumps(self_test(), indent=2))
        return
    input_bytes = args.input.read_bytes()
    input_file_digest = hashlib.sha256(input_bytes).hexdigest()
    document = json.loads(input_bytes)
    if args.validate_only:
        require(not args.out, "Validation-only mode does not write an output")
        print(json.dumps(validate_observations(document), indent=2))
        return
    require(args.out is not None, "Replay needs --out for a new recommendation file")
    require(args.out.resolve() != args.input.resolve(), "Output must differ from the input")
    require(not args.out.exists(), "Use a new output filename; existing evidence is preserved")
    result = replay(document)
    require(sha(args.input) == input_file_digest, "Input file changed during replay; use a stable recorded snapshot")
    result["input_file_sha256"] = input_file_digest
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"output":str(args.out), "records":len(result["recommendations"]),
                      "recommended_moves":result["recommended_move_count"],
                      "recorded_moves":result["recorded_actual_move_count"],
                      "recommendations_applied":False}, indent=2))


if __name__ == "__main__":
    main()
