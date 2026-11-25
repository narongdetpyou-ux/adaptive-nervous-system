"""Three-stage fluid experiment with conserved resources in transit.

The original scoring interval and common 32-minute follow-up are separate.
Environment truth is confined to service, never controller inputs.
"""
from collections import Counter
import numpy as np
from abc_service.frozen import catalog
from capacity_policy import CapacityAwarePolicy
from v3_policy import V3Policy,V3Config,CapacityBalance
from v3_scenarios import GROUP_INDEX,REGIME_DELAYS,FOLLOWUP_MINUTES,POLICIES,policy_seeds

TOL=1e-6


def make_controller(policy,scenario_seed,group,policy_seed,total_horizon,delay,config=None):
    if policy not in POLICIES or policy_seed not in policy_seeds(policy):raise ValueError('Invalid policy seed')
    if policy=='static':return None
    if policy=='capacity_v2':return CapacityAwarePolicy('catalog')
    if policy=='abc_capacity':
        rng=np.random.default_rng(np.random.SeedSequence([policy_seed,scenario_seed,GROUP_INDEX[group]]))
        return CapacityAwarePolicy('abc_only',uniforms=rng.random(total_horizon))
    if policy=='capacity_balance':return CapacityBalance()
    return V3Policy(mode='full' if policy=='v3_full' else 'signal_only',transit_delay=delay,config=config)


class TransitLedger:
    def __init__(self,delay):
        if delay not in (0,1,3):raise ValueError('Unsupported transit duration')
        self.delay=delay;self.committed=np.full(3,69.);self.active=self.committed.copy()
        self.pending=np.zeros(3);self.ready_at=None;self.last_move=-10**9
        self.permitted={tuple(a['delta']) for a in catalog()['actions']}
        self.last_start=-1
    def start(self,t):
        if t!=self.last_start+1:raise ValueError('Nonconsecutive environment minute')
        self.last_start=t
        if self.ready_at is not None and t>=self.ready_at:
            self.active+=self.pending;self.pending[:]=0.;self.ready_at=None
        self.check()
    def apply(self,t,delta):
        delta=np.asarray(delta,dtype=float)
        if delta.shape!=(3,) or not np.isfinite(delta).all() or abs(delta.sum())>TOL:raise ValueError('Invalid transfer')
        if not np.any(delta):return
        if tuple(delta) not in self.permitted or t<5 or t-self.last_move<8:raise ValueError('Catalogue/warmup/dwell violation')
        if self.pending.any() or self.ready_at is not None:raise ValueError('Cannot overlap transfers')
        target=self.committed+delta
        if min(target)<10:raise ValueError('Minimum stage reserve violation')
        self.committed=target;self.last_move=t
        if self.delay==0:self.active+=delta
        else:
            self.active+=np.minimum(delta,0.)
            self.pending=np.maximum(delta,0.)
            self.ready_at=t+self.delay
        self.check()
    def check(self):
        error=max(abs(float(self.committed.sum())-207.),abs(float(self.active.sum()+self.pending.sum())-207.),float(np.max(abs(self.committed-self.active-self.pending))))
        if error>TOL or min(self.active)<0 or min(self.pending)<0:raise ValueError('Resource conservation failed')
        return error


def simulate_case(policy,scenario,policy_seed=None,regime='instant',detailed=False,config=None):
    if regime not in REGIME_DELAYS:raise ValueError('Invalid regime')
    delay=REGIME_DELAYS[regime];H=scenario.horizon;total_horizon=H+FOLLOWUP_MINUTES
    ctrl=make_controller(policy,scenario.seed,scenario.group,policy_seed,total_horizon,delay,config)
    ledger=TransitLedger(delay);q=np.zeros(3);history=[];arrived=0.;completed=0.
    total_queues=[];completion=[];lost=[];moves_at=[];mass_max=0.;budget_max=0.;trace=[];branches=Counter()
    for t in range(total_horizon):
        ledger.start(t)
        arrival=float(scenario.arrivals[t]) if t<H else 0.
        rates=scenario.productivity[t] if t<H else scenario.productivity[-1]
        q[0]+=arrival;arrived+=arrival;observed=q.copy();before=ledger.committed.copy()
        if ctrl is None:delta=np.zeros(3);note={'branch':'fixed'}
        else:delta,note=ctrl.decide(t,q.copy(),[x.copy() for x in history],before.copy(),arrival)
        ledger.apply(t,delta);branches[note['branch']]+=1
        if np.any(delta):moves_at.append(t)
        budget_max=max(budget_max,ledger.check());lost.append(float(ledger.pending.sum()))
        flow=np.zeros(3)
        for stage in range(3):
            flow[stage]=min(q[stage],ledger.active[stage]*rates[stage])
            q[stage]-=flow[stage]
            if stage<2:q[stage+1]+=flow[stage]
            else:completed+=flow[stage]
        mass=abs(arrived-completed-float(q.sum()));mass_max=max(mass_max,float(mass))
        if mass>TOL or min(q)<0:raise ValueError('Work conservation failed')
        if ctrl is not None:ctrl.observe(t,ledger.active.copy(),flow.copy(),q.copy())
        history=(history+[q.copy()])[-5:]
        total_queues.append(float(q.sum()));completion.append(float(completed))
        if detailed:
            trace.append({'t':t,'phase':'scoring' if t<H else 'followup','arrival':arrival,
                          'observed':observed.tolist(),'productivity':rates.tolist(),
                          'staff_before':before.tolist(),'committed_staff':ledger.committed.tolist(),
                          'active_staff':ledger.active.tolist(),'pending_staff':ledger.pending.tolist(),
                          'ready_at':ledger.ready_at,'delta':delta.tolist(),'flow':flow.tolist(),
                          'queue':q.tolist(),'completed_cumulative':float(completed),
                          'resource_minutes_lost':float(ledger.pending.sum()),'branch':note['branch'],
                          'decision_evidence':note})
    primary=total_queues[:H];tail=total_queues[H:]
    clear=0 if primary[-1]<=TOL else next((j+1 for j,x in enumerate(tail) if x<=TOL),None)
    row={'regime':regime,'group':scenario.group,'scenario_seed':scenario.seed,'source':scenario.source,
         'policy':policy,'policy_seed':policy_seed,'horizon':H,'followup_minutes':FOLLOWUP_MINUTES,
         'mean_backlog':float(np.mean(primary)),'peak_backlog':max(primary),'end_backlog':primary[-1],
         'arrivals':arrived,'completed':completion[H-1],'moves':sum(t<H for t in moves_at),
         'tail_mean_backlog':float(np.mean(tail)),'tail_auc':float(sum(tail)),'tail_end_backlog':tail[-1],
         'tail_completed':completion[-1]-completion[H-1],'total_completed':completion[-1],
         'tail_moves':sum(t>=H for t in moves_at),'total_moves':len(moves_at),
         'drain_time_observed':clear,'drain_time_capped':FOLLOWUP_MINUTES if clear is None else clear,
         'drain_censored':clear is None,'resource_minutes_lost':float(sum(lost[:H])),
         'tail_resource_minutes_lost':float(sum(lost[H:])),
         'in_transit_at_end':float(ledger.pending.sum()),
         'outstanding_resource_minutes':0. if ledger.ready_at is None else float(ledger.pending.sum())*max(0,ledger.ready_at-total_horizon),
         'max_mass_error':mass_max,'max_budget_error':budget_max,
         'evidence_holds':branches['insufficient_capacity_evidence'],'model_rejections':branches['model_rejected']}
    return row,trace
