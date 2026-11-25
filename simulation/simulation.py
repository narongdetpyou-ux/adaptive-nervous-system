"""Reproducible synthetic queue simulator. Frozen ABC is imported, not rewritten."""
import csv
import hashlib
import json
import math
from pathlib import Path
import numpy as np
from abc_service.frozen import module, catalog, verify

ROOT = Path(__file__).resolve().parent
PROTOCOL = json.loads((ROOT / 'protocol.json').read_text())
H = PROTOCOL['horizon']
BUDGET = float(PROTOCOL['budget'])
BASE = np.array(PROTOCOL['initial_staff'], dtype=float)
GROUPS = PROTOCOL['scenarios']
SEEDS = PROTOCOL['scenario_seeds']
ABC_SEEDS = PROTOCOL['abc_seeds']
POLICIES = PROTOCOL['policies']
REP = ('shifting_bottleneck', 100)
TOL = 1e-6

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def load_trace(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f))
    counts = [float(r['requests']) for r in rows]
    if len(counts) != 30 or any(not math.isfinite(x) or x < 0 or x != int(x) for x in counts):
        raise ValueError('Expected 30 nonnegative integer minute counts')
    return [int(x) for x in counts]

def scenarios(trace, seeds=SEEDS):
    if len(trace) != 30 or any(type(x) is not int or x < 0 for x in trace):
        raise ValueError('Invalid source trace')
    raw = np.array(trace + [int(round(np.median(trace[:7])))] * (H-30), dtype=float)
    for g, group in enumerate(GROUPS):
        for seed in seeds:
            rng = np.random.default_rng(np.random.SeedSequence([g, seed]))
            arr = np.rint(raw * rng.uniform(.85, 1.15) * rng.uniform(.90, 1.10, H))
            prod = PROTOCOL['nominal_productivity_per_unit_per_minute'] * rng.uniform(.9, 1.1, (H, 3))
            factor = rng.uniform(.35, .60)
            if group == 'shifting_bottleneck':
                for stage, start in enumerate([10,16,22]):
                    prod[start:start+6, stage] *= factor
            elif g > 0:
                prod[10:23, g-1] *= factor
            yield group, seed, arr, prod

def baseline_choice(t, q, history, staff, last):
    # Equal action space and dwell; greedily maximize current queue weight.
    if len(history) < 5 or t-last < 8 or max(q) <= 10:
        return np.zeros(3), last, 'hold'
    moves = np.asarray([a['delta'] for a in catalog()['actions']])
    feasible = np.flatnonzero((staff+moves >= 0).all(axis=1))
    if not len(feasible):
        return np.zeros(3), last, 'hold'
    score = moves[feasible] @ q
    k = int(np.argmax(score))
    if score[k] <= 0:
        return np.zeros(3), last, 'hold'
    return moves[feasible[k]].copy(), t, 'selected'

def simulate(policy, group, seed, policy_seed, arr, prod, detailed=False, persistent_guard=False):
    q = np.zeros(3); staff = BASE.copy(); history = []; last = -10**9
    agent = None
    if policy == 'abc':
        rng = np.random.default_rng(np.random.SeedSequence([policy_seed, seed, GROUPS.index(group)]))
        agent = module().ABC_Causal207(catalog(), BASE.copy(), rng.random(H))
    totals=[]; moves=0; completed=0.; arrived=0.; mass_max=0.; budget_max=0.; trace=[]
    cleared = None
    permitted = {tuple(a['delta']) for a in catalog()['actions']}
    for t in range(H):
        q[0] += arr[t]; arrived += arr[t]; before=q.copy()
        note=None
        if policy == 'abc':
            if persistent_guard and (len(history)<2 or not np.any((history[-1]>10)&(history[-2]>10))):
                delta=np.zeros(3); branch='gate_hold'
            else:
                delta, note = agent.decide(t, q.copy(), history, staff.copy())
                branch=note['branch']
        elif policy == 'maxweight':
            delta, _, branch = baseline_choice(t,q,history,staff,last)
        elif policy == 'static':
            delta=np.zeros(3); branch='fixed'
        else:
            raise ValueError('Unknown policy')
        if delta.shape != (3,) or not np.isfinite(delta).all() or abs(delta.sum()) > TOL:
            raise ValueError('Invalid transfer')
        if np.any(delta):
            if tuple(delta) not in permitted or t-last < 8 or t < 5:
                raise ValueError('Catalog/dwell/warmup violation')
            moves += 1; last = t
        staff += delta
        err=abs(staff.sum()-BUDGET); budget_max=max(budget_max,float(err))
        if err > TOL or (staff < 0).any(): raise ValueError('Budget violation')
        cap=staff*prod[t]; flow=np.zeros(3)
        for k in range(3):
            flow[k]=min(q[k],cap[k]); q[k]-=flow[k]
            if k<2: q[k+1]+=flow[k]
            else: completed+=flow[k]
        mass=abs(arrived-completed-q.sum()); mass_max=max(mass_max,float(mass))
        if mass>TOL or (q < -TOL).any(): raise ValueError('Lost/duplicated work')
        total=float(q.sum()); totals.append(total)
        if t >= 18 and total <= TOL and cleared is None: cleared=t
        history.append(q.copy()); history=history[-5:]
        if detailed:
            trace.append({'t':t,'arrival':float(arr[t]),'observed':before.tolist(),'productivity':prod[t].tolist(),
                          'staff':staff.tolist(),'delta':delta.tolist(),'flow':flow.tolist(),'queue':q.tolist(),
                          'branch':branch,'completed_cumulative':float(completed),
                          **({'abc_evidence':note} if note and branch=='selected' else {})})
    row={'group':group,'scenario_seed':seed,'policy':policy,'policy_seed':policy_seed,
         'mean_backlog':float(np.mean(totals)),'peak_backlog':max(totals),'end_backlog':totals[-1],
         'arrivals':float(arrived),'completed':float(completed),'moves':moves,'max_mass_error':mass_max,
         'max_budget_error':budget_max,'first_empty_minute_after_peak':cleared}
    return row,trace

def evaluate(policy, trace):
    rows=[]; traces=[]
    for group, seed, arr, prod in scenarios(trace):
        for ps in ABC_SEEDS if policy=='abc' else [None]:
            row, detail=simulate(policy,group,seed,ps,arr,prod,(group,seed)==REP)
            rows.append(row)
            if detail: traces.append({'summary':row,'minutes':detail})
    return {'policy':policy,'rows':rows,'representative':traces,'frozen_hashes':verify()}
