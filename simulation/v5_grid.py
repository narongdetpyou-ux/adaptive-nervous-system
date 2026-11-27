"""Declared V5 development and selection grids (v5_selection_plan.json); never validation or OOD.

run       writes one complete CSV per seed under local_runs/v5_grid/<split>/ and
          skips seeds whose file is already complete, so an interrupted run resumes.
summarize applies the declared selection rule and the V4 archive check and writes
          the committed summaries.
"""
import argparse
import csv
import hashlib
import json
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from pathlib import Path

import numpy as np

from capacity_scenarios import GENERATED_GROUPS, _generated
from simulation import load_trace, scenarios as old_scenarios
from v4_metrics import _cvar_last_axis, _row_metric_values
from v4_scenarios import GROUPS, REGIMES, Scenario
from v5_development import study_arms
from v5_simulation import simulate_case

ROOT = Path(__file__).resolve().parent
PLAN = json.loads((ROOT / 'v5_selection_plan.json').read_text())
DECLARATION = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())
TOL = DECLARATION['tolerance']
DATA = DECLARATION['data']


def _inclusive(pair):
    low, high = pair
    return tuple(range(low, high + 1))


SPLITS = {
    'development': tuple(s for pair in DATA['development_seed_ranges'] for s in _inclusive(pair)),
    'selection': _inclusive(DATA['development_selection_seed_range']),
}
CLOSED = (DATA['validation_seed_range'], DATA['ood_validation_seed_range'])
KNOWN = _inclusive(DATA['diagnostic_known_seeds'])
CONFIGS = tuple(PLAN['eligible_configurations'])
METRICS = ('mean_deficit', 'total_dropped', 'end_deficit', 'tail_auc_deficit', 'total_moves')
LOSS_METRICS = tuple(DECLARATION['loss_endpoint_point_nonworsening']['metrics'])
LOSS_BASELINES = tuple(DECLARATION['loss_endpoint_point_nonworsening']['baselines'])
RISK_BASELINES = tuple(DECLARATION['risk_point_nonworsening']['baselines'])
ROWS_PER_SEED = len(GROUPS) * len(REGIMES) * len(study_arms())
OUTPUT = ROOT / 'local_runs/v5_grid'

if CONFIGS != tuple(DECLARATION['development']['variants']):
    raise ValueError('Eligible configurations differ from the declared development variants')


def check_open(seeds):
    for seed in seeds:
        if any(low <= seed <= high for low, high in CLOSED):
            raise ValueError(f'Seed {seed} is in a closed validation or OOD range')
    return tuple(seeds)


def explicit_cases(seeds):
    """The calls of v4_scenarios.generate_scenarios, for an explicit open seed tuple."""
    return _build_cases(check_open(seeds))


def _build_cases(seeds):
    """Unguarded builder: only the frozen validation runner and reviewer call it with closed seeds."""
    seeds = tuple(seeds)
    trace = load_trace(ROOT / 'data/load_test_summary_per_minute.csv')
    result = [
        Scenario(group, seed, arrivals, productivity, 'legacy_flash', {
            'generation': 'unchanged original Flash Sale generator; v0.4 seed split',
            'trace': 'data/load_test_summary_per_minute.csv',
        })
        for group, seed, arrivals, productivity in old_scenarios(trace, seeds)
    ]
    for group in GENERATED_GROUPS:
        for seed in seeds:
            original = _generated(group, seed)
            result.append(Scenario(original.group, original.seed, original.arrivals,
                                   original.productivity, original.source, original.metadata))
    return result


@cache
def split_cases(split):
    cases = {(case.seed, case.group): case for case in explicit_cases(SPLITS[split])}
    if len(cases) != len(SPLITS[split]) * len(GROUPS):
        raise ValueError('Incomplete case population')
    return cases


def run_task(task):
    split, seed, group, regime = task
    case = split_cases(split)[seed, group]
    rows = []
    for policy, space in study_arms():
        row, _ = simulate_case(policy, case, regime=regime, action_space=space)
        _row_metric_values(row)
        if row['max_mass_error'] > TOL or row['max_budget_error'] > TOL:
            raise ValueError(f'Accounting error: {task} {policy}/{space}')
        rows.append({**row, 'action_space': space})
    return task, rows


def seed_path(split, seed, output=OUTPUT):
    return Path(output) / split / f'episodes-{seed}.csv'


def complete(path):
    if not path.exists():
        return False
    with path.open(newline='') as handle:
        return sum(1 for _ in csv.DictReader(handle)) == ROWS_PER_SEED


def run(split, workers, output=OUTPUT, seeds=None):
    seeds = check_open(SPLITS[split] if seeds is None else seeds)
    if not set(seeds) <= set(SPLITS[split]):
        raise ValueError('Seeds outside the declared split')
    directory = Path(output) / split
    directory.mkdir(parents=True, exist_ok=True)
    order = [(regime, group) for regime in REGIMES for group in GROUPS]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for seed in seeds:
            path = seed_path(split, seed, output)
            if complete(path):
                print(f'{split} {seed} already complete', flush=True)
                continue
            started = time.monotonic()
            tasks = [(split, seed, group, regime) for regime, group in order]
            results = dict(pool.map(run_task, tasks))
            rows = [row for regime, group in order for row in results[split, seed, group, regime]]
            if len(rows) != ROWS_PER_SEED:
                raise ValueError(f'Incomplete seed {seed}')
            with tempfile.NamedTemporaryFile('w', newline='', dir=directory, delete=False, suffix='.part') as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
                writer.writeheader()
                writer.writerows(rows)
            os.replace(handle.name, path)
            print(f'{split} {seed} done {len(rows)} rows in {time.monotonic()-started:.0f}s '
                  f'{time.strftime("%H:%M:%S", time.gmtime())}', flush=True)


def load(split, seeds=None, output=OUTPUT):
    """{(regime, policy, space): array cases x METRICS}, cases ordered by (group, seed)."""
    seeds = SPLITS[split] if seeds is None else seeds
    table = {}
    for seed in seeds:
        path = seed_path(split, seed, output)
        if not complete(path):
            raise ValueError(f'Missing or incomplete {path}')
        with path.open(newline='') as handle:
            for row in csv.DictReader(handle):
                key = (row['regime'], row['policy'], row['action_space'])
                table.setdefault(key, {})[row['group'], int(row['scenario_seed'])] = [float(row[m]) for m in METRICS]
    order = sorted((group, seed) for group in GROUPS for seed in seeds)
    data = {}
    for key, values in table.items():
        if set(values) != set(order):
            raise ValueError(f'Unpaired cases for {key}')
        data[key] = np.array([values[c] for c in order])
    return data, order


def stats(data, regime, policy, space='variant'):
    matrix = data[regime, policy, space]
    static = data[regime, 'static', 'variant'][:, METRICS.index('mean_deficit')]
    harm = np.maximum(matrix[:, METRICS.index('mean_deficit')] - static, 0.)
    return {**dict(zip(METRICS, matrix.mean(axis=0).tolist())), 'risk': float(_cvar_last_axis(harm))}


def rank(data):
    rows = []
    for config in CONFIGS:
        loss, risk = [], []
        for regime in REGIMES:
            mine = stats(data, regime, config)
            for baseline in LOSS_BASELINES:
                theirs = stats(data, regime, baseline)
                loss += [(regime, baseline, m) for m in LOSS_METRICS if mine[m] > theirs[m] + TOL]
            risk += [(regime, b) for b in RISK_BASELINES if mine['risk'] > stats(data, regime, b)['risk'] + TOL]
        noisy = stats(data, 'delayed_noisy', config)
        rows.append({'config': config, 'loss_endpoint_failures': len(loss), 'risk_failures': len(risk),
                     'delayed_noisy_mean_total_dropped': noisy['total_dropped'],
                     'delayed_noisy_risk': noisy['risk'],
                     'failed_loss_endpoint_checks': [list(x) for x in loss],
                     'failed_risk_checks': [list(x) for x in risk]})
    rows.sort(key=lambda r: (r['loss_endpoint_failures'], r['risk_failures'],
                             r['delayed_noisy_mean_total_dropped'], r['delayed_noisy_risk'], r['config']))
    return rows


def archive_check(data, order):
    """Known V4 validation cases must reproduce the archived V4 decision values."""
    decision = json.loads((ROOT / 'v4_results/validation/decision.json').read_text())
    keep = [i for i, (_, seed) in enumerate(order) if seed in KNOWN]
    known = {k: v[keep] for k, v in data.items()}
    if len(keep) != len(KNOWN) * len(GROUPS):
        raise ValueError('Known cases missing from the development data')
    worst = 0.
    for check in decision['loss_and_endpoint_point_nonworsening']['checks'] + decision['mean_noninferiority']['checks']:
        metric = check.get('metric', 'mean_deficit')
        for policy, value in (('v4_hybrid', check['candidate_mean']), (check['baseline'], check['baseline_mean'])):
            worst = max(worst, abs(stats(known, check['regime'], policy)[metric] - value))
    for check in decision['risk_point_nonworsening']['checks']:
        for policy, value in (('v4_hybrid', check['candidate_risk']), (check['baseline'], check['baseline_risk'])):
            worst = max(worst, abs(stats(known, check['regime'], policy)['risk'] - value))
    if worst > TOL:
        raise ValueError(f'Known V4 cases differ from the archive by {worst}')
    return {'cases_per_regime': len(keep), 'largest_abs_difference': worst, 'tolerance': TOL,
            'checked': 'loss/endpoint and mean-deficit means of v4_hybrid, capacity_v2, capacity_balance; '
                       'risks of v4_hybrid, v3_full, capacity_v2, capacity_balance'}


def file_hashes(split, output=OUTPUT):
    return {f'episodes-{s}.csv': hashlib.sha256(seed_path(split, s, output).read_bytes()).hexdigest()
            for s in SPLITS[split]}


def table(data):
    return {regime: {f'{p}/{s}' if s != 'variant' else p: stats(data, regime, p, s)
                     for p, s in study_arms()} for regime in REGIMES}


def summarize(output=OUTPUT, destination=ROOT / 'v5_results'):
    selection, selection_order = load('selection', output=output)
    development, development_order = load('development', output=output)
    ranking = rank(selection)
    both = {k: np.vstack([development[k], selection[k]]) for k in selection}
    summaries = {
        'selection': {'plan': PLAN['version'], 'split': 'selection', 'seeds': list(SPLITS['selection']),
                      'cases_per_regime': len(selection_order), 'tolerance': TOL,
                      'chosen_configuration': ranking[0]['config'], 'ranking': ranking,
                      'means_and_risk': table(selection), 'episode_files_sha256': file_hashes('selection', output),
                      'promotion': 'NOT_EVALUATED'},
        'development_grid': {'plan': PLAN['version'], 'split': 'development', 'seeds': list(SPLITS['development']),
                             'cases_per_regime': len(development_order),
                             'known_v4_archive_check': archive_check(development, development_order),
                             'sensitivity_ranking_development': rank(development),
                             'sensitivity_ranking_development_and_selection': rank(both),
                             'means_and_risk': table(development),
                             'episode_files_sha256': file_hashes('development', output)},
    }
    for name, summary in summaries.items():
        folder = Path(destination) / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + '\n')
    print('chosen configuration:', ranking[0]['config'])
    return summaries


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'summarize'))
    parser.add_argument('--split', choices=tuple(SPLITS))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.action == 'run':
        if not args.split:
            parser.error('run needs --split')
        run(args.split, args.workers, args.output)
    else:
        summarize(args.output)
