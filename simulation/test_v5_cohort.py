import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from v4_metrics import CORE_METRICS
from v4_scenarios import GROUPS, REGIMES
from v5_development import study_arms
from v5_development_cohort import DROP, METRICS, middle_switch, read_seed, summarize


class DevelopmentCohortTests(unittest.TestCase):
    def test_duplicate_and_missing_episodes(self):
        row = dict.fromkeys((*CORE_METRICS, 'max_mass_error', 'max_budget_error'), 0)
        row.update(regime=REGIMES[0], group=GROUPS[0], policy='static', action_space='variant',
                   scenario_seed=2000, drain_censored='False')
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'rows.csv'
            for count, reason in [(1, 'Incomplete paired data'), (2, 'duplicate episode')]:
                with path.open('w', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=row)
                    writer.writeheader()
                    writer.writerows([row] * count)
                with self.assertRaisesRegex(ValueError, reason):
                    read_seed(path, 2000)

    def test_switch_window_excludes_prior_and_later_moves(self):
        trace = [{'t': t, 'applied_delta': [0, 0, 0], 'staff_before': [69]*3,
                  'dropped_by_stage': [0, 3, 0]} for t in range(60)]
        for t in (38, 59):
            trace[t]['applied_delta'] = [-5, 5, 0]
        self.assertIsNone(middle_switch(trace)['first_reinforcement_minute'])
        trace[48]['applied_delta'] = [-5, 5, 0]
        self.assertEqual(middle_switch(trace)['first_reinforcement_minute'], 48)
        self.assertEqual(middle_switch(trace)['middle_stage_dropped_in_window'], 60)

    def test_bootstrap_keeps_case_pairing_and_family_weights(self):
        seeds = (2001, 2002)
        cases = len(GROUPS)*len(seeds)
        data = {(regime, arm): np.zeros((cases, len(METRICS)))
                for regime in REGIMES for arm in study_arms()}
        full = data['delayed_noisy', ('v5_full', 'variant')]
        full[:, DROP] = np.tile([100, 10000], len(GROUPS))
        v4 = data['delayed_noisy', ('v4_hybrid', 'variant')]
        v4[:, DROP] = full[:, DROP] + np.repeat(np.arange(len(GROUPS)), len(seeds))
        report = summarize(data, seeds)
        effect = report['delayed_noisy_effects']['v4_hybrid_minus_v5_drop']
        self.assertEqual(effect['ci95'], [5, 5])
        self.assertEqual(effect['mean'], 5)
        for row in report['delayed_noisy_family_drops']:
            if row['baseline'] == 'v4_hybrid':
                expected = GROUPS.index(row['group'])
                self.assertEqual(row['ci95'], [expected, expected])


if __name__ == '__main__':
    unittest.main()
