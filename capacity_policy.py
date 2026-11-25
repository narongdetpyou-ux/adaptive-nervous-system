"""Observation-only capacity estimator and two transparent catalogue gates.

The frozen ABC source is untouched. ``abc_only`` can only veto ABC's proposal;
``catalog`` is a separate model-ranked controller, not the original ABC policy.
No method accepts true productivity, future arrivals, or a scenario label.
"""
from dataclasses import asdict, dataclass
import copy
import numpy as np
from abc_service.frozen import catalog, module


@dataclass(frozen=True)
class CapacityConfig:
    nominal_rate: float = 200.0
    warmup: int = 5
    dwell: int = 8
    lookahead: int = 8
    minimum_staff: float = 10.0
    saturated_update: float = 0.7
    unsaturated_prior_reversion: float = 0.10
    rate_lower_factor: float = 0.85
    demand_spread: float = 0.20
    arrival_window: int = 3
    terminal_weight: float = 0.25
    relative_gain: float = 0.02
    absolute_gain: float = 10.0
    worst_case_regression_fraction: float = 0.0
    tolerance: float = 1e-7

    def __post_init__(self):
        if not all(np.isfinite(x) for x in asdict(self).values()):
            raise ValueError('Configuration must be finite')
        if any(type(x) is not int for x in (self.warmup,self.dwell,self.lookahead,self.arrival_window)):
            raise ValueError('Time and window settings must be integers')
        if self.lookahead < self.dwell or self.dwell < 1 or self.warmup < 1:
            raise ValueError('Lookahead must cover the entire dwell interval')
        if not (0 < self.minimum_staff < 69 and self.nominal_rate > 0):
            raise ValueError('Invalid nominal capacity or reserve')
        if not (0 < self.saturated_update <= 1 and 0 <= self.unsaturated_prior_reversion <= 1):
            raise ValueError('Invalid estimator update')
        if not (0 < self.rate_lower_factor <= 1 and 0 <= self.demand_spread < 1):
            raise ValueError('Invalid uncertainty assumptions')
        if self.relative_gain < 0 or self.absolute_gain < 0 or self.worst_case_regression_fraction < 0:
            raise ValueError('Invalid acceptance threshold')
        if self.arrival_window < 1 or self.terminal_weight < 0:
            raise ValueError('Invalid forecast configuration')

    def as_dict(self):
        return asdict(self)


def vector(value, name):
    a = np.asarray(value, dtype=float)
    if a.shape != (3,) or not np.isfinite(a).all() or (a < 0).any():
        raise ValueError('Invalid ' + name)
    return a


class RateEstimator:
    """Distinguish a busy server's measured rate from censored light-load flow.

Prior reversion is an explicit modeling assumption, not measured recovery.
With no resources assigned, zero flow contains no rate information.
"""
    def __init__(self, config=None):
        self.config = config or CapacityConfig()
        self.rate = np.full(3, self.config.nominal_rate, dtype=float)
        self.last_saturated = np.full(3, -1, dtype=int)
        self.latest_saturated = np.zeros(3, dtype=bool)
        self.lower_bound = np.zeros(3)
        self.last_observed = -1

    def update(self, t, staff, flow, end_queue):
        if not isinstance(t, (int, np.integer)) or t <= self.last_observed:
            raise ValueError('Observations must arrive once, in increasing time order')
        staff = vector(staff, 'staff')
        flow = vector(flow, 'flow')
        q = vector(end_queue, 'end queue')
        if np.any((staff == 0) & (flow > self.config.tolerance)):
            raise ValueError('Positive flow without resources')
        self.latest_saturated[:] = False
        for i in range(3):
            if staff[i] == 0:
                self.lower_bound[i] = 0
                continue
            bound = float(flow[i] / staff[i])
            self.lower_bound[i] = bound
            if q[i] > self.config.tolerance:
                # The very first saturated observation should reveal a fault
                # immediately rather than smoothing it against an unmeasured prior.
                alpha = 1.0 if self.last_saturated[i] < 0 else self.config.saturated_update
                self.rate[i] = alpha * bound + (1-alpha) * self.rate[i]
                self.last_saturated[i] = int(t)
                self.latest_saturated[i] = True
            else:
                # Never replace a rate by demand-limited service/staff. Reversion
                # toward the public nominal prior is kept separate and explicit.
                a = self.config.unsaturated_prior_reversion
                prior_reverted = (1-a) * self.rate[i] + a * self.config.nominal_rate
                self.rate[i] = max(bound, prior_reverted)
        self.last_observed = int(t)

    def snapshot(self, t=None):
        return self.rate.copy()

    def evidence(self, t):
        return {
            'rate': self.rate.tolist(),
            'last_observed_minute': self.last_observed,
            'last_saturated_minute': self.last_saturated.tolist(),
            'saturation_age': [None if x < 0 else int(t-x) for x in self.last_saturated],
            'latest_saturated': self.latest_saturated.tolist(),
            'latest_flow_per_resource_lower_bound': self.lower_bound.tolist(),
            'nominal_prior': self.config.nominal_rate,
        }


def prediction_costs(queues, allocations, rates, future_arrivals, terminal_weight=0.25):
    """Vectorized upstream-first fluid replay, no access to the real environment.

rates: [ensemble,horizon,stage], arrivals: [ensemble,horizon]. The current
arrival is already in queues; arrivals[:,0] is intentionally ignored.
Returns one mean-backlog-plus-terminal cost per ensemble and candidate.
"""
    q0 = vector(queues, 'prediction queues')
    allocations = np.asarray(allocations, dtype=float)
    rates = np.asarray(rates, dtype=float)
    future_arrivals = np.asarray(future_arrivals, dtype=float)
    if allocations.ndim != 2 or allocations.shape[1] != 3 or (allocations < 0).any():
        raise ValueError('Invalid candidate allocations')
    if rates.ndim != 3 or rates.shape[2] != 3 or future_arrivals.shape != rates.shape[:2]:
        raise ValueError('Invalid forecast dimensions')
    if rates.shape[1] < 1 or not all(np.isfinite(v).all() for v in (allocations,rates,future_arrivals)):
        raise ValueError('Invalid prediction input')
    if (rates < 0).any() or (future_arrivals < 0).any():
        raise ValueError('Negative forecast')
    q = np.broadcast_to(q0, (len(rates), len(allocations), 3)).copy()
    total = np.zeros(q.shape[:2])
    for h in range(rates.shape[1]):
        if h:
            q[:,:,0] += future_arrivals[:,h,None]
        for i in range(3):
            f = np.minimum(q[:,:,i], allocations[None,:,i] * rates[:,h,None,i])
            q[:,:,i] -= f
            if i < 2:
                q[:,:,i+1] += f
        total += q.sum(axis=2)
    return total / rates.shape[1] + terminal_weight * q.sum(axis=2)


class CapacityAwarePolicy:
    def __init__(self, mode='catalog', uniforms=None, config=None):
        if mode not in ('catalog','abc_only'):
            raise ValueError('Unknown capacity policy mode')
        self.config = config or CapacityConfig()
        self.mode = mode
        self.estimator = RateEstimator(self.config)
        self.last_change = -10**9
        self.last_decision = -1
        self.arrival_history = []
        self.moves = np.asarray([a['delta'] for a in catalog()['actions']], dtype=float)
        self.abc = None
        if mode == 'abc_only':
            if uniforms is None:
                raise ValueError('ABC gating requires its explicit random stream')
            self.abc = module().ABC_Causal207(catalog(), np.full(3,69.), np.asarray(uniforms))

    def observe(self, t, staff, flow, end_queue):
        if t != self.last_decision:
            raise ValueError('Observe only after this minute was decided')
        self.estimator.update(t, staff, flow, end_queue)

    def forecasts(self):
        c = self.config
        point = self.estimator.snapshot()
        demand = float(np.mean(self.arrival_history[-c.arrival_window:]))
        rate_paths = []
        demand_paths = []
        for demand_multiplier in (1-c.demand_spread, 1.0, 1+c.demand_spread):
            for rate_mode in ('lower','point','recover_to_prior'):
                if rate_mode == 'lower':
                    path = np.tile(point*c.rate_lower_factor, (c.lookahead,1))
                elif rate_mode == 'point':
                    path = np.tile(point, (c.lookahead,1))
                else:
                    weight = np.linspace(0,1,c.lookahead)[:,None]
                    path = (1-weight)*point + weight*c.nominal_rate
                rate_paths.append(path)
                demand_paths.append(np.full(c.lookahead,demand*demand_multiplier))
        return np.asarray(rate_paths),np.asarray(demand_paths)

    def decide(self, t, queues, history, staff, current_arrival):
        if not isinstance(t,(int,np.integer)) or t != self.last_decision+1:
            raise ValueError('Decide exactly once per consecutive minute')
        if self.estimator.last_observed != t-1:
            raise ValueError('Missing previous observation')
        queues = vector(queues,'queues')
        staff = vector(staff,'staff')
        if not np.isclose(staff.sum(),207.,rtol=0,atol=self.config.tolerance):
            raise ValueError('Budget must equal 207')
        if not np.isfinite(current_arrival) or current_arrival < 0:
            raise ValueError('Invalid arrival')
        if len(history) != min(t,5):
            raise ValueError('Supply the last five end-of-minute queues')
        if any(vector(x,'history').shape != (3,) for x in history):
            raise ValueError('Invalid history')
        self.last_decision = int(t)
        self.arrival_history.append(float(current_arrival))
        self.arrival_history = self.arrival_history[-self.config.arrival_window:]
        note = {'t':int(t),'mode':self.mode,'selected_action_id':None,
                'last_change_before':int(self.last_change),
                'estimator':self.estimator.evidence(t)}
        zero = np.zeros(3)
        if t < self.config.warmup:
            return zero,{**note,'branch':'warmup'}
        if t-self.last_change < self.config.dwell:
            return zero,{**note,'branch':'locked'}

        proposed = None
        candidate_ids = np.flatnonzero((staff+self.moves >= self.config.minimum_staff).all(axis=1))
        if self.mode == 'abc_only':
            # ABC.decide mutates its state. Only this speculative copy may mutate
            # until a proposal has passed the gate.
            proposed = copy.deepcopy(self.abc)
            delta,abc_note = proposed.decide(t,queues.copy(),history,staff.copy())
            note['abc_proposal'] = abc_note
            if not np.any(delta):
                return zero,{**note,'branch':'abc_'+abc_note['branch']}
            action_id = abc_note['selected_action_id']
            if action_id not in candidate_ids:
                return zero,{**note,'branch':'reserve_rejected'}
            candidate_ids = np.array([action_id],dtype=int)
        if not len(candidate_ids):
            return zero,{**note,'branch':'no_feasible_candidate'}

        allocations = np.vstack([staff,staff+self.moves[candidate_ids]])
        rates,demands = self.forecasts()
        costs = prediction_costs(queues,allocations,rates,demands,self.config.terminal_weight)
        hold = costs[:,0]
        gains = hold[:,None]-costs[:,1:]
        mean_gain = gains.mean(axis=0)
        worst_gain = gains.min(axis=0)
        threshold = max(self.config.absolute_gain,self.config.relative_gain*float(hold.mean()))
        allowed_loss = self.config.worst_case_regression_fraction*np.maximum(hold,1.)[:,None]
        admissible = (mean_gain > threshold) & ((gains >= -allowed_loss-self.config.tolerance).all(axis=0))
        note.update(candidate_count=int(len(candidate_ids)),
                    forecast_horizon=self.config.lookahead,
                    forecast_demand=float(demands[3,0]),
                    hold_cost_by_model=hold.tolist(),minimum_mean_gain=threshold,
                    feasible_improving_candidates=int(admissible.sum()))
        # Hold wins all ties. Among improving moves: lower average predicted
        # cost, then fewer moved units, then immutable catalogue action ID.
        ranked = sorted(range(len(candidate_ids)),
                        key=lambda j:(-float(mean_gain[j]),float(np.abs(self.moves[candidate_ids[j]]).sum()),int(candidate_ids[j])))
        chosen = next((j for j in ranked if admissible[j]),None)
        best = ranked[0] if chosen is None else chosen
        note.update(best_considered_action_id=int(candidate_ids[best]),
                    candidate_cost_by_model=costs[:,best+1].tolist(),
                    predicted_mean_gain=float(mean_gain[best]),
                    predicted_worst_gain=float(worst_gain[best]))
        if chosen is None:
            return zero,{**note,'branch':'model_rejected'}
        action_id = int(candidate_ids[chosen])
        if self.mode == 'abc_only':
            self.abc = proposed
        self.last_change = int(t)
        return self.moves[action_id].copy(),{
            **note,'branch':'selected','selected_action_id':action_id,
            'last_change_after':int(t)}
