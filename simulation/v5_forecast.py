"""Common scenario scoring and explicit loss ranking."""
import numpy as np
from v4_policy import forecast_costs

def score(queues, staff, allocations, rates, demand, environment, terminal_weight, horizons):
    outputs = [forecast_costs(queues,staff,allocations,rates[:,:h],demand[:,:h],
                              environment,terminal_weight) for h in horizons]
    costs = np.mean([o['cost'] for o in outputs],axis=0)
    # Use longest-window cumulative losses, not sum of nested losses.
    drops = outputs[-1]['new_drops']
    return costs,drops

def loss_candidates(drops, references, tolerance):
    mean = drops.mean(axis=0)
    worst = drops.max(axis=0)
    # Compare worst and mean against each feasible reference. If no common
    # dominance exists, retain the lowest predicted worst loss, then mean.
    allowed = np.ones(len(mean), dtype=bool)
    for ref in references:
        allowed &= (mean <= mean[ref]+tolerance) & (worst <= worst[ref]+tolerance)
    if not allowed.any():
        allowed = worst <= worst.min()+tolerance
        allowed &= mean <= mean[allowed].min()+tolerance
    return allowed,mean,worst
