"""V6 = frozen V5 Full plus `drain` and/or `restraint` (v6_acceptance_predeclared.json).

V6Policy subclasses the frozen V5Policy and never edits it. decide() is V5's
decision procedure line for line, with three marked hooks:
1. drain: the forecast terminal term counts retained work by the stage visits
   still ahead of it (v6_forecast.score); drain_weight 0 is exactly V5.
2. restraint, while RestraintMonitor reports a reason. In the original
   hurdle mode (restraint_neutral 0), candidate transfers above restraint_max_transfer are dropped,
   the gain hurdle is multiplied by restraint_hurdle_factor, and the V5 loss
   veto lets the controller hold when acting would cut predicted drops by no
   more than restraint_loss_fraction. In the neutral mode (restraint_neutral 1, amendment 1 of
   v6_acceptance_predeclared.json) the controller holds when its allocation is
   within restraint_neutral_tolerance of the even split, and otherwise takes
   the feasible catalog move that most reduces the distance to it.
3. restraint bookkeeping: the monitor reads the detector's events and the
   committed staff, and remembers each own move's intended allocation.
With no V6 feature (`v6_base`) every decision equals V5 Full's.
"""
from dataclasses import dataclass
import itertools

import numpy as np

from capacity_policy import vector
from v4_environment import project_action
from v5_forecast import loss_candidates
from v5_policy import V5Config, V5Policy
from v5_regime import switching_paths
from v6_forecast import score
from v6_restraint import RestraintMonitor


@dataclass(frozen=True)
class V6Config(V5Config):
    # Defaults chosen on development seeds by the rule in V6_DESIGN.md:
    # drain from the best drain-only set (drain_w1), restraint from the best
    # drain-plus-restraint set (full_neutral16). The hurdle and loss fields
    # only apply in the original hurdle mode.
    drain_weight: float = 1.0
    restraint_hurdle_factor: float = 3.0
    restraint_max_transfer: float = 1e9
    restraint_loss_fraction: float = 0.25
    restraint_memory: int = 16
    restraint_neutral: float = 1.0  # 0: the original hurdle mode; 1: neutral mode (amendment 1)
    restraint_neutral_tolerance: float = 10.0


V6_VARIANTS = {
    'v6_base': (),
    'v6_drain': ('drain',),
    'v6_restraint': ('restraint',),
    'v6_full': ('drain', 'restraint'),
}


class V6Policy(V5Policy):
    def __init__(self, environment, variant='v6_full', config=None):
        if variant not in V6_VARIANTS:
            raise ValueError('Unknown V6 variant')
        super().__init__(environment, 'v5_full', config or V6Config())
        self.variant = variant
        self.v6_features = V6_VARIANTS[variant]
        if self.config.restraint_neutral not in (0.0, 1.0):
            raise ValueError('restraint_neutral must be 0 (hurdle mode) or 1 (neutral mode)')
        hurdle = self.config.restraint_neutral == 0.0
        self.monitor = RestraintMonitor(environment, self.config.dwell, self.config.restraint_memory,
                                        detector_switching=hurdle, fast_switching=not hurdle)

    def decide(self, t, queues, history, staff, current_arrival):
        if type(t) is not int or t != self.last_decision + 1 or self.estimator.last_observed != t - 1:
            raise ValueError('decision chronology')
        queues = vector(queues, 'queues'); staff = vector(staff, 'staff')
        if abs(staff.sum() - self.environment.budget) > self.config.tolerance or len(history) != min(t, 5):
            raise ValueError('invalid input')
        if not np.isfinite(current_arrival) or current_arrival < 0:
            raise ValueError('invalid arrival')
        self.last_decision = t
        c = self.config; env = self.environment
        self.arrival_history = (self.arrival_history + [float(current_arrival)])[-c.arrival_window:]
        demand = float(np.mean(self.arrival_history)); point = self.estimator.snapshot()
        lower, upper, _, _ = self.bounds(t)
        q = self.ledger.reconstruct(t, queues, demand, point) if 'observer' in self.features else queues
        note = {'t': t, 'variant': self.variant, 'selected_action_id': None,
                'estimated_queue': q.tolist(), 'estimated_rates': point.tolist(),
                'switching': self.detector.switching, 'latest_feedback_source_t': t - 1 - env.sensor_lag}
        # Hook 3: restraint bookkeeping from observed data only.
        restrained = []
        if 'restraint' in self.v6_features:
            self.monitor.note_detector(self.detector.events)
            self.monitor.note_staff(t, staff)
            restrained = self.monitor.reasons(t, self.detector.switching)
            note['restraint'] = restrained
        zero = np.zeros(3)

        def hold(branch):
            return zero.copy(), {**note, 'branch': branch}
        if t < c.warmup: return hold('warmup')
        if t - self.last_change < c.dwell: return hold('locked')
        raw = [zero]; actual = [zero]; ids = [-1]
        for aid, move in enumerate(self.moves):
            if min(staff + move) < env.minimum_staff: continue
            delta = project_action(staff, move, env)
            if self.cap_transfers:
                limit = c.maximum_transfer + c.tolerance
                if np.maximum(move, 0).sum() > limit or np.maximum(delta, 0).sum() > limit:
                    continue
            # Hook 2a: smaller transfers only while restrained.
            if restrained:
                limit = c.restraint_max_transfer + c.tolerance
                if np.maximum(move, 0).sum() > limit or np.maximum(delta, 0).sum() > limit:
                    continue
            if np.any(delta): raw.append(move); actual.append(delta); ids.append(aid)
        if len(ids) == 1: return hold('no_feasible_move')
        # Hook 2d: neutral restraint returns toward the even split, then holds.
        if restrained and c.restraint_neutral == 1.0:
            neutral = np.full(3, env.budget / 3.0)
            gap = np.abs(staff - neutral).sum() / 2
            if gap < c.restraint_neutral_tolerance:
                return hold('v6_restrained_hold')
            reached = np.abs(staff + np.asarray(actual) - neutral).sum(axis=1) / 2
            size = np.abs(actual).sum(axis=1)
            j = min(range(len(ids)), key=lambda j: (reached[j], size[j], ids[j]))
            if j == 0 or reached[j] >= gap - c.tolerance:
                return hold('v6_restrained_hold')
            self.ledger.record(t, staff, raw[j])
            self.monitor.record_move(t, staff, actual[j])
            self.last_change = t
            return raw[j].copy(), {**note, 'branch': 'v6_restrained_neutral', 'selected_action_id': ids[j],
                                   'neutral_gap_before': float(gap), 'neutral_gap_after': float(reached[j])}
        allocations = staff + np.asarray(actual)
        target = env.budget / np.maximum(point, 1e-9); target *= env.budget / target.sum()
        distance = np.square(allocations - target).sum(axis=1)
        movement = np.abs(actual).sum(axis=1)
        balance = min(range(len(ids)), key=lambda j: (distance[j], movement[j], ids[j]))
        persistent = len(history) >= 2 and np.any((np.asarray(history[-1]) > 10) & (np.asarray(history[-2]) > 10))
        if not persistent: balance = 0
        horizons = (8, 16, 32) if 'multihorizon' in self.features else (c.lookahead,)
        h = horizons[-1]
        paths = [np.tile(point * c.rate_lower_factor, (h, 1)), np.tile(point, (h, 1)),
                 point[None, :] + np.linspace(0, 1, h)[:, None] * (c.nominal_rate - point)[None, :]]
        rates = []; arr = []
        for factor in (1 - c.demand_spread, 1., 1 + c.demand_spread):
            for path in paths:
                rates.append(path); arr.append(np.full(h, demand * factor))
        for factor in (1 - c.demand_spread, 1., 1 + c.demand_spread):
            for bits in itertools.product((0, 1), repeat=3):
                rates.append(np.tile(np.where(bits, upper, lower), (h, 1))); arr.append(np.full(h, demand * factor))
            if 'regime' in self.features:
                for path in switching_paths(point, lower, upper, h, self.detector.switching):
                    rates.append(path); arr.append(np.full(h, demand * factor))
        # Hook 1: drain-aware terminal term (exactly V5's score when drain is off).
        drain_weight = c.drain_weight if 'drain' in self.v6_features else 0.0
        costs, drops = score(q, staff, allocations, np.asarray(rates), np.asarray(arr), env, c.terminal_weight,
                             horizons, drain_weight)
        gain = costs[:, 0, None] - costs
        mean = gain[:9].mean(axis=0)
        stress = np.maximum(-gain[9:], 0.)
        n = max(1, int(np.ceil(len(stress) * c.stress_tail_fraction)))
        tail = np.sort(stress, axis=0)[-n:].mean(axis=0)
        imbalance = np.abs(staff - target).sum() / 2
        escape = max(c.escape_floor, 1 / (1 + self.pressure_streak / c.pressure_time_scale)) if imbalance >= 5 else 1.
        adjusted = mean - c.risk_weight * escape * tail
        hurdle = max(c.absolute_gain, c.relative_gain * demand) * escape
        # Hook 2b: a higher gain hurdle while restrained.
        if restrained:
            hurdle *= c.restraint_hurdle_factor
        allowed = np.ones(len(ids), dtype=bool)
        if 'loss' in self.features:
            allowed, avg_loss, worst_loss = loss_candidates(drops, [0, balance], c.loss_tolerance)
        else:
            avg_loss = drops.mean(axis=0); worst_loss = drops.max(axis=0)
        ranking = sorted(np.flatnonzero(allowed), key=lambda j: (-adjusted[j], movement[j], ids[j]))
        best = int(ranking[0])
        if 'balance' in self.features and allowed[balance] and adjusted[balance] > hurdle and adjusted[balance] >= (1 - c.balance_preference_fraction) * adjusted[best]: best = balance
        # Hook 2c: while restrained, a small predicted drop reduction no longer forces a move.
        if restrained and not allowed[0]:
            if avg_loss[0] - avg_loss[best] <= c.restraint_loss_fraction * max(float(avg_loss[0]), 1.0):
                allowed[0] = True
        # A loss veto may require action even when gain is below the usual hurdle.
        if allowed[0] and adjusted[best] <= hurdle:
            best = 0
            if self.idle_streak >= c.idle_recovery_observations:
                for j in sorted(range(1, len(ids)), key=lambda j: (distance[j], movement[j], ids[j])):
                    if allowed[j] and distance[j] < distance[0] - c.tolerance and mean[j] >= -c.tolerance and gain[:, j].min() >= -c.absolute_gain:
                        best = j; break
        note.update(balance_action_id=None if balance == 0 else ids[balance],
                    horizon=list(horizons), predicted_mean_drops=float(avg_loss[best]),
                    predicted_worst_drops=float(worst_loss[best]), hold_allowed=bool(allowed[0]),
                    predicted_gain=float(adjusted[best]), eligible_actions=int(allowed.sum()))
        if best == 0: return hold('v6_restrained_hold' if restrained else 'v5_hold')
        self.ledger.record(t, staff, raw[best])
        if 'restraint' in self.v6_features:
            self.monitor.record_move(t, staff, actual[best])
        self.last_change = t
        return raw[best].copy(), {**note, 'branch': 'v6_restrained_selected' if restrained else 'v5_selected',
                                  'selected_action_id': ids[best]}
