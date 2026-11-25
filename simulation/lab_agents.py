"""Neuron Flow agents and independent accounting validator."""
import math
from collections import defaultdict
from statistics import mean
from neuron_flow import TransientAgentError
from simulation import GROUPS,SEEDS,ABC_SEEDS,POLICIES,REP,H,BUDGET,TOL,BASE,scenarios,evaluate,verify,catalog

def require(condition,message):
    if not condition: raise ValueError(message)

def evaluate_agent(message):
    p=message['params']['policy']
    if message['params'].get('inject_retry') and message['attempt']==1:
        raise TransientAgentError('Controlled first-attempt failure before simulation')
    result=evaluate(p,message['input']['trace'])
    if message['params'].get('corrupt'):
        result['rows'][0]['completed'] += 1
    return result

def close(a,b):
    return math.isclose(a,b,rel_tol=1e-10,abs_tol=TOL)

def vector_close(a,b):
    return len(a)==len(b) and all(close(x,y) for x,y in zip(a,b))

def validate_trace(rep,arr,prod):
    """Replay capacity/flow using ordinary Python arithmetic, not the simulator."""
    r=rep['summary']; queue=[0.0]*3; staff=list(map(float,BASE)); done=0.; total=0.; backlogs=[]; last=-10**9; moves=0
    permitted={tuple(a['delta']) for a in catalog()['actions']}
    require(len(rep['minutes'])==H,'Wrong horizon')
    for t,m in enumerate(rep['minutes']):
        require(m['t']==t and close(m['arrival'],arr[t]),'Arrival mismatch')
        require(vector_close(m['productivity'],prod[t]),'Capacity mismatch')
        queue[0]+=float(arr[t]); total+=float(arr[t]); require(vector_close(queue,m['observed']),'Observation mismatch')
        delta=m['delta']; require(len(delta)==3 and close(sum(delta),0),'Bad transfer')
        if any(delta):
            require(r['policy']!='static' and tuple(delta) in permitted and t-last>=8 and t>=5,'Invalid move')
            if r['policy']=='abc':
                evidence=m['abc_evidence']; selected=evidence['selected_action_id']; shortlist=evidence['shortlist']
                require(evidence['branch']=='selected' and 1<=len(shortlist)<=3,'Bad ABC evidence')
                require(tuple(catalog()['actions'][selected]['delta'])==tuple(delta),'Action ID mismatch')
                require(close(sum(s['probability'] for s in shortlist),1.0),'Invalid selection probability')
                u=evidence['uniform']; rank=evidence['selected_rank']; lower=sum(s['probability'] for s in shortlist[:rank-1]); upper=lower+shortlist[rank-1]['probability']
                require(1<=rank<=len(shortlist) and 0<=u<1 and lower<=u<upper and shortlist[rank-1]['action_id']==selected,'Choice not supported by recorded draw')
            moves+=1; last=t
        staff=[s+d for s,d in zip(staff,delta)]
        require(min(staff)>=0 and close(sum(staff),BUDGET) and vector_close(staff,m['staff']),'Resource invariant')
        flow=[]
        for i in range(3):
            f=min(queue[i],staff[i]*float(prod[t][i])); flow.append(f); queue[i]-=f
            if i<2: queue[i+1]+=f
            else: done+=f
        require(vector_close(flow,m['flow']) and vector_close(queue,m['queue']) and close(done,m['completed_cumulative']),'Replay mismatch')
        require(close(total,done+sum(queue)),'Work invariant'); backlogs.append(sum(queue))
    require(close(mean(backlogs),r['mean_backlog']) and close(max(backlogs),r['peak_backlog']) and close(backlogs[-1],r['end_backlog']) and close(done,r['completed']) and moves==r['moves'],'Summary mismatch')

def validate_evaluation(message,out):
    policy=message['params']['policy']; require(out['policy']==policy,'Wrong policy')
    require(out['frozen_hashes']==verify(),'Frozen hash mismatch')
    env={(g,s):(arr,prod) for g,s,arr,prod in scenarios(message['input']['trace'])}
    expected={(g,s,ps) for g,s in env for ps in (ABC_SEEDS if policy=='abc' else [None])}
    actual={(r['group'],r['scenario_seed'],r['policy_seed']) for r in out['rows']}
    require(actual==expected and len(out['rows'])==len(expected),'Missing/duplicated episode')
    by_key={}
    for r in out['rows']:
        k=(r['group'],r['scenario_seed']); by_key[(*k,r['policy_seed'])]=r
        require(r['policy']==policy,'Wrong row policy')
        require(close(r['arrivals'],sum(env[k][0])),'Wrong total input')
        require(close(r['arrivals'],r['completed']+r['end_backlog']),'Lost work in summary')
        require(0<=r['end_backlog']<=r['peak_backlog'] and 0<=r['mean_backlog']<=r['peak_backlog'],'Bad backlog')
        require(0<=r['max_mass_error']<=TOL and 0<=r['max_budget_error']<=TOL,'Invariant violation')
        require(type(r['moves']) is int and 0<=r['moves']<=7,'Bad move count')
        if policy=='static': require(r['moves']==0,'Static moved resources')
    reps=out['representative']; expected_rep=set(ABC_SEEDS if policy=='abc' else [None]); require(len(reps)==len(expected_rep),'Missing representative trace')
    require({rep['summary']['policy_seed'] for rep in reps}==expected_rep,'Wrong representative seeds')
    for rep in reps:
        r=rep['summary']; require(r==by_key[(*REP,r['policy_seed'])],'Representative summary mismatch')
        validate_trace(rep,*env[REP])

def aggregate(deps):
    by=defaultdict(list)
    for p in POLICIES:
        for r in deps[p]['rows']: by[(r['group'],r['scenario_seed'],p)].append(r)
    metrics=['mean_backlog','peak_backlog','end_backlog','completed','moves']
    per_case=[]
    for g in GROUPS:
        for s in SEEDS:
            case={'group':g,'scenario_seed':s}
            for p in POLICIES:
                case[p]={m:mean(r[m] for r in by[g,s,p]) for m in metrics}
            per_case.append(case)
    overall={p:{m:mean(c[p][m] for c in per_case) for m in metrics} for p in POLICIES}
    per_group={g:{p:mean(c[p]['mean_backlog'] for c in per_case if c['group']==g) for p in POLICIES} for g in GROUPS}
    wins={}
    for p in ['static','maxweight']:
        d=[c[p]['mean_backlog']-c['abc']['mean_backlog'] for c in per_case]
        wins[p]={'wins':sum(x>TOL for x in d),'ties':sum(abs(x)<=TOL for x in d),'losses':sum(x<-TOL for x in d),
                 'reduction_pct':100*(overall[p]['mean_backlog']-overall['abc']['mean_backlog'])/overall[p]['mean_backlog'] if overall[p]['mean_backlog'] else None}
    return {'scenarios':len(per_case),'episodes':sum(len(deps[p]['rows']) for p in POLICIES),'minutes':sum(len(deps[p]['rows']) for p in POLICIES)*H,
            'overall':overall,'by_group':per_group,'abc_vs':wins,'case_results':per_case,
            'max_mass_error':max(r['max_mass_error'] for p in POLICIES for r in deps[p]['rows']),
            'max_budget_error':max(r['max_budget_error'] for p in POLICIES for r in deps[p]['rows'])}

def compare_agent(message): return aggregate(message['dependencies'])

def validate_comparison(message,out): require(out==aggregate(message['dependencies']),'Comparison mismatch')

def noop_agent(message): return {'ok':True}
def validate_noop(message,out): require(out=={'ok':True},'Wrong no-op')
