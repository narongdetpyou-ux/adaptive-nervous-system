"""Declared OOD families: generators, transfer-failure physics, pairing and observation boundary."""
import json
import unittest

import numpy as np

import v5_ood
from v4_environment import REGIME_CONFIGS
from v4_scenarios import generate_scenarios
from v5_simulation import simulate_case

POLICIES = tuple(v5_ood.PLAN['policies'])
SMOKE = v5_ood.SMOKE_SEEDS[0]


def canonical(value):
    return json.dumps(value, sort_keys=True, default=lambda x: x.tolist() if hasattr(x, 'tolist') else float(x))


class GeneratorTests(unittest.TestCase):
    def test_cases_are_deterministic_and_well_formed(self):
        for family in v5_ood.FAMILIES:
            with self.subTest(family=family):
                a, b = v5_ood.build_case(family, SMOKE), v5_ood.build_case(family, SMOKE)
                self.assertEqual(canonical(a.manifest()), canonical(b.manifest()))
                self.assertEqual(a.scenario.arrivals.shape, (96,))
                self.assertEqual(a.scenario.productivity.shape, (96, 3))
                self.assertTrue(np.all(a.scenario.productivity > 0))
                other = v5_ood.build_case(family, SMOKE + 1)
                self.assertNotEqual(canonical(a.manifest()), canonical(other.manifest()))

    def test_reserved_and_validation_seeds_are_refused(self):
        for seed in (8000, 8063, 6000, 6063):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                v5_ood.build_case('rapid_switching', seed)

    def test_declared_family_shapes(self):
        for seed in v5_ood.SMOKE_SEEDS:
            rapid = v5_ood.build_case('rapid_switching', seed).metadata['segments']
            self.assertEqual(rapid[0][1], 12)
            self.assertEqual(rapid[-1][2], 84)
            for stage, start, end in rapid[:-1]:
                self.assertTrue(5 <= end - start <= 9)
            self.assertTrue(1 <= rapid[-1][2] - rapid[-1][1] <= 9)
            for previous, current in zip(rapid, rapid[1:]):
                self.assertEqual(previous[2], current[1])
                self.assertNotEqual(previous[0], current[0])
        for seed in v5_ood.SMOKE_SEEDS:
            windows = v5_ood.build_case('transfer_failure', seed).windows
            (a1, b1), (a2, b2) = windows
            self.assertTrue(15 <= a1 <= 40 and 12 <= b1 - a1 <= 20)
            self.assertTrue(a2 >= b1 + 4 and b2 <= 80 and 12 <= b2 - a2 <= 20)
        self.assertEqual(v5_ood.family_config('buffer_cascade').buffer_capacities, v5_ood.SMALL_BUFFERS)
        self.assertEqual(v5_ood.family_config('rapid_switching').buffer_capacities,
                         REGIME_CONFIGS['delayed_noisy'].buffer_capacities)

    def test_sensor_degradation_multipliers(self):
        noise = v5_ood.build_case('sensor_degradation', SMOKE).scenario.sensor_noise
        logs = np.log(noise['end_queue'][:, 0])
        self.assertGreater(np.std(logs[30:70]), 2 * np.std(np.r_[logs[:30], logs[70:]]))
        plain = v5_ood.build_case('rapid_switching', SMOKE).scenario.sensor_noise
        self.assertLess(np.std(np.log(plain['end_queue'][:, 0])), 0.1)


class TransferFailureTests(unittest.TestCase):
    def drive(self, windows, move_at=10):
        config = v5_ood.family_config('transfer_failure')
        env = v5_ood.TransferFailureEnv(config, windows)
        for t in range(30):
            env.begin(t, 1000.0)
            delta = [-30.0, 30.0, 0.0] if t == move_at else [0.0, 0.0, 0.0]
            record = env.service(t, delta, [200.0, 200.0, 200.0])
            self.assertAlmostEqual(sum(record['committed_staff']), 207.0)
            self.assertLessEqual(record['max_mass_error'], 1e-6)
        return env

    def test_move_inside_window_bounces_half_back_to_the_donor(self):
        env = self.drive(((5, 20),))
        self.assertEqual(env.failure_events, [{'move_t': 10, 'arrival_t': 13, 'failed': [0.0, 15.0, 0.0],
                                               'returned': [15.0, 0.0, 0.0]}])
        np.testing.assert_array_equal(env.committed, [54.0, 84.0, 69.0])
        np.testing.assert_array_equal(env.committed, env.active)

    def test_move_outside_window_is_unchanged(self):
        env = self.drive(((20, 30),))
        self.assertEqual(env.failure_events, [])
        np.testing.assert_array_equal(env.committed, [39.0, 99.0, 69.0])


class EpisodeTests(unittest.TestCase):
    def test_every_family_and_policy_runs_with_shared_shocks(self):
        for family in v5_ood.FAMILIES:
            case = v5_ood.build_case(family, SMOKE)
            identity = canonical(case.manifest())
            for policy in POLICIES:
                with self.subTest(family=family, policy=policy):
                    row, trace = v5_ood.run_episode(policy, case, detailed=True)
                    self.assertEqual(row['regime'], f'ood_{family}')
                    self.assertLessEqual(row['max_mass_error'], 1e-6)
                    self.assertLessEqual(row['max_budget_error'], 1e-6)
                    self.assertEqual(canonical(case.manifest()), identity)
                    for record in trace:
                        decision = record['decision_input']
                        if decision['source_t'] is not None:
                            self.assertEqual(decision['observation_age'], case.config.sensor_lag)
                            self.assertEqual(decision['source_t'], record['t'] - case.config.sensor_lag)

    def test_default_simulation_path_is_unchanged(self):
        case = [c for c in generate_scenarios('development') if c.group == 'generated_switching_fault'][0]
        for policy in ('v5_full', 'capacity_balance'):
            for regime in ('legacy_instant', 'delayed_noisy'):
                with self.subTest(policy=policy, regime=regime):
                    default, _ = simulate_case(policy, case, regime=regime)
                    explicit, _ = simulate_case(policy, case, environment=REGIME_CONFIGS[regime])
                    self.assertEqual(default, explicit)


if __name__ == '__main__':
    unittest.main()
