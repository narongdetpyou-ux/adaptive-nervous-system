"""The declared V5 grids: case identity, closed ranges, ranking order and resumption."""
import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

import v5_grid
from v4_scenarios import GROUPS, REGIMES, generate_scenarios
from v5_development import run_pair, study_arms


class CaseIdentityTests(unittest.TestCase):
    def test_explicit_cases_match_the_frozen_generator(self):
        for split, seeds in (('development', range(2000, 2008)), ('validation', range(4000, 4064))):
            with self.subTest(split=split):
                frozen = [c.manifest() for c in generate_scenarios(split)]
                explicit = [c.manifest() for c in v5_grid.explicit_cases(tuple(seeds))]
                self.assertEqual(explicit, frozen)

    def test_one_task_matches_the_development_runner(self):
        task = ('development', 2000, 'generated_switching_fault', 'delayed_noisy')
        _, rows = v5_grid.run_task(task)
        self.assertEqual(rows, run_pair((2000, 'generated_switching_fault', 'delayed_noisy')))


class SplitTests(unittest.TestCase):
    def test_declared_splits(self):
        self.assertEqual(v5_grid.SPLITS['selection'], tuple(range(5000, 5016)))
        self.assertEqual(v5_grid.SPLITS['development'], tuple(range(2000, 2008)) + tuple(range(4000, 4064)))
        self.assertEqual(len(v5_grid.CONFIGS), 12)

    def test_closed_ranges_are_refused(self):
        for seed in (6000, 6063, 8000, 8063):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                v5_grid.check_open([seed])
        with self.assertRaises(ValueError):
            v5_grid.explicit_cases((6010,))
        with self.assertRaises(ValueError):
            v5_grid.run('selection', 1, seeds=(2000,))


def synthetic(dropped):
    """Every arm and regime at zero, except delayed_noisy total_dropped from `dropped`."""
    data = {}
    for regime in REGIMES:
        for policy, space in study_arms():
            matrix = np.zeros((4, len(v5_grid.METRICS)))
            if regime == 'delayed_noisy':
                matrix[:, v5_grid.METRICS.index('total_dropped')] = dropped.get(policy, 10.)
            data[regime, policy, space] = matrix
    return data


class RankingTests(unittest.TestCase):
    def test_ties_fall_back_to_configuration_id(self):
        self.assertEqual(v5_grid.rank(synthetic({}))[0]['config'], 'v4_hybrid')

    def test_delayed_noisy_drops_break_ties(self):
        self.assertEqual(v5_grid.rank(synthetic({'v5_full': 5.}))[0]['config'], 'v5_full')

    def test_loss_failures_come_first(self):
        ranking = v5_grid.rank(synthetic({'v5_full': 5., 'v5_b': 20.}))
        by_config = {r['config']: r for r in ranking}
        self.assertEqual(by_config['v5_b']['loss_endpoint_failures'], 2)
        self.assertEqual(ranking[-1]['config'], 'v5_b')
        self.assertEqual(ranking[0]['config'], 'v5_full')


class ResumeTests(unittest.TestCase):
    def test_complete_seed_file_is_not_recomputed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = v5_grid.seed_path('selection', 5000, tmp)
            path.parent.mkdir(parents=True)
            with path.open('w', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['regime'])
                writer.writerows([['x']] * v5_grid.ROWS_PER_SEED)
            before = path.read_bytes()
            v5_grid.run('selection', 1, output=tmp, seeds=(5000,))
            self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
