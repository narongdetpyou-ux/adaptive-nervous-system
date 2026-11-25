"""Execute the finite, predeclared six-configuration known-case development grid.

No validation cases are generated here. All trial rows/configurations are kept,
including unsuccessful ones. Selection uses point estimates only; the final
experiment will use the independently reserved split and frozen interval rule.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil

from capacity_agents import write_json
from v4_policy import V4Config
from v4_scenarios import generate_scenarios, REGIMES, scenario_manifest
from v4_simulation import simulate_case
from v4_metrics import aggregate

ROOT=Path(__file__).resolve().parent
BASELINES=('static','capacity_v2','capacity_balance','v3_full')
GRID=tuple((risk,cap) for risk in (.1,.3,.6) for cap in (20.,30.))
EXECUTION_FILES=('v4_environment.py','v4_scenarios.py','v4_policy.py','v4_simulation.py',
                 'v4_metrics.py','develop_v4.py')
_CASES=None


def hashes():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in EXECUTION_FILES}


def _initialize():
    global _CASES
    _CASES=generate_scenarios('development')


def _job(policy,regime,configuration):
    cfg=V4Config(**configuration)
    return [simulate_case(policy,case,regime=regime,config=cfg)[0] for case in _CASES]


def ranking(summary,risk,cap):
    violations=0;ratios=[];risks=[];moves=[];details={}
    for regime,part in summary['regimes'].items():
        means=part['overall'];harm=part['static_relative_harm']
        candidate=means['v4_hybrid']['mean_deficit']
        bad={base:candidate>1.05*means[base]['mean_deficit']+1e-6
             for base in ('capacity_v2','capacity_balance')}
        violations+=sum(bad.values())
        denominator=max(min(harm[base]['cvar90'] for base in ('v3_full','capacity_v2','capacity_balance')),1.)
        value=harm['v4_hybrid']['cvar90'];ratios.append(value/denominator)
        risks.append(value);moves.append(means['v4_hybrid']['moves'])
        details[regime]={'mean_margin_violations':bad,'candidate_mean_deficit':candidate,
                        'candidate_cvar90':value,'risk_ratio_denominator':denominator,
                        'normalized_risk_ratio':value/denominator}
    key=[int(violations),max(ratios),sum(risks),sum(moves)/len(moves),risk,cap]
    return key,details


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args();out=args.out.resolve()
    if args.workers not in (1,2,3):raise ValueError('Use one to three local workers')
    if out.exists() and any(out.iterdir()):raise ValueError('Use an empty development directory')
    out.mkdir(parents=True,exist_ok=True);(out/'source_snapshot').mkdir()
    before=hashes()
    for name in EXECUTION_FILES:shutil.copy2(ROOT/name,out/'source_snapshot'/name)
    protocol=json.loads((ROOT/'v4_protocol_draft.json').read_text())
    write_json(out/'protocol_before_execution.json',protocol)
    write_json(out/'scenario_manifest.json',scenario_manifest('development'))
    configurations={f'risk_{risk:g}_cap_{cap:g}':asdict(V4Config(risk_weight=risk,maximum_transfer=cap))
                    for risk,cap in GRID}
    plan={'status':'specified before this development grid','known_cases':88,'regimes':list(REGIMES),
          'baseline_policies':list(BASELINES),'candidate_configurations':configurations,
          'ranking':'count of12 point mean-margin violations; worst regime risk ratio to minimum ofV3/V2/Balance with denominator floor1; sum of6 risks; mean moves; risk_weight; cap',
          'source_sha256':before,'bootstrap_draws':0,'no_validation_execution':True}
    write_json(out/'plan.json',plan)
    tasks=[]
    base=asdict(V4Config())
    for regime in REGIMES:
        for policy in BASELINES:tasks.append(('baselines',policy,regime,base))
    for label,cfg in configurations.items():
        for regime in REGIMES:tasks.append((label,'v4_hybrid',regime,cfg))
    completed=0
    with ProcessPoolExecutor(max_workers=args.workers,initializer=_initialize) as pool:
        future_map={pool.submit(_job,policy,regime,cfg):(label,policy,regime)
                    for label,policy,regime,cfg in tasks}
        for future in as_completed(future_map):
            label,policy,regime=future_map[future];rows=future.result()
            write_json(out/label/f'{regime}__{policy}.json',{'policy':policy,'regime':regime,'rows':rows})
            completed+=1
            print(json.dumps({'completed_tasks':completed,'total_tasks':len(tasks),'configuration':label,
                              'policy':policy,'regime':regime,'episodes':len(rows)}),flush=True)
    baseline_rows=[]
    for path in sorted((out/'baselines').glob('*.json')):baseline_rows.extend(json.loads(path.read_text())['rows'])
    choices=[]
    for label,cfg in configurations.items():
        candidate=[]
        for path in sorted((out/label).glob('*.json')):candidate.extend(json.loads(path.read_text())['rows'])
        summary=aggregate(baseline_rows+candidate,policies=BASELINES+('v4_hybrid',),
                          candidates=('v4_hybrid',),baselines=BASELINES,bootstrap_draws=0)
        write_json(out/label/'summary.json',summary)
        key,details=ranking(summary,cfg['risk_weight'],cfg['maximum_transfer'])
        choices.append({'label':label,'configuration':cfg,'rank':key,'per_regime':details})
    if hashes()!=before:raise ValueError('Execution code changed during development; retain trial evidence and rerun under a new directory')
    choices.sort(key=lambda x:tuple(x['rank']))
    selected=choices[0]
    result={'version':'0.4.0','scope':'known-case development only','selected_label':selected['label'],
            'selected_configuration':selected['configuration'],'ranked_trials':choices,
            'baseline_episodes':len(baseline_rows),'candidate_episodes_per_trial':528,
            'total_development_grid_episodes':len(baseline_rows)+528*len(choices),
            'source_sha256':before,'no_validation_execution':True}
    write_json(out/'selection.json',result)
    target=ROOT/'v4_selected_config.json'
    if target.exists() and json.loads(target.read_text())!=selected['configuration']:
        raise ValueError('Selected configuration already exists and differs; retain the new selection for review')
    write_json(target,selected['configuration'])
    print(json.dumps({'selected':selected['label'],'rank':selected['rank'],
                      'grid_episodes':result['total_development_grid_episodes']},indent=2),flush=True)


if __name__=='__main__':main()
