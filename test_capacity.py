"""Meaningful regression checks for causal capacity control and its verifier."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from audit_capacity import audit_rows, audit_source_integrity, audit_trace, independent_policy_means


def run_observed_policy(arrivals, true_rates, mode="catalog", config=None):
    """Test driver: true rates are confined to service after each decision."""
    from capacity_policy import CapacityAwarePolicy
    policy = CapacityAwarePolicy(mode=mode, uniforms=np.linspace(.01,.99,len(arrivals)), config=config)
    queue = np.zeros(3)
    staff = np.full(3, 69.0)
    history = []
    records = []
    for t, arrival in enumerate(arrivals):
        queue[0] += arrival
        before_abc = None if policy.abc is None else (
            policy.abc.core.last_change, policy.abc.core.current_alloc.copy())
        delta, note = policy.decide(t, queue.copy(), history, staff.copy(), float(arrival))
        staff += delta
        flow = np.zeros(3)
        for stage in range(3):
            flow[stage] = min(queue[stage], staff[stage] * true_rates[t,stage])
            queue[stage] -= flow[stage]
            if stage < 2:
                queue[stage+1] += flow[stage]
        policy.observe(t, staff.copy(), flow.copy(), queue.copy())
        history = (history + [queue.copy()])[-5:]
        after_abc = None if policy.abc is None else (
            policy.abc.core.last_change, policy.abc.core.current_alloc.copy())
        records.append(dict(t=t, delta=delta.copy(), note=note, staff=staff.copy(),
                            queue=queue.copy(), before_abc=before_abc, after_abc=after_abc))
    return policy, records


def accounting_fixture():
    """Two hand-calculated minutes: 300 enter, 138 leave, 162 remain."""
    row = dict(group="hand_calculation", scenario_seed=0, policy="static", policy_seed=None,
               mean_backlog=96.5, peak_backlog=162.0, end_backlog=162.0,
               arrivals=300.0, completed=138.0, moves=0,
               max_mass_error=0.0, max_budget_error=0.0)
    minutes = [
        dict(t=0, arrival=100.0, observed=[100.0, 0.0, 0.0], productivity=[1.0]*3,
             staff=[69.0]*3, delta=[0.0]*3, flow=[69.0]*3, queue=[31.0, 0.0, 0.0],
             completed_cumulative=69.0, branch="fixed"),
        dict(t=1, arrival=200.0, observed=[231.0, 0.0, 0.0], productivity=[1.0]*3,
             staff=[69.0]*3, delta=[0.0]*3, flow=[69.0]*3, queue=[162.0, 0.0, 0.0],
             completed_cumulative=138.0, branch="fixed"),
    ]
    scenario = SimpleNamespace(group="hand_calculation", seed=0,
                               arrivals=np.array([100.0, 200.0]), productivity=np.ones((2,3)))
    return dict(summary=row, minutes=minutes), scenario


class AccountingAuditTests(unittest.TestCase):
    def test_upstream_sources_are_byte_identical(self):
        self.assertEqual(len(audit_source_integrity(Path(__file__).resolve().parent)), 11)

    def test_independent_replay_matches_hand_calculation(self):
        rep, env = accounting_fixture()
        check = audit_trace(rep, env.arrivals, env.productivity)
        self.assertEqual(check["minutes_replayed"], 2)
        self.assertEqual(check["max_mass_error"], 0.0)
        self.assertEqual(audit_rows([rep["summary"]], [env], {"static":[None]})["episodes_checked"], 1)

    def test_verifier_rejects_corrupted_physical_state(self):
        rep, env = accounting_fixture()
        for field in ("arrival", "observed", "flow", "queue", "staff", "productivity", "completed_cumulative"):
            with self.subTest(field=field):
                bad = copy.deepcopy(rep)
                if isinstance(bad["minutes"][1][field], list):
                    bad["minutes"][1][field][0] += 1.0
                else:
                    bad["minutes"][1][field] += 1.0
                with self.assertRaises(ValueError):
                    audit_trace(bad, env.arrivals, env.productivity)

    def test_verifier_rejects_corrupted_summary_and_scope(self):
        rep, env = accounting_fixture()
        bad = copy.deepcopy(rep["summary"])
        bad["completed"] += 1.0
        with self.assertRaisesRegex(ValueError, "Lost work"):
            audit_rows([bad], [env], {"static":[None]})
        for rows in ([], [rep["summary"], rep["summary"]]):
            with self.assertRaisesRegex(ValueError, "Missing, duplicated"):
                audit_rows(rows, [env], {"static":[None]})

    def test_policy_replicates_do_not_change_case_weight(self):
        rep, _ = accounting_fixture()
        first = rep["summary"]
        second = {**first, "scenario_seed":1, "mean_backlog":1000.0}
        rows = [{**first, "policy_seed":s} for s in (11,29,47)] + [second]
        self.assertEqual(independent_policy_means(rows)["static"]["mean_backlog"], (96.5 + 1000.0) / 2)


class BenchmarkDesignTests(unittest.TestCase):
    def test_splits_are_reproducible_and_seed_disjoint(self):
        from capacity_scenarios import SPLIT_SEEDS, GROUPS, generate_scenarios
        self.assertFalse(set(SPLIT_SEEDS["development"]) & set(SPLIT_SEEDS["validation"]))
        one = generate_scenarios("development")
        two = generate_scenarios("development")
        self.assertEqual(len(one), len(GROUPS) * len(SPLIT_SEEDS["development"]))
        for left, right in zip(one, two):
            self.assertEqual((left.group, left.seed), (right.group, right.seed))
            np.testing.assert_array_equal(left.arrivals, right.arrivals)
            np.testing.assert_array_equal(left.productivity, right.productivity)
            self.assertFalse(left.arrivals.flags.writeable)
            self.assertFalse(left.productivity.flags.writeable)

    @staticmethod
    def metric_fixture(zero=False):
        from capacity_scenarios import GROUPS, POLICIES, policy_seeds
        rows = []
        for group in GROUPS:
            for case_seed in (1, 2):
                for policy in POLICIES:
                    for index, policy_seed in enumerate(policy_seeds(policy)):
                        value = 0.0 if zero else (80.0 if policy == "capacity_aware" else 100.0)
                        # Unequal draws would bias pooling; case averaging must
                        # recover exactly 100 for each stochastic policy.
                        if not zero and len(policy_seeds(policy)) == 3:
                            value += (index - 1) * 10.0
                        rows.append(dict(group=group, scenario_seed=case_seed, policy=policy,
                                         policy_seed=policy_seed, mean_backlog=value,
                                         peak_backlog=value, end_backlog=value,
                                         completed=1000.0-value, moves=0))
        return rows

    def test_paired_bootstrap_preserves_constant_improvement(self):
        from capacity_metrics import aggregate
        from capacity_scenarios import GROUPS
        summary = aggregate(self.metric_fixture(), bootstrap_draws=100)
        pair = summary["capacity_aware_vs"]["abc"]
        self.assertEqual(pair["wins"], len(GROUPS) * 2)
        self.assertEqual(pair["losses"], 0)
        self.assertAlmostEqual(pair["reduction_pct"], 20.0)
        np.testing.assert_allclose(pair["improvement_ci95"], [20.0, 20.0])
        np.testing.assert_allclose(pair["reduction_pct_ci95"], [20.0, 20.0])
        self.assertAlmostEqual(summary["overall"]["abc"]["mean_backlog"], 100.0)

    def test_zero_baseline_has_no_percentage_claim(self):
        from capacity_metrics import aggregate
        summary = aggregate(self.metric_fixture(zero=True), bootstrap_draws=50)
        pair = summary["capacity_aware_vs"]["static"]
        self.assertIsNone(pair["reduction_pct"])
        self.assertIsNone(pair["reduction_pct_ci95"])
        self.assertEqual(pair["wins"], 0)
        self.assertEqual(pair["losses"], 0)
        self.assertEqual(pair["improvement_ci95"], [0.0, 0.0])


class CapacityPolicyTests(unittest.TestCase):
    def test_censored_flow_is_lower_bound_not_capacity_collapse(self):
        from capacity_policy import RateEstimator
        estimator = RateEstimator()
        estimator.update(0, [69]*3, [1000]*3, [0]*3)
        np.testing.assert_allclose(estimator.snapshot(), [200]*3)
        estimator.update(1, [69]*3, [6900,1000,1000], [200,0,0])
        np.testing.assert_allclose(estimator.snapshot(), [100,200,200])
        self.assertEqual(estimator.last_saturated.tolist(), [1,-1,-1])
        estimator.update(2, [69]*3, [690]*3, [0]*3)
        # No measurement of recovery: the explicitly configured prior
        # reversion yields 110; observed 10 is merely the current lower bound.
        np.testing.assert_allclose(estimator.snapshot(), [110,200,200])
        self.assertEqual(estimator.lower_bound.tolist(), [10]*3)
        copy_of_rate = estimator.snapshot()
        copy_of_rate[:] = 0
        self.assertTrue((estimator.snapshot() > 0).all())

    def test_zero_resources_do_not_imply_zero_productivity(self):
        from capacity_policy import RateEstimator
        estimator = RateEstimator()
        estimator.update(0, [0,100,107], [0,900,900], [1,0,0])
        self.assertEqual(estimator.snapshot()[0], 200.0)
        self.assertEqual(estimator.last_saturated[0], -1)
        with self.assertRaisesRegex(ValueError, "without resources"):
            estimator.update(1, [0,100,107], [1,900,900], [1,0,0])

    def test_prediction_does_not_double_count_current_arrival(self):
        from capacity_policy import prediction_costs
        allocations = [[69,69,69],[60,78,69]]
        rates = np.ones((1,2,3))
        got = prediction_costs([300,0,0], allocations, rates, [[999999,200]])
        np.testing.assert_allclose(got, [[387.0,405.0]])
        np.testing.assert_array_equal(got, prediction_costs([300,0,0], allocations, rates, [[0,200]]))

    def test_decision_cannot_use_current_or_future_hidden_rates(self):
        arrivals = np.full(32, 10000.0)
        rates_one = np.full((32,3), 200.0)
        rates_two = rates_one.copy()
        rates_one[12:,1] = 40.0
        rates_two[12:,2] = 350.0
        for mode in ("catalog","abc_only"):
            with self.subTest(mode=mode):
                _, left = run_observed_policy(arrivals, rates_one, mode)
                _, right = run_observed_policy(arrivals, rates_two, mode)
                # At decision 12 the new rates have not yet been observed.
                for a, b in zip(left[:13], right[:13]):
                    np.testing.assert_array_equal(a["delta"], b["delta"])
                    self.assertEqual(a["note"], b["note"])

    def test_underload_holds_and_rejected_abc_does_not_commit(self):
        arrivals = np.full(24, 4000.0)
        rates = np.full((24,3), 200.0)
        for mode in ("catalog","abc_only"):
            with self.subTest(mode=mode):
                _, records = run_observed_policy(arrivals, rates, mode)
                self.assertTrue(all(not np.any(r["delta"]) for r in records))
                self.assertTrue(all(not np.any(r["queue"]) for r in records))
                if mode == "abc_only":
                    selected_proposals = [r for r in records if r["note"].get("abc_proposal",{}).get("branch") == "selected"]
                    self.assertTrue(selected_proposals)
                    for r in selected_proposals:
                        self.assertEqual(r["note"]["branch"], "model_rejected")
                        self.assertEqual(r["before_abc"][0], r["after_abc"][0])
                        np.testing.assert_array_equal(r["before_abc"][1], r["after_abc"][1])

    def test_accepted_moves_respect_contract_and_commit_abc_state(self):
        from abc_service.frozen import catalog
        permitted = {tuple(a["delta"]) for a in catalog()["actions"]}
        arrivals = np.full(64, 11000.0)
        arrivals[:10] = 5000.0
        rates = np.full((64,3), 200.0)
        rates[10:36,1] = 75.0
        for mode in ("catalog","abc_only"):
            with self.subTest(mode=mode):
                _, records = run_observed_policy(arrivals, rates, mode)
                moves = [r for r in records if np.any(r["delta"])]
                self.assertTrue(moves)
                self.assertGreaterEqual(moves[0]["t"], 5)
                self.assertTrue(all(b["t"] - a["t"] >= 8 for a,b in zip(moves, moves[1:])))
                for r in records:
                    self.assertAlmostEqual(float(sum(r["staff"])), 207.0)
                    self.assertGreaterEqual(float(min(r["staff"])), 10.0)
                    if np.any(r["delta"]):
                        self.assertIn(tuple(r["delta"]), permitted)
                        self.assertGreater(r["note"]["predicted_mean_gain"], r["note"]["minimum_mean_gain"])
                        self.assertGreaterEqual(r["note"]["predicted_worst_gain"], -1e-7)
                        if mode == "abc_only":
                            self.assertEqual(r["after_abc"][0], r["t"])
                            np.testing.assert_array_equal(r["after_abc"][1], r["staff"]-69)
                    elif mode == "abc_only":
                        self.assertEqual(r["before_abc"][0], r["after_abc"][0])
                        np.testing.assert_array_equal(r["before_abc"][1], r["after_abc"][1])

    def test_observation_order_and_dwell_forecast_are_enforced(self):
        from capacity_policy import CapacityAwarePolicy, CapacityConfig
        with self.assertRaisesRegex(ValueError, "entire dwell"):
            CapacityConfig(lookahead=7)
        policy = CapacityAwarePolicy()
        with self.assertRaisesRegex(ValueError, "consecutive"):
            policy.decide(1,[0]*3,[],[69]*3,0)
        policy.decide(0,[0]*3,[],[69]*3,0)
        with self.assertRaisesRegex(ValueError, "Missing previous"):
            policy.decide(1,[0]*3,[[0]*3],[69]*3,0)
        policy.observe(0,[69]*3,[0]*3,[0]*3)
        with self.assertRaisesRegex(ValueError, "once"):
            policy.observe(0,[69]*3,[0]*3,[0]*3)


if __name__ == "__main__":
    unittest.main()
