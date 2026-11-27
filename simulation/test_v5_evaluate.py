"""Gate logic of v5_evaluate on synthetic rows: PASS, each breach, and missing evidence."""
import unittest

import v5_evaluate as ev

FAMILIES = ('generated_underload', 'generated_switching_fault')
SEEDS = (1, 2, 3)
OFFSET = {'static': 0, 'v3_full': 20, 'capacity_v2': 10, 'capacity_balance': 5, 'v4_hybrid': 3, 'v5_full': -5}
DROPS = {'static': 80, 'v3_full': 70, 'capacity_v2': 60, 'capacity_balance': 45, 'v4_hybrid': 50, 'v5_full': 40}


def row(regime, policy, family, seed, **overrides):
    md = 100 + seed % 3 + OFFSET[policy]
    values = {'regime': regime, 'policy': policy, 'group': family, 'scenario_seed': str(seed),
              'mean_deficit': md, 'total_dropped': DROPS[policy], 'end_deficit': md * 2,
              'tail_auc_deficit': md * 30, 'total_moves': 3, 'max_mass_error': 0.0, 'max_budget_error': 0.0}
    if policy == 'v5_full' and family == 'generated_underload':
        values.update(total_dropped=0.0, total_moves=0)
    values.update(overrides)
    return {k: str(v) for k, v in values.items()}


def validation_rows(special=None):
    special = special or {}
    return [row(r, p, f, s, **special.get((r, p, f, s), {}))
            for r in ev.REGIMES for p in ev.POLICIES for f in FAMILIES for s in SEEDS]


def ood_rows(special=None):
    special = special or {}
    return [row(f'ood_{f}', p, f, s, **special.get((f, p), {}))
            for f in ev.OOD_FAMILIES for p in ev.POLICIES for s in SEEDS]


GOOD = {name: True for name in ev.INTEGRITY}


class DecisionTests(unittest.TestCase):
    def test_everything_good_passes(self):
        record = ev.evaluate(validation_rows(), ood_rows(), GOOD)
        self.assertEqual(record['decision'], 'PASS', record['failed_gates'])
        self.assertEqual(len(record['original']['loss_endpoint_point_nonworsening']['checks']), 36)
        self.assertEqual(len(record['original']['risk_point_nonworsening']['checks']), 24)
        self.assertEqual(len(record['original']['mean_noninferiority']['checks']), 12)

    def test_loss_endpoint_breach_fails(self):
        special = {('legacy_instant', 'v5_full', f, s): {'end_deficit': 10_000} for f in FAMILIES for s in SEEDS}
        record = ev.evaluate(validation_rows(special), ood_rows(), GOOD)
        self.assertEqual(record['decision'], 'FAIL')
        self.assertIn('loss_endpoint_point_nonworsening', record['failed_gates'])

    def test_underload_move_fails(self):
        special = {('startup', 'v5_full', 'generated_underload', 2): {'total_moves': 1}}
        record = ev.evaluate(validation_rows(special), ood_rows(), GOOD)
        self.assertEqual(record['decision'], 'FAIL')
        self.assertIn('underload', record['failed_gates'])

    def test_ood_breach_fails(self):
        special = {(f, 'v5_full'): {'total_dropped': 1_000} for f in ev.OOD_FAMILIES}
        record = ev.evaluate(validation_rows(), ood_rows(special), GOOD)
        self.assertEqual(record['decision'], 'FAIL')
        self.assertIn('ood', record['failed_gates'])

    def test_false_integrity_flag_fails(self):
        record = ev.evaluate(validation_rows(), ood_rows(), {**GOOD, 'tamper_test': False})
        self.assertEqual(record['decision'], 'FAIL')

    def test_missing_evidence_is_never_pass(self):
        flags = dict(GOOD)
        del flags['deterministic_repeat']
        self.assertEqual(ev.evaluate(validation_rows(), ood_rows(), flags)['decision'], 'NOT_EVALUATED')
        rows = validation_rows()[1:]
        self.assertEqual(ev.evaluate(rows, ood_rows(), GOOD)['decision'], 'NOT_EVALUATED')
        ood = [r for r in ood_rows() if r['group'] != 'transfer_failure']
        self.assertEqual(ev.evaluate(validation_rows(), ood, GOOD)['decision'], 'NOT_EVALUATED')

    def test_accounting_error_fails(self):
        special = {('finite_pipeline', 'v4_hybrid', 'generated_switching_fault', 1): {'max_mass_error': 1.0}}
        record = ev.evaluate(validation_rows(special), ood_rows(), GOOD)
        self.assertEqual(record['decision'], 'FAIL')
        self.assertIn('validation accounting', record['failed_gates'])


if __name__ == '__main__':
    unittest.main()
