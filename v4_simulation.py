"""Causal sensor adapter and separate physical/deficit scoring for v0.4.

Controllers receive only sampled observations and their current commanded
allocation.  The full environment record (including true rates) is retained
only for independent verification.  All comparator feedback uses resource
exposure from the same source interval as the measured flow.
"""
from collections import Counter
import numpy as np

from capacity_policy import CapacityAwarePolicy
from v3_policy import V3Policy, CapacityBalance
from v4_policy import HybridPolicy, V4Config
from v4_environment import Env, EnvironmentConfig, REGIME_CONFIGS
from v4_scenarios import POLICIES, FOLLOWUP_MINUTES, drain_arrays

TOL=1e-6


def make_controller(policy,environment,config=None):
    if policy not in POLICIES:raise ValueError('Unknown policy')
    if isinstance(environment,str):environment=REGIME_CONFIGS[environment]
    if not isinstance(environment,EnvironmentConfig):raise TypeError('Environment config required')
    if policy=='static':return None
    if policy=='capacity_v2':return CapacityAwarePolicy('catalog')
    if policy=='capacity_balance':return CapacityBalance()
    if policy=='v3_full':return V3Policy('full',transit_delay=environment.transit_delay)
    modes={'v4_hybrid':'hybrid','v4_no_risk':'no_risk','v4_no_recovery':'no_recovery'}
    return HybridPolicy(environment,mode=modes[policy],config=config or V4Config())


def _measurement(channel,values,source_t,scenario,environment):
    measured=np.asarray(values,dtype=float).copy()
    if environment.sensor_noise_sigma>0:
        measured*=scenario.sensor_noise[channel][source_t]
    return measured


def simulate_case(policy,scenario,policy_seed=None,regime='legacy_instant',detailed=False,config=None):
    if policy_seed is not None:raise ValueError('All v4 policies are deterministic')
    if regime not in REGIME_CONFIGS:raise ValueError('Unknown physical regime')
    environment=REGIME_CONFIGS[regime];env=Env(environment)
    ctrl=make_controller(policy,environment,config)
    arrivals,productivity=drain_arrays(scenario)
    horizon=scenario.horizon;total=horizon+FOLLOWUP_MINUTES
    pre_records=[];post_records=[];history=[];trace=[];moves=[];branches=Counter()
    deficits=[];retained=[];completions=[];drops=[];pending_loss=[];startup_loss=[];inflight=[]
    service_queue_peak=0.
    for t in range(total):
        before=env.begin(t,float(arrivals[t]));pre_records.append(before)
        source=t-environment.sensor_lag
        if source<0:
            observed_queue=np.zeros(3);observed_arrival=0.
        else:
            sampled=pre_records[source]
            observed_queue=_measurement('pre_queue',sampled['start_queue'],source,scenario,environment)
            observed_arrival=float(_measurement('arrival',sampled['arrival'],source,scenario,environment))
        decision_input={
            'delivery_t':t,'source_t':None if source<0 else source,
            'observation_age':None if source<0 else t-source,
            'queues':observed_queue.tolist(),'arrival':observed_arrival,
            'history':[q.tolist() for q in history],
            'committed_staff':env.committed.tolist(),
        }
        if ctrl is None:
            raw=np.zeros(3);note={'branch':'fixed'}
        else:
            raw,note=ctrl.decide(t,observed_queue.copy(),[q.copy() for q in history],
                                 env.committed.copy(),observed_arrival)
        record=env.service(t,raw,productivity[t]);post_records.append(record)
        if np.any(record['applied_delta']):moves.append(t)
        branches[note['branch']]+=1
        if source<0:
            feedback_staff=np.full(3,environment.budget/3)
            feedback_flow=np.zeros(3);feedback_queue=np.zeros(3)
        else:
            sampled=post_records[source]
            feedback_staff=np.array(sampled['effective_staff'],dtype=float)
            feedback_flow=_measurement('flow',sampled['flow'],source,scenario,environment)
            feedback_queue=_measurement('end_queue',sampled['end_queue'],source,scenario,environment)
        feedback_input={
            'delivery_t':t,'source_t':None if source<0 else source,
            'observation_age':None if source<0 else t-source,
            'effective_staff':feedback_staff.tolist(),'flow':feedback_flow.tolist(),
            'end_queue':feedback_queue.tolist(),
            'resource_normalization':'same-source known effective service units, including startup',
        }
        if ctrl is not None:
            ctrl.observe(t,feedback_staff.copy(),feedback_flow.copy(),feedback_queue.copy())
        history=(history+[feedback_queue.copy()])[-5:]
        deficits.append(record['drop_adjusted_deficit']);retained.append(record['retained_work'])
        completions.append(record['completed_cumulative']);drops.append(record['cumulative_drops'])
        pending_loss.append(record['pending_resource_minutes']);startup_loss.append(record['startup_capacity_minutes'])
        inflight.append(float(sum(record['inflight_work'])))
        service_queue_peak=max(service_queue_peak,float(sum(record['end_queue'])))
        if detailed:
            trace.append({**record,'phase':'primary' if t<horizon else 'followup',
                          'decision_input':decision_input,'feedback_input':feedback_input,
                          'branch':note['branch'],'decision_evidence':note})
    first=deficits[:horizon];tail=deficits[horizon:]
    queue_first=retained[:horizon];queue_tail=retained[horizon:]
    clear=0 if queue_first[-1]<=TOL else next((i+1 for i,q in enumerate(queue_tail) if q<=TOL),None)
    outstanding_pending=0. if env.ready_at is None else float(env.pending.sum())*max(env.ready_at-total,0)
    row={
        'regime':regime,'group':scenario.group,'scenario_seed':scenario.seed,'source':scenario.source,
        'policy':policy,'policy_seed':None,'horizon':horizon,'followup_minutes':FOLLOWUP_MINUTES,
        'mean_deficit':float(np.mean(first)),'peak_deficit':float(max(first)),'end_deficit':float(first[-1]),
        'tail_mean_deficit':float(np.mean(tail)),'tail_auc_deficit':float(sum(tail)),'tail_end_deficit':float(tail[-1]),
        'mean_queue':float(np.mean(queue_first)),'peak_queue':float(max(queue_first)),'end_queue':float(queue_first[-1]),
        'tail_mean_queue':float(np.mean(queue_tail)),'tail_auc_queue':float(sum(queue_tail)),'tail_end_queue':float(queue_tail[-1]),
        'offered':float(sum(arrivals[:horizon])),
        'completed':completions[horizon-1],'tail_completed':completions[-1]-completions[horizon-1],
        'total_completed':completions[-1],
        'dropped':drops[horizon-1],'tail_dropped':drops[-1]-drops[horizon-1],'total_dropped':drops[-1],
        'moves':sum(t<horizon for t in moves),'tail_moves':sum(t>=horizon for t in moves),'total_moves':len(moves),
        'resource_minutes_lost':float(sum(pending_loss[:horizon])),
        'tail_resource_minutes_lost':float(sum(pending_loss[horizon:])),
        'startup_capacity_minutes_lost':float(sum(startup_loss[:horizon])),
        'tail_startup_capacity_minutes_lost':float(sum(startup_loss[horizon:])),
        'drain_time_observed':clear,'drain_time_capped':FOLLOWUP_MINUTES if clear is None else clear,
        'drain_censored':clear is None,
        'end_inflight_work':inflight[horizon-1],'tail_end_inflight_work':inflight[-1],
        'peak_service_queue_max_over_full_run':service_queue_peak,
        'in_transit_at_end':float(env.pending.sum()),'outstanding_resource_minutes':outstanding_pending,
        'max_mass_error':env.max_mass_error,'max_budget_error':env.max_budget_error,
        'balance_preferred_moves':branches['balance_preferred'],
        'idle_recovery_moves':branches['idle_balance_recovery'],'soft_risk_holds':branches['soft_risk_hold'],
    }
    if abs(row['offered']-row['completed']-row['end_queue']-row['dropped'])>TOL:
        raise ValueError('Original horizon loses work')
    if abs(row['offered']-row['total_completed']-row['tail_end_queue']-row['total_dropped'])>TOL:
        raise ValueError('Followup loses work')
    if abs(row['end_deficit']-row['end_queue']-row['dropped'])>TOL:
        raise ValueError('Deficit fails to include dropped work')
    return row,trace
