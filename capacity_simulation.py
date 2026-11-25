"""Version 0.2 experiment adapter: environment truth stays outside controllers."""
from collections import Counter
import numpy as np
from abc_service.frozen import module,catalog
from simulation import baseline_choice
from capacity_policy import CapacityAwarePolicy,CapacityConfig
from capacity_scenarios import GROUP_INDEX,POLICIES,policy_seeds

TOL = 1e-6


def simulate_case(policy, scenario, policy_seed=None, detailed=False, config=None):
    if policy not in POLICIES or policy_seed not in policy_seeds(policy):
        raise ValueError('Invalid policy or seed')
    config = config or CapacityConfig()
    q = np.zeros(3)
    staff = np.full(3,69.)
    history = []
    last = -10**9
    H = scenario.horizon
    uniform = None
    if policy_seed is not None:
        rng = np.random.default_rng(np.random.SeedSequence([policy_seed,scenario.seed,GROUP_INDEX[scenario.group]]))
        uniform = rng.random(H)
    abc = module().ABC_Causal207(catalog(),staff.copy(),uniform) if policy in ('abc','abc_guard') else None
    capacity = None
    if policy in ('abc_capacity','capacity_aware'):
        capacity = CapacityAwarePolicy('abc_only' if policy=='abc_capacity' else 'catalog',uniform,config)
    permitted = {tuple(a['delta']) for a in catalog()['actions']}
    totals = []
    moves = 0
    completed = 0.
    arrived = 0.
    mass_max = 0.
    budget_max = 0.
    trace = []
    branches = Counter()
    for t in range(H):
        arrival = float(scenario.arrivals[t])
        q[0] += arrival
        arrived += arrival
        before = q.copy()
        note = None
        if abc is not None:
            if policy=='abc_guard' and (len(history)<2 or not np.any((history[-1]>10)&(history[-2]>10))):
                delta,branch = np.zeros(3),'gate_hold'
            else:
                delta,note = abc.decide(t,q.copy(),[x.copy() for x in history],staff.copy())
                branch = note['branch']
        elif capacity is not None:
            # The controller receives only observations. In particular the
            # Scenario object, rate arrays and their metadata never cross here.
            delta,note = capacity.decide(t,q.copy(),[x.copy() for x in history],staff.copy(),arrival)
            branch = note['branch']
        elif policy=='maxweight':
            delta,_,branch = baseline_choice(t,q.copy(),history,staff.copy(),last)
        else:
            delta,branch = np.zeros(3),'fixed'
        branches[branch] += 1
        if delta.shape != (3,) or not np.isfinite(delta).all() or abs(delta.sum())>TOL:
            raise ValueError('Invalid resource transfer')
        if np.any(delta):
            if tuple(delta) not in permitted or t<5 or t-last<8:
                raise ValueError('Action-space/warmup/dwell violation')
            last = t
            moves += 1
        staff += delta
        err = abs(staff.sum()-207.)
        budget_max = max(budget_max,float(err))
        if err>TOL or (staff<0).any():
            raise ValueError('Resource conservation violation')
        if capacity is not None and min(staff)<config.minimum_staff:
            raise ValueError('Capacity-policy reserve violation')
        # Only the environment service step accesses current true productivity.
        flow = np.zeros(3)
        for i in range(3):
            flow[i] = min(q[i],staff[i]*scenario.productivity[t,i])
            q[i] -= flow[i]
            if i<2:
                q[i+1] += flow[i]
            else:
                completed += flow[i]
        mass = abs(arrived-completed-q.sum())
        mass_max = max(mass_max,float(mass))
        if mass>TOL or (q<0).any():
            raise ValueError('Work conservation violation')
        if capacity is not None:
            capacity.observe(t,staff.copy(),flow.copy(),q.copy())
        totals.append(float(q.sum()))
        history.append(q.copy())
        history = history[-5:]
        if detailed:
            trace.append({'t':t,'arrival':arrival,'observed':before.tolist(),
                          'productivity':scenario.productivity[t].tolist(),
                          'staff':staff.tolist(),'delta':delta.tolist(),
                          'flow':flow.tolist(),'queue':q.tolist(),'branch':branch,
                          'completed_cumulative':float(completed),
                          **({'decision_evidence':note} if note is not None else {})})
    row = {'group':scenario.group,'scenario_seed':scenario.seed,'source':scenario.source,
           'policy':policy,'policy_seed':policy_seed,'horizon':H,
           'mean_backlog':float(np.mean(totals)),'peak_backlog':max(totals),'end_backlog':totals[-1],
           'arrivals':float(arrived),'completed':float(completed),'moves':moves,
           'max_mass_error':mass_max,'max_budget_error':budget_max,
           'model_rejections':branches['model_rejected'],'reserve_rejections':branches['reserve_rejected']}
    return row,trace
