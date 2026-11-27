"""The independent reviewer: separate statistics, replay, scenario identity and tamper detection."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import test_v5_evaluate as synthetic
import v5_evaluate
import v5_review
import v5_validation
from v4_metrics import _bootstrap_indices, _cvar_last_axis
from v4_scenarios import GROUPS


class StatisticTests(unittest.TestCase):
    def test_cvar_and_percentile_match_the_reference_definitions(self):
        rng = np.random.default_rng(3)
        for n in (7, 10, 23, 704):
            values = rng.gamma(2.0, 3.0, n)
            self.assertAlmostEqual(v5_review.cvar90(values.tolist()), float(_cvar_last_axis(values)), places=9)
            self.assertAlmostEqual(v5_review.percentile(values.tolist(), .025), float(np.percentile(values, 2.5)), places=9)

    def test_bootstrap_draws_match_the_declared_stream(self):
        labels = [GROUPS[i % 4] for i in range(37)]
        reference = _bootstrap_indices(np.array(labels), 50, 5026025)
        self.assertEqual(v5_review.bootstrap_draws(labels, 50, 5026025), reference.tolist())

    def test_statistics_match_the_evaluator_and_tampering_is_detected(self):
        rows, ood = synthetic.validation_rows(), synthetic.ood_rows()
        decision = v5_evaluate.evaluate(rows, ood, synthetic.GOOD)
        stats = v5_review.independent_statistics(rows)
        self.assertEqual(v5_review.compare_with_decision(stats, decision), [])
        self.assertEqual(v5_review.compare_ood(v5_review.independent_ood(ood), decision), [])
        self.assertTrue(v5_review.tamper_detected(rows, ood, decision))
        # An underload move the evaluator counted must be counted independently too.
        moved = synthetic.validation_rows({('startup', 'v5_full', 'generated_underload', 2): {'total_moves': 1}})
        moved_decision = v5_evaluate.evaluate(moved, ood, synthetic.GOOD)
        self.assertEqual(v5_review.compare_with_decision(v5_review.independent_statistics(moved), moved_decision), [])
        self.assertEqual(v5_review.compare_with_decision(v5_review.independent_statistics(rows), moved_decision),
                         [('underload',)])
        # Changing only a baseline's OOD value must also be caught.
        changed = [dict(r) for r in ood]
        target = next(r for r in changed if r['policy'] == 'v4_hybrid')
        target['total_dropped'] = str(float(target['total_dropped']) + 50)
        self.assertTrue(v5_review.compare_ood(v5_review.independent_ood(changed), decision))


class RunReplayTests(unittest.TestCase):
    def test_small_run_replays_exactly_and_manifests_regenerate(self):
        with tempfile.TemporaryDirectory() as tmp:
            v5_validation.run_seeds('ood', (9000,), tmp, 2, families=('transfer_failure', 'sensor_degradation'),
                                    trace_ood=(9000,))
            v5_validation.run_seeds('validation', (2000,), tmp, 2, families=('generated_switching_fault',),
                                    regimes=('delayed_noisy',), trace_validation=(2000,))
            replay = v5_review.replay_traces(tmp, allow_reserved=False)
            self.assertEqual(replay['traces'], 18)
            self.assertEqual(replay['exact'], 18)
            self.assertEqual(replay['controller_replay'], 18)
            self.assertEqual(replay['invariant_problems'], [])
            self.assertEqual(v5_review.scenario_identity(tmp, allow_reserved=False), [])
            path = Path(tmp) / 'manifests' / 'ood-9000.json'
            stored = json.loads(path.read_text())
            stored['transfer_failure']['windows'][0][0] += 1
            path.write_text(json.dumps(stored))
            self.assertEqual(v5_review.scenario_identity(tmp, allow_reserved=False), [('ood', '9000', 'transfer_failure')])

    def test_rerun_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            for out in (a, b):
                v5_validation.run_seeds('ood', (9001,), out, 2, families=('compound_stress',), trace_ood=(9001,))
            files = sorted(p.relative_to(a) for p in Path(a).rglob('*') if p.is_file())
            self.assertTrue(files)
            for name in files:
                self.assertEqual((Path(a) / name).read_bytes(), (Path(b) / name).read_bytes(), name)


if __name__ == '__main__':
    unittest.main()
