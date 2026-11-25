"""Apply the exact predeclared v0.3 engineering acceptance rule.

This post-processing evaluator does not run or tune a controller. Criteria come
from the protocol, whose acceptance section is pinned to the declaration read
before new validation. A different declaration needs a separately reviewed
evaluator. Development results are explicitly labeled descriptive.

Usage:
    python evaluate_v3.py --out v3_results/validation \
        --report v3_results/validation/decision.json

The default tests evidence is v3_results/unit_tests.json. It records the tests
actually run when that file was created; this evaluator does not rerun tests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DECLARED_ACCEPTANCE_SHA256 = "49cc704a73d2b680a9d2c26632f79155720d02110825bbb814cb29cb758581ac"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def numeric(value, name):
    require(type(value) in (float,int) and math.isfinite(value), "Invalid numeric evidence: " + name)
    return float(value)


def lower_interval(value, name):
    require(isinstance(value,list) and len(value)==2, "Missing two-sided interval: " + name)
    low,high=(numeric(item,name) for item in value)
    require(low<=high, "Reversed interval: " + name)
    return low


def evaluate(summary, verification, protocol, tests):
    """Return an auditable pass/fail decision without changing any criterion.

    Inputs are parsed JSON objects. Integrity claims here refer to supplied
    verification/test evidence; the CLI additionally checks summary identity
    and, for validation, verifies the frozen experiment identity.
    """
    criteria=protocol["acceptance_criteria"]
    require(canonical_sha(criteria)==DECLARED_ACCEPTANCE_SHA256,
            "Acceptance declaration differs from the evaluator's prevalidation specification")
    candidate=criteria["candidate"]
    tolerance=numeric(criteria["tolerance_absolute"],"absolute tolerance")
    split=verification["split"]
    require(split in ("development","validation"),"Unknown result split")
    expected=protocol["scenario_design"][split]["counts"]
    declared_regimes=protocol["resource_model"]["regimes_minutes"]
    require(set(summary["regimes"])==set(declared_regimes),"Missing or unexpected regime")
    advancement_specs={item["id"]:item for item in criteria["advancement_all_required"]}
    require(set(advancement_specs)=={
        "primary_and_noninferiority","surge_only_mean","mean_vs_static_each_regime",
        "static_relative_cvar_each_regime","tail_auc_each_regime",
        "original_terminal_each_regime","invariants_tests_underload",
    },"Unsupported advancement criteria")

    primary_spec=criteria["primary"]
    primary_evidence=summary["regimes"][primary_spec["regime"]]["comparisons"][candidate][primary_spec["baseline"]]
    primary_low=lower_interval(primary_evidence["improvement_ci95"],"primary absolute improvement")
    primary={
        "condition":primary_spec["condition"],"regime":primary_spec["regime"],
        "baseline":primary_spec["baseline"],"passed":primary_low>0.0,
        "absolute_improvement_ci95":primary_evidence["improvement_ci95"],
        "reduction_pct":primary_evidence["reduction_pct"],
        "reduction_pct_ci95":primary_evidence["reduction_pct_ci95"],
    }
    ni_spec=criteria["noninferiority"]
    ni_evidence=summary["regimes"][ni_spec["regime"]]["comparisons"][candidate][ni_spec["baseline"]]
    baseline_mean=numeric(ni_evidence["baseline_mean_backlog"],"noninferiority baseline mean")
    margin=numeric(ni_spec["engineering_margin_pct"],"noninferiority margin")
    if baseline_mean==0.0:
        ni_low=lower_interval(ni_evidence["improvement_ci95"],"zero-baseline noninferiority")
        ni_pass=ni_low>=-tolerance
        ni_mode="Zero V2 mean: absolute lower interval endpoint >= negative numerical tolerance"
    elif ni_evidence["reduction_pct_ci95"] is None:
        ni_low=None
        ni_pass=False
        ni_mode="Percentage interval unavailable with positive V2 mean; criterion is not established"
    else:
        ni_low=lower_interval(ni_evidence["reduction_pct_ci95"],"noninferiority percentage")
        ni_pass=ni_low>=-margin
        ni_mode="Percentage lower interval endpoint >= negative declared engineering margin"
    noninferiority={
        "condition":ni_spec["condition"],"regime":ni_spec["regime"],"baseline":ni_spec["baseline"],
        "engineering_margin_pct":margin,"evaluation_mode":ni_mode,"passed":ni_pass,
        "evaluated_lower_endpoint":ni_low,"absolute_improvement_ci95":ni_evidence["improvement_ci95"],
        "reduction_pct":ni_evidence["reduction_pct"],"reduction_pct_ci95":ni_evidence["reduction_pct_ci95"],
    }
    checks={
        "primary_and_noninferiority":{
            "condition":advancement_specs["primary_and_noninferiority"]["condition"],
            "passed":primary["passed"] and noninferiority["passed"],
            "primary_passed":primary["passed"],"noninferiority_passed":noninferiority["passed"],
        },
    }
    surge_spec=advancement_specs["surge_only_mean"]
    surge=summary["regimes"][surge_spec["regime"]]["by_group"][surge_spec["group"]]["overall"]
    surge_candidate=numeric(surge[candidate]["mean_backlog"],"surge candidate mean")
    surge_static=numeric(surge["static"]["mean_backlog"],"surge static mean")
    checks["surge_only_mean"]={
        "condition":surge_spec["condition"],"regime":surge_spec["regime"],"group":surge_spec["group"],
        "candidate_mean_backlog":surge_candidate,"static_mean_backlog":surge_static,
        "allowed_numerical_tolerance":tolerance,"passed":surge_candidate<=surge_static+tolerance,
    }
    for criterion_id in ("mean_vs_static_each_regime","static_relative_cvar_each_regime",
                         "tail_auc_each_regime","original_terminal_each_regime"):
        specification=advancement_specs[criterion_id]
        regime_results={}
        for regime in specification["regimes"]:
            data=summary["regimes"][regime]
            if criterion_id=="mean_vs_static_each_regime":
                evidence=data["comparisons"][candidate]["static"]
                lower=lower_interval(evidence["improvement_ci95"],regime+" versus static")
                result={"absolute_improvement_ci95":evidence["improvement_ci95"],
                        "reduction_pct":evidence["reduction_pct"],"passed":lower>0.0}
            elif criterion_id=="static_relative_cvar_each_regime":
                # Both risks are measured relative to Static. The candidate
                # versus V2 comparison's harm-CVaR is a DIFFERENT quantity.
                a=numeric(data["static_relative_harm"][candidate]["cvar90"],regime+" candidate static-relative CVaR")
                b=numeric(data["static_relative_harm"]["capacity_v2"]["cvar90"],regime+" V2 static-relative CVaR")
                both_zero=abs(a)<=tolerance and abs(b)<=tolerance
                result={"candidate_cvar90_vs_static":a,"v2_cvar90_vs_static":b,
                        "strict_reduction":a<b,"both_numerically_zero":both_zero,"passed":a<b or both_zero}
            else:
                metric="tail_auc" if criterion_id=="tail_auc_each_regime" else "end_backlog"
                a=numeric(data["overall"][candidate][metric],regime+" candidate "+metric)
                b=numeric(data["overall"]["capacity_v2"][metric],regime+" V2 "+metric)
                result={"metric":metric,"candidate_mean":a,"v2_mean":b,
                        "signed_excess":a-b,"allowed_numerical_tolerance":tolerance,"passed":a<=b+tolerance}
            regime_results[regime]=result
        checks[criterion_id]={"condition":specification["condition"],"regimes":regime_results,
                              "passed":all(result["passed"] for result in regime_results.values()),
                              "failed_regimes":[r for r,result in regime_results.items() if not result["passed"]]}

    # Scope and invariant checks refer to completed workflow evidence. Exact
    # physical replay remains reproducible through audit_v3 and the reviewer.
    scope_mapping={"scenarios":"scenarios","regime_count":"regimes",
                   "scenario_regime_pairs":"scenario_regime_pairs","episodes":"episodes",
                   "original_minutes":"original_minutes","followup_minutes":"followup_minutes","minutes":"total_minutes"}
    scope_ok=all(summary[k]==expected[v] for k,v in scope_mapping.items())
    row_expected={"scenarios_checked":expected["scenarios"],"regimes_checked":expected["regimes"],
                  "scenario_regime_pairs":expected["scenario_regime_pairs"],"episodes_checked":expected["episodes"],
                  "original_minutes":expected["original_minutes"],"followup_minutes":expected["followup_minutes"],
                  "simulated_minutes":expected["total_minutes"]}
    legacy=protocol["scenario_design"]["legacy_families"]
    generated=protocol["scenario_design"]["generated_families"]
    per_policy_minutes=len(legacy["groups"])*(legacy["horizon"]+protocol["followup"]["minutes"])
    per_policy_minutes+=len(generated["groups"])*(generated["horizon"]+protocol["followup"]["minutes"])
    replay_expected={"traces":(len(legacy["groups"])+len(generated["groups"]))*len(declared_regimes)*len(protocol["policies"]),
                     "minutes":per_policy_minutes*len(declared_regimes)*len(protocol["policies"])}
    expected_attempts={f"{regime}__{policy}":2 if regime=="instant" and policy==candidate else 1
                       for regime in declared_regimes for policy in protocol["policies"]}
    expected_attempts["comparison"]=1
    invariant_checks={
        "workflow_succeeded":verification["status"]=="SUCCEEDED",
        "summary_scope_matches_protocol":scope_ok,
        "row_audit_matches_protocol":verification["row_audit"]==row_expected,
        "declared_scope_matches_protocol":verification["expected_scope"]==expected,
        "mass_conservation":0<=numeric(summary["max_mass_error"],"maximum mass error")<=tolerance,
        "resource_conservation":0<=numeric(summary["max_budget_error"],"maximum resource error")<=tolerance,
        "prespecified_physical_replay_complete":all(verification["independent_trace_replay"][k]==v for k,v in replay_expected.items()),
        "statistics_check_recorded":bool(verification.get("independent_statistics_check")),
        "retry_attempts_match":verification["steps"]==expected_attempts,
        "duplicate_job_reused":verification["duplicate_submission_created"] is False,
        "restart_preserves_attempts":verification["restart_replay_preserved_attempts"] is True,
        "restart_preserves_report":verification["restart_replay_preserved_report"] is True,
        "corrupt_output_rejected":verification["negative_control"]["status"]=="FAILED" and
            verification["negative_control"]["steps"]=={"bad":"FAILED","blocked":"BLOCKED","independent":"SUCCEEDED"} and
            verification["negative_control"]["bad_error"]["type"]=="OutputValidationError",
    }
    test_checks={
        "declared_modules":tests.get("modules")==["test_lab","test_capacity","test_v3"],
        "tests_ran":type(tests.get("tests_run")) is int and tests["tests_run"]==42,
        "successful":tests.get("successful") is True,
        "no_failures_or_skips":all(tests.get(k)==0 for k in ("failures","errors","skipped","expected_failures","unexpected_successes")),
    }
    underload_by_regime={}
    work_fields=("mean_backlog","peak_backlog","end_backlog","tail_mean_backlog","tail_auc","tail_end_backlog",
                 "resource_minutes_lost","tail_resource_minutes_lost")
    moves_fields=("moves","tail_moves","total_moves")
    expected_seeds=set(protocol["scenario_design"][split]["scenario_seeds"])
    for regime,data in summary["regimes"].items():
        cases=[case for case in data["case_results"] if case["group"]=="generated_underload"]
        actual_seeds={case["scenario_seed"] for case in cases}
        complete=actual_seeds==expected_seeds and len(cases)==len(expected_seeds)
        failures=[]
        for case in cases:
            values=case[candidate]
            bad={field:values[field] for field in work_fields if abs(numeric(values[field],"underload "+field))>tolerance}
            bad.update({field:values[field] for field in moves_fields if numeric(values[field],"underload "+field)!=0.0})
            if bad:failures.append({"scenario_seed":case["scenario_seed"],"nonzero_metrics":bad})
        underload_by_regime[regime]={"cases_checked":len(cases),"expected_cases":len(expected_seeds),
                                    "scope_complete":complete,"failed_cases":failures,"passed":complete and not failures}
    checks["invariants_tests_underload"]={
        "condition":advancement_specs["invariants_tests_underload"]["condition"],
        "invariant_checks":invariant_checks,"test_checks":test_checks,"tests_run":tests.get("tests_run"),
        "underload":underload_by_regime,
        "passed":all(invariant_checks.values()) and all(test_checks.values()) and
            all(value["passed"] for value in underload_by_regime.values()),
    }
    passed=all(check["passed"] for check in checks.values())
    return {
        "version":"0.3.0","split":split,"candidate":candidate,
        "acceptance_criteria_sha256":DECLARED_ACCEPTANCE_SHA256,
        "evaluation_scope":"fresh validation" if split=="validation" else "known-case development; descriptive application of the declared rule only",
        "primary":primary,"noninferiority":noninferiority,
        "advancement_checks":checks,"advancement_passed":passed,
        "failed_criteria":[name for name,result in checks.items() if not result["passed"]],
        "decision":"meets_declared_engineering_conditions" if passed else "does_not_meet_declared_engineering_conditions",
        "scope":criteria["scope"],"reporting":criteria["reporting"],
        "interpretation":"A failed condition remains a failure; the rule is an intersection, not a vote. Development outcomes do not establish held-out success.",
    }


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out",type=Path,default=ROOT/"v3_results"/"validation",help="Existing result directory")
    parser.add_argument("--protocol",type=Path,help="Protocol JSON; defaults to frozen protocol when present, otherwise draft")
    parser.add_argument("--tests",type=Path,default=ROOT/"v3_results"/"unit_tests.json",help="Recorded combined-test JSON evidence")
    parser.add_argument("--report",type=Path,help="Optional decision JSON destination; otherwise print only")
    args=parser.parse_args()
    out=args.out.resolve()
    protocol_path=args.protocol or (ROOT/"v3_protocol.json" if (ROOT/"v3_protocol.json").is_file() else ROOT/"v3_protocol_draft.json")
    protocol=read_json(protocol_path);summary=read_json(out/"summary.json");verification=read_json(out/"verification.json")
    require(sha(out/"summary.json")==verification["summary_sha256"],"Summary checksum differs from workflow evidence")
    frozen=None
    if verification["split"]=="validation":
        from freeze_v3 import verify_frozen
        frozen=verify_frozen()
        require(frozen==verification["freeze_verification"],"Validation result belongs to another frozen experiment")
        require(sha(protocol_path)==frozen["protocol_sha256"],"Validation evaluation requires the exact frozen protocol")
    result=evaluate(summary,verification,protocol,read_json(args.tests))
    result["evaluation_metadata"]={
        "evaluator_file":Path(__file__).name,"evaluator_sha256":sha(__file__),
        "protocol_file":Path(protocol_path).name,"protocol_sha256":sha(protocol_path),
        "summary_sha256":sha(out/"summary.json"),"verification_sha256":sha(out/"verification.json"),
        "tests_evidence_sha256":sha(args.tests),"frozen_experiment_verified":frozen is not None,
    }
    encoded=json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)+"\n"
    if args.report is not None:
        target=args.report.resolve()
        require(target not in {Path(protocol_path).resolve(),(out/"summary.json").resolve(),
                              (out/"verification.json").resolve(),args.tests.resolve(),Path(__file__).resolve()},
                "Decision report would replace an input artifact")
        if target.exists():
            previous=read_json(target)
            require(isinstance(previous,dict) and "advancement_checks" in previous and "acceptance_criteria_sha256" in previous,
                    "Decision path contains a different artifact")
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(encoded,encoding="utf-8")
    print(encoded,end="")


if __name__=="__main__":
    main()
