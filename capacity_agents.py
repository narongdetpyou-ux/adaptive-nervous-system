"""Neuron Flow agents and independent validators for the v0.2 experiment.

Large minute traces and the full statistical summary are atomic local artifacts;
their relative paths and hashes cross the bounded JSON worker boundary.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

from neuron_flow import TransientAgentError
from capacity_policy import CapacityConfig
from capacity_scenarios import generate_scenarios, POLICIES, policy_seeds
from capacity_simulation import simulate_case
from capacity_metrics import aggregate
from audit_capacity import (audit_rows, audit_trace, audit_source_integrity,
                            independent_policy_means, require, close)

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.capacity-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_json(path, value):
    data = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    atomic_bytes(path, data)


def write_gzip_json(path, value):
    data = (json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    buffer = io.BytesIO()
    with gzip.GzipFile(filename='', fileobj=buffer, mode='wb', mtime=0) as handle:
        handle.write(data)
    atomic_bytes(path, buffer.getvalue())


def _input(message):
    data = message['input']
    split = data['split']
    require(split in ('development', 'validation'), 'Unknown split')
    output = Path(data['out']).resolve()
    config = CapacityConfig(**data['capacity_config'])
    source = audit_source_integrity(ROOT)
    if split == 'validation':
        from freeze_capacity import verify_frozen
        frozen = verify_frozen()
        protocol = json.loads((ROOT / 'capacity_protocol.json').read_text())
        require(protocol['controller_configuration'] == config.as_dict(), 'Configuration differs from frozen protocol')
        require(frozen == data['freeze_verification'], 'Frozen identity changed')
    return split, output, config, source


def _freeze_after(message):
    if message['input']['split'] == 'validation':
        from freeze_capacity import verify_frozen
        require(verify_frozen() == message['input']['freeze_verification'], 'Frozen files changed during evaluation')


def _checked_path(output, relative):
    path = (output / relative).resolve()
    require(path.is_relative_to(output), 'Artifact path leaves experiment output')
    require(path.is_file(), 'Missing experiment artifact')
    return path


def evaluate_agent(message):
    split, output, config, source = _input(message)
    policy = message['params']['policy']
    require(policy in POLICIES, 'Unknown policy')
    if message['params'].get('inject_retry') and message['attempt'] == 1:
        raise TransientAgentError('Controlled first-attempt failure before any simulation')
    if message['params'].get('reuse_original'):
        result = json.loads((output / 'rows' / f'{policy}.json').read_text())
    else:
        cases = generate_scenarios(split)
        rows, representatives = [], []
        seen_groups = set()
        for case in cases:
            representative = case.group not in seen_groups
            seen_groups.add(case.group)
            seeds = policy_seeds(policy)
            for index, seed in enumerate(seeds):
                keep_trace = representative and index == 0
                row, minutes = simulate_case(policy, case, seed, detailed=keep_trace, config=config)
                rows.append(row)
                if keep_trace:
                    representatives.append({'summary':row, 'minutes':minutes})
        relative = f'traces/{policy}.json.gz'
        path = output / relative
        write_gzip_json(path, representatives)
        result = {
            'policy':policy, 'split':split, 'rows':rows,
            'representative_traces': {
                'path':relative, 'sha256':sha(path), 'count':len(representatives),
                'minutes':sum(len(rep['minutes']) for rep in representatives),
                'selection':'first scenario seed in each family, first policy seed only; selected before execution',
            },
            'upstream_hashes':source,
        }
        write_json(output / 'rows' / f'{policy}.json', result)
    if message['params'].get('corrupt'):
        result['rows'][0]['completed'] += 1
    _freeze_after(message)
    return result


def validate_evaluation(message, result):
    split, output, config, source = _input(message)
    policy = message['params']['policy']
    require(result['policy'] == policy and result['split'] == split, 'Wrong evaluation identity')
    require(result['upstream_hashes'] == source, 'Wrong upstream source identity')
    cases = generate_scenarios(split)
    audit_rows(result['rows'], cases, {policy:policy_seeds(policy)})
    descriptor = result['representative_traces']
    require(descriptor['path'] == f'traces/{policy}.json.gz', 'Unexpected trace path')
    path = _checked_path(output, descriptor['path'])
    require(sha(path) == descriptor['sha256'], 'Trace artifact checksum mismatch')
    with gzip.open(path, 'rt', encoding='utf-8') as handle:
        representatives = json.load(handle)
    first_cases = {}
    for case in cases:
        first_cases.setdefault(case.group, case)
    expected = {(group, case.seed, policy_seeds(policy)[0]) for group, case in first_cases.items()}
    actual = {(r['summary']['group'], r['summary']['scenario_seed'], r['summary']['policy_seed']) for r in representatives}
    require(actual == expected and len(representatives) == len(expected), 'Representative trace scope differs')
    require(descriptor['count'] == len(representatives), 'Trace count mismatch')
    row_by_key = {(r['group'],r['scenario_seed'],r['policy_seed']):r for r in result['rows']}
    minutes_checked = 0
    for representative in representatives:
        row = representative['summary']
        key = row['group'],row['scenario_seed'],row['policy_seed']
        require(row == row_by_key[key], 'Trace summary differs from episode row')
        case = first_cases[row['group']]
        checked = audit_trace(representative, case.arrivals, case.productivity)
        minutes_checked += checked['minutes_replayed']
    require(minutes_checked == descriptor['minutes'], 'Trace minute count mismatch')
    _freeze_after(message)


def _all_rows(dependencies):
    require(set(dependencies) == set(POLICIES), 'Missing policy dependency')
    return [row for policy in POLICIES for row in dependencies[policy]['rows']]


def _overview(summary):
    return {key:summary[key] for key in (
        'scenarios','episodes','minutes','overall','capacity_aware_vs','abc_capacity_vs',
        'max_mass_error','max_budget_error')}


def compare_agent(message):
    split, output, config, source = _input(message)
    rows = _all_rows(message['dependencies'])
    audit_rows(rows, generate_scenarios(split), {p:policy_seeds(p) for p in POLICIES})
    summary = aggregate(rows)
    write_json(output / 'summary.json', summary)
    _freeze_after(message)
    return {'summary_path':'summary.json','summary_sha256':sha(output/'summary.json'),
            'overview':_overview(summary)}


def validate_comparison(message, result):
    split, output, config, source = _input(message)
    require(result['summary_path'] == 'summary.json', 'Unexpected summary path')
    path = _checked_path(output, result['summary_path'])
    require(sha(path) == result['summary_sha256'], 'Statistical summary checksum mismatch')
    summary = json.loads(path.read_text())
    rows = _all_rows(message['dependencies'])
    require(summary == aggregate(rows), 'Statistical aggregation mismatch')
    require(result['overview'] == _overview(summary), 'Overview differs from full summary')
    means = independent_policy_means(rows)
    for policy, metrics in means.items():
        for metric, value in metrics.items():
            require(close(summary['overall'][policy][metric],value), 'Independent mean calculation disagrees')
    _freeze_after(message)


def noop_agent(message):
    return {'ok':True}


def validate_noop(message, result):
    require(result == {'ok':True}, 'Wrong no-op result')
