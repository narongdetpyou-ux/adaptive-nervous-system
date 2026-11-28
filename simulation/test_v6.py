"""V6 mechanisms: exact V5 parity without features, drain arithmetic, restraint signals, causality, underload, seed guards."""
import dataclasses
import tempfile
import unittest

import numpy as np

import v5_grid
import v5_ood
import v6_grid
import v6_simulation as sim
from v4_environment import REGIME_CONFIGS
from v4_policy import forecast_costs as v4_forecast
from v4_scenarios import REGIMES
from v5_simulation import simulate_case
from v6_forecast import forecast_costs, stage_weights
from v6_policy import V6Config, V6Policy
from v6_restraint import RestraintMonitor


def _case(seed, family):
    return next(c for c in v5_grid._build_cases((seed,)) if c.group == family)


def _strip(trace):
    """Traces without the variant label, which is the only intended difference."""
    return [{k: ({kk: vv for kk, vv in v.items() if kk != 'variant'} if k == 'decision_evidence' else v)
             for k, v in record.items()} for record in trace]


class ForecastTests(unittest.TestCase):
    def test_zero_drain_weight_is_exactly_the_v4_forecast(self):
        rng = np.random.default_rng(7)
        for env in list(REGIME_CONFIGS.values()) + [v5_ood.family_config('compound_stress')]:
            for _ in range(10):
                h, actions, scenarios = int(rng.integers(1, 33)), int(rng.integers(1, 6)), int(rng.integers(1, 12))
                queues, old = rng.uniform(0, 50000, 3), np.full(3, 69.0)
                alloc = old + rng.integers(-20, 21, (actions, 3)).astype(float)
                alloc -= (alloc.sum(axis=1, keepdims=True) - 207) / 3
                rates, arrivals = rng.uniform(80, 260, (scenarios, h, 3)), rng.uniform(0, 15000, (scenarios, h))
                a = v4_forecast(queues, old, alloc, rates, arrivals, env, 0.5)
                b = forecast_costs(queues, old, alloc, rates, arrivals, env, 0.5, 0.0)
                for key in ('cost', 'terminal_deficit', 'terminal_retained', 'new_drops'):
                    self.assertTrue(np.array_equal(a[key], b[key]), (env.name, key))

    def test_drain_counts_retained_work_by_remaining_stage_visits(self):
        self.assertEqual(stage_weights(2.0).tolist(), [3.0, 2.0, 1.0])
        env = REGIME_CONFIGS['legacy_instant']
        staff = np.full(3, 69.0)
        out = forecast_costs([300., 200., 100.], staff, staff[None, :], np.zeros((1, 1, 3)), np.zeros((1, 1)), env, 0.5, 2.0)
        self.assertAlmostEqual(float(out['terminal_deficit'][0, 0]), 600.0)
        self.assertAlmostEqual(float(out['terminal_weighted'][0, 0]), 300 * 3 + 200 * 2 + 100)
        with self.assertRaises(ValueError):
            forecast_costs([1., 1., 1.], staff, staff[None, :], np.zeros((1, 1, 3)), np.zeros((1, 1)), env, 0.5, -1.0)


class ParityTests(unittest.TestCase):
    def test_v6_without_features_reproduces_v5_full_exactly(self):
        for family in ('generated_sustained_overload', 'generated_pulsed_short_fault'):
            for regime in ('legacy_instant', 'delayed_noisy'):
                case = _case(2000, family)
                a, ta = simulate_case('v5_full', case, regime=regime, detailed=True)
                b, tb = sim.simulate('v6_base', case, regime=regime, detailed=True)
                self.assertEqual(a, {**b, 'policy': 'v5_full'}, (family, regime))
                self.assertEqual(_strip(ta), _strip(tb), (family, regime))
        ood = v5_ood.build_case('transfer_failure', 9000)
        a, ta = v5_ood.run_episode('v5_full', ood, detailed=True)
        b, tb = sim.run_ood('v6_base', ood, detailed=True)
        self.assertEqual(a, {**b, 'policy': 'v5_full'})
        self.assertEqual(_strip(ta), _strip(tb))

    def test_rows_carry_the_v6_label_and_baselines_are_untouched(self):
        case = _case(2000, 'generated_switching_fault')
        row, _ = sim.simulate('v6_full', case, regime='delayed_noisy')
        self.assertEqual(row['policy'], 'v6_full')
        self.assertEqual(sim.simulate('v4_hybrid', case, regime='delayed_noisy'),
                         simulate_case('v4_hybrid', case, regime='delayed_noisy'))
        with self.assertRaises(ValueError):
            sim.simulate('v4_hybrid', case, regime='delayed_noisy', config=object())


class RestraintTests(unittest.TestCase):
    def test_reversal_bounce_switching_and_memory(self):
        env = REGIME_CONFIGS['delayed_noisy']
        monitor = RestraintMonitor(env, dwell=8, memory=16)
        self.assertEqual(monitor.reach, 3 + 2 + 8)
        monitor.note_detector([(10, 1)])
        monitor.note_detector([(10, 1), (14, 1)])
        self.assertEqual(monitor.reasons(14 + env.sensor_lag + 4, False), ['reversal'])
        self.assertEqual(monitor.reasons(14 + env.sensor_lag + 17, False), [])
        hurdle = RestraintMonitor(env, dwell=8, memory=16, detector_switching=True, fast_switching=False)
        self.assertEqual(hurdle.reasons(20, True), ['switching'])
        slow = RestraintMonitor(env, dwell=8, memory=16)
        slow.note_detector([(10, 1), (30, 1)])
        self.assertEqual(slow.reasons(34, False), [])
        moves = RestraintMonitor(env, dwell=8, memory=16)
        moves.record_move(20, [69., 69., 69.], [10., -10., 0.])
        moves.note_staff(23, [69., 69., 69.])
        self.assertEqual(moves.bounces, [])
        moves.note_staff(24, [74., 59., 74.])
        self.assertEqual(moves.bounces, [24])
        self.assertEqual(moves.reasons(30, False), ['bounce'])
        arrived = RestraintMonitor(env, dwell=8, memory=16)
        arrived.record_move(20, [69., 69., 69.], [10., -10., 0.])
        arrived.note_staff(24, [79., 59., 69.])
        self.assertEqual(arrived.bounces, [])

    def test_fast_switching_needs_two_short_intervals(self):
        env = REGIME_CONFIGS['delayed_noisy']
        fast = RestraintMonitor(env, dwell=8, memory=16)
        fast.note_detector([(10, 0), (15, 1), (20, 2), (25, 0)])
        self.assertEqual(fast.fast_switches, [25])
        self.assertIn('fast_switching', fast.reasons(25 + env.sensor_lag + 1, False))
        slow = RestraintMonitor(env, dwell=8, memory=16)
        slow.note_detector([(10, 0), (30, 1), (50, 2), (70, 0)])
        self.assertEqual(slow.fast_switches, [])
        hurdle = RestraintMonitor(env, dwell=8, memory=16, detector_switching=True, fast_switching=False)
        hurdle.note_detector([(10, 0), (15, 1), (20, 2), (25, 0)])
        self.assertEqual(hurdle.reasons(28, False), [])
        self.assertEqual(hurdle.reasons(28, True), ['switching'])

    def test_neutral_restraint_moves_toward_the_even_split_then_holds(self):
        config = dataclasses.replace(V6Config(), restraint_neutral=1.0, restraint_max_transfer=1e9)
        # Seed 9001: restraint starts at [44, 104, 59], far from the even split.
        ood = v5_ood.build_case('rapid_switching', 9001)
        _, trace = sim.run_ood('v6_restraint', ood, detailed=True, config=config)
        neutral = [r for r in trace if r['branch'] == 'v6_restrained_neutral']
        self.assertTrue(neutral)
        for record in neutral:
            evidence = record['decision_evidence']
            self.assertLess(evidence['neutral_gap_after'], evidence['neutral_gap_before'])
        self.assertTrue(any(r['branch'] == 'v6_restrained_hold' for r in trace))
        with self.assertRaises(ValueError):
            V6Policy(REGIME_CONFIGS['delayed_noisy'], 'v6_full', dataclasses.replace(V6Config(), restraint_neutral=0.5))

    def test_restraint_sees_failed_transfers_and_restrains(self):
        ood = v5_ood.build_case('transfer_failure', 9000)
        _, trace = sim.run_ood('v6_restraint', ood, detailed=True)
        reasons = {x for record in trace for x in record['decision_evidence'].get('restraint', [])}
        self.assertIn('bounce', reasons)
        self.assertTrue(any(record['branch'].startswith('v6_restrained') for record in trace))


class CausalityTests(unittest.TestCase):
    def test_decisions_replay_from_recorded_observations_only(self):
        case = _case(2001, 'shifting_bottleneck')
        env = REGIME_CONFIGS['delayed_noisy']
        for variant in sim.VARIANTS:
            _, trace = sim.simulate(variant, case, regime='delayed_noisy', detailed=True)
            controller = V6Policy(env, variant)
            for record in trace:
                d, f = record['decision_input'], record['feedback_input']
                if d['source_t'] is not None:
                    self.assertEqual(d['observation_age'], env.sensor_lag)
                raw, _ = controller.decide(d['delivery_t'], np.array(d['queues']), [np.array(h) for h in d['history']],
                                           np.array(d['committed_staff']), d['arrival'])
                self.assertTrue(np.allclose(raw, record['raw_proposal_delta'], rtol=0, atol=1e-12), (variant, record['t']))
                controller.observe(record['t'], np.array(f['effective_staff']), np.array(f['flow']), np.array(f['end_queue']))


class UnderloadTests(unittest.TestCase):
    def test_variants_never_move_or_drop_under_underload(self):
        case = _case(2002, 'generated_underload')
        for regime in REGIMES:
            for variant in sim.VARIANTS:
                row, _ = sim.simulate(variant, case, regime=regime)
                self.assertLessEqual(row['total_dropped'], 1e-6, (regime, variant))
                self.assertEqual(row['total_moves'], 0, (regime, variant))


class SeedGuardTests(unittest.TestCase):
    def test_held_out_seeds_are_refused(self):
        for seed in (7000, 7063, 8100, 8163):
            with self.assertRaises(ValueError):
                v6_grid.check_open((seed,))
        self.assertEqual(v6_grid.check_open((6000, 8000, 5100, 8200)), (6000, 8000, 5100, 8200))

    def test_selection_seeds_need_a_committed_plan(self):
        if v6_grid.selection_open():
            self.skipTest('v6_selection_plan.json is committed')
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(RuntimeError):
            v6_grid.run('selection', 'grid', 1, output=tmp)


if __name__ == '__main__':
    unittest.main()
