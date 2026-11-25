"""Research prototype, deterministic, no environment truth or scenario labels."""
from dataclasses import dataclass
import itertools
import numpy as np
from v4_policy import HybridPolicy, V4Config
from v4_environment import project_action
from capacity_policy import vector
from v5_observer import ActionLedger
from v5_regime import RegimeDetector, switching_paths
from v5_forecast import score, loss_candidates

FEATURES=('observer','loss','regime','balance','multihorizon')
VARIANTS={'v5_'+k:(feature,) for k,feature in zip('abcde',FEATURES)}
VARIANTS['v5_full']=FEATURES
VARIANTS.update({'v5_no_'+f:tuple(x for x in FEATURES if x!=f) for f in FEATURES})

@dataclass(frozen=True)
class V5Config(V4Config):
    risk_weight: float = .1
    # Match actual V4 selected config, not the original default.
    loss_tolerance: float = 1e-6

class V5Policy(HybridPolicy):
    def __init__(self,environment,variant='v5_full',config=None):
        super().__init__(environment,config=config or V5Config())
        self.features=VARIANTS[variant]
        self.variant=variant
        self.ledger=ActionLedger(environment)
        self.detector=RegimeDetector()

    def observe(self,t,effective_staff,flow,end_queue):
        super().observe(t,effective_staff,flow,end_queue)
        self.detector.observe(t-self.environment.sensor_lag,np.asarray(effective_staff),
                              np.asarray(flow),np.asarray(end_queue))

    def decide(self,t,queues,history,staff,current_arrival):
        if type(t) is not int or t!=self.last_decision+1 or self.estimator.last_observed!=t-1:
            raise ValueError('decision chronology')
        queues=vector(queues,'queues');staff=vector(staff,'staff')
        if abs(staff.sum()-self.environment.budget)>self.config.tolerance or len(history)!=min(t,5):
            raise ValueError('invalid input')
        if not np.isfinite(current_arrival) or current_arrival<0:
            raise ValueError('invalid arrival')
        self.last_decision=t
        c=self.config;env=self.environment
        self.arrival_history=(self.arrival_history+[float(current_arrival)])[-c.arrival_window:]
        demand=float(np.mean(self.arrival_history));point=self.estimator.snapshot()
        lower,upper,_,_=self.bounds(t)
        q=self.ledger.reconstruct(t,queues,demand,point) if 'observer' in self.features else queues
        note={'t':t,'variant':self.variant,'selected_action_id':None,
              'estimated_queue':q.tolist(),'estimated_rates':point.tolist(),
              'switching':self.detector.switching,'latest_feedback_source_t':t-1-env.sensor_lag}
        zero=np.zeros(3)
        def hold(branch):
            return zero.copy(),{**note,'branch':branch}
        if t<c.warmup:return hold('warmup')
        if t-self.last_change<c.dwell:return hold('locked')
        raw=[zero];actual=[zero];ids=[-1]
        for aid,move in enumerate(self.moves):
            if min(staff+move)<env.minimum_staff:continue
            delta=project_action(staff,move,env)
            # Independent balance variant uses full physically legal catalogue.
            if 'balance' not in self.features and (np.maximum(move,0).sum()>c.maximum_transfer+c.tolerance or np.maximum(delta,0).sum()>c.maximum_transfer+c.tolerance):continue
            if np.any(delta):raw.append(move);actual.append(delta);ids.append(aid)
        if len(ids)==1:return hold('no_feasible_move')
        allocations=staff+np.asarray(actual)
        target=env.budget/np.maximum(point,1e-9);target*=env.budget/target.sum()
        distance=np.square(allocations-target).sum(axis=1)
        movement=np.abs(actual).sum(axis=1)
        balance=min(range(len(ids)),key=lambda j:(distance[j],movement[j],ids[j]))
        persistent=len(history)>=2 and np.any((np.asarray(history[-1])>10)&(np.asarray(history[-2])>10))
        if not persistent:balance=0
        horizons=(8,16,32) if 'multihorizon' in self.features else (c.lookahead,)
        h=horizons[-1]
        paths=[np.tile(point*c.rate_lower_factor,(h,1)),np.tile(point,(h,1)),
               point[None,:]+np.linspace(0,1,h)[:,None]*(c.nominal_rate-point)[None,:]]
        rates=[];arr=[]
        for factor in (1-c.demand_spread,1.,1+c.demand_spread):
            for path in paths:
                rates.append(path);arr.append(np.full(h,demand*factor))
        for factor in (1-c.demand_spread,1.,1+c.demand_spread):
            for bits in itertools.product((0,1),repeat=3):
                rates.append(np.tile(np.where(bits,upper,lower),(h,1)));arr.append(np.full(h,demand*factor))
            if 'regime' in self.features:
                for path in switching_paths(point,lower,upper,h,self.detector.switching):
                    rates.append(path);arr.append(np.full(h,demand*factor))
        costs,drops=score(q,staff,allocations,np.asarray(rates),np.asarray(arr),env,c.terminal_weight,horizons)
        gain=costs[:,0,None]-costs
        mean=gain[:9].mean(axis=0)
        stress=np.maximum(-gain[9:],0.)
        n=max(1,int(np.ceil(len(stress)*c.stress_tail_fraction)))
        tail=np.sort(stress,axis=0)[-n:].mean(axis=0)
        imbalance=np.abs(staff-target).sum()/2
        escape=max(c.escape_floor,1/(1+self.pressure_streak/c.pressure_time_scale)) if imbalance>=5 else 1.
        adjusted=mean-c.risk_weight*escape*tail
        hurdle=max(c.absolute_gain,c.relative_gain*demand)*escape
        allowed=np.ones(len(ids),dtype=bool)
        if 'loss' in self.features:
            allowed,avg_loss,worst_loss=loss_candidates(drops,[0,balance],c.loss_tolerance)
        else:
            avg_loss=drops.mean(axis=0);worst_loss=drops.max(axis=0)
        ranking=sorted(np.flatnonzero(allowed),key=lambda j:(-adjusted[j],movement[j],ids[j]))
        best=int(ranking[0])
        if 'balance' in self.features and allowed[balance] and adjusted[balance]>hurdle and adjusted[balance]>=(1-c.balance_preference_fraction)*adjusted[best]:best=balance
        # A loss veto may require action even when gain is below the usual hurdle.
        if allowed[0] and adjusted[best]<=hurdle:
            best=0
            if self.idle_streak>=c.idle_recovery_observations:
                for j in sorted(range(1,len(ids)),key=lambda j:(distance[j],movement[j],ids[j])):
                    if allowed[j] and distance[j]<distance[0]-c.tolerance and mean[j]>=-c.tolerance and gain[:,j].min()>=-c.absolute_gain:
                        best=j;break
        note.update(balance_action_id=None if balance==0 else ids[balance],
                    horizon=list(horizons),predicted_mean_drops=float(avg_loss[best]),
                    predicted_worst_drops=float(worst_loss[best]),hold_allowed=bool(allowed[0]),
                    predicted_gain=float(adjusted[best]),eligible_actions=int(allowed.sum()))
        if best==0:return hold('v5_hold')
        self.ledger.record(t,staff,raw[best])
        self.last_change=t
        return raw[best].copy(),{**note,'branch':'v5_selected','selected_action_id':ids[best]}
