"""Read-only independent physical and observation replay of saved v0.3 results.

No policy experiment is rerun or tuned. The verifier reconstructs recorded
resource/queue transitions and replays the three new deterministic controllers
from their observed history. Statistical point values are recomputed with
ordinary Python arithmetic; frozen bootstrap intervals are not re-estimated.

    python review_v3_results.py --out v3_results/validation \
        --report v3_results/validation/independent_review.json
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
from statistics import mean

import numpy as np

from audit_capacity import audit_source_integrity
from audit_v3 import audit_rows,audit_trace,audit_controller_replay,require,close
from freeze_v3 import verify_frozen
from v3_policy import V3Config,V3Policy,CapacityBalance
from v3_scenarios import generate_scenarios,scenario_manifest,REGIME_DELAYS,POLICIES,policy_seeds

ROOT=Path(__file__).resolve().parent
OBSERVATION_POLICIES=("v3_full","v3_signal_only","capacity_balance")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def episode_key(row):
    return row["regime"],row["group"],row["scenario_seed"],row["policy"],row["policy_seed"]


def artifact(out,descriptor):
    path=(out/descriptor["path"]).resolve()
    require(path.is_relative_to(out) and path.is_file(),"Missing artifact or path outside result directory")
    require(sha(path)==descriptor["sha256"],"Artifact checksum mismatch: "+descriptor["path"])
    return path


def scalar_cvar90(values):
    ordered=sorted(values,reverse=True)
    mass=.1*len(ordered)
    whole=int(math.floor(mass))
    fraction=mass-whole
    numerator=sum(ordered[:whole])
    if fraction and whole<len(ordered):numerator+=fraction*ordered[whole]
    return numerator/mass


def check_statistics(rows,summary):
    """Independent case averages, point differences and static-relative risk."""
    grouped=defaultdict(list)
    for row in rows:
        grouped[row["regime"],row["group"],row["scenario_seed"],row["policy"]].append(row)
    case_values={
        key:{metric:mean(float(row["drain_censored"]) if metric=="drain_uncleared_fraction" else row[metric]
                         for row in replicas) for metric in summary["metrics"]}
        for key,replicas in grouped.items()
    }
    checked_means=checked_comparisons=checked_cvars=0
    for regime,part in summary["regimes"].items():
        case_keys={(group,seed) for r,group,seed,policy in case_values if r==regime}
        require(len(part["case_results"])==len(case_keys)==part["scenarios"],"Saved case count mismatch")
        saved_keys={(case["group"],case["scenario_seed"]) for case in part["case_results"]}
        require(saved_keys==case_keys,"Saved case scope mismatch")
        for case in part["case_results"]:
            for policy in POLICIES:
                for metric,value in case_values[regime,case["group"],case["scenario_seed"],policy].items():
                    require(close(value,case[policy][metric]),"Case-level policy-draw averaging mismatch")
        slices=[(part,case_keys)]
        for source,selected in part["source_slices"].items():
            keys={(case["group"],case["scenario_seed"]) for case in part["case_results"] if case["source"]==source}
            slices.append((selected,keys))
        for group,selected in part["by_group"].items():
            slices.append((selected,{key for key in case_keys if key[0]==group}))
        for selected,keys in slices:
            require(len(keys)==selected["scenarios"],"Slice scope mismatch")
            for policy in POLICIES:
                for metric in summary["metrics"]:
                    value=mean(case_values[regime,g,s,policy][metric] for g,s in keys)
                    require(close(value,selected["overall"][policy][metric]),"Independent aggregate mean mismatch")
                    checked_means+=1
                harms=[max(0.,case_values[regime,g,s,policy]["mean_backlog"]-
                              case_values[regime,g,s,"static"]["mean_backlog"]) for g,s in keys]
                risk=selected["static_relative_harm"][policy]
                require(risk["count"]==sum(h>1e-6 for h in harms),"Static-relative harm count mismatch")
                for field,value in (("mean_positive_harm",mean(harms)),("maximum_harm",max(harms)),("cvar90",scalar_cvar90(harms))):
                    require(close(value,risk[field]),"Static-relative harm magnitude mismatch: "+field)
                checked_cvars+=1
            for candidate,comparisons in selected["comparisons"].items():
                for baseline,evidence in comparisons.items():
                    differences=[case_values[regime,g,s,baseline]["mean_backlog"]-
                                 case_values[regime,g,s,candidate]["mean_backlog"] for g,s in keys]
                    require(close(mean(differences),evidence["mean_improvement"]),"Paired mean difference mismatch")
                    for field,count in (("wins",sum(d>1e-6 for d in differences)),
                                        ("ties",sum(abs(d)<=1e-6 for d in differences)),
                                        ("losses",sum(d<-1e-6 for d in differences))):
                        require(count==evidence[field],"Paired case count mismatch")
                    checked_comparisons+=1
    return {"aggregate_point_means_checked":checked_means,"paired_primary_comparisons_checked":checked_comparisons,
            "static_relative_cvar_point_values_checked":checked_cvars,
            "scope":"Independent scalar point calculations; bootstrap intervals remain those in the frozen statistical artifact"}


def check_csv(out,rows):
    key=lambda row:(row["regime"],row["group"],str(row["scenario_seed"]),row["policy"],
                    "" if row["policy_seed"] is None else str(row["policy_seed"]))
    originals={key(row):row for row in rows}
    with (out/"episodes.csv").open(newline="",encoding="utf-8") as handle:
        csv_rows=list(csv.DictReader(handle))
    require(len(csv_rows)==len(rows),"CSV row count mismatch")
    seen=set()
    for row in csv_rows:
        identity=(row["regime"],row["group"],row["scenario_seed"],row["policy"],row["policy_seed"])
        require(identity in originals and identity not in seen,"Missing or duplicated CSV episode")
        seen.add(identity)
        require(set(row)==set(originals[identity]),"CSV columns differ")
        for field,value in originals[identity].items():
            require(row[field]==("" if value is None else str(value)),"CSV value differs: "+field)
    require(seen==set(originals),"CSV episode set differs")


def review(out):
    out=Path(out).resolve()
    frozen=verify_frozen()
    protocol=read_json(ROOT/"v3_protocol.json")
    summary=read_json(out/"summary.json")
    verification=read_json(out/"verification.json")
    require(verification["split"]=="validation" and verification["status"]=="SUCCEEDED","Completed validation required")
    require(verification["freeze_verification"]==frozen,"Frozen identity differs from result")
    require(verification["controller_configuration"]==protocol["controller_configuration"],"Configuration mismatch")
    require(sha(out/"summary.json")==verification["summary_sha256"],"Summary checksum mismatch")
    receipt=artifact(out,{"path":verification["receipt_path"],"sha256":verification["receipt_sha256"]})
    require(receipt.stat().st_size==verification["receipt_bytes"],"Receipt size mismatch")
    require(audit_source_integrity(ROOT)==verification["upstream_sha256"],"Upstream source mismatch")
    manifest=scenario_manifest("validation")
    require(read_json(out/"scenario_manifest.json")==manifest,"Saved environment manifest differs")
    require(manifest["manifest_sha256"]==verification["scenario_manifest_sha256"]==protocol["validation_manifest_sha256"],
            "Environment identity mismatch")
    scenarios=generate_scenarios("validation")
    first_cases={}
    for case in scenarios:first_cases.setdefault(case.group,case)
    descriptors={};rows=[]
    trace_index=read_json(out/"representative_traces_index.json")
    for regime in REGIME_DELAYS:
        for policy in POLICIES:
            name=regime+"__"+policy
            descriptor=read_json(out/"indices"/(name+".json"))
            require((descriptor["split"],descriptor["regime"],descriptor["policy"])==("validation",regime,policy),
                    "Policy artifact identity mismatch")
            require(descriptor["rows"]["path"]=="rows/"+name+".json", "Unexpected row path")
            require(descriptor["traces"]["path"]=="traces/"+name+".json.gz", "Unexpected trace path")
            require(descriptor["traces"]==trace_index[name],"Trace index disagrees with policy result")
            payload=read_json(artifact(out,descriptor["rows"]))
            require(payload["regime"]==regime and payload["policy"]==policy,"Row identity mismatch")
            require(len(payload["rows"])==descriptor["rows"]["count"],"Row descriptor count mismatch")
            rows.extend(payload["rows"]);descriptors[name]=descriptor
    require(set(trace_index)==set(descriptors),"Trace-index scope differs")
    scope=audit_rows(rows,scenarios,{policy:policy_seeds(policy) for policy in POLICIES})
    require(scope==verification["row_audit"],"Recorded row audit differs")
    require(scope["episodes_checked"]==protocol["expected_validation_scope"]["episodes"],"Episode scope differs from protocol")
    require(scope["simulated_minutes"]==protocol["expected_validation_scope"]["total_minutes"],"Minute scope differs from protocol")
    check_csv(out,rows)
    point_checks=check_statistics(rows,summary)
    row_map={episode_key(row):row for row in rows}
    trace_count=physical_minutes=controller_count=controller_decisions=0
    replay_by_policy={policy:{"traces":0,"decisions":0} for policy in OBSERVATION_POLICIES}
    transit_total=outstanding_total=0.0
    for name,descriptor in descriptors.items():
        regime,policy=descriptor["regime"],descriptor["policy"]
        with gzip.open(artifact(out,descriptor["traces"]),"rt",encoding="utf-8") as handle:
            reps=json.load(handle)
        expected={(regime,group,case.seed,policy,policy_seeds(policy)[0]) for group,case in first_cases.items()}
        require({episode_key(rep["summary"]) for rep in reps}==expected and len(reps)==len(expected),"Representative scope mismatch")
        require(len(reps)==descriptor["traces"]["count"],"Trace count mismatch")
        local_minutes=0
        for rep in reps:
            row=rep["summary"]
            require(row==row_map[episode_key(row)],"Trace summary differs from row artifact")
            check=audit_trace(rep,first_cases[row["group"]],regime)
            trace_count+=1;physical_minutes+=check["minutes_replayed"];local_minutes+=check["minutes_replayed"]
            transit_total+=check["resource_minutes_in_transit"]
            outstanding_total+=check["unobserved_future_resource_minutes"]
            if policy in OBSERVATION_POLICIES:
                # Constructor inputs contain no environment, future arrivals,
                # terminal schedule, scenario labels, or true productivity.
                controller=(CapacityBalance() if policy=="capacity_balance" else
                            V3Policy(mode="full" if policy=="v3_full" else "signal_only",
                                     transit_delay=REGIME_DELAYS[regime],config=V3Config(**protocol["controller_configuration"])))
                observed=audit_controller_replay(rep,controller)
                controller_count+=1;controller_decisions+=observed["decisions_replayed"]
                replay_by_policy[policy]["traces"]+=1
                replay_by_policy[policy]["decisions"]+=observed["decisions_replayed"]
        require(local_minutes==descriptor["traces"]["minutes"],"Trace minute count mismatch")
    require(trace_count==198 and physical_minutes==22104,"Unexpected complete physical replay scope")
    require(controller_count==99 and controller_decisions==11052,"Unexpected observation replay scope")
    underload=[row for row in rows if row["group"]=="generated_underload" and row["policy"]=="v3_full"]
    underload_fields=("mean_backlog","peak_backlog","end_backlog","tail_mean_backlog","tail_auc","tail_end_backlog",
                      "moves","tail_moves","total_moves","resource_minutes_lost","tail_resource_minutes_lost")
    underload_nonzero=[{k:row[k] for k in ("regime","scenario_seed",*underload_fields)} for row in underload
                       if any(abs(row[field])>1e-6 for field in underload_fields)]
    require(len(underload)==96,"Unexpected underload scope")
    require(verify_frozen()==frozen,"Frozen source changed during replay")
    return {
        "version":"0.3.0","split":"validation","scope":scope,
        "checks":{
            "frozen_v3_and_parent_v2_identity_verified":True,"upstream_sources_verified":True,
            "environment_manifest_verified":True,"row_trace_summary_receipt_hashes_verified":True,
            "csv_rows_match_json":True,"full_row_scope_and_accounting_verified":True,
            "independent_point_statistics":point_checks,
            "independent_scalar_physical_replay":{"traces":trace_count,"minutes":physical_minutes,
                "scope":"Original H and all32 follow-up intervals; active/committed/pending resources, activation time, flow, accounting and censoring"},
            "observation_only_controller_replay":{"traces":controller_count,"decisions":controller_decisions,
                "all_actions_and_full_evidence_match":True,"by_policy":replay_by_policy,
                "inputs":"Current observed queue and arrival, prior observed queues, committed allocation; after decision, active allocation/completed flow/end queue. No true rates or future schedule."},
            "generated_underload":{"episodes_checked":len(underload),"nonzero_cases":underload_nonzero,"passed":not underload_nonzero},
        },
        "representative_transfer_exposure":{"observed_resource_minutes":transit_total,
            "future_resource_minutes_after_observation_window":outstanding_total,
            "identity":"Observed plus unobserved exposure equals total transferred units times physical delay for each trace"},
        "review_metadata":{"script":Path(__file__).name,"script_sha256":sha(__file__),
            "protocol_sha256":frozen["protocol_sha256"],"summary_sha256":sha(out/"summary.json"),
            "python":platform.python_version(),"numpy":np.__version__,
            "scope":"Saved-evidence replay and point-statistic verification only; no fresh policy experiment, tuning or changed acceptance threshold"},
        "limitations":[
            "Full physical replay covers198prespecified representative traces, not all8448rollouts; every rollout has inline and summary invariants.",
            "Observation replay covers the99prespecified traces for the three new deterministic controllers; oldV2andABCbehavior is covered by frozen sources, original tests, and physical replay.",
            "Bootstrap intervals are produced by the frozen statistical module; this independent review recomputes point means and risks without claiming an independent interval implementation.",
            "An implementation/replay pass is separate from the engineering acceptance decision. See decision.json for all failed criteria.",
        ],
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out",type=Path,default=ROOT/"v3_results"/"validation")
    parser.add_argument("--report",type=Path,help="Optional JSON report output; otherwise print only")
    args=parser.parse_args()
    result=review(args.out)
    encoded=json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n"
    if args.report is not None:
        target=args.report.resolve()
        require(target!=Path(__file__).resolve(),"Report must not overwrite reviewer source")
        if target.exists():
            previous=read_json(target)
            require(isinstance(previous,dict) and "checks" in previous and "review_metadata" in previous,
                    "Report destination contains another artifact")
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(encoded,encoding="utf-8")
    print(encoded,end="")


if __name__=="__main__":
    main()
