"""Artifact-backed Neuron Flow agents for the v0.3 regime experiment.

Worker messages contain only small descriptors. Complete episode rows, traces
and statistics remain checksum-bound local artifacts to respect the original
runtime's one-MiB message limit without changing upstream code.
"""
from __future__ import annotations

from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean

from neuron_flow import TransientAgentError
from capacity_agents import atomic_bytes, write_json, write_gzip_json, sha
from audit_capacity import audit_source_integrity
from v3_policy import V3Config
from v3_scenarios import generate_scenarios, REGIMES, POLICIES, policy_seeds
from v3_simulation import simulate_case
from v3_metrics import aggregate
from audit_v3 import require, close

ROOT = Path(__file__).resolve().parent


def step_name(regime, policy):
    require(regime in REGIMES and policy in POLICIES,'Unknown regime/policy')
    return regime+'__'+policy


def _input(message):
    data = message['input']
    require(data['split'] in ('development','validation'),'Unknown split')
    output = Path(data['out']).resolve()
    config = V3Config(**data['controller_configuration'])
    audit_source_integrity(ROOT)
    if data['split']=='validation':
        from freeze_v3 import verify_frozen
        require(verify_frozen()==data['freeze_verification'],'Frozen experiment identity changed')
        protocol = json.loads((ROOT/'v3_protocol.json').read_text())
        require(protocol['controller_configuration']==config.as_dict(),'Configuration differs from frozen protocol')
    return data['split'],output,config


def _freeze_after(message):
    if message['input']['split']=='validation':
        from freeze_v3 import verify_frozen
        require(verify_frozen()==message['input']['freeze_verification'],'Frozen experiment changed during computation')


def artifact_path(output, descriptor):
    path = (output/descriptor['path']).resolve()
    require(path.is_relative_to(output),'Artifact path leaves the output directory')
    require(path.is_file(),'Missing experiment artifact')
    require(sha(path)==descriptor['sha256'],'Experiment artifact checksum mismatch')
    return path


def load_rows(output, result):
    payload = json.loads(artifact_path(output,result['rows']).read_text())
    require(payload['regime']==result['regime'] and payload['policy']==result['policy'],'Row artifact identity mismatch')
    require(len(payload['rows'])==result['rows']['count'],'Row artifact count mismatch')
    return payload['rows']


def evaluate_agent(message):
    split,output,config = _input(message)
    regime,policy = message['params']['regime'],message['params']['policy']
    name = step_name(regime,policy)
    if message['params'].get('inject_retry') and message['attempt']==1:
        raise TransientAgentError('Controlled first-attempt failure before v0.3 computation')
    if message['params'].get('reuse_original'):
        original = json.loads((output/'indices'/f'{name}.json').read_text())
        rows = load_rows(output,original)
        rows[0]['completed'] += 1
        relative = f'negative/{name}.json'
        write_json(output/relative,{'regime':regime,'policy':policy,'rows':rows})
        result = {**original,'rows':{'path':relative,'sha256':sha(output/relative),'count':len(rows)}}
    else:
        scenarios = generate_scenarios(split)
        rows,representatives,seen_groups = [],[],set()
        for scenario in scenarios:
            first_case = scenario.group not in seen_groups
            seen_groups.add(scenario.group)
            for index,seed in enumerate(policy_seeds(policy)):
                detailed = first_case and index==0
                row,minutes = simulate_case(policy,scenario,seed,regime=regime,detailed=detailed,config=config)
                rows.append(row)
                if detailed:
                    representatives.append({'summary':row,'minutes':minutes})
        row_relative,trace_relative = f'rows/{name}.json',f'traces/{name}.json.gz'
        write_json(output/row_relative,{'regime':regime,'policy':policy,'rows':rows})
        write_gzip_json(output/trace_relative,representatives)
        result = {
            'split':split,'regime':regime,'policy':policy,
            'rows':{'path':row_relative,'sha256':sha(output/row_relative),'count':len(rows)},
            'traces':{'path':trace_relative,'sha256':sha(output/trace_relative),'count':len(representatives),
                      'minutes':sum(len(rep['minutes']) for rep in representatives),
                      'selection':'first scenario seed per family, first policy seed; fixed before execution'},
        }
        write_json(output/'indices'/f'{name}.json',result)
    _freeze_after(message)
    return result


def validate_evaluation(message,result):
    from audit_v3 import audit_rows,audit_trace
    split,output,config = _input(message)
    regime,policy = message['params']['regime'],message['params']['policy']
    name = step_name(regime,policy)
    require((result['split'],result['regime'],result['policy'])==(split,regime,policy),'Evaluation identity mismatch')
    row_expected = f'negative/{name}.json' if message['params'].get('reuse_original') else f'rows/{name}.json'
    require(result['rows']['path']==row_expected,'Unexpected row artifact path')
    require(result['traces']['path']==f'traces/{name}.json.gz','Unexpected trace artifact path')
    rows = load_rows(output,result)
    scenarios = generate_scenarios(split)
    audit_rows(rows,scenarios,{policy:policy_seeds(policy)},regimes=(regime,))
    trace_path = artifact_path(output,result['traces'])
    with gzip.open(trace_path,'rt',encoding='utf-8') as handle:
        representatives = json.load(handle)
    first_cases = {}
    for case in scenarios:
        first_cases.setdefault(case.group,case)
    expected = {(group,case.seed,policy_seeds(policy)[0]) for group,case in first_cases.items()}
    actual = {(rep['summary']['group'],rep['summary']['scenario_seed'],rep['summary']['policy_seed']) for rep in representatives}
    require(actual==expected and len(representatives)==len(expected),'Representative trace scope mismatch')
    require(result['traces']['count']==len(representatives),'Representative trace count mismatch')
    row_map = {(row['group'],row['scenario_seed'],row['policy_seed']):row for row in rows}
    minutes = 0
    for rep in representatives:
        row = rep['summary']
        require(row==row_map[row['group'],row['scenario_seed'],row['policy_seed']],'Representative row differs from row artifact')
        audit_trace(rep,first_cases[row['group']],regime)
        minutes += len(rep['minutes'])
    require(minutes==result['traces']['minutes'],'Representative minute count mismatch')
    _freeze_after(message)


def all_rows(output,dependencies):
    expected = {step_name(regime,policy) for regime in REGIMES for policy in POLICIES}
    require(set(dependencies)==expected,'Missing regime/policy dependency')
    return [row for regime in REGIMES for policy in POLICIES
            for row in load_rows(output,dependencies[step_name(regime,policy)])]


def overview(summary):
    result = {key:summary[key] for key in (
        'scenarios','regime_count','scenario_regime_pairs','episodes','original_minutes',
        'followup_minutes','minutes','max_mass_error','max_budget_error')}
    result['regimes'] = {}
    for regime,part in summary['regimes'].items():
        result['regimes'][regime] = {
            'overall':part['overall'],
            'v3_full_vs':{
                baseline:{key:comparison[key] for key in (
                    'reduction_pct','reduction_pct_ci95','improvement_ci95','wins','ties','losses')}
                for baseline,comparison in part['comparisons']['v3_full'].items()},
            'static_relative_harm':{
                policy:{key:risk[key] for key in ('count','mean_positive_harm','cvar90','maximum_harm')}
                for policy,risk in part['static_relative_harm'].items()},
        }
    return result


def compare_agent(message):
    from audit_v3 import audit_rows
    split,output,config = _input(message)
    rows = all_rows(output,message['dependencies'])
    audit_rows(rows,generate_scenarios(split),{p:policy_seeds(p) for p in POLICIES},regimes=REGIMES)
    summary = aggregate(rows)
    write_json(output/'summary.json',summary)
    _freeze_after(message)
    return {'summary':{'path':'summary.json','sha256':sha(output/'summary.json')},'overview':overview(summary)}


def validate_comparison(message,result):
    """Check scope and independent paired means without rerunning bootstrap.

The frozen statistical module computes confidence intervals; this independent
controller-side check verifies all point means, effect signs and harm counts.
"""
    from audit_v3 import audit_rows
    split,output,config = _input(message)
    require(result['summary']['path']=='summary.json','Unexpected summary artifact path')
    summary = json.loads(artifact_path(output,result['summary']).read_text())
    require(result['overview']==overview(summary),'Overview differs from statistical artifact')
    rows = all_rows(output,message['dependencies'])
    scenarios = generate_scenarios(split)
    audit_rows(rows,scenarios,{p:policy_seeds(p) for p in POLICIES},regimes=REGIMES)
    require(summary['episodes']==len(rows) and summary['scenarios']==len(scenarios),'Aggregate scope mismatch')
    require(summary['regime_count']==len(REGIMES) and summary['scenario_regime_pairs']==len(scenarios)*len(REGIMES),'Aggregate regime scope mismatch')
    by_case = defaultdict(list)
    for row in rows:
        by_case[row['regime'],row['group'],row['scenario_seed'],row['policy']].append(row)
    values = {}
    for key,replicas in by_case.items():
        values[key] = {metric:mean(float(r['drain_censored']) if metric=='drain_uncleared_fraction' else r[metric]
                                 for r in replicas) for metric in summary['metrics']}
    for regime in REGIMES:
        part = summary['regimes'][regime]
        require(part['scenarios']==len(scenarios),'Regime case count differs')
        for policy in POLICIES:
            selected = [value for key,value in values.items() if key[0]==regime and key[3]==policy]
            for metric in summary['metrics']:
                require(close(mean(value[metric] for value in selected),part['overall'][policy][metric]),'Independent aggregate mean disagrees')
            harms = [max(values[regime,case.group,case.seed,policy]['mean_backlog']-
                         values[regime,case.group,case.seed,'static']['mean_backlog'],0.) for case in scenarios]
            risk = part['static_relative_harm'][policy]
            require(sum(h>1e-6 for h in harms)==risk['count'],'Static-relative harm count disagrees')
            require(close(mean(harms),risk['mean_positive_harm']) and close(max(harms),risk['maximum_harm']),'Static-relative harm magnitude disagrees')
        for candidate,comparisons in part['comparisons'].items():
            for baseline,comparison in comparisons.items():
                differences = [values[regime,c.group,c.seed,baseline]['mean_backlog']-
                               values[regime,c.group,c.seed,candidate]['mean_backlog'] for c in scenarios]
                require(close(mean(differences),comparison['mean_improvement']),'Independent paired difference disagrees')
                require(sum(x>1e-6 for x in differences)==comparison['wins'] and
                        sum(x<-1e-6 for x in differences)==comparison['losses'],'Paired win/loss count disagrees')
    _freeze_after(message)


def noop_agent(message):
    return {'ok':True}


def validate_noop(message,result):
    require(result=={'ok':True},'Invalid no-op result')
