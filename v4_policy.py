"""Observation-only balance proposals with a finite, inspectable risk score.

Engineering stress scenarios are not calibrated outcome probabilities.  The
upper-quartile stress penalty below is distinct from empirical CVaR90 across
held-out episodes.  Only physical feasibility is an unconditional action veto.
"""
from dataclasses import dataclass, asdict
import itertools
import numpy as np

from abc_service.frozen import catalog
from capacity_policy import CapacityConfig, RateEstimator, vector
from v4_environment import EnvironmentConfig, project_action


@dataclass(frozen=True)
class V4Config(CapacityConfig):
    lookahead: int = 16
    terminal_weight: float = 0.5
    relative_gain: float = 0.01
    risk_weight: float = 0.3
    maximum_transfer: float = 20.0
    evidence_window: int = 5
    rate_band: float = 0.15
    sparse_rate_band: float = 0.25
    stale_anchor_minutes: int = 8
    pressure_time_scale: float = 8.0
    escape_floor: float = 0.25
    idle_recovery_observations: int = 3
    balance_preference_fraction: float = 0.1
    stress_tail_fraction: float = 0.25

    def __post_init__(self):
        super().__post_init__()
        for name in ('evidence_window','stale_anchor_minutes','idle_recovery_observations'):
            if type(getattr(self,name)) is not int or getattr(self,name)<1:
                raise ValueError('Invalid integer setting: '+name)
        for name in ('risk_weight','maximum_transfer','rate_band','sparse_rate_band',
                     'pressure_time_scale','escape_floor','balance_preference_fraction','stress_tail_fraction'):
            if not np.isfinite(getattr(self,name)):
                raise ValueError('Nonfinite configuration: '+name)
        if self.risk_weight<0 or self.maximum_transfer<=0 or self.pressure_time_scale<=0:
            raise ValueError('Invalid action/risk scale')
        if not 0<self.rate_band<=self.sparse_rate_band<1:
            raise ValueError('Invalid stress bands')
        if not 0<self.escape_floor<=1 or not 0<=self.balance_preference_fraction<1:
            raise ValueError('Invalid recovery/arbitration setting')
        if not 0<self.stress_tail_fraction<=1:
            raise ValueError('Invalid stress tail fraction')


def forecast_costs(queues, old_staff, allocations, rates, arrivals, environment,
                   terminal_weight=0.5):
    """Vectorized model of known transfer/startup/buffer/pipeline constraints.

    The current arrival is already in queues.  Past drops are a common sunk
    deficit and cancel in action comparisons; future drops stay in the cost.
    Decisions are eligible only after all previous resource transitions finish.
    This function accepts predicted rates, never actual environment arrays.
    """
    queues=vector(queues,'forecast queues');old_staff=vector(old_staff,'forecast old staff')
    allocations=np.asarray(allocations,dtype=float)
    rates=np.asarray(rates,dtype=float);arrivals=np.asarray(arrivals,dtype=float)
    if not isinstance(environment,EnvironmentConfig):raise TypeError('Known environment config required')
    if allocations.ndim!=2 or allocations.shape[1]!=3:
        raise ValueError('Invalid allocation matrix')
    if rates.ndim!=3 or rates.shape[2]!=3 or arrivals.shape!=rates.shape[:2] or rates.shape[1]<1:
        raise ValueError('Invalid forecast shape')
    if any(not np.isfinite(x).all() or (x<0).any() for x in (allocations,rates,arrivals)):
        raise ValueError('Invalid forecast values')
    incoming=np.maximum(allocations-old_staff,0.)
    q=np.broadcast_to(queues,(len(rates),len(allocations),3)).copy()
    inflight=np.zeros_like(q);dropped=np.zeros(q.shape[:2]);area=np.zeros(q.shape[:2])
    caps=np.array([np.inf if x is None else x for x in environment.buffer_capacities])
    for h in range(rates.shape[1]):
        if h:
            q+=inflight;inflight[:]=0
            q[:,:,0]+=arrivals[:,h,None]
        overflow=np.maximum(q-caps,0.)
        dropped+=overflow.sum(axis=2);q=np.minimum(q,caps)
        if h<environment.transit_delay:
            effective=allocations-incoming
        else:
            age=h-environment.transit_delay
            factor=environment.startup_factors[age] if age<len(environment.startup_factors) else 1.
            effective=allocations-(1-factor)*incoming
        capacity=effective[None,:,:]*rates[:,h,None,:]
        if environment.same_minute_forwarding:
            for stage in range(3):
                flow=np.minimum(q[:,:,stage],capacity[:,:,stage]);q[:,:,stage]-=flow
                if stage<2:
                    q[:,:,stage+1]+=flow
                    overflow=np.maximum(q[:,:,stage+1]-caps[stage+1],0.)
                    dropped+=overflow;q[:,:,stage+1]-=overflow
        else:
            flow=np.minimum(q,capacity);q-=flow
            inflight[:,:,1:]=flow[:,:,:2]
        retained=q.sum(axis=2)+inflight.sum(axis=2)
        deficit=retained+dropped;area+=deficit
    return {'cost':area/rates.shape[1]+terminal_weight*deficit,
            'terminal_deficit':deficit,'terminal_retained':retained,'new_drops':dropped}


class HybridPolicy:
    def __init__(self,environment,mode='hybrid',config=None):
        if mode not in ('hybrid','no_risk','no_recovery'):
            raise ValueError('Unknown hybrid ablation')
        if not isinstance(environment,EnvironmentConfig):raise TypeError('Environment config required')
        self.environment=environment;self.mode=mode;self.config=config or V4Config()
        if self.config.dwell<environment.dwell_minutes or self.config.warmup<environment.warmup_minutes:
            raise ValueError('Policy clocks cannot violate environment clocks')
        self.estimator=RateEstimator(self.config)
        self.moves=np.array([x['delta'] for x in catalog()['actions']],dtype=float)
        self.last_decision=-1;self.last_change=-10**9
        self.arrival_history=[];self.samples=[[] for _ in range(3)]
        self.last_busy_rate=np.full(3,np.nan);self.last_busy_t=np.full(3,-1,dtype=int)
        self.last_flow_bound=np.zeros(3)
        self.pressure_streak=0;self.idle_streak=0;self.last_queue_sum=0.

    def observe(self,t,effective_staff,flow,end_queue):
        if t!=self.last_decision:raise ValueError('Observe after the corresponding decision')
        effective_staff=vector(effective_staff,'effective resources')
        flow=vector(flow,'measured flow');q=vector(end_queue,'measured end queue')
        self.estimator.update(t,effective_staff,flow,q)
        source_t=t-self.environment.sensor_lag
        for stage in range(3):
            self.samples[stage]=[(when,x) for when,x in self.samples[stage]
                                 if when>source_t-self.config.evidence_window]
            bound=float(flow[stage]/effective_staff[stage]) if effective_staff[stage]>0 else 0.
            self.last_flow_bound[stage]=bound
            if source_t>=0 and effective_staff[stage]>0 and q[stage]>self.config.tolerance:
                self.samples[stage].append((source_t,bound))
                self.last_busy_rate[stage]=bound;self.last_busy_t[stage]=source_t
        total=float(q.sum())
        # A 2% slack avoids resetting a growth streak for small sensor jitter.
        growing=total>10 and total>=self.last_queue_sum-max(10.,.02*self.last_queue_sum)
        self.pressure_streak=self.pressure_streak+1 if growing and source_t>=0 else 0
        self.idle_streak=self.idle_streak+1 if total<=10 and source_t>=0 else 0
        self.last_queue_sum=total

    def bounds(self,t):
        point=self.estimator.snapshot();lower=[];upper=[];counts=[];ages=[]
        for stage in range(3):
            values=[value for when,value in self.samples[stage]
                    if when>=t-self.environment.sensor_lag-self.config.evidence_window]
            count=len(values);counts.append(count)
            age=None if self.last_busy_t[stage]<0 else int(t-self.last_busy_t[stage])
            ages.append(age)
            band=self.config.rate_band if count>=2 else self.config.sparse_rate_band
            # The known measurement delay adds uncertainty, not future truth.
            band=min(.45,band+.025*self.environment.sensor_lag)
            lo=point[stage]*(1-band);hi=point[stage]*(1+band)
            if values:
                lo=min(lo,min(values)*(1-self.config.rate_band))
                hi=max(hi,max(values)*(1+self.config.rate_band))
            if age is not None and age<=self.config.stale_anchor_minutes:
                anchor=max(self.last_busy_rate[stage],self.last_flow_bound[stage])*(1-self.config.rate_band)
                lo=min(lo,anchor)
            lower.append(max(0.,float(lo)));upper.append(float(hi))
        return np.array(lower),np.array(upper),counts,ages

    def forecasts(self,lower,upper):
        point=self.estimator.snapshot();h=self.config.lookahead
        demand=float(np.mean(self.arrival_history))
        paths=[np.tile(point*self.config.rate_lower_factor,(h,1)),np.tile(point,(h,1)),
               point[None,:]+np.linspace(0,1,h)[:,None]*(self.config.nominal_rate-point)[None,:]]
        rates=[];arrivals=[]
        factors=(1-self.config.demand_spread,1.,1+self.config.demand_spread)
        for factor in factors:
            for path in paths:
                rates.append(path);arrivals.append(np.full(h,demand*factor))
        for factor in factors:
            for bits in itertools.product((0,1),repeat=3):
                rate=np.where(np.array(bits,dtype=bool),upper,lower)
                rates.append(np.tile(rate,(h,1)));arrivals.append(np.full(h,demand*factor))
        return np.asarray(rates),np.asarray(arrivals)

    def decide(self,t,queues,history,staff,current_arrival):
        if type(t) is not int or t!=self.last_decision+1 or self.estimator.last_observed!=t-1:
            raise ValueError('Decide once per consecutive delivery minute')
        queues=vector(queues,'measured start queue');staff=vector(staff,'committed resources')
        if abs(float(staff.sum())-self.environment.budget)>self.config.tolerance:
            raise ValueError('Invalid current resource budget')
        if len(history)!=min(t,5):raise ValueError('Expected observed five-minute history')
        if not np.isfinite(current_arrival) or current_arrival<0:raise ValueError('Invalid measured arrival')
        self.last_decision=t
        self.arrival_history=(self.arrival_history+[float(current_arrival)])[-self.config.arrival_window:]
        lower,upper,counts,ages=self.bounds(t)
        point=self.estimator.snapshot();target=self.environment.budget/np.maximum(point,1e-9)
        target=target/np.sum(target)*self.environment.budget
        note={'t':t,'mode':self.mode,'selected_action_id':None,'last_change_before':self.last_change,
              'estimated_rates':point.tolist(),'rate_lower':lower.tolist(),'rate_upper':upper.tolist(),
              'recent_busy_counts':counts,'busy_observation_ages':ages,
              'balance_target':target.tolist(),'pressure_streak':self.pressure_streak,
              'idle_streak':self.idle_streak,'sensor_lag':self.environment.sensor_lag,
              'latest_feedback_source_t':t-1-self.environment.sensor_lag}
        zero=np.zeros(3)
        if t<self.config.warmup:return zero,{**note,'branch':'warmup'}
        if t-self.last_change<self.config.dwell:return zero,{**note,'branch':'locked'}
        ids=[];actual=[]
        for aid,raw in enumerate(self.moves):
            if float(np.maximum(raw,0).sum())>self.config.maximum_transfer+self.config.tolerance:continue
            if min(staff+raw)<self.config.minimum_staff:continue
            applied=project_action(staff,raw,self.environment)
            if float(np.maximum(applied,0).sum())>self.config.maximum_transfer+self.config.tolerance:continue
            if not np.any(applied):continue
            ids.append(aid);actual.append(applied)
        if not ids:return zero,{**note,'branch':'no_feasible_move'}
        ids=np.array(ids,dtype=int);actual=np.array(actual)
        allocations=np.vstack([staff,staff+actual])
        distance=((allocations-target)**2).sum(axis=1)
        balance_order=sorted(range(len(ids)),key=lambda j:(float(distance[j+1]),
                             float(abs(actual[j]).sum()),int(ids[j])))
        balance_index=balance_order[0] if distance[balance_order[0]+1]<distance[0]-self.config.tolerance else None
        rates,arrivals=self.forecasts(lower,upper)
        predicted=forecast_costs(queues,staff,allocations,rates,arrivals,self.environment,self.config.terminal_weight)
        costs=predicted['cost'];gain=costs[:,0,None]-costs[:,1:]
        mean_gain=gain[:9].mean(axis=0)
        stress_harm=np.maximum(-gain[9:],0.)
        tail_count=max(1,int(np.ceil(len(stress_harm)*self.config.stress_tail_fraction)))
        stress_tail=np.sort(stress_harm,axis=0)[-tail_count:].mean(axis=0)
        imbalance=float(abs(staff-target).sum()/2)
        escape=1.
        if self.mode!='no_recovery' and imbalance>=5:
            escape=max(self.config.escape_floor,1./(1.+self.pressure_streak/self.config.pressure_time_scale))
        risk_weight=0. if self.mode=='no_risk' else self.config.risk_weight*escape
        adjusted=mean_gain-risk_weight*stress_tail
        # Flow scale is independent of accumulated backlog. An old allocation
        # cannot become impossible to fix merely because its queue grew larger.
        hurdle=max(self.config.absolute_gain,self.config.relative_gain*float(np.mean(self.arrival_history)))*escape
        note.update(forecast_models=len(rates),forecast_horizon=self.config.lookahead,
                    feasible_moves=len(ids),balance_action_id=None if balance_index is None else int(ids[balance_index]),
                    imbalance=imbalance,escape_factor=escape,effective_risk_weight=risk_weight,
                    mean_hold_cost=float(costs[:9,0].mean()),gain_hurdle=hurdle)
        ranked=sorted(range(len(ids)),key=lambda j:(-float(adjusted[j]),float(abs(actual[j]).sum()),int(ids[j])))
        best=ranked[0];chosen=None;branch='soft_risk_hold'
        if adjusted[best]>hurdle:
            chosen=best;branch='model_selected'
            if balance_index is not None and adjusted[balance_index]>hurdle and adjusted[balance_index]>=(1-self.config.balance_preference_fraction)*adjusted[best]:
                chosen=balance_index;branch='balance_preferred'
        elif self.mode!='no_recovery' and self.idle_streak>=self.config.idle_recovery_observations:
            # A model-neutral return toward the observed-rate balance is
            # allowed during idle service. It need not invent positive queue
            # reduction when both allocations already empty every queue.
            for j in balance_order:
                if distance[j+1]>=distance[0]-self.config.tolerance:break
                if mean_gain[j]>=-self.config.tolerance and float(gain[:,j].min())>=-self.config.absolute_gain:
                    chosen=j;branch='idle_balance_recovery';break
        considered=best if chosen is None else chosen
        note.update(best_considered_action_id=int(ids[considered]),
                    predicted_mean_gain=float(mean_gain[considered]),
                    predicted_stress_tail_harm=float(stress_tail[considered]),
                    adjusted_gain=float(adjusted[considered]),
                    predicted_mean_terminal_change=float((predicted['terminal_deficit'][:9,considered+1]-predicted['terminal_deficit'][:9,0]).mean()))
        if chosen is None:return zero,{**note,'branch':branch}
        self.last_change=t;aid=int(ids[chosen])
        return self.moves[aid].copy(),{**note,'branch':branch,'selected_action_id':aid,
                                     'applied_delta_forecast':actual[chosen].tolist(),'last_change_after':t}
