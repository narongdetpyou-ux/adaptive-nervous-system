"""Replace exactly one V5 decision in an otherwise unmodified V5 run (development diagnosis only)."""
import numpy as np

from v4_environment import project_action
from v5_policy import V5Policy

# Branches where V5 itself was free to choose an action at that minute.
FREE_BRANCHES = ('v5_hold', 'v5_selected')


class NotApplicable(ValueError):
    """The declared replacement cannot be made at that minute of that case."""


class InterventionPolicy(V5Policy):
    """V5 whose decision at one minute is replaced by a catalog action or by a hold.

    V5 still processes every observation and makes its own decision at the
    replaced minute, so all observation-driven state matches an unmodified run.
    Only the action-dependent state, the action ledger and the dwell clock, is
    rewritten to describe the replacement. Without a minute it is plain V5.
    """

    def __init__(self, environment, variant='v5_full', config=None, *, action_space='variant',
                 minute=None, action=None):
        super().__init__(environment, variant, config, action_space=action_space)
        if minute is not None and (type(minute) is not int or minute < 0):
            raise ValueError('Replacement minute must be a non-negative integer')
        if minute is None and action is not None:
            raise ValueError('A replacement action needs a minute')
        self.minute = minute
        self.action = None if action is None else np.asarray(action, dtype=float).copy()
        self.action_id = None
        if self.action is not None:
            matches = [i for i, move in enumerate(self.moves) if np.array_equal(move, self.action)]
            if self.action.shape != (3,) or not matches:
                raise ValueError('Replacement must be a catalog action')
            self.action_id = matches[0]
        self.replacement = None

    def decide(self, t, queues, history, staff, current_arrival):
        ledger_size = len(self.ledger.actions)
        previous_change = self.last_change
        raw, note = super().decide(t, queues, history, staff, current_arrival)
        if t != self.minute:
            return raw, note
        if note['branch'] not in FREE_BRANCHES:
            raise NotApplicable(f"V5 was not free to act at minute {t}: {note['branch']}")
        staff = np.asarray(staff, dtype=float)
        move = np.zeros(3)
        if self.action is not None:
            move = self.action.copy()
            env, c = self.environment, self.config
            if min(staff + move) < env.minimum_staff:
                raise NotApplicable(f'Replacement violates the minimum staff level at minute {t}')
            delta = project_action(staff, move, env)
            if not np.any(delta):
                raise NotApplicable(f'Replacement has no feasible effect at minute {t}')
            if self.cap_transfers:
                limit = c.maximum_transfer + c.tolerance
                if np.maximum(move, 0).sum() > limit or np.maximum(delta, 0).sum() > limit:
                    raise NotApplicable(f'Replacement exceeds the transfer cap at minute {t}')
        # Undo V5's own commitment at this minute, then commit the replacement.
        del self.ledger.actions[ledger_size:]
        self.last_change = previous_change
        if np.any(move):
            self.ledger.record(t, staff, move)
            self.last_change = t
        self.replacement = {'t': t, 'replaced_branch': note['branch'],
                            'replaced_action_id': note.get('selected_action_id'),
                            'action_id': self.action_id, 'action': move.tolist()}
        branch = 'intervention_move' if np.any(move) else 'intervention_hold'
        return move, {**note, 'branch': branch, 'selected_action_id': self.action_id,
                      'replaced_branch': note['branch'],
                      'replaced_action_id': note.get('selected_action_id')}
