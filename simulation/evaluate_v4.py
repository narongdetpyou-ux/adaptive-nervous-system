"""Evaluate the v0.4 acceptance rule declared before development outcomes.

This file does not alter the rule to match results. Missing integrity evidence
fails the corresponding condition. All numerical checks remain inspectable in
the written decision. The primary risk is STATIC-relative harm, not direct
candidate-minus-V3 harm.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

from v4_metrics import CORE_METRICS, BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, _row_metric_values
from v4_scenarios import GROUPS, LEGACY_GROUPS, REGIMES, POLICIES, SPLIT_SEEDS, FOLLOWUP_MINUTES

ROOT = Path(__file__).resolve().parent
INTEGRITY_FLAGS = (
    "tests_passed", "frozen_identity_passed", "source_and_sensor_boundary_passed",
    "full_episode_accounting_passed", "paired_scope_passed", "independent_replay_passed",
)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_acceptance():
    path = ROOT/"v4_acceptance_predeclared.json"
    expected = (ROOT/"v4_acceptance_predeclared.sha256").read_text().strip()
    if _sha(path) != expected:
        raise ValueError("The acceptance declaration does not match its recorded hash")
    return json.loads(path.read_text()), expected


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
        raise ValueError(f"Nonfinite or nonnumeric decision evidence: {name}")
    return float(value)


def _interval(value, name):
    if not isinstance(value,list) or len(value)!=2:
        raise ValueError(f"Expected a two-sided interval: {name}")
    low,high=(_finite(x,name) for x in value)
    if low>high:raise ValueError(f"Reversed interval: {name}")
    return [low,high]


def _assert_close(actual,expected,label,tol):
    actual=_finite(actual,label)
    if not math.isclose(actual,expected,rel_tol=1e-9,abs_tol=tol):
        raise ValueError(f"Summary disagrees with retained rows: {label}")


def _independent_cvar(values):
    """Scalar sorted-list implementation independent of vector metric code."""
    ordered=sorted(values,reverse=True)
    mass=len(ordered)*0.10
    whole=math.floor(mass+1e-12)
    fraction=mass-whole
    total=math.fsum(ordered[:whole])
    if fraction>1e-12 and whole<len(ordered):total+=fraction*ordered[whole]
    return total/mass


def _verify_point_evidence(summary,rows,tol,split=None):
    if not rows:raise ValueError("The evaluator requires the complete episode rows")
    if summary.get("version")!="0.4.0":raise ValueError("Unexpected summary version")
    if set(summary["regimes"])!=set(REGIMES) or set(summary["policies"])!=set(POLICIES):
        raise ValueError("Acceptance requires every declared regime and policy")
    if summary["analysis"]["bootstrap_draws"]!=BOOTSTRAP_DRAWS or summary["analysis"]["bootstrap_seed"]!=BOOTSTRAP_SEED:
        raise ValueError("Decision requires the frozen 2000-draw bootstrap specification")
    by={}
    seeds=set()
    for row in rows:
        key=(row["regime"],row["group"],row["scenario_seed"],row["policy"])
        if key in by:raise ValueError("Duplicate episode supplied to the evaluator")
        if row["regime"] not in REGIMES or row["group"] not in GROUPS or row["policy"] not in POLICIES:
            raise ValueError("Unexpected episode scope")
        if type(row["scenario_seed"]) is not int or row["policy_seed"] is not None:
            raise ValueError("Unexpected policy/scenario replicate")
        expected_horizon=60 if row["group"] in LEGACY_GROUPS else 96
        expected_source="legacy_flash" if row["group"] in LEGACY_GROUPS else "generated_synthetic"
        if type(row["horizon"]) is not int or row["horizon"]!=expected_horizon:
            raise ValueError("Unexpected original episode horizon")
        if type(row["followup_minutes"]) is not int or row["followup_minutes"]!=FOLLOWUP_MINUTES:
            raise ValueError("Unexpected episode follow-up horizon")
        if row["source"]!=expected_source:
            raise ValueError("Episode source differs from the fixed family source")
        _row_metric_values(row)
        by[key]=row;seeds.add(row["scenario_seed"])
    if split is None:
        matches=[name for name,values in SPLIT_SEEDS.items() if set(values)==seeds]
        if len(matches)!=1:raise ValueError("Cannot identify the declared scenario split")
        split=matches[0]
    if split not in SPLIT_SEEDS or seeds!=set(SPLIT_SEEDS[split]):
        raise ValueError("Scenario seeds do not match the declared split")
    cases=[(group,seed) for group in GROUPS for seed in SPLIT_SEEDS[split]]
    expected_keys={(r,g,s,p) for r in REGIMES for g,s in cases for p in POLICIES}
    if set(by)!=expected_keys:raise ValueError("Incomplete paired regime/family/seed/policy scope")
    expected_episodes=len(cases)*len(REGIMES)*len(POLICIES)
    if summary["episodes"]!=expected_episodes or summary["scenarios"]!=len(cases):
        raise ValueError("Summary scope counters disagree with complete rows")
    if summary["regime_count"]!=len(REGIMES) or summary["scenario_regime_pairs"]!=len(cases)*len(REGIMES):
        raise ValueError("Summary regime counters disagree with complete rows")
    original=sum(row["horizon"] for row in rows)
    if summary["original_minutes"]!=original or summary["followup_minutes"]!=expected_episodes*FOLLOWUP_MINUTES:
        raise ValueError("Summary minute accounting is inconsistent")
    if summary["minutes"]!=original+expected_episodes*FOLLOWUP_MINUTES:
        raise ValueError("Summary total minute counter is inconsistent")
    point_means,risks={},{}
    for regime in REGIMES:
        report=summary["regimes"][regime]
        if report["scenarios"]!=len(cases) or report["episodes"]!=len(cases)*len(POLICIES):
            raise ValueError("Per-regime scope counters disagree with retained rows")
        point_means[regime]={};risks[regime]={}
        static=[by[regime,g,s,"static"]["mean_deficit"] for g,s in cases]
        for policy in POLICIES:
            selected=[by[regime,g,s,policy] for g,s in cases]
            means={m:statistics.fmean(row[m] for row in selected) for m in CORE_METRICS}
            means["drain_uncleared_fraction"]=statistics.fmean(float(row["drain_censored"]) for row in selected)
            for metric,value in means.items():
                _assert_close(report["overall"][policy][metric],value,f"{regime}/{policy}/{metric}",tol)
            harm=[max(row["mean_deficit"]-reference,0.) for row,reference in zip(selected,static)]
            risk=_independent_cvar(harm)
            _assert_close(report["static_relative_harm"][policy]["cvar90"],risk,f"{regime}/{policy}/CVaR90",tol)
            if report["static_relative_harm"][policy]["count"]!=sum(value>tol for value in harm):
                raise ValueError("Static-relative harm count disagrees with rows")
            point_means[regime][policy]=means;risks[regime][policy]=risk
        for group,seed in cases:
            offered=[by[regime,group,seed,p]["offered"] for p in POLICIES]
            if max(offered)-min(offered)>tol:
                raise ValueError("Paired policies have different offered work")
    return split,by,point_means,risks


def evaluate(summary,rows,*,integrity,acceptance=None,split=None):
    """Return exact declared decisions; point evidence is independently checked.

    `integrity` is an explicit evidence block with the six INTEGRITY_FLAGS.
    Values must be the boolean True. Absent/false values cannot pass. Confidence
    intervals are taken from the frozen metric computation and are not claimed
    to have an independently reimplemented bootstrap here.
    """
    declared,declaration_sha=load_acceptance()
    if acceptance is not None and acceptance!=declared:
        raise ValueError("Requested acceptance rule differs from the predeclared rule")
    acceptance=declared;tol=acceptance["tolerance_absolute"];candidate=acceptance["candidate"]
    split,by,means,risks=_verify_point_evidence(summary,rows,tol,split)
    primary_rule=acceptance["primary"];regime=primary_rule["regime"];baseline=primary_rule["baseline"]
    primary_metric=summary["regimes"][regime]["static_harm_comparisons"][candidate][baseline]["cvar90"]
    primary_ci=_interval(primary_metric["risk_reduction_ci95"],"primary risk reduction")
    reduction=risks[regime][baseline]-risks[regime][candidate]
    _assert_close(primary_metric["risk_reduction"],reduction,"primary risk reduction",tol)
    primary={"passed":primary_ci[0]>0.,"regime":regime,"baseline":baseline,
             "candidate_risk":risks[regime][candidate],"baseline_risk":risks[regime][baseline],
             "risk_reduction":reduction,"risk_reduction_ci95":primary_ci,
             "condition":"lower endpoint > 0; both zero does not establish superiority"}
    risk_checks=[]
    for regime in acceptance["risk_point_nonworsening"]["regimes"]:
        for baseline in acceptance["risk_point_nonworsening"]["baselines"]:
            new,old=risks[regime][candidate],risks[regime][baseline]
            risk_checks.append({"regime":regime,"baseline":baseline,"candidate_risk":new,
                                "baseline_risk":old,"risk_reduction":old-new,"passed":new<=old+tol})
    mean_checks=[]
    margin=acceptance["mean_noninferiority"]["margin_fraction"]
    for regime in acceptance["mean_noninferiority"]["regimes"]:
        for baseline in acceptance["mean_noninferiority"]["baselines"]:
            new,old=means[regime][candidate]["mean_deficit"],means[regime][baseline]["mean_deficit"]
            item=summary["regimes"][regime]["comparisons"][candidate][baseline]["mean_noninferiority"]
            if item["margin_fraction"]!=margin:raise ValueError("Noninferiority margin changed")
            ci=_interval(item["allowance_slack_ci95"],"mean noninferiority")
            slack=(1+margin)*old-new
            _assert_close(item["allowance_slack"],slack,"noninferiority point slack",tol)
            mean_checks.append({"regime":regime,"baseline":baseline,"candidate_mean":new,
                                "baseline_mean":old,"margin_fraction":margin,"allowance_slack":slack,
                                "allowance_slack_ci95":ci,"passed":ci[0]>=-tol})
    endpoint_checks=[]
    rule=acceptance["loss_and_endpoint_point_nonworsening"]
    for regime in rule["regimes"]:
        for baseline in rule["baselines"]:
            for metric in rule["metrics"]:
                new,old=means[regime][candidate][metric],means[regime][baseline][metric]
                endpoint_checks.append({"regime":regime,"baseline":baseline,"metric":metric,
                                        "candidate_mean":new,"baseline_mean":old,
                                        "mean_improvement":old-new,"passed":new<=old+tol})
    integrity=integrity if isinstance(integrity,dict) else {}
    integrity_checks={name:integrity.get(name) is True for name in INTEGRITY_FLAGS}
    underload=[];underload_count=0
    group=acceptance["underload"]["group"]
    for regime in REGIMES:
        for seed in SPLIT_SEEDS[split]:
            row=by[regime,group,seed,candidate];underload_count+=1
            if row["total_dropped"]>tol or row["total_moves"]>tol:
                underload.append({"regime":regime,"scenario_seed":seed,
                                  "total_dropped":row["total_dropped"],"total_moves":row["total_moves"]})
    checks={
        "primary_final_regime_risk_superiority":{"passed":primary["passed"]},
        "risk_point_nonworsening_every_regime":{"passed":all(c["passed"] for c in risk_checks)},
        "mean_noninferiority_every_regime":{"passed":all(c["passed"] for c in mean_checks)},
        "loss_and_endpoint_point_nonworsening":{"passed":all(c["passed"] for c in endpoint_checks)},
        "integrity":{"passed":all(integrity_checks.values())},
        "underload_zero_drops_and_moves":{"passed":not underload},
    }
    failed=[name for name,item in checks.items() if not item["passed"]]
    return {
        "version":"0.4.0","split":split,"candidate":candidate,
        "acceptance_declaration_sha256":declaration_sha,
        "scope":{"scenarios":summary["scenarios"],"episodes":summary["episodes"],
                 "regimes":list(REGIMES),"policies":list(POLICIES)},
        "primary":primary,
        "risk_point_nonworsening":{"passed":checks["risk_point_nonworsening_every_regime"]["passed"],
                                  "checks":risk_checks,"scope":"Point engineering conditions, not statistical risk noninferiority."},
        "mean_noninferiority":{"passed":checks["mean_noninferiority_every_regime"]["passed"],"checks":mean_checks},
        "loss_and_endpoint_point_nonworsening":{"passed":checks["loss_and_endpoint_point_nonworsening"]["passed"],"checks":endpoint_checks},
        "integrity":{"passed":checks["integrity"]["passed"],"checks":integrity_checks,
                     "missing_or_failed":[name for name,passed in integrity_checks.items() if not passed]},
        "underload":{"passed":not underload,"episodes_checked":underload_count,"violations":underload,
                     "scope":"Retained/pipeline work may be positive; every full-run drop and move must be zero."},
        "advancement_checks":checks,"failed_criteria":failed,"advancement_passed":not failed,
        "decision":"meets_declared_engineering_conditions" if not failed else "does_not_meet_declared_engineering_conditions",
        "limitations":[
            "The primary risk is defined relative to Static within the same realism regime.",
            "Exact scalar point means/CVaR are checked against retained rows; bootstrap intervals use the frozen metric implementation.",
            "The 5% margin applies only to mean demand deficit, not to CVaR, dropped work or endpoint point checks.",
            "Passing this synthetic engineering intersection does not establish safe live deployment or universal workload performance.",
        ],
    }


def _read_rows(directory):
    rows=[]
    for path in sorted((directory/"rows").glob("*.json")):
        data=json.loads(path.read_text())
        parts=path.stem.split("__",1)
        if len(parts)!=2 or parts[0] not in REGIMES or parts[1] not in POLICIES:
            raise ValueError(f"Unexpected row artifact filename: {path.name}")
        if isinstance(data,dict):
            if data.get("regime")!=parts[0] or data.get("policy")!=parts[1]:
                raise ValueError(f"Row artifact identity disagrees with its path: {path.name}")
            data=data.get("rows")
        if not isinstance(data,list):raise ValueError(f"Rows artifact must contain a JSON row list: {path.name}")
        if any(row.get("regime")!=parts[0] or row.get("policy")!=parts[1] for row in data):
            raise ValueError(f"Row identity disagrees with its artifact: {path.name}")
        rows.extend(data)
    if not rows:raise ValueError("No retained rows/*.json files found")
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results",type=Path,required=True)
    parser.add_argument("--protocol",type=Path)
    parser.add_argument("--review",type=Path)
    parser.add_argument("--tests",type=Path,default=ROOT/"v4_results/unit_tests.json")
    parser.add_argument("--split",choices=tuple(SPLIT_SEEDS))
    parser.add_argument("--out",type=Path)
    args=parser.parse_args();directory=args.results.resolve()
    summary_path=directory/"summary.json"
    summary=json.loads(summary_path.read_text());rows=_read_rows(directory)
    protocol_path=args.protocol or ROOT/"v4_protocol.json"
    if not protocol_path.exists():protocol_path=ROOT/"v4_protocol_draft.json"
    protocol=json.loads(protocol_path.read_text())
    review_path=args.review or directory/"independent_review.json"
    review=json.loads(review_path.read_text()) if review_path.exists() else {}
    integrity=dict(review.get("acceptance_integrity",{}))
    tests=json.loads(args.tests.read_text()) if args.tests.exists() else {}
    tests_ok=(tests.get("successful") is True and isinstance(tests.get("tests_run"),int)
              and tests["tests_run"]>0 and tests.get("failures")==0 and tests.get("errors")==0)
    integrity["tests_passed"]=integrity.get("tests_passed") is True and tests_ok
    verification_path=directory/"verification.json"
    verification=json.loads(verification_path.read_text()) if verification_path.exists() else {}
    receipt_ok=verification.get("status")=="SUCCEEDED" and verification.get("summary_sha256")==_sha(summary_path)
    integrity["paired_scope_passed"]=integrity.get("paired_scope_passed") is True and receipt_ok
    result=evaluate(summary,rows,integrity=integrity,acceptance=protocol["acceptance_criteria"],split=args.split)
    result["evidence"]={"summary_sha256":_sha(summary_path),"protocol_sha256":_sha(protocol_path),
                        "review_sha256":_sha(review_path) if review_path.exists() else None,
                        "tests_sha256":_sha(args.tests) if args.tests.exists() else None,
                        "workflow_summary_receipt_matches":receipt_ok}
    out=args.out or directory/"decision.json"
    temporary=out.with_suffix(out.suffix+".tmp")
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    temporary.replace(out)
    print(json.dumps({"decision":result["decision"],"failed_criteria":result["failed_criteria"],
                      "path":str(out)},ensure_ascii=False))


if __name__=="__main__":
    main()
