"""Single-decision replacement on top of unmodified V5, and the switch-study bookkeeping."""
import unittest

import numpy as np

from diagnose_v4_for_v5 import known_cases
from v4_environment import REGIME_CONFIGS, project_action
from v5_intervention import InterventionPolicy, NotApplicable
from v5_policy import V5Policy
from v5_simulation import simulate_case

REGIME = 'delayed_noisy'
ENV = REGIME_CONFIGS[REGIME]


def canonical_trace(trace):
    from v5_switch_study import canonical
    return [canonical(r) for r in trace]


class InterventionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = known_cases()[0]
        cls.row, cls.trace = simulate_case('v5_full', cls.case, regime=REGIME, detailed=True)
        cls.moved = [r['t'] for r in cls.trace if any(abs(x) > 1e-9 for x in r['applied_delta'])]

    def run_with(self, controller):
        return simulate_case('v5_full', self.case, regime=REGIME, detailed=True, controller=controller)

    def test_without_replacement_matches_v5_full(self):
        row, trace = self.run_with(InterventionPolicy(ENV, 'v5_full'))
        self.assertEqual(row, self.row)
        self.assertEqual(canonical_trace(trace), canonical_trace(self.trace))

    def test_hold_keeps_prefix_and_marks_replaced_minute(self):
        minute = self.moved[0]
        controller = InterventionPolicy(ENV, 'v5_full', minute=minute, action=None)
        row, trace = self.run_with(controller)
        self.assertEqual(canonical_trace(trace[:minute]), canonical_trace(self.trace[:minute]))
        self.assertEqual(trace[minute]['branch'], 'intervention_hold')
        self.assertEqual(trace[minute]['applied_delta'], [0.0, 0.0, 0.0])
        self.assertNotEqual(self.trace[minute]['applied_delta'], [0.0, 0.0, 0.0])
        self.assertFalse(any(when == minute for when, _, _ in controller.ledger.actions))
        self.assertEqual(controller.replacement['replaced_branch'], 'v5_selected')

    def test_replacement_is_recorded_and_starts_the_dwell(self):
        minute = self.moved[0]
        staff = np.asarray(self.trace[minute]['staff_before'], dtype=float)
        own = self.trace[minute]['decision_evidence']['selected_action_id']
        probe = V5Policy(ENV, 'v5_full')
        choice = next(i for i, move in enumerate(probe.moves)
                      if i != own and min(staff + move) >= ENV.minimum_staff
                      and np.any(project_action(staff, move, ENV)))
        controller = InterventionPolicy(ENV, 'v5_full', minute=minute, action=probe.moves[choice])
        _, trace = self.run_with(controller)
        entries = [(when, delta) for when, _, delta in controller.ledger.actions if when == minute]
        self.assertEqual(len(entries), 1)
        expected = project_action(staff, probe.moves[choice], ENV)
        np.testing.assert_allclose(entries[0][1], expected)
        np.testing.assert_allclose(trace[minute]['applied_delta'], expected)
        self.assertEqual(controller.replacement['action_id'], choice)
        later = [r['t'] for r in trace[minute + 1:] if any(abs(x) > 1e-9 for x in r['applied_delta'])]
        if later:
            self.assertGreaterEqual(later[0] - minute, controller.config.dwell)

    def test_rejects_minutes_where_v5_was_not_free(self):
        with self.assertRaises(NotApplicable):
            self.run_with(InterventionPolicy(ENV, 'v5_full', minute=0, action=None))
        locked = self.moved[0] + 1
        self.assertEqual(self.trace[locked]['branch'], 'locked')
        with self.assertRaises(NotApplicable):
            self.run_with(InterventionPolicy(ENV, 'v5_full', minute=locked, action=None))

    def test_rejects_invalid_replacements(self):
        with self.assertRaises(ValueError):
            InterventionPolicy(ENV, 'v5_full', minute=50, action=[1.0, -1.0, 0.0])
        with self.assertRaises(ValueError):
            InterventionPolicy(ENV, 'v5_full', minute=None, action=[-30.0, 0.0, 30.0])
        _, capped = simulate_case('v5_full', self.case, regime=REGIME, detailed=True, action_space='capped')
        free = next(r['t'] for r in capped if r['branch'] in ('v5_hold', 'v5_selected'))
        controller = InterventionPolicy(ENV, 'v5_full', action_space='capped', minute=free, action=[-30.0, 0.0, 30.0])
        with self.assertRaisesRegex(NotApplicable, 'transfer cap'):
            self.run_with(controller)

    def test_supplied_controller_matches_default_path(self):
        for variant in ('v5_full', 'v5_no_balance'):
            with self.subTest(variant=variant):
                default, _ = simulate_case(variant, self.case, regime=REGIME)
                supplied, _ = simulate_case(variant, self.case, regime=REGIME, controller=V5Policy(ENV, variant))
                self.assertEqual(supplied, default)

    def test_supplied_controller_validation(self):
        other = REGIME_CONFIGS['legacy_instant']
        bad = [dict(policy='v5_full', controller=V5Policy(other, 'v5_full')),
               dict(policy='capacity_balance', controller=V5Policy(ENV, 'v5_full')),
               dict(policy='v5_full', controller=V5Policy(ENV, 'v5_full'), action_space='capped')]
        used = V5Policy(ENV, 'v5_full')
        used.decide(0, np.zeros(3), [], np.full(3, ENV.budget / 3), 0.)
        bad.append(dict(policy='v5_full', controller=used))
        for kwargs in bad:
            with self.subTest(kwargs=sorted(kwargs)), self.assertRaises(ValueError):
                simulate_case(scenario=self.case, regime=REGIME, **kwargs)


class StudyBookkeepingTests(unittest.TestCase):
    def test_closed_seed_ranges_are_refused(self):
        from v5_switch_study import check_seeds
        self.assertEqual(check_seeds([2000, 4008]), (2000, 4008))
        for seed in (5000, 5015, 6000, 6063, 8000, 8063):
            with self.subTest(seed=seed), self.assertRaises(ValueError):
                check_seeds([seed])

    def test_key_minutes_follow_the_plan(self):
        from v5_switch_study import key_minutes

        def trace(moves):
            return [{'t': t, 'applied_delta': moves.get(t, [0.0, 0.0, 0.0])} for t in range(128)]
        v5 = trace({41: [-30.0, 0.0, 30.0], 49: [-30.0, 30.0, 0.0]})
        balance = trace({42: [-7.0, 7.0, 0.0]})
        self.assertEqual(key_minutes(v5, balance), {'m1': 41, 'm2': 49, 'b': 42})
        late = trace({60: [-30.0, 30.0, 0.0]})
        self.assertEqual(key_minutes(late, trace({})), {'m1': None, 'm2': None, 'b': None})

    def test_same_pattern_alternatives(self):
        from v5_switch_study import same_pattern
        moves = V5Policy(ENV, 'v5_full').moves
        self.assertEqual(same_pattern(moves, np.array([-30.0, 30.0, 0.0])),
                         {'middle_max@m2': None, 'middle_min@m2': 31})
        self.assertEqual(same_pattern(moves, np.array([-5.0, 10.0, -5.0])),
                         {'middle_max@m2': 46, 'middle_min@m2': 1})


if __name__ == '__main__':
    unittest.main()
