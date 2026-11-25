"""Retrospective v0.3 failure diagnosis for v0.4 DEVELOPMENT only.

All selected seeds are already-seen v0.3 validation cases. This script never
uses v0.4's reserved 4000+ seeds. Future rates/arrivals appear ONLY in explicitly
labeled offline constant-action counterfactuals, never controller inputs.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import gzip
import json
import math
from pathlib import Path

import numpy as np

from abc_service.frozen import catalog
from freeze_v3 import verify_frozen
from v3_scenarios import generate_scenarios,REGIME_DELAYS
from v3_simulation import simulate_case

ROOT=Path(__file__).resolve().parent
POLICIES=("static","capacity_v2","capacity_balance","v3_full")
SELECTED=(
    ("transit_3","generated_switching_fault",2000),
    ("transit_3","generated_sustained_overload",2022),
    ("instant","generated_pulsed_short_fault",2003),
    ("transit_3","generated_switching_fault",2019),
)


def risk_contributions(part):
    ranked=sorted(part["case_results"],key=lambda c:max(c["v3_full"]["mean_backlog"]-c["static"]["mean_backlog"],0),reverse=True)
    mass=.1*len(ranked);whole=int(math.floor(mass));weighted=defaultdict(float);members=[]
    for rank,case in enumerate(ranked):
        weight=1.0 if rank<whole else mass-whole if rank==whole else 0.0
        harm=max(case["v3_full"]["mean_backlog"]-case["static"]["mean_backlog"],0.0)
        if not weight or not harm:continue
        contribution=weight*harm/mass
        weighted[case["group"]]+=contribution
        members.append({"group":case["group"],"seed":case["scenario_seed"],"harm":harm,"tail_weight":weight,"cvar_contribution":contribution})
    total=sum(weighted.values())
    return {"cvar90":total,"contributions_by_group":{
        group:{"absolute":value,"percentage_of_cvar":100*value/total if total else None}
        for group,value in sorted(weighted.items(),key=lambda item:-item[1])},"tail_members":members}


def load_or_replay(out,regime,case,policy,expected):
    source=ROOT/"v3_results/validation/traces"/(regime+"__"+policy+".json.gz")
    with gzip.open(source,"rt",encoding="utf-8") as handle:
        representatives=json.load(handle)
    rep=next((r for r in representatives if r["summary"]["group"]==case.group and r["summary"]["scenario_seed"]==case.seed),None)
    replayed=False
    if rep is None:
        row,minutes=simulate_case(policy,case,None,regime=regime,detailed=True)
        rep={"summary":row,"minutes":minutes};replayed=True
    for metric,value in expected.items():
        if metric=="drain_uncleared_fraction":actual=float(rep["summary"]["drain_censored"])
        else:actual=rep["summary"][metric]
        if not math.isclose(float(actual),float(value),rel_tol=1e-10,abs_tol=1e-6):
            raise ValueError("Known-case reproduction disagrees: "+policy+"/"+metric)
    relative=f"traces/{regime}__{case.group}__{case.seed}__{policy}.json.gz"
    path=out/relative;path.parent.mkdir(parents=True,exist_ok=True)
    with gzip.open(path,"wt",encoding="utf-8") as handle:json.dump(rep,handle,separators=(",",":"))
    return rep,relative,replayed


def compact_observation(minute):
    note=minute["decision_evidence"]
    estimator=note.get("estimator",{})
    return {"t":minute["t"],"phase":minute["phase"],"branch":minute["branch"],
            "delta":minute["delta"],"allocation_before":minute["staff_before"],"allocation":minute["committed_staff"],
            "end_queue":minute["queue"],"arrival":minute["arrival"],
            "estimated_rates":note.get("estimated_rates",estimator.get("rate")),
            "lower":note.get("rate_lower"),"upper":note.get("rate_upper"),
            "busy_counts":note.get("recent_busy_counts"),
            "predicted_mean_gain":note.get("predicted_mean_gain"),
            "minimum_mean_gain":note.get("minimum_mean_gain"),
            "predicted_worst_gain":note.get("predicted_worst_gain"),
            "true_rates_OFFLINE_ONLY":minute["productivity"]}


def describe(rep):
    horizon=rep["summary"]["horizon"]
    scoring=rep["minutes"][:horizon]
    moves=[compact_observation(m) for m in scoring if any(m["delta"])]
    eligible=[];last=-10**9
    for minute in scoring:
        if any(minute["delta"]):last=minute["t"]
        elif minute["t"]>=5 and minute["t"]-last>=8 and sum(minute["queue"])>1e-6:
            eligible.append(compact_observation(minute))
    return {"summary":rep["summary"],"scoring_branch_counts":dict(Counter(m["branch"] for m in scoring)),
            "first_move_minute":moves[0]["t"] if moves else None,"moves":moves,
            "eligible_backlogged_holds":eligible}


def balance_proposal_from_observations(minute):
    staff=np.asarray(minute["staff_before"],dtype=float)
    point=np.maximum(np.asarray(minute["decision_evidence"]["estimated_rates"],dtype=float),1e-9)
    target=207./point/np.sum(1./point)
    actions=np.asarray([a["delta"] for a in catalog()["actions"]],dtype=float)
    ids=np.flatnonzero((staff+actions>=10).all(axis=1))
    costs=((staff+actions[ids]-target)**2).sum(axis=1)
    ranked=sorted(range(len(ids)),key=lambda j:(float(costs[j]),float(abs(actions[ids[j]]).sum()),int(ids[j])))
    aid=int(ids[ranked[0]])
    if float(((staff-target)**2).sum())-float(costs[ranked[0]])<=1e-7:return np.zeros(3),target
    return actions[aid].copy(),target


def offline_constant_action(minute,case,delay,delta,horizon=8):
    """Evaluator ONLY: realized future path with no further moves for 8 min."""
    t0=minute["t"]
    q=np.asarray(minute["observed"],dtype=float).copy()
    old=np.asarray(minute["staff_before"],dtype=float)
    new=old+delta;incoming=np.maximum(delta,0.)
    totals=[];done=0.0
    for h in range(horizon):
        t=t0+h
        if h:q[0]+=float(case.arrivals[t]) if t<case.horizon else 0.0
        rate=case.productivity[t] if t<case.horizon else case.productivity[-1]
        active=new-incoming if h<delay else new
        for stage in range(3):
            flow=min(q[stage],active[stage]*rate[stage]);q[stage]-=flow
            if stage<2:q[stage+1]+=flow
            else:done+=flow
        totals.append(float(q.sum()))
    return {"delta":delta.tolist(),"new_committed_allocation":new.tolist(),"horizon_minutes":horizon,
            "realized_mean_backlog":float(np.mean(totals)),"realized_terminal_backlog":totals[-1],
            "realized_cost_mean_plus_quarter_terminal":float(np.mean(totals))+.25*totals[-1],
            "completed_during_window":done}


def diagnose(out):
    out.mkdir(parents=True,exist_ok=True)
    frozen=verify_frozen()
    summary=json.loads((ROOT/"v3_results/validation/summary.json").read_text())
    known={(case.group,case.seed):case for case in generate_scenarios("validation")}
    group_risks={regime:risk_contributions(part) for regime,part in summary["regimes"].items()}
    details=[];reruns=0
    for regime,group,seed in SELECTED:
        case=known[group,seed]
        original=next(c for c in summary["regimes"][regime]["case_results"] if c["group"]==group and c["scenario_seed"]==seed)
        item={"regime":regime,"group":group,"seed":seed,"selection":"Post-hoc diagnosis of already-known V3 cases; V4 development evidence only","policies":{}}
        reps={}
        for policy in POLICIES:
            rep,path,rerun=load_or_replay(out,regime,case,policy,original[policy]);reruns+=int(rerun)
            reps[policy]=rep
            item["policies"][policy]={"trace_file":path,"known_case_reproduced":True,"diagnostic_rerun":rerun,**describe(rep)}
        if (group,seed)==("generated_switching_fault",2000):checkpoints=(20,49,58)
        elif (group,seed)==("generated_sustained_overload",2022):
            moves=[m["t"] for m in reps["v3_full"]["minutes"][:case.horizon] if any(m["delta"])]
            checkpoints=tuple(dict.fromkeys([moves[0],moves[0]+8,56,72])) if moves else (16,32,56,72)
        else:checkpoints=()
        item["offline_counterfactuals"]=[]
        for t in checkpoints:
            minute=reps["v3_full"]["minutes"][t]
            if minute["ready_at"] is not None and minute["ready_at"]>t and not any(minute["delta"]):continue
            proposal,target=balance_proposal_from_observations(minute)
            options={"hold":np.zeros(3),"actual_v3_action":np.array(minute["delta"]),"observed_rate_balance_proposal":proposal}
            item["offline_counterfactuals"].append({
                "decision_minute":t,"information_rule":"Candidate balance target uses recorded rate estimates only. Counterfactual outcomes below use true future solely offline; never a controller input or reserved V4 evaluation.",
                "balance_target":target.tolist(),"observation":compact_observation(minute),
                "options":{label:offline_constant_action(minute,case,REGIME_DELAYS[regime],delta) for label,delta in options.items()},
                "scope":"Fixed one-action path for eight intervals, no later moves; this local diagnostic is not the complete alternative-policy outcome."})
        details.append(item)
    if verify_frozen()!=frozen:raise ValueError("Frozen V3 changed during diagnosis")
    result={"purpose":"Known V3 validation is reused openly as V4 development diagnosis",
            "reserved_v4_seeds_used":False,"known_v3_source_identity":frozen,
            "selection":{"known_population_cases":352,"selected_case_regime_pairs":len(SELECTED),
                         "policies_per_selected_case":len(POLICIES),"diagnostic_rollout_reproductions":reruns,
                         "scope":"No new independent statistical evidence; all rows reproduce previously observed V3 results."},
            "static_relative_cvar_contributions":group_risks,"selected_diagnostics":details,
            "recommendations":[
                "Use the observed-rate balance allocation as a candidate and donor-capacity anchor. Do not let draining an existing downstream queue justify an excessively low upstream allocation without an explicit flow-capacity check.",
                "Separate recipient evidence from candidate risk. A real persistent measured fault can be present while simultaneous independent extremes veto every response; expose the reason and assess a bounded balance step rather than a binary all-catalog freeze.",
                "Consider a bounded adaptive or recovery allowance from observable estimates when an earlier allocation becomes unbalanced. No reset or extra capacity may be created; each recovery must remain a legal paid transfer.",
                "Retain model checks on balance proposals. Balance itself performs poorly in switching case2019 with a3-minute transfer, so its responsiveness is not a blanket safety guarantee.",
                "Compare gains only after charging real transfer, pipeline, buffer-loss, startup and observation effects. Include dropped work in offered-minus-completed so a policy cannot improve its score by rejecting work.",
            ],"limits":[
                "This is selected-case diagnosis, not a causal estimate of each rule's global effect.",
                "Controller trajectories differ, so comparisons between their actions are contextual. Local counterfactuals hold the starting V3 state fixed and use a specifically declared eight-minute action path.",
                "The V3 original families, all inspected seeds and selected worst cases are development information for V4; 4000+ V4 seeds remain untouched.",
            ]}
    (out/"diagnosis.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n")
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out",type=Path,default=ROOT/"v4_results/diagnosis")
    args=parser.parse_args()
    result=diagnose(args.out.resolve())
    print(json.dumps({"selection":result["selection"],"cvar_contributions":result["static_relative_cvar_contributions"],
                      "output":str((args.out.resolve()/"diagnosis.json"))},indent=2))


if __name__=="__main__":main()
