"""Approximate causal reconstruction; uses delivered samples and own action ledger only."""
import numpy as np
from v4_environment import project_action

class ActionLedger:
    def __init__(self, environment):
        self.env = environment
        self.actions = []
        self.initial = np.full(3, environment.budget / 3.)

    def record(self, t, staff, raw):
        delta = project_action(staff, raw, self.env)
        if np.any(delta):
            self.actions.append((t, np.asarray(staff, float).copy(), delta.copy()))

    def effective(self, t):
        staff = self.initial.copy()
        for when, old, delta in self.actions:
            if when > t:
                break
            staff = old + delta
            incoming = np.maximum(delta, 0.)
            age = t - when - self.env.transit_delay
            factor = 0. if age < 0 else (self.env.startup_factors[age]
                       if age < len(self.env.startup_factors) else 1.)
            staff = staff - (1. - factor) * incoming
        return staff

    def reconstruct(self, t, measured, demand, rates):
        q = np.asarray(measured, float).copy()
        cap = np.array([np.inf if x is None else x for x in self.env.buffer_capacities])
        start = t - self.env.sensor_lag
        if start < 0:
            return q
        q = np.minimum(q, cap)
        for minute in range(start, t):
            capacity = self.effective(minute) * rates
            if self.env.same_minute_forwarding:
                for i in range(3):
                    flow = min(q[i], capacity[i])
                    q[i] -= flow
                    if i < 2:
                        q[i+1] = min(cap[i+1], q[i+1] + flow)
                q[0] += demand
            else:
                flow = np.minimum(q, capacity)
                q -= flow
                q += np.array([demand, flow[0], flow[1]])
            q = np.minimum(q, cap)
        return q
