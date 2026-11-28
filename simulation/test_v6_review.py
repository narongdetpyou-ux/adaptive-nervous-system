"""The V6 independent reviewer: separate statistics and hypotheses, replay, scenario identity and tamper detection."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import test_v6_evaluate as synthetic
import v6_review
import v6_validation
from v4_scenarios import GROUPS
from v6_evaluate import Gates, stratified_indices

POLICIES = synthetic.BASELINES + ('v6_full',)


class StatisticTests(unittest.TestCase):
    def test_bootstrap_draws_match_the_evaluator_stream(self):
        labels = [GROUPS[i % len(GROUPS)] for i in range(37)]
        self.assertEqual(v6_review.bootstrap_draws(labels, GROUPS, 30, 6026028),
                         stratified_indices(labels, GROUPS, 30, 6026028).tolist())
        ood = [synthetic.V6['ood']['families'][i % 6] for i in range(24)]
        families = synthetic.V6['ood']['families']
        self.assertEqual(v6_review.bootstrap_draws(ood, families, 30, 6026028),
                         stratified_indices(ood, families, 30, 6026028).tolist())

    def test_statistics_and_hypotheses_match_the_evaluator_and_tampering_is_detected(self):
        validation, ood = synthetic.rows(POLICIES, end_gain=40.0)
        gates = Gates(synthetic.V6, 'v6_full', POLICIES)
        decision = gates.evaluate(validation, ood, {})
        decision['hypotheses'] = gates.hypotheses(validation, ood)
        stats = v6_review.independent_statistics(validation, 'v6_full')
        self.assertEqual(v6_review.compare_with_decision(stats, decision, 'v6_full'), [])
        self.assertEqual(v6_review.compare_ood(v6_review.independent_ood(ood, 'v6_full'), decision, 'v6_full'), [])
        self.assertTrue(v6_review.tamper_detected(validation, ood, decision, 'v6_full'))
        changed = [dict(r) for r in validation]
        target = next(r for r in changed if r['policy'] == 'v5_full' and r['regime'] == 'legacy_instant')
        target['end_deficit'] = str(float(target['end_deficit']) + 500)
        self.assertIn(('H1', 'legacy_instant', 'end_deficit'),
                      v6_review.compare_with_decision(v6_review.independent_statistics(changed, 'v6_full'), decision, 'v6_full'))


class RunReplayTests(unittest.TestCase):
    def test_small_run_replays_exactly_and_manifests_regenerate(self):
        policies = v6_validation.policies()
        with tempfile.TemporaryDirectory() as tmp:
            v6_validation.run_seeds('ood', (9000,), tmp, 2, families=('transfer_failure',), trace_ood=(9000,))
            v6_validation.run_seeds('validation', (2000,), tmp, 2, families=('generated_pulsed_short_fault',),
                                    regimes=('delayed_noisy',), trace_validation=(2000,))
            replay = v6_review.replay_traces(tmp, v6_validation.candidate(), allow_reserved=False)
            self.assertEqual(replay['traces'], 2 * len(policies))
            self.assertEqual(replay['exact'], replay['traces'])
            self.assertEqual(replay['controller_replay'], replay['traces'])
            self.assertEqual(replay['invariant_problems'], [])
            self.assertEqual(v6_review.scenario_identity(tmp, allow_reserved=False), [])
            path = Path(tmp) / 'manifests' / 'ood-9000.json'
            stored = json.loads(path.read_text())
            stored['transfer_failure']['windows'][0][0] += 1
            path.write_text(json.dumps(stored))
            self.assertEqual(v6_review.scenario_identity(tmp, allow_reserved=False), [('ood', '9000', 'transfer_failure')])

    def test_rerun_is_byte_identical_and_closed_seeds_are_refused(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for out in (a, b):
                v6_validation.run_seeds('ood', (9001,), out, 2, families=('compound_stress',), trace_ood=(9001,))
            files = sorted(p.relative_to(a) for p in Path(a).rglob('*') if p.is_file())
            self.assertTrue(files)
            for name in files:
                self.assertEqual((Path(a) / name).read_bytes(), (Path(b) / name).read_bytes(), name)
        for seed in (7000, 8100):
            with self.assertRaises(ValueError):
                v6_validation.validation_case(seed, 'generated_underload', False)
            with self.assertRaises(ValueError):
                v6_validation.ood_case('rapid_switching', seed, False)

    def test_runner_refuses_without_a_valid_freeze(self):
        import v6_freeze
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(v6_freeze, 'RECORD', Path(tmp) / 'missing.json'):
            with self.assertRaises((ValueError, FileNotFoundError)):
                v6_validation.require_frozen()


if __name__ == '__main__':
    unittest.main()
