"""Independent physical and sensor-boundary tests for the prespecified v0.4 model.

Only hand-built fixtures and known development cases are used.  This module
does not generate or evaluate the reserved v0.4 validation cases.
"""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from audit_v4 import (
    ReferenceEnvironment, audit_physical_records, reference_projection,
    retained_clearance, vector_close, audit_trace, audit_rows,
    audit_controller_replay, audit_observation_delivery,
)
from v4_environment import Env, REGIME_CONFIGS, project_action
from v4_scenarios import Scenario, generate_sensor_noise, expected_counts


HOLD = [0.0, 0.0, 0.0]


def fixture_records(regime, minutes=40):
    """Unrelated deterministic audit data with finite-buffer overflow and moves."""
    env = Env(regime)
    records = []
    scheduled = {5: [5, -2.5, -2.5], 13: [-3, 6, -3],
                 21: [-30, 0, 30], 29: [20, -15, -5]}
    for t in range(minutes):
        arrival = 45000.0 if t < 12 else 22000.0 if t < 25 else 0.0
        rates = [115.0 if t < 20 else 210.0, 170.0, 95.0 if 12 <= t < 24 else 220.0]
        env.begin(t, arrival)
        records.append(env.service(t, scheduled.get(t, HOLD), rates))
    return records


class V4PhysicsTests(unittest.TestCase):
    def test_six_regimes_match_independent_scalar_reference(self):
        for regime in REGIME_CONFIGS:
            with self.subTest(regime=regime):
                result = audit_physical_records(fixture_records(regime), REGIME_CONFIGS[regime])
                self.assertEqual(result["minutes"], 40)
                self.assertEqual(result["moves"], 4)
                self.assertEqual(result["future_pending_resource_minutes"], 0)
                self.assertEqual(result["future_startup_capacity_minutes"], 0)

    def test_integer_projection_preserves_budget_and_stage_tie_break(self):
        cfg = REGIME_CONFIGS["integer_transit3"]
        actual = project_action([69, 69, 69], [5, -2.5, -2.5], cfg)
        self.assertEqual(actual.tolist(), [5, -2, -3])
        self.assertEqual(reference_projection([69, 69, 69], [5, -2.5, -2.5], cfg.as_dict()),
                         actual.tolist())
        self.assertEqual(sum(np.array([69, 69, 69]) + actual), 207)
        with self.assertRaises(ValueError):
            project_action([69, 69, 69], actual, cfg)  # projected action is not raw catalogue

    def test_raw_reserve_checked_before_rounding(self):
        cfg = REGIME_CONFIGS["integer_transit3"]
        # The raw 9.5 reserve is invalid even though a projection might reach10.
        with self.assertRaises(ValueError):
            project_action([12, 69, 126], [-2.5, 5, -2.5], cfg)
        with self.assertRaises(ValueError):
            reference_projection([12, 69, 126], [-2.5, 5, -2.5], cfg.as_dict())

    def test_resource_transit_then_startup_has_exact_paid_loss(self):
        env, records = Env("startup"), []
        for t in range(13):
            env.begin(t, 100000)
            records.append(env.service(t, [6, -3, -3] if t == 5 else HOLD, [1, 1, 1]))
        self.assertEqual([records[t]["pending_resource_minutes"] for t in range(5, 11)],
                         [6, 6, 6, 0, 0, 0])
        self.assertEqual([records[t]["startup_capacity_minutes"] for t in range(5, 11)],
                         [0, 0, 0, 3, 1.5, 0])
        self.assertEqual(records[8]["active_staff"], [75, 66, 66])
        self.assertEqual(records[8]["effective_staff"], [72, 66, 66])
        self.assertEqual(records[9]["effective_staff"], [73.5, 66, 66])
        self.assertEqual(records[10]["effective_staff"], [75, 66, 66])
        for row in records:
            self.assertAlmostEqual(207 - sum(row["effective_staff"]),
                                   row["pending_resource_minutes"] + row["startup_capacity_minutes"])
            self.assertLessEqual(row["flow"][0], row["effective_staff"][0])
        audit = audit_physical_records(records, REGIME_CONFIGS["startup"])
        self.assertEqual(audit["observed_pending_resource_minutes"], 18)
        self.assertEqual(audit["observed_startup_capacity_minutes"], 4.5)

    def test_truncated_trace_keeps_future_transition_exposure(self):
        records = fixture_records("startup", 6)
        result = audit_physical_records(records, REGIME_CONFIGS["startup"])
        self.assertEqual(result["observed_pending_resource_minutes"], 5)
        self.assertEqual(result["future_pending_resource_minutes"], 10)
        self.assertEqual(result["observed_startup_capacity_minutes"], 0)
        self.assertEqual(result["future_startup_capacity_minutes"], 3.75)

    def test_hold_never_resets_activation_or_dwell(self):
        env = Env("startup")
        for t in range(14):
            env.begin(t, 0)
            delta = [6, -3, -3] if t in (5, 13) else HOLD
            row = env.service(t, delta, [200] * 3)
            if t == 8:
                self.assertIsNone(row["ready_at"])
                self.assertEqual(row["pending_staff"], HOLD)
        self.assertEqual(env.last_move, 13)
        self.assertEqual(env.committed.tolist(), [81, 63, 63])

    def test_invalid_actions_do_not_change_resource_ledger(self):
        env = Env("startup")
        env.begin(0, 100)
        with self.assertRaises(ValueError):
            env.service(0, [6, -3, -3], [200] * 3)
        np.testing.assert_array_equal(env.committed, [69] * 3)
        self.assertEqual(env.last_move, -10**9)
        row = env.service(0, HOLD, [200] * 3)
        self.assertEqual(row["committed_staff"], [69] * 3)
        for t in range(1, 7):
            env.begin(t, 100)
            if t == 6:
                before = env.committed.copy()
                with self.assertRaises(ValueError):
                    env.service(t, [-3, 6, -3], [200] * 3)
                np.testing.assert_array_equal(env.committed, before)
            env.service(t, [6, -3, -3] if t == 5 else HOLD, [200] * 3)

    def test_pipeline_requires_two_forwarding_intervals(self):
        env = Env("finite_pipeline")
        rows = []
        for t in range(3):
            env.begin(t, 100 if t == 0 else 0)
            rows.append(env.service(t, HOLD, [200] * 3))
        self.assertEqual([r["completed_cumulative"] for r in rows], [0, 0, 100])
        self.assertEqual(rows[0]["inflight_work"], [0, 100, 0])
        self.assertEqual(rows[1]["inflight_work"], [0, 0, 100])
        self.assertEqual(rows[0]["queue"], HOLD)
        self.assertEqual(rows[0]["retained_work"], 100)

    def test_finite_admission_drops_at_destination_not_blocking(self):
        cfg = replace(REGIME_CONFIGS["finite_pipeline"], buffer_capacities=(100, 10, 5))
        env, rows = Env(cfg), []
        for t in range(3):
            env.begin(t, 150 if t == 0 else 0)
            rows.append(env.service(t, HOLD, [200] * 3))
        self.assertEqual(rows[0]["flow"], [100, 0, 0])
        self.assertEqual(rows[0]["dropped_by_stage"], [50, 0, 0])
        self.assertEqual(rows[1]["dropped_by_stage"], [0, 90, 0])
        self.assertEqual(rows[2]["dropped_by_stage"], [0, 0, 5])
        self.assertEqual(rows[2]["completed_cumulative"], 5)
        self.assertEqual(rows[2]["retained_work"], 0)
        self.assertEqual(rows[2]["drop_adjusted_deficit"], 145)
        self.assertEqual(rows[2]["cumulative_drops_by_stage"], [50, 90, 5])
        self.assertEqual(retained_clearance([r["retained_work"] for r in rows], 1), (2, False))
        audit_physical_records(rows, cfg)

    def test_corrupt_physical_output_is_rejected(self):
        rows = fixture_records("delayed_noisy")
        for field in ("flow", "pending_staff", "effective_staff", "inflight_work",
                      "cumulative_drops", "drop_adjusted_deficit", "startup_capacity_minutes"):
            with self.subTest(field=field):
                corrupt = deepcopy(rows)
                if isinstance(corrupt[8][field], list):
                    corrupt[8][field][0] += 1
                else:
                    corrupt[8][field] += 1
                with self.assertRaises(ValueError):
                    audit_physical_records(corrupt, REGIME_CONFIGS["delayed_noisy"])

    def test_begin_record_is_detached_from_simulation_state(self):
        env = Env("finite_pipeline")
        record = env.begin(0, 100)
        record["start_queue"][0] = 999999
        record["staff_before"][0] = 0
        row = env.service(0, HOLD, [200] * 3)
        self.assertEqual(row["start_queue"], [100, 0, 0])
        self.assertEqual(row["staff_before"], [69] * 3)

    def test_invalid_service_order_or_nonfinite_truth_is_rejected(self):
        env = Env("legacy_instant")
        with self.assertRaises(ValueError):
            env.service(0, HOLD, [200] * 3)
        with self.assertRaises(ValueError):
            env.begin(1, 1)
        with self.assertRaises(ValueError):
            env.begin(0, float("nan"))
        env.begin(0, 1)
        with self.assertRaises(ValueError):
            env.begin(1, 1)
        with self.assertRaises(ValueError):
            env.service(0, HOLD, [200, float("inf"), 200])
        env.service(0, HOLD, [200] * 3)
        with self.assertRaises(ValueError):
            env.service(0, HOLD, [200] * 3)


class V4ScenarioTests(unittest.TestCase):
    def test_common_noise_reproducible_and_readonly(self):
        first = generate_sensor_noise("audit_fixture", 7, 52)
        second = generate_sensor_noise("audit_fixture", 7, 52)
        different = generate_sensor_noise("audit_fixture", 8, 52)
        for name in first:
            np.testing.assert_array_equal(first[name], second[name])
            self.assertFalse(np.array_equal(first[name], different[name]))
            self.assertFalse(first[name].flags.writeable)
            self.assertTrue(np.all(first[name] > 0))
        self.assertFalse(np.array_equal(first["end_queue"], first["flow"]))

    def test_scenario_copies_truth_and_sensor_arrays(self):
        arrivals, rates = np.full(20, 1000.), np.full((20, 3), 200.)
        noise = {k: v.copy() for k, v in generate_sensor_noise("audit_fixture", 7, 52).items()}
        scenario = Scenario("audit_fixture", 7, arrivals, rates, "hand_built", {}, noise)
        arrivals[0] = rates[0, 0] = noise["flow"][0, 0] = 999999
        self.assertEqual(scenario.arrivals[0], 1000)
        self.assertEqual(scenario.productivity[0, 0], 200)
        self.assertLess(scenario.sensor_noise["flow"][0, 0], 2)
        for values in (scenario.arrivals, scenario.productivity, *scenario.sensor_noise.values()):
            self.assertFalse(values.flags.writeable)

    def test_reserved_scope_count_without_generating_holdout(self):
        result = expected_counts("validation")
        self.assertEqual(result["scenarios"], 704)
        self.assertEqual(result["scenario_regime_pairs"], 4224)
        self.assertEqual(result["episodes"], 29568)
        self.assertEqual(result["policy_draws_per_case"], 7)
        self.assertEqual(result["total_minutes"], result["original_minutes"] + result["followup_minutes"])


class V4PolicyTests(unittest.TestCase):
    def test_candidate_forecast_pays_pipeline_drop_transit_and_startup(self):
        from v4_policy import forecast_costs
        for regime, cfg in REGIME_CONFIGS.items():
            with self.subTest(regime=regime):
                initial = ReferenceEnvironment(cfg)
                for t in range(5):
                    initial.begin(t, 43000)
                    initial.service(t, HOLD, [170, 120, 190])
                pre = initial.begin(5, 31000)
                old_staff = pre["staff_before"]
                proposals = [HOLD, [5, -2.5, -2.5], [20, -15, -5]]
                allocations = [np.array(old_staff) + reference_projection(old_staff, p, cfg.as_dict())
                               for p in proposals]
                arrivals = np.array([[31000 if h < 7 else 15000 for h in range(16)]], dtype=float)
                rates = np.array([[[160 if h < 9 else 200, 135, 175] for h in range(16)]], dtype=float)
                predicted = forecast_costs(pre["start_queue"], old_staff, allocations, rates, arrivals, cfg, .5)
                for j, proposal in enumerate(proposals):
                    ref, deficits = deepcopy(initial), []
                    sunk_drops = sum(ref.drops)
                    for h in range(16):
                        if h:
                            ref.begin(5 + h, arrivals[0, h])
                        row = ref.service(5 + h, proposal if h == 0 else HOLD, rates[0, h])
                        deficits.append(row["drop_adjusted_deficit"] - sunk_drops)
                    expected = sum(deficits) / 16 + .5 * deficits[-1]
                    self.assertAlmostEqual(predicted["cost"][0, j], expected, places=7)
                    self.assertAlmostEqual(predicted["terminal_deficit"][0, j], deficits[-1], places=7)
                    self.assertAlmostEqual(predicted["new_drops"][0, j], sum(ref.drops) - sunk_drops, places=7)

    def test_stale_low_rate_anchor_expires_at_declared_age(self):
        from v4_policy import HybridPolicy
        policy = HybridPolicy(REGIME_CONFIGS["legacy_instant"])
        policy.estimator.rate[:] = [180, 180, 180]
        policy.last_busy_rate[:] = [70, 70, 70]
        policy.last_busy_t[:] = 0
        policy.last_flow_bound[:] = [60, 60, 60]
        before = policy.bounds(policy.config.stale_anchor_minutes)
        after = policy.bounds(policy.config.stale_anchor_minutes + 1)
        np.testing.assert_allclose(before[0], [59.5] * 3)
        np.testing.assert_allclose(after[0], [135.] * 3)
        np.testing.assert_array_equal(before[1], after[1])

    @staticmethod
    def ready_policy(mode="hybrid"):
        from v4_policy import HybridPolicy
        policy = HybridPolicy(REGIME_CONFIGS["legacy_instant"], mode=mode)
        history = []
        for t in range(5):
            policy.decide(t, [20000, 5000, 2000], history[-5:], [69] * 3, 10000)
            policy.observe(t, [69] * 3, [7000, 12000, 13000], [9000, 4000, 1000])
            history.append([9000, 4000, 1000])
        return policy, history

    def test_recovery_hurdle_uses_flow_scale_not_accumulated_queue(self):
        first, history = self.ready_policy()
        second = deepcopy(first)
        _, a = first.decide(5, [20000, 10000, 5000], history, [69] * 3, 10000)
        _, b = second.decide(5, [2000000, 1000000, 500000], history, [69] * 3, 10000)
        self.assertEqual(a["gain_hurdle"], b["gain_hurdle"])
        self.assertLessEqual(a["gain_hurdle"], 100.)
        self.assertEqual(a["forecast_horizon"], 16)
        self.assertEqual(a["forecast_models"], 33)

    def test_no_risk_zeroes_stress_penalty_only(self):
        policies = [self.ready_policy(mode)[0] for mode in ("hybrid", "no_risk")]
        lower0, upper0, _, _ = policies[0].bounds(5)
        lower1, upper1, _, _ = policies[1].bounds(5)
        np.testing.assert_array_equal(lower0, lower1)
        np.testing.assert_array_equal(upper0, upper1)
        r0, a0 = policies[0].forecasts(lower0, upper0)
        r1, a1 = policies[1].forecasts(lower1, upper1)
        np.testing.assert_array_equal(r0, r1)
        np.testing.assert_array_equal(a0, a1)
        history = [[9000, 4000, 1000]] * 5
        _, note = policies[1].decide(5, [20000, 10000, 5000], history, [69] * 3, 10000)
        self.assertEqual(note["effective_risk_weight"], 0)
        self.assertEqual(note["adjusted_gain"], note["predicted_mean_gain"])

    def test_pressure_escape_disabled_in_no_recovery_ablation(self):
        hybrid, history = self.ready_policy("hybrid")
        ablated, _ = self.ready_policy("no_recovery")
        _, full_note = hybrid.decide(5, [20000, 10000, 5000], history, [69] * 3, 10000)
        _, ablated_note = ablated.decide(5, [20000, 10000, 5000], history, [69] * 3, 10000)
        self.assertLess(full_note["escape_factor"], 1)
        self.assertEqual(ablated_note["escape_factor"], 1)
        self.assertGreater(ablated_note["gain_hurdle"], full_note["gain_hurdle"])


class V4ObservationTests(unittest.TestCase):
    @staticmethod
    def case(horizon=24):
        return Scenario("audit_causal_fixture", 7, np.full(horizon, 22000.),
                        np.full((horizon, 3), 200.), "hand_built", {})

    def test_hidden_future_changes_cannot_change_observable_prefix(self):
        from v4_simulation import simulate_case
        first = self.case()
        arrivals, rates = first.arrivals.copy(), first.productivity.copy()
        arrivals[13:] = 6000
        rates[12:] = [70, 250, 180]
        second = Scenario(first.group, first.seed, arrivals, rates, first.source, {}, first.sensor_noise)
        for regime, inclusive_end in (("legacy_instant", 12), ("delayed_noisy", 14)):
            for policy in ("capacity_v2", "capacity_balance", "v3_full", "v4_hybrid", "v4_no_risk", "v4_no_recovery"):
                with self.subTest(regime=regime, policy=policy):
                    _, a = simulate_case(policy, first, regime=regime, detailed=True)
                    _, b = simulate_case(policy, second, regime=regime, detailed=True)
                    self.assertNotEqual(a[12]["true_productivity"], b[12]["true_productivity"])
                    for t in range(inclusive_end + 1):
                        self.assertEqual(a[t]["decision_input"], b[t]["decision_input"])
                        self.assertEqual(a[t]["raw_proposal_delta"], b[t]["raw_proposal_delta"])
                        self.assertEqual(a[t]["decision_evidence"], b[t]["decision_evidence"])

    def test_lagged_flow_uses_source_startup_exposure_and_source_noise(self):
        from v4_simulation import simulate_case
        class Scripted:
            def decide(self, t, queues, history, staff, arrival):
                return np.array([6, -3, -3] if t == 5 else HOLD), {"branch": "scripted"}
            def observe(self, t, staff, flow, queue):
                pass
        scenario = self.case()
        with patch("v4_simulation.make_controller", return_value=Scripted()):
            row, trace = simulate_case("capacity_balance", scenario, regime="delayed_noisy", detailed=True)
        audit_trace({"summary": row, "minutes": trace}, scenario, "delayed_noisy")
        audit_controller_replay({"summary": row, "minutes": trace}, Scripted())
        self.assertEqual(trace[10]["feedback_input"]["source_t"], 8)
        self.assertEqual(trace[10]["feedback_input"]["effective_staff"], [72, 66, 66])
        self.assertEqual(trace[10]["active_staff"], [75, 66, 66])
        np.testing.assert_array_equal(trace[10]["feedback_input"]["flow"],
                                      np.array(trace[8]["flow"]) * scenario.sensor_noise["flow"][8])
        for t in (0, 1):
            self.assertIsNone(trace[t]["decision_input"]["source_t"])
            self.assertEqual(trace[t]["decision_input"]["queues"], HOLD)
            self.assertEqual(trace[t]["feedback_input"]["flow"], HOLD)
        corrupt = deepcopy(trace)
        corrupt[10]["feedback_input"]["effective_staff"] = corrupt[10]["active_staff"]
        with self.assertRaises(ValueError):
            audit_observation_delivery(corrupt, scenario, REGIME_CONFIGS["delayed_noisy"])

    def test_full_wrapper_replay_and_corrupt_row_rejection(self):
        from v4_simulation import simulate_case, make_controller
        scenario = self.case()
        row, trace = simulate_case("v4_hybrid", scenario, regime="delayed_noisy", detailed=True)
        rep = {"summary": row, "minutes": trace}
        self.assertEqual(audit_trace(rep, scenario, "delayed_noisy")["minutes_replayed"], 56)
        self.assertEqual(audit_controller_replay(rep, make_controller("v4_hybrid", "delayed_noisy"))["decisions_replayed"], 56)
        audit_rows([row], [scenario], {"v4_hybrid": (None,)}, regimes=("delayed_noisy",))
        corrupt = dict(row, completed=row["completed"] + 1)
        with self.assertRaises(ValueError):
            audit_rows([corrupt], [scenario], {"v4_hybrid": (None,)}, regimes=("delayed_noisy",))
        with self.assertRaises(ValueError):
            audit_rows([row, row], [scenario], {"v4_hybrid": (None,)}, regimes=("delayed_noisy",))

    def test_legacy_wrappers_reproduce_known_v3_policies_exactly(self):
        from v4_simulation import simulate_case
        from v3_simulation import simulate_case as old_simulate
        from v4_scenarios import generate_scenarios
        known = generate_scenarios("development")
        selected = [s for s in known if s.seed == 2000 and s.group in
                    ("surge_only", "generated_switching_fault", "generated_underload")]
        self.assertEqual(len(selected), 3)
        mappings = {"mean_queue": "mean_backlog", "end_queue": "end_backlog",
                    "tail_auc_queue": "tail_auc", "tail_end_queue": "tail_end_backlog",
                    "completed": "completed", "total_completed": "total_completed",
                    "moves": "moves", "total_moves": "total_moves",
                    "resource_minutes_lost": "resource_minutes_lost"}
        for scenario in selected:
            for regime, old_regime in (("legacy_instant", "instant"), ("legacy_transit3", "transit_3")):
                for policy in ("static", "capacity_v2", "capacity_balance", "v3_full"):
                    with self.subTest(case=scenario.group, regime=regime, policy=policy):
                        new_row, new_trace = simulate_case(policy, scenario, regime=regime, detailed=True)
                        old_row, old_trace = old_simulate(policy, scenario, regime=old_regime, detailed=True)
                        for new_field, old_field in mappings.items():
                            self.assertEqual(new_row[new_field], old_row[old_field])
                        # Offered-completed and sum(queue) differ by rounding
                        # at about1e-10 despite exactly identical trajectories.
                        for new_field, old_field in (("mean_deficit", "mean_backlog"),
                                                     ("end_deficit", "end_backlog"),
                                                     ("tail_auc_deficit", "tail_auc"),
                                                     ("tail_end_deficit", "tail_end_backlog")):
                            self.assertAlmostEqual(new_row[new_field], old_row[old_field], delta=1e-6)
                        for new_minute, old_minute in zip(new_trace, old_trace):
                            for field in ("delta", "flow", "queue", "active_staff", "pending_staff", "committed_staff",
                                          "completed_cumulative", "branch", "decision_evidence"):
                                self.assertEqual(new_minute[field], old_minute[field])


if __name__ == "__main__":
    unittest.main()
