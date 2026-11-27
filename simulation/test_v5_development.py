import unittest


class MatchedActionSpaceTests(unittest.TestCase):
    def test_native_and_matched_spaces(self):
        from v5_simulation import make_controller
        for variant, native_cap in [('v5_full', False), ('v5_no_balance', True)]:
            native = make_controller(variant, 'delayed_noisy')
            self.assertEqual(native.cap_transfers, native_cap)
            for space in ['capped', 'catalog']:
                with self.subTest(policy=variant, space=space):
                    matched = make_controller(variant, 'delayed_noisy', action_space=space)
                    self.assertEqual(matched.features, native.features)
                    self.assertEqual(matched.config, native.config)
                    self.assertEqual(matched.cap_transfers, space == 'capped')

    def test_invalid_space_and_frozen_baseline_override(self):
        from v5_simulation import make_controller
        for policy, space in [('v5_full', 'typo'), ('v4_hybrid', 'catalog')]:
            with self.subTest(policy=policy, space=space), self.assertRaises(ValueError):
                make_controller(policy, 'delayed_noisy', action_space=space)

    def test_archive_replay_rejects_changed_metric(self):
        from v5_known_study import compare_archive
        row = {'policy': 'static', 'scenario_seed': 4008, 'total_dropped': 5.}
        with self.assertRaisesRegex(ValueError, 'total_dropped'):
            compare_archive(row, {'total_dropped': 5.01}, 1e-6)


class DevelopmentSliceTests(unittest.TestCase):
    def test_complete_distinct_arms(self):
        from v5_development import study_arms
        arms = study_arms()
        self.assertEqual(len(set(arms)), 18)
        self.assertEqual(len(arms), 18)
        self.assertIn(('v5_full', 'capped'), arms)
        self.assertIn(('v5_no_balance', 'catalog'), arms)

    def test_runner_rejects_non_development_seeds_before_generation(self):
        from unittest.mock import patch
        from pathlib import Path
        from v5_development import run
        with patch('v5_development.generate_scenarios') as generator:
            for seed in (4008, 5000, 6000, 8000):
                with self.assertRaises(ValueError):
                    run(seed, 1, Path('unused'))
            generator.assert_not_called()


if __name__ == '__main__':
    unittest.main()
