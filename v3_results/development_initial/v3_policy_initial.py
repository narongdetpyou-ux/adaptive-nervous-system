"""Observation-only v0.3 controls. No scenario labels or true rates are inputs.

The uncertainty bands are engineering stress assumptions, NOT calibrated
confidence intervals or a guarantee against unknown future faults.
"""
from dataclasses import dataclass,asdict
import itertools
import numpy as np
from abc_service.frozen import catalog
from capacity_policy import CapacityConfig,CapacityAwarePolicy,RateEstimator,vector


@dataclass(frozen=True)
class V3Config(CapacityConfig):
    evidence_window: int = 5
    minimum_busy_observations: int = 2
    rate_band: float = 0.15
    sparse_rate_band: float = 0.25

    def __post_init__(self):
        super().__post_init__()
        if type(self.evidence_window) is not int or type(self.minimum_busy_observations) is not int:
            raise ValueError('Evidence counts must be integers')
        if not 1 <= self.minimum_busy_observations <= self.evidence_window:
            raise ValueError('Invalid evidence window')
        if not 0 < self.rate_band <= self.sparse_rate_band < 1:
            raise ValueError('Invalid rate uncertainty')


def transition_prediction(queues,old_staff,allocations,rates,arrivals,delay,terminal_weight=.25):
    """Predict constant candidates with incoming units temporarily unavailable.

The positive part of each candidate transfer arrives after `delay` complete
service intervals; donors depart immediately. Holding loses no resources.
"""
    queues=vector(queues,'queues');old_staff=vector(old_staff,'old staff')
    allocations=np.asarray(allocations,dtype=float)
    rates=np.asarray(rates,dtype=float);arrivals=np.asarray(arrivals,dtype=float)
    if type(delay) is not int or delay<0 or delay>rates.shape[1]:
        raise ValueError('Invalid forecast transition delay')
    if allocations.ndim!=2 or allocations.shape[1]!=3 or (allocations<0).any():
        raise ValueError('Invalid allocations')
    if rates.ndim!=3 or rates.shape[2]!=3 or arrivals.shape!=rates.shape[:2]:
        raise ValueError('Invalid forecast shapes')
    if not all(np.isfinite(x).all() for x in (allocations,rates,arrivals)) or (rates<0).any() or (arrivals<0).any():
        raise ValueError('Invalid forecast values')
    incoming=np.maximum(allocations-old_staff,0.)
    q=np.broadcast_to(queues,(len(rates),len(allocations),3)).copy()
    area=np.zeros(q.shape[:2])
    for h in range(rates.shape[1]):
        if h:q[:,:,0]+=arrivals[:,h,None]
        active=allocations-incoming if h<delay else allocations
        for stage in range(3):
            flow=np.minimum(q[:,:,stage],active[None,:,stage]*rates[:,h,None,stage])
            q[:,:,stage]-=flow
            if stage<2:q[:,:,stage+1]+=flow
        area+=q.sum(axis=2)
    terminal=q.sum(axis=2)
    return area/rates.shape[1]+terminal_weight*terminal,terminal


class V3Policy:
    def __init__(self,mode='full',transit_delay=0,config=None):
        if mode not in ('full','signal_only'):raise ValueError('Invalid v3 mode')
        if type(transit_delay) is not int or transit_delay not in (0,1,3):raise ValueError('Invalid delay')
        self.config=config or V3Config()
        self.mode=mode;self.transit_delay=transit_delay
        # Keep the point estimator and base 9 forecasts identical to v0.2;
        # evidence bounds and vetoes are separate, inspectable additions.
        base_fields=CapacityConfig.__dataclass_fields__
        base_config=CapacityConfig(**{k:v for k,v in asdict(self.config).items() if k in base_fields})
        self.base=CapacityAwarePolicy('catalog',config=base_config)
        self.estimator=self.base.estimator
        self.moves=self.base.moves
        self.last_decision=-1;self.last_change=-10**9
        self.busy_samples=[[] for _ in range(3)]
        self.last_busy_rate=np.full(3,np.nan)
        self.last_flow_bound=np.zeros(3)

    def observe(self,t,active_staff,flow,end_queue):
        if t!=self.last_decision:raise ValueError('Observe after the corresponding decision')
        active_staff=vector(active_staff,'active staff');flow=vector(flow,'flow');end_queue=vector(end_queue,'queue')
        self.estimator.update(t,active_staff,flow,end_queue)
        for stage in range(3):
            self.busy_samples[stage]=[(when,x) for when,x in self.busy_samples[stage] if when>t-self.config.evidence_window]
            if active_staff[stage]>0:
                bound=float(flow[stage]/active_staff[stage]);self.last_flow_bound[stage]=bound
                if end_queue[stage]>self.config.tolerance:
                    self.busy_samples[stage].append((int(t),bound))
                    self.last_busy_rate[stage]=bound
            else:self.last_flow_bound[stage]=0.

    def bounds(self,t):
        point=self.estimator.snapshot();counts=[];lo=[];hi=[]
        for stage in range(3):
            samples=[x for when,x in self.busy_samples[stage] if when>=t-self.config.evidence_window]
            counts.append(len(samples))
            margin=self.config.rate_band if len(samples)>=self.config.minimum_busy_observations else self.config.sparse_rate_band
            low=point[stage]*(1-margin);high=point[stage]*(1+margin)
            if samples:
                low=min(low,min(samples)*(1-self.config.rate_band))
                high=max(high,max(samples)*(1+self.config.rate_band))
            if np.isfinite(self.last_busy_rate[stage]):
                # A prior-driven increase is not observed recovery. A fresh
                # censored lower bound can raise the conservative anchor.
                anchor=max(self.last_busy_rate[stage],self.last_flow_bound[stage])*(1-self.config.rate_band)
                low=min(low,anchor)
            lo.append(max(0.,float(low)));hi.append(float(high))
        return np.array(lo),np.array(hi),np.array(counts,dtype=int)

    def decide(self,t,queues,history,staff,current_arrival):
        if type(t) is not int or t!=self.last_decision+1:raise ValueError('Decide consecutive minutes')
        if self.estimator.last_observed!=t-1:raise ValueError('Missing observation')
        queues=vector(queues,'queues');staff=vector(staff,'committed staff')
        if abs(float(staff.sum())-207)>self.config.tolerance:raise ValueError('Invalid committed budget')
        if len(history)!=min(t,5):raise ValueError('Expected five observed end queues')
        if not np.isfinite(current_arrival) or current_arrival<0:raise ValueError('Invalid current arrival')
        self.last_decision=t
        self.base.arrival_history=(self.base.arrival_history+[float(current_arrival)])[-self.config.arrival_window:]
        low,high,counts=self.bounds(t)
        note={'t':t,'mode':self.mode,'last_change_before':self.last_change,'selected_action_id':None,
              'estimated_rates':self.estimator.snapshot().tolist(),'rate_lower':low.tolist(),
              'rate_upper':high.tolist(),'recent_busy_counts':counts.tolist(),
              'last_observed_minute':self.estimator.last_observed,'known_transit_delay':self.transit_delay}
        zero=np.zeros(3)
        if t<self.config.warmup:return zero,{**note,'branch':'warmup'}
        if t-self.last_change<self.config.dwell:return zero,{**note,'branch':'locked'}
        legal=np.flatnonzero((staff+self.moves>=self.config.minimum_staff).all(axis=1))
        candidates=[];evidence_rejected=0;separation_rejected=0
        for aid in legal:
            move=self.moves[aid];recipient=int(np.argmax(move));donors=np.flatnonzero(move<0)
            if counts[recipient]<self.config.minimum_busy_observations:
                evidence_rejected+=1;continue
            # Require a shortage visible despite independent stage uncertainty.
            # Current committed capacities are used only when no move is in
            # progress (dwell 8 is longer than every modeled transfer delay).
            if high[recipient]*staff[recipient] >= max(low[d]*staff[d] for d in donors):
                separation_rejected+=1;continue
            candidates.append(int(aid))
        note.update(legal_candidates=len(legal),evidence_rejected=evidence_rejected,separation_rejected=separation_rejected)
        if not candidates:return zero,{**note,'branch':'insufficient_capacity_evidence'}
        ids=np.array(candidates,dtype=int)
        allocations=np.vstack([staff,staff+self.moves[ids]])
        rates,arrivals=self.base.forecasts()
        if self.mode=='full':
            extra_rates=[];extra_arrivals=[]
            demand=float(np.mean(self.base.arrival_history))
            for factor in (1-self.config.demand_spread,1.,1+self.config.demand_spread):
                for bits in itertools.product((0,1),repeat=3):
                    r=np.where(np.array(bits,dtype=bool),high,low)
                    extra_rates.append(np.tile(r,(self.config.lookahead,1)))
                    extra_arrivals.append(np.full(self.config.lookahead,demand*factor))
            rates=np.concatenate([rates,np.asarray(extra_rates)])
            arrivals=np.concatenate([arrivals,np.asarray(extra_arrivals)])
        modeled_delay=self.transit_delay if self.mode=='full' else 0
        costs,terminal=transition_prediction(queues,staff,allocations,rates,arrivals,modeled_delay,self.config.terminal_weight)
        gain=costs[:,0,None]-costs[:,1:]
        mean_gain=gain.mean(axis=0);worst=gain.min(axis=0)
        threshold=max(self.config.absolute_gain,self.config.relative_gain*float(costs[:,0].mean()))
        admissible=(mean_gain>threshold)&(worst>=-self.config.tolerance)
        note.update(forecast_models=len(rates),forecast_horizon=self.config.lookahead,
                    modeled_delay=modeled_delay,mean_hold_cost=float(costs[:,0].mean()),minimum_mean_gain=threshold,
                    candidate_count=len(ids),admissible_count=int(admissible.sum()))
        ranked=sorted(range(len(ids)),key=lambda j:(-float(mean_gain[j]),float(abs(self.moves[ids[j]]).sum()),int(ids[j])))
        chosen=next((j for j in ranked if admissible[j]),None)
        best=ranked[0] if chosen is None else chosen
        note.update(best_considered_action_id=int(ids[best]),predicted_mean_gain=float(mean_gain[best]),
                    predicted_worst_gain=float(worst[best]),
                    predicted_worst_terminal_change=float((terminal[:,best+1]-terminal[:,0]).max()))
        if chosen is None:return zero,{**note,'branch':'model_rejected'}
        self.last_change=t;aid=int(ids[chosen])
        return self.moves[aid].copy(),{**note,'branch':'selected','selected_action_id':aid,'last_change_after':t}


class CapacityBalance:
    """Simple observed-rate baseline: approach equal estimated stage capacities.

Uses the same point estimator, legal catalogue, reserve and clocks. It has no
forecast or future data. Persistent observed queues trigger its balancing.
"""
    def __init__(self):
        self.estimator=RateEstimator();self.last_change=-10**9;self.last_decision=-1
        self.moves=np.asarray([a['delta'] for a in catalog()['actions']],dtype=float)
    def observe(self,t,active_staff,flow,end_queue):
        if t!=self.last_decision:raise ValueError('Observe order')
        self.estimator.update(t,active_staff,flow,end_queue)
    def decide(self,t,queues,history,staff,current_arrival):
        if t!=self.last_decision+1 or self.estimator.last_observed!=t-1:raise ValueError('Decision order')
        self.last_decision=t;staff=vector(staff,'staff')
        note={'t':t,'selected_action_id':None,'estimated_rates':self.estimator.snapshot().tolist()}
        zero=np.zeros(3)
        if t<5:return zero,{**note,'branch':'warmup'}
        if t-self.last_change<8:return zero,{**note,'branch':'locked'}
        if len(history)<2 or not np.any((history[-1]>10)&(history[-2]>10)):
            return zero,{**note,'branch':'no_persistent_backlog'}
        rate=np.maximum(self.estimator.snapshot(),1e-9)
        target=207./rate/np.sum(1./rate)
        base=float(np.sum((staff-target)**2))
        ids=np.flatnonzero((staff+self.moves>=10).all(axis=1))
        if not len(ids):return zero,{**note,'branch':'no_candidate'}
        costs=((staff+self.moves[ids]-target)**2).sum(axis=1)
        ranked=sorted(range(len(ids)),key=lambda j:(float(costs[j]),float(abs(self.moves[ids[j]]).sum()),int(ids[j])))
        best=ranked[0];aid=int(ids[best])
        if base-float(costs[best])<=1e-7:return zero,{**note,'branch':'hold_closer_than_moves','target':target.tolist()}
        self.last_change=t
        return self.moves[aid].copy(),{**note,'branch':'selected','selected_action_id':aid,'target':target.tolist()}
