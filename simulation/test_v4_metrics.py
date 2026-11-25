"""Focused statistical/accounting tests; no reserved scenarios are generated."""
import copy
import math
import unittest

import numpy as np

from evaluate_v4 import evaluate, INTEGRITY_FLAGS, load_acceptance
from v4_metrics import (
    CORE_METRICS, aggregate, empirical_cvar, _row_metric_values, _effect,
)
from v4_scenarios import GROUPS, LEGACY_GROUPS, REGIMES, POLICIES, SPLIT_SEEDS


def _fixture_row(regime,group,seed,policy,deficit):
    if group=="generated_underload":deficit=0.
    row={metric:0. for metric in CORE_METRICS}
    row.update({
        "regime":regime,"group":group,"scenario_seed":seed,"policy":policy,"policy_seed":None,
        "source":"legacy_flash" if group in LEGACY_GROUPS else "generated_synthetic",
        "horizon":60 if group in LEGACY_GROUPS else 96,"followup_minutes":32,
        "mean_deficit":float(deficit),"peak_deficit":float(deficit),"end_deficit":float(deficit),
        "mean_queue":float(deficit),"end_queue":float(deficit),
        "tail_mean_deficit":deficit/2,"tail_auc_deficit":16*deficit,
        "offered":1000.,"completed":1000.-deficit,"tail_completed":float(deficit),"total_completed":1000.,
        "drain_time_capped":16. if deficit else 0.,"drain_censored":False,
        "peak_service_queue_max_over_full_run":float(deficit),
        "max_mass_error":0.,"max_budget_error":0.,
    })
    return row


def _fixture_rows():
    scores={"static":30.,"capacity_v2":20.,"capacity_balance":20.,"v3_full":50.,
            "v4_hybrid":10.,"v4_no_risk":15.,"v4_no_recovery":25.}
    return [_fixture_row(regime,group,seed,policy,scores[policy])
            for regime in REGIMES for group in GROUPS
            for seed in SPLIT_SEEDS["development"] for policy in POLICIES]


class V4MetricTests(unittest.TestCase):
    def test_fractional_cvar_boundary_has_exact_ten_percent_mass(self):
        self.assertAlmostEqual(empirical_cvar(list(range(11))), (10.+.1*9.)/1.1)
        self.assertEqual(empirical_cvar([0.]*11),0.)

    def test_invalid_cvar_input_rejected(self):
        for values in ([],[1.,math.nan],[[1.,2.]]):
            with self.assertRaises(ValueError):empirical_cvar(values)
        with self.assertRaises(ValueError):empirical_cvar([1.],0.)

    def test_dropped_work_stays_in_deficit_but_is_not_retained_drainage(self):
        row=_fixture_row(REGIMES[0],GROUPS[0],2000,"static",10.)
        row.update({"mean_queue":0.,"end_queue":0.,"dropped":10.,"total_dropped":10.,
                    "tail_completed":0.,"total_completed":990.,"tail_mean_deficit":10.,
                    "tail_auc_deficit":320.,"tail_end_deficit":10.,"drain_time_capped":0.})
        values=_row_metric_values(row)
        self.assertEqual(values["mean_deficit"],10.)
        self.assertEqual(values["tail_end_deficit"],10.)
        self.assertEqual(values["drain_uncleared_fraction"],0.)
        row["end_deficit"]=0.
        with self.assertRaises(ValueError):_row_metric_values(row)

    def test_boolean_and_nonfinite_metric_rejected(self):
        row=_fixture_row(REGIMES[0],GROUPS[0],2000,"static",10.)
        for bad in (True,float("inf"),float("nan")):
            invalid=dict(row,mean_deficit=bad)
            with self.assertRaises(ValueError):_row_metric_values(invalid)

    def test_completion_effect_has_correct_direction(self):
        result=_effect(np.array([110.]),np.array([100.]),np.array([110.,110.]),np.array([100.,100.]),higher=True)
        self.assertEqual(result["mean_improvement"],10.)
        self.assertEqual(result["wins"],1)
        self.assertEqual(result["direction"],"higher_is_better")

    def test_zero_baseline_relative_effect_is_undefined(self):
        result=_effect(np.array([0.]),np.array([0.]),np.array([0.,0.]),np.array([0.,0.]))
        self.assertIsNone(result["reduction_pct"])
        self.assertIsNone(result["reduction_pct_ci95"])
        self.assertEqual(result["mean_improvement"],0.)

    def test_point_only_subset_and_missing_pair_validation(self):
        policies=("static","v4_hybrid")
        rows=[_fixture_row(REGIMES[0],GROUPS[0],2000,p,20. if p=="static" else 10.) for p in policies]
        result=aggregate(rows,policies=policies,candidates=("v4_hybrid",),baselines=("static",),bootstrap_draws=0)
        comparison=result["regimes"][REGIMES[0]]["comparisons"]["v4_hybrid"]["static"]
        self.assertEqual(comparison["mean_improvement"],10.)
        self.assertIsNone(comparison["improvement_ci95"])
        self.assertIsNone(comparison["mean_noninferiority"]["allowance_slack_ci95"])
        self.assertEqual(result["analysis"]["interval_mode"],"point_only_development")
        with self.assertRaises(ValueError):
            aggregate(rows[:1],policies=policies,candidates=("v4_hybrid",),baselines=("static",),bootstrap_draws=0)


class V4AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # This deterministic arithmetic fixture is not a simulation or a
        # development outcome. It exercises every declared evaluation cell.
        cls.rows=_fixture_rows()
        cls.summary=aggregate(cls.rows)
        cls.integrity={name:True for name in INTEGRITY_FLAGS}

    def test_complete_synthetic_acceptance_fixture_passes_exact_rule(self):
        result=evaluate(self.summary,self.rows,integrity=self.integrity,split="development")
        self.assertTrue(result["advancement_passed"])
        self.assertAlmostEqual(result["primary"]["risk_reduction"],20.)
        self.assertEqual(result["underload"]["episodes_checked"],48)
        test=self.summary["regimes"][REGIMES[0]]["comparisons"]["v4_hybrid"]["capacity_v2"]["mean_noninferiority"]
        # Ten of the eleven equal-weight families have 1.05*20-10 = 11.
        self.assertAlmostEqual(test["allowance_slack"],10.)
        self.assertAlmostEqual(test["allowance_slack_ci95"][0],10.)

    def test_missing_integrity_flag_fails(self):
        evidence=dict(self.integrity);del evidence["source_and_sensor_boundary_passed"]
        result=evaluate(self.summary,self.rows,integrity=evidence)
        self.assertFalse(result["advancement_passed"])
        self.assertIn("integrity",result["failed_criteria"])

    def test_zero_primary_ci_lower_bound_does_not_pass(self):
        summary=copy.deepcopy(self.summary)
        ci=summary["regimes"]["delayed_noisy"]["static_harm_comparisons"]["v4_hybrid"]["v3_full"]["cvar90"]["risk_reduction_ci95"]
        ci[0]=0.
        result=evaluate(summary,self.rows,integrity=self.integrity)
        self.assertFalse(result["primary"]["passed"])
        self.assertIn("primary_final_regime_risk_superiority",result["failed_criteria"])

    def test_original_tail_and_drop_guard_cannot_be_omitted(self):
        declared,_=load_acceptance()
        self.assertEqual(set(declared["loss_and_endpoint_point_nonworsening"]["metrics"]),
                         {"total_dropped","end_deficit","tail_auc_deficit"})
        altered=copy.deepcopy(declared);altered["mean_noninferiority"]["margin_fraction"]=.10
        with self.assertRaises(ValueError):
            evaluate(self.summary,self.rows,integrity=self.integrity,acceptance=altered)

    def test_row_summary_corruption_is_rejected(self):
        summary=copy.deepcopy(self.summary)
        summary["regimes"][REGIMES[0]]["overall"]["static"]["total_dropped"]+=1.
        with self.assertRaises(ValueError):evaluate(summary,self.rows,integrity=self.integrity)

    def test_all_declared_cells_required(self):
        with self.assertRaises(ValueError):evaluate(self.summary,self.rows[1:],integrity=self.integrity)


if __name__=="__main__":
    unittest.main()
