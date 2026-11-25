"""ABC-Causal only: 207 conserved resource units, incremental moves, dwell 8.

The queue/trend score, threshold, warmup, hidden stochastic process and saved
target events are preserved. The requested probabilistic transfer selector is
an explicit controller change; no other controller, retrieval or fit is run.
"""
from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import itertools
import json
import platform
from pathlib import Path
import time
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parent
BUDGET = 207.0
DWELL = 8
WARMUP = 5
THRESHOLD = 10.0
TEMPERATURE = 0.25
POLICY_SEEDS = [11, 29, 47]
RAW = [
    ("+5/-2.5/-2.5", [5., -2.5, -2.5]),
    ("+6/-3/-3", [6., -3., -3.]),
    ("+7/-3/-4", [7., -3., -4.]),
    ("+8/-2/-6", [8., -2., -6.]),
    ("+9/-5/-4", [9., -5., -4.]),
    ("+5/-2.5/-3", [5., -2.5, -3.]),
    ("+6/-4/-2", [6., -4., -2.]),
    ("+7/-7", [7., -7., 0.]),
    ("+8/-1/-7", [8., -1., -7.]),
    ("+9/-5/-4", [9., -5., -4.]),
    ("+10/-5/-5", [10., -5., -5.]),
    ("+20/-5/-15", [20., -5., -15.]),
    ("+30/-30", [30., -30., 0.]),
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def original_class_text():
    src = (ROOT / "original/ABC.H.txt").read_text(encoding="utf-8")
    nodes = [n for n in ast.parse(src).body if isinstance(n, ast.ClassDef)
             and n.name == "ABC_CausalAgent"]
    if len(nodes) != 1:
        raise ValueError("Exactly one original ABC_CausalAgent required")
    return ast.get_source_segment(src, nodes[0])


def original_class():
    scope = {"np": np}
    exec(compile(original_class_text(), "original/ABC.H.txt:ABC_CausalAgent", "exec"), scope)
    return scope["ABC_CausalAgent"]


def build_catalog():
    rows, seen, actions = [], {}, []
    for index, (label, vector) in enumerate(RAW, start=1):
        key = tuple(vector)
        total = float(sum(vector))
        duplicate = seen.get(key)
        status = "rejected_non_zero_sum" if abs(total) > 1e-12 else (
            "duplicate" if duplicate is not None else "accepted")
        rows.append({"input_index": index, "user_text": label, "vector": vector,
                     "sum": total, "status": status, "duplicate_of": duplicate})
        if duplicate is None:
            seen[key] = index
        if status == "accepted":
            # Map each signed template onto public stage indices. Identical
            # donor amounts and duplicate user rows never multiply probability.
            for v in sorted(set(itertools.permutations(vector))):
                actions.append({"action_id": len(actions), "input_index": index,
                                "label": label, "delta": list(v),
                                "recipient": int(np.argmax(v))})
    return {"raw_entries": rows, "actions": actions,
            "invalid_entry_handling": "Retain raw row 6 as a catalog design error; do not repair it or let the conserved-budget selector apply it.",
            "duplicate_handling": "Row 10 repeats row 5 and contributes no extra probability.",
            "two_stage_handling": "Unmentioned third stage receives zero."}


def freeze():
    path = ROOT / "protocol.json"
    if path.exists():
        p = json.loads(path.read_text()); check_frozen(p); return p
    parent = json.loads((ROOT / "provenance/parent_protocol.json").read_text())
    catalog = build_catalog()
    dump(ROOT / "action_catalog.json", catalog)
    p = {
        "schema": "abc-causal-207-probabilistic-incremental-1",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data_kind": "synthetic saved benchmark; no live or real-world deployment",
        "methods": ["ABC-Causal"], "resource_budget": BUDGET,
        "base_staff_role_order": (np.array([20., 15., 18.]) * BUDGET / 53).tolist(),
        "resource_scale": BUDGET / 53, "resource_semantics": "incremental transfer from current staff; never reset to base outside a listed transfer",
        "minimum_dwell_after_transfer": DWELL, "warmup_minutes": WARMUP,
        "causal_score": "current noisy queues + 2 * (last noisy end queue - first of last 5 noisy end queues)",
        "trigger_threshold": THRESHOLD,
        "recipient": "original argmax causal score; public-index first wins exact ties",
        "feasibility": "catalog sum zero, same argmax recipient, current staff + delta nonnegative; no clipping or projection",
        "shortlist": "At most 3 distinct feasible mapped moves with highest dot(delta, causal_score); action_id breaks exact ties.",
        "probability_rule": "Softmax of dot(delta,score)/(30*max(1,max(abs(score)))) divided by fixed temperature 0.25; sample exactly once using pre-recorded uniform at this minute.",
        "softmax_temperature": TEMPERATURE,
        "probability_meaning": "selection probability, not estimated probability that an action is optimal",
        "no_signal": "Keep current allocation if maximum score <= 10; no implicit return-to-base transfer.",
        "no_legal_move": "Keep current staff if no listed feasible transfer exists; record this branch explicitly.",
        "history_vs_memory": "Only last 5 in-episode noisy queues; no source experience, structural memory, semantic retrieval, learned weights or fitting.",
        "horizon_minutes": parent["horizon_minutes"],
        "environment_cases_per_graph": parent["target_episodes_per_graph"],
        "graphs": {"matched": "3-stage serial path", "mismatched": "entry splits equally into two independent terminal stages"},
        "policy_seeds": POLICY_SEEDS,
        "policy_randomness": "SeedSequence([policy_seed, environment_seed low32, environment_seed high32]); all 360 uniforms generated before episode; same stream for path/fork.",
        "target_master_seed": parent["target_master_seed"],
        "noise_settings": parent["noise_settings"],
        "generator_dynamics": parent["generator_dynamics"],
        "flow_rule": "Preserve upstream-first fluid service; a job may cross multiple stages in one minute; fork splits rather than duplicates work.",
        "production_rule": "current staff * original saved hidden per-unit productivity; outages stay zero even after resources move",
        "preserved_inputs": "All arrivals, hidden productivity, sensor noise, node permutations, routing and horizon exactly reused; only staff total changes.",
        "policy_inputs": "minute, noisy current queue, last 5 noisy end queues, own known current staff/lock; no hidden productivity, outage labels, future events or results",
        "primary_metric": "Mean whole-episode true end-of-minute total unfinished jobs, lower is better",
        "secondary_metrics": ["final unfinished jobs", "peak unfinished jobs", "completed jobs", "transfer count", "fraction selecting rank 2 or 3", "resource utilization"],
        "aggregation": "Average 3 policy seeds per environment case first; then equal-weight average of 300 cases per graph. Report every mode/noise cell and all seeds.",
        "uncertainty": "95% stratified bootstrap means over the 300 environment cases: 50 draws within each of 6 cells, 10000 replicates; preserves path/fork pairing; no pseudo-replication of policy seeds.",
        "failure_rule": "First runtime exception terminates the affected rollout, keeps traceback and partial trace; no repair, replacement controller, target retry or silent exclusion.",
        "tuning": "No parameters changed after evaluation; previously studied benchmark is reused at user request, not newly analyst-unseen data.",
        "interpretations": ["Unanswered optional clarification: transfers interpreted as increments from current allocation.", "Input row 6 sums -0.5 and is retained but ineligible under requested fixed budget207.", "Input row10 duplicates row5; deduplicated without extra probability.", "A maximum of three alternatives, one sampled move; not three simultaneous moves.", "Probabilistic selector and incremental state replace old fixed absolute-offset chooser; queue score, warmup and threshold retained."],
        "parent_bundle": {"filename": "ABC_dwell5_shift5_2026-10-02.zip", "library_file_id": "libfile_ae02bbf75378819186ead669ddc93431", "sha256": "5baad106b8585254d31375eba7ad8ee10d0bf08516d6f75f8b289a7684abef3e"},
        "abc_source": {"filename": "ABC.H.txt", "library_file_id": "libfile_117f840ef7808191bc4e1907298ea57a"},
        "code_sha256": digest(__file__), "generator_sha256": digest(ROOT / "original_stress.py"),
        "abc_file_sha256": digest(ROOT / "original/ABC.H.txt"),
        "abc_class_sha256": hashlib.sha256(original_class_text().encode()).hexdigest(),
        "catalog_sha256": digest(ROOT / "action_catalog.json"), "design_sha256": digest(ROOT / "target_design.json"),
        "environment_hashes": {f.name: digest(f) for f in sorted((ROOT / "target_environments").glob("*.npz"))},
    }
    assert p["environment_hashes"] == parent["target_environment_hashes"]
    assert len(p["environment_hashes"]) == 600
    assert p["abc_file_sha256"] == parent["abc_file_sha256"]
    assert p["abc_class_sha256"] == parent["abc_class_sha256"]
    assert p["generator_sha256"] == parent["generator_sha256"]
    dump(path, p)
    return p


def check_frozen(p):
    for key, path in [("code_sha256", __file__), ("catalog_sha256", ROOT / "action_catalog.json"),
                      ("design_sha256", ROOT / "target_design.json"),
                      ("abc_file_sha256", ROOT / "original/ABC.H.txt"),
                      ("generator_sha256", ROOT / "original_stress.py")]:
        if digest(path) != p[key]:
            raise ValueError("Frozen file changed: " + key)
    for name, expected in p["environment_hashes"].items():
        if digest(ROOT / "target_environments" / name) != expected:
            raise ValueError("Frozen environment changed: " + name)


class ABC_Causal207:
    def __init__(self, catalog, base_staff, uniforms):
        self.core = original_class()(dwell_time=DWELL)
        self.core.current_alloc = np.zeros(3, dtype=float)
        self.base_staff = base_staff.copy()
        self.uniforms = uniforms
        self.actions = catalog["actions"]
        self.moves = np.asarray([a["delta"] for a in self.actions])
        self.recipients = np.asarray([a["recipient"] for a in self.actions])

    def decide(self, t, queues, history, staff):
        note = {"t": int(t), "last_change_before": int(self.core.last_change),
                "staff_before": staff.tolist(), "observed": queues.tolist(),
                "score": None, "recipient": None, "shortlist": [],
                "selected_action_id": None, "selected_rank": None, "uniform": None}
        if len(history) < WARMUP:
            note["branch"] = "warmup"
        elif t - self.core.last_change < self.core.dwell_time:
            note["branch"] = "locked"
        else:
            recent = np.array(history[-5:])
            dQ_dt = recent[-1] - recent[0]
            score = queues + 2.0 * dQ_dt
            if not np.isfinite(score).all():
                raise FloatingPointError("Invalid causal score")
            target = int(np.argmax(score))
            note.update(score=score.tolist(), recipient=target)
            if score[target] <= THRESHOLD:
                note["branch"] = "no_signal"
            else:
                relevant = self.recipients == target
                nonnegative = (staff + self.moves >= 0).all(axis=1)
                feasible = np.flatnonzero(relevant & nonnegative)
                note.update(recipient_options=int(relevant.sum()),
                            rejected_insufficient_donors=int((relevant & ~nonnegative).sum()),
                            feasible_options=int(len(feasible)))
                if not len(feasible):
                    note["branch"] = "no_legal_move"
                else:
                    utilities = self.moves[feasible] @ score
                    ranked = sorted(range(len(feasible)), key=lambda j: (-float(utilities[j]), int(feasible[j])))[:3]
                    ids = feasible[ranked]
                    scale = 30.0 * max(1.0, float(np.abs(score).max()))
                    logits = utilities[ranked] / scale / TEMPERATURE
                    weights = np.exp(logits - logits.max())
                    probs = weights / weights.sum()
                    u = float(self.uniforms[t])
                    chosen_rank = int(np.searchsorted(np.cumsum(probs), u, side="right"))
                    # Invalid floating-point/categorical outputs are errors,
                    # never clipped, projected, resampled or substituted.
                    if chosen_rank >= len(ids):
                        raise FloatingPointError("Sampled rank outside shortlist")
                    chosen_id = int(ids[chosen_rank])
                    delta = self.moves[chosen_id].copy()
                    if abs(float(delta.sum())) > 1e-12 or (staff + delta < 0).any():
                        raise ValueError("Selected infeasible transfer")
                    self.core.current_alloc = staff + delta - self.base_staff
                    self.core.last_change = t
                    note.update(branch="selected", selected_action_id=chosen_id,
                                selected_rank=chosen_rank + 1, uniform=u,
                                utility_scale=scale,
                                shortlist=[{"action_id": int(i), "delta": self.moves[i].tolist(),
                                            "utility": float(v), "probability": float(pr)}
                                           for i, v, pr in zip(ids, utilities[ranked], probs)])
                    note.update(delta=delta.tolist(), last_change_after=int(self.core.last_change))
                    return delta, note
        note.update(delta=[0., 0., 0.], last_change_after=int(self.core.last_change))
        return np.zeros(3), note


def load_environment(condition, episode):
    with np.load(ROOT / "target_environments" / f"environment_{condition}_{episode:03d}.npz") as data:
        return {k: data[k].copy() for k in data.files}


def run_episode(design, condition, policy_seed, p, catalog, log, errors):
    env = load_environment(condition, design["episode"])
    base = env["staff"] * BUDGET / 53
    staff = base.copy()
    seed = int(design["seed"])
    rng = np.random.default_rng(np.random.SeedSequence([policy_seed, seed & 0xffffffff, seed >> 32]))
    uniforms = rng.random(p["horizon_minutes"])
    agent = ABC_Causal207(catalog, base, uniforms)
    queue, history = np.zeros(3), []
    completed, arrived, transfers, rank_nonfirst = 0., 0., 0, 0
    branch_counts = {}
    keys = ["queue", "observed_current", "observed_end", "staff", "delta", "processed",
            "capacity", "completed", "mass_error", "resource_error", "action_id", "rank"]
    fields = {k: [] for k in keys}
    started = time.perf_counter()
    status, failure = "complete", None
    for t in range(p["horizon_minutes"]):
        note = None
        try:
            queue[int(env["order"][0])] += env["arrivals"][t]
            observed = np.maximum(0, queue + env["sensor_noise"][t, 0])
            delta, note = agent.decide(t, observed.copy(), history, staff.copy())
            if delta.shape != (3,) or not np.isfinite(delta).all():
                raise ValueError("Invalid transfer shape or value")
            staff += delta
            resource_error = float(staff.sum() - BUDGET)
            if abs(resource_error) > 1e-9 or (staff < 0).any():
                raise ValueError("Conserved-resource invariant failed")
            if np.any(delta):
                transfers += 1
                rank_nonfirst += int(note["selected_rank"] != 1)
            branch_counts[note["branch"]] = branch_counts.get(note["branch"], 0) + 1
            processed = np.zeros(3)
            capacity = staff * env["productivity"][t]
            for i in env["order"]:
                amount = min(queue[i], capacity[i]); queue[i] -= amount; processed[i] = amount
                routing = env["routing"][i]
                if routing.sum():
                    queue += amount * routing
                else:
                    completed += amount
            arrived += env["arrivals"][t]
            mass_error = float(arrived - completed - queue.sum())
            if abs(mass_error) > 1e-7 or (queue < -1e-9).any():
                raise ValueError("Work-conservation invariant failed")
            end_observed = np.maximum(0, queue + env["sensor_noise"][t, 1])
            history.append(end_observed.copy())
            if len(history) > 5:
                history.pop(0)
            note.update(episode=design["episode"], condition=condition, policy_seed=policy_seed,
                        staff_after=staff.tolist(), status="applied")
            log.write(json.dumps(note, separators=(",", ":")) + "\n")
            values = {"queue": queue.copy(), "observed_current": observed, "observed_end": end_observed,
                      "staff": staff.copy(), "delta": delta, "processed": processed, "capacity": capacity,
                      "completed": completed, "mass_error": mass_error, "resource_error": resource_error,
                      "action_id": -1 if note["selected_action_id"] is None else note["selected_action_id"],
                      "rank": 0 if note["selected_rank"] is None else note["selected_rank"]}
            for k, v in values.items():
                fields[k].append(v)
        except Exception as exc:
            status = "failed"
            failure = {"episode": design["episode"], "condition": condition, "policy_seed": policy_seed,
                       "t": t, "type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc(),
                       "decision": note}
            errors.append(failure)
            log.write(json.dumps({**failure, "status": "failed"}, separators=(",", ":")) + "\n")
            break
    trace = {k: np.asarray(v) for k, v in fields.items()}
    trace["policy_uniforms"] = uniforms
    n = len(trace["completed"])
    backlog = trace["queue"].sum(axis=1) if n else np.array([])
    row = {**{k: v for k, v in design.items() if k != "permutation"}, "condition": condition,
           "method": "ABC-Causal", "policy_seed": policy_seed, "status": status, "steps_completed": n,
           "mean_backlog": float(backlog.mean()) if n else None,
           "peak_backlog": float(backlog.max()) if n else None,
           "final_backlog": float(queue.sum()), "completed_jobs": float(completed),
           "arrivals_total": float(env["arrivals"].sum()), "transfers": transfers,
           "nonfirst_selections": rank_nonfirst,
           "utilization": float(trace["processed"].sum() / trace["capacity"].sum()) if n else None,
           "max_resource_error": float(np.abs(trace["resource_error"]).max()) if n else None,
           "max_mass_error": float(np.abs(trace["mass_error"]).max()) if n else None,
           "minimum_stage_staff": float(trace["staff"].min()) if n else None,
           "no_legal_move_minutes": branch_counts.get("no_legal_move", 0),
           "no_signal_minutes": branch_counts.get("no_signal", 0),
           "elapsed_seconds": time.perf_counter() - started}
    return row, trace


def run_suite():
    p = freeze(); check_frozen(p)
    output = ROOT / "results"
    if (output / "rollouts.csv").exists() or (output / "decisions.jsonl.gz").exists():
        raise FileExistsError("Results already exist; no target rerun or overwrite")
    (output / "traces").mkdir(parents=True, exist_ok=True)
    catalog = json.loads((ROOT / "action_catalog.json").read_text())
    design = json.loads((ROOT / "target_design.json").read_text())
    rows, errors = [], []
    started = time.perf_counter()
    with gzip.open(output / "decisions.jsonl.gz", "wt", encoding="utf-8", compresslevel=3) as log:
        for d in design:
            for condition in ["matched", "mismatched"]:
                for policy_seed in p["policy_seeds"]:
                    row, trace = run_episode(d, condition, policy_seed, p, catalog, log, errors)
                    rows.append(row)
                    np.savez_compressed(output / "traces" / f"abc_{condition}_{d['episode']:03d}_seed{policy_seed}.npz", **trace)
            if (d["episode"] + 1) % 25 == 0:
                print(json.dumps({"environment_seeds_completed": d["episode"] + 1,
                                  "rollouts_completed": len(rows), "failed": len(errors),
                                  "elapsed_seconds": round(time.perf_counter() - started, 2)}), flush=True)
    with (output / "rollouts.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    dump(output / "errors.json", errors)
    dump(output / "execution.json", {"started_after_protocol_freeze": True, "rollouts": len(rows),
         "failed_rollouts": len(errors), "elapsed_seconds": time.perf_counter() - started,
         "python": platform.python_version(), "numpy": np.__version__, "platform": platform.platform(),
         "protocol_sha256": digest(ROOT / "protocol.json"), "runtime_policy_methods": ["ABC-Causal"],
         "training_calls": 0, "retrieval_calls": 0, "target_retries": 0})
    print(json.dumps({"rollouts": len(rows), "failed": len(errors), "elapsed_seconds": time.perf_counter() - started}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true")
    args = parser.parse_args()
    if args.freeze_only:
        p = freeze()
        print(json.dumps({"frozen": True, "budget": p["resource_budget"], "dwell": DWELL,
                          "raw_entries": len(RAW), "mapped_moves": len(build_catalog()["actions"]),
                          "policy_seeds": POLICY_SEEDS}))
    else:
        run_suite()
