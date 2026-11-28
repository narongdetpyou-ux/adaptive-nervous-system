"""The parameterised V6 evaluator: exact parity with v5_evaluate, point summary, H1/H2 and the declared population."""
import json
import unittest
from pathlib import Path

import test_v5_evaluate as synthetic
import v5_evaluate
from v6_evaluate import Gates, load_declaration

ROOT = Path(__file__).resolve().parent
V6 = load_declaration()
BASELINES = tuple(V6['policies']['baselines'])
FAMILIES = ('generated_underload', 'generated_switching_fault')
SEEDS = (1, 2, 3)
OFFSET = {'static': 0, 'v3_full': 20, 'capacity_v2': 10, 'capacity_balance': 5, 'v4_hybrid': 3, 'v5_full': -4, 'v6_full': -5}
DROPS = {'static': 80, 'v3_full': 70, 'capacity_v2': 60, 'capacity_balance': 45, 'v4_hybrid': 50, 'v5_full': 41, 'v6_full': 40}
# OOD: every adaptive policy is harmed against Static, V5 Full more than the candidate.
OOD_OFFSET = {'static': 0, 'v3_full': 20, 'capacity_v2': 10, 'capacity_balance': 12, 'v4_hybrid': 8, 'v5_full': 9, 'v6_full': 3}


def row(regime, policy, family, seed, end_gain=0.0, ood_harm=0.0):
    offset = OOD_OFFSET if regime.startswith('ood_') else OFFSET
    md = 100 + seed % 3 + offset[policy] + (ood_harm * seed if policy == 'v6_full' else 0)
    end = md * 2 - (end_gain * seed if policy == 'v6_full' else 0)
    values = {'regime': regime, 'policy': policy, 'group': family, 'scenario_seed': str(seed),
              'mean_deficit': md, 'total_dropped': DROPS[policy], 'end_deficit': end,
              'tail_auc_deficit': end * 15, 'total_moves': 3, 'max_mass_error': 0.0, 'max_budget_error': 0.0}
    if policy in ('v6_full', 'v5_full') and family == 'generated_underload':
        values.update(total_dropped=0.0, total_moves=0)
    return {k: str(v) for k, v in values.items()}


def rows(policies, end_gain=0.0, ood_harm=0.0):
    validation = [row(r, p, f, s, end_gain=end_gain) for r in V6['data']['regimes'] for p in policies
                  for f in FAMILIES for s in SEEDS]
    ood = [row(f'ood_{f}', p, f, s, ood_harm=ood_harm) for f in V6['ood']['families'] for p in policies for s in SEEDS]
    return validation, ood


class ParityTests(unittest.TestCase):
    def test_v5_declaration_and_candidate_reproduce_v5_evaluate(self):
        gates = Gates(json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text()), 'v5_full', v5_evaluate.POLICIES)
        scenarios = [
            (synthetic.validation_rows(), synthetic.ood_rows(), synthetic.GOOD),
            (synthetic.validation_rows({('legacy_instant', 'v5_full', f, s): {'end_deficit': 10_000}
                                        for f in synthetic.FAMILIES for s in synthetic.SEEDS}), synthetic.ood_rows(), synthetic.GOOD),
            (synthetic.validation_rows({('startup', 'v5_full', 'generated_underload', 2): {'total_moves': 1}}),
             synthetic.ood_rows(), synthetic.GOOD),
            (synthetic.validation_rows(), synthetic.ood_rows({(f, 'v5_full'): {'total_dropped': 1_000}
                                                             for f in v5_evaluate.OOD_FAMILIES}), synthetic.GOOD),
            (synthetic.validation_rows(), synthetic.ood_rows(), {**synthetic.GOOD, 'tamper_test': False}),
            (synthetic.validation_rows()[1:], synthetic.ood_rows(), synthetic.GOOD),
        ]
        for validation, ood, integrity in scenarios:
            expected = v5_evaluate.evaluate(validation, ood, integrity)
            mine = gates.evaluate(validation, ood, integrity, integrity_names=v5_evaluate.INTEGRITY)
            expected.pop('version'); mine.pop('version')
            self.assertEqual(mine, expected)


class V6Tests(unittest.TestCase):
    def test_point_summary_counts_every_declared_check(self):
        policies = BASELINES + ('v6_full',)
        validation, ood = rows(policies)
        summary = Gates(V6, 'v6_full', policies).point_summary(validation, ood)
        self.assertEqual(summary['failed_point_checks'], 0)
        worse = [dict(r, end_deficit=str(float(r['end_deficit']) + 10_000))
                 if r['policy'] == 'v6_full' and r['regime'] == 'legacy_instant' else r for r in validation]
        summary = Gates(V6, 'v6_full', policies).point_summary(worse, ood)
        self.assertEqual(summary['failed_loss_endpoint_checks'],
                         [['legacy_instant', 'capacity_v2', 'end_deficit'], ['legacy_instant', 'capacity_balance', 'end_deficit']])
        self.assertEqual(summary['failed_point_checks'], 2)

    def test_round_hypotheses_against_v5_full(self):
        policies = BASELINES + ('v6_full',)
        gates = Gates(V6, 'v6_full', policies)
        better = gates.hypotheses(*rows(policies, end_gain=40.0, ood_harm=0.0))
        self.assertTrue(better['H1_drain']['supported'])
        self.assertEqual(len(better['H1_drain']['checks']), 6)
        self.assertTrue(better['H2_restraint']['supported'])
        worse = gates.hypotheses(*rows(policies, end_gain=-40.0, ood_harm=10.0))
        self.assertFalse(worse['H1_drain']['supported'])
        self.assertFalse(worse['H2_restraint']['supported'])

    def test_declared_v6_population(self):
        population = Gates(V6, 'v6_full', BASELINES + ('v6_full',)).declared_population()
        self.assertEqual(len(population['validation']), 704)
        self.assertEqual({s for _, s in population['validation']}, set(range(7000, 7064)))
        self.assertEqual(population['ood'], tuple(range(8100, 8164)))

    def test_stratified_draws_equal_the_frozen_v4_draws_for_the_original_families(self):
        import numpy as np
        from v4_metrics import _bootstrap_indices
        from v4_scenarios import GROUPS
        from v6_evaluate import stratified_indices
        labels = [GROUPS[i % len(GROUPS)] for i in range(53)]
        self.assertTrue(np.array_equal(stratified_indices(labels, GROUPS, 40, 6026028),
                                       _bootstrap_indices(np.array(labels), 40, 6026028)))
        ood = [V6['ood']['families'][i % 6] for i in range(30)]
        draws = stratified_indices(ood, V6['ood']['families'], 40, 6026028)
        self.assertEqual(draws.shape, (40, 30))
        for line in draws:
            self.assertEqual(sorted(ood[i] for i in line), sorted(ood))

    def test_candidate_must_be_evaluated(self):
        with self.assertRaises(ValueError):
            Gates(V6, 'v6_full', BASELINES)


if __name__ == '__main__':
    unittest.main()
