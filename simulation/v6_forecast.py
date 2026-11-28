"""Drain-aware forecast cost for V6 (v6_acceptance_predeclared.json, mechanism `drain`).

The vectorised model is the one of v4_policy.forecast_costs, line for line. The
only change is the terminal term: retained work is counted by the stage visits
still ahead of it. Work queued at, or travelling to, stage s weighs
1 + drain_weight * (2 - s) / 2, so drain_weight 2 counts 3, 2 and 1 visits.
With drain_weight 0 the expression is exactly v4_policy.forecast_costs.
"""
import numpy as np

from capacity_policy import vector
from v4_environment import EnvironmentConfig
from v5_forecast import loss_candidates  # noqa: F401  (re-exported: the unchanged V5 loss filter)


def stage_weights(drain_weight):
    return 1.0 + drain_weight * np.array([2.0, 1.0, 0.0]) / 2.0


def forecast_costs(queues, old_staff, allocations, rates, arrivals, environment, terminal_weight=0.5, drain_weight=0.0):
    queues = vector(queues, 'forecast queues'); old_staff = vector(old_staff, 'forecast old staff')
    allocations = np.asarray(allocations, dtype=float)
    rates = np.asarray(rates, dtype=float); arrivals = np.asarray(arrivals, dtype=float)
    if not isinstance(environment, EnvironmentConfig): raise TypeError('Known environment config required')
    if allocations.ndim != 2 or allocations.shape[1] != 3:
        raise ValueError('Invalid allocation matrix')
    if rates.ndim != 3 or rates.shape[2] != 3 or arrivals.shape != rates.shape[:2] or rates.shape[1] < 1:
        raise ValueError('Invalid forecast shape')
    if any(not np.isfinite(x).all() or (x < 0).any() for x in (allocations, rates, arrivals)):
        raise ValueError('Invalid forecast values')
    if not np.isfinite(drain_weight) or drain_weight < 0:
        raise ValueError('Invalid drain weight')
    incoming = np.maximum(allocations - old_staff, 0.)
    q = np.broadcast_to(queues, (len(rates), len(allocations), 3)).copy()
    inflight = np.zeros_like(q); dropped = np.zeros(q.shape[:2]); area = np.zeros(q.shape[:2])
    caps = np.array([np.inf if x is None else x for x in environment.buffer_capacities])
    for h in range(rates.shape[1]):
        if h:
            q += inflight; inflight[:] = 0
            q[:, :, 0] += arrivals[:, h, None]
        overflow = np.maximum(q - caps, 0.)
        dropped += overflow.sum(axis=2); q = np.minimum(q, caps)
        if h < environment.transit_delay:
            effective = allocations - incoming
        else:
            age = h - environment.transit_delay
            factor = environment.startup_factors[age] if age < len(environment.startup_factors) else 1.
            effective = allocations - (1 - factor) * incoming
        capacity = effective[None, :, :] * rates[:, h, None, :]
        if environment.same_minute_forwarding:
            for stage in range(3):
                flow = np.minimum(q[:, :, stage], capacity[:, :, stage]); q[:, :, stage] -= flow
                if stage < 2:
                    q[:, :, stage + 1] += flow
                    overflow = np.maximum(q[:, :, stage + 1] - caps[stage + 1], 0.)
                    dropped += overflow; q[:, :, stage + 1] -= overflow
        else:
            flow = np.minimum(q, capacity); q -= flow
            inflight[:, :, 1:] = flow[:, :, :2]
        retained = q.sum(axis=2) + inflight.sum(axis=2)
        deficit = retained + dropped; area += deficit
    if drain_weight == 0:
        terminal = deficit
    else:
        weights = stage_weights(drain_weight)
        terminal = (q * weights).sum(axis=2) + (inflight * weights).sum(axis=2) + dropped
    return {'cost': area / rates.shape[1] + terminal_weight * terminal,
            'terminal_deficit': deficit, 'terminal_retained': retained, 'new_drops': dropped,
            'terminal_weighted': terminal}


def score(queues, staff, allocations, rates, demand, environment, terminal_weight, horizons, drain_weight=0.0):
    """v5_forecast.score with the drain-aware terminal term."""
    outputs = [forecast_costs(queues, staff, allocations, rates[:, :h], demand[:, :h], environment,
                              terminal_weight, drain_weight) for h in horizons]
    costs = np.mean([o['cost'] for o in outputs], axis=0)
    drops = outputs[-1]['new_drops']
    return costs, drops
