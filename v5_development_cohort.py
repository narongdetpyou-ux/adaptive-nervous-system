"""Analyze complete, paired development slices and replay the middle-stage switch."""
import argparse
import csv
import hashlib
import json
from itertools import product
from tempfile import TemporaryDirectory
from pathlib import Path

import numpy as np

from v4_metrics import CORE_METRICS, _bootstrap_indices, _cvar_last_axis, _row_metric_values
from v4_scenarios import GROUPS, REGIMES, SPLIT_SEEDS
from v5_development import BASELINES, ROOT, development_cases, study_arms
from v5_simulation import simulate_case

SEEDS = SPLIT_SEEDS['development']
METRICS = ('mean_deficit', 'total_dropped', 'end_deficit', 'tail_auc_deficit', 'total_moves')
DROP = METRICS.index('total_dropped')
DEFICIT = METRICS.index('mean_deficit')
CANDIDATE = ('v5_full', 'variant')
PROTOCOL = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())


def read_seed(path, seed):
    expected = set(product(REGIMES, GROUPS, study_arms()))
    rows = {}
    with path.open(newline='') as handle:
        for r in csv.DictReader(handle):
            key = r['regime'], r['group'], (r['policy'], r['action_space'])
            if int(r['scenario_seed']) != seed or key not in expected or key in rows:
                raise ValueError(f'Unexpected or duplicate episode in seed {seed}: {key}')
            if r['drain_censored'] not in ('True', 'False'):
                raise ValueError('Invalid drain flag')
            metrics = {k: float(r[k]) for k in (*CORE_METRICS, 'max_mass_error', 'max_budget_error')}
            metrics['drain_censored'] = r['drain_censored'] == 'True'
            _row_metric_values(metrics)
            rows[key] = tuple(metrics[k] for k in METRICS)
    if rows.keys() != expected:
        raise ValueError(f'Incomplete paired data for seed {seed}')
    return rows


def read_cohort(directory, seeds):
    by_seed = {s: read_seed(directory / f'episodes-{s}.csv', s) for s in seeds}
    return {(regime, arm): np.array([by_seed[s][regime, group, arm]
                                     for group in GROUPS for s in seeds])
            for regime in REGIMES for arm in study_arms()}


def middle_switch(trace):
    # The frozen generator impairs the middle stage during [39,59).
    window = trace[39:59]
    if [r['t'] for r in window] != list(range(39, 59)):
        raise ValueError('Incomplete middle-stage switch trace')
    first = next((r for r in window if r['applied_delta'][1] > 0), None)
    return {
        'first_reinforcement_minute': None if first is None else first['t'],
        'first_reinforcement_units': None if first is None else first['applied_delta'][1],
        'committed_before_switch': window[0]['staff_before'][1],
        'middle_stage_dropped_in_window': sum(r['dropped_by_stage'][1] for r in window),
    }


def switching_replays(seeds, data):
    results = []
    for i, seed in enumerate(seeds):
        case = development_cases()[seed, 'generated_switching_fault']
        case_index = GROUPS.index(case.group) * len(seeds) + i
        for policy in ('v4_hybrid', 'capacity_balance', 'v5_full'):
            row, trace = simulate_case(policy, case, regime='delayed_noisy', detailed=True)
            expected = data['delayed_noisy', (policy, 'variant')][case_index]
            actual = [row[k] for k in METRICS]
            if not np.allclose(actual, expected, atol=PROTOCOL['tolerance'], rtol=0):
                raise ValueError(f'Trace disagrees with episode row for {policy}, seed {seed}')
            results.append({'seed': seed, 'policy': policy, 'episode_dropped': row['total_dropped'],
                            **middle_switch(trace)})
    return results


def summarize(data, seeds):
    tolerance = PROTOCOL['tolerance']
    boot = PROTOCOL['metrics']['bootstrap']
    labels = np.repeat(GROUPS, len(seeds))
    draws = _bootstrap_indices(labels, boot['replicates'], boot['seed'])
    output = {'seeds': list(seeds), 'episode_count': len(labels)*len(data),
              'regimes': {}, 'delayed_noisy_family_drops': [], 'promotion': 'NOT_EVALUATED'}
    # The frozen V4 archive reports the entire eight-seed cohort.
    archive = None
    if tuple(seeds) == tuple(SEEDS):
        archive = json.loads((ROOT / 'v4_results/development/summary.json').read_text())['regimes']
    for regime in REGIMES:
        static = data[regime, ('static', 'variant')][:, DEFICIT]
        means, risks = {}, {}
        for policy, space in study_arms():
            name = policy if space == 'variant' else f'{policy}/{space}'
            matrix = data[regime, (policy, space)]
            harm = np.maximum(matrix[:, DEFICIT] - static, 0)
            risks[name] = harm
            means[name] = dict(zip(METRICS, matrix.mean(axis=0).tolist()))
            means[name]['risk'] = float(_cvar_last_axis(harm))
            if archive is not None and policy in BASELINES and space == 'variant':
                expected = archive[regime]['overall'][policy]
                for k in METRICS:
                    if abs(means[name][k] - expected[k]) > tolerance:
                        raise ValueError(f'Frozen baseline mismatch for {regime}/{policy}/{k}')
                if abs(means[name]['risk'] - archive[regime]['static_relative_harm'][policy]['cvar90']) > tolerance:
                    raise ValueError(f'Frozen risk mismatch for {regime}/{policy}')
        point_checks = {}
        candidate = means['v5_full']
        for rule, metrics in [('loss_endpoint_point_nonworsening', PROTOCOL['loss_endpoint_point_nonworsening']['metrics']),
                              ('risk_point_nonworsening', ['risk'])]:
            point_checks[rule] = {f'{b}/{m}': candidate[m] <= means[b][m]+tolerance
                                  for b in PROTOCOL[rule]['baselines'] for m in metrics}
        full = data[regime, CANDIDATE]
        underload = full[labels == PROTOCOL['underload']['family']]
        quiet = np.all(underload[:, [DROP, METRICS.index('total_moves')]] <= tolerance, axis=1)
        output['regimes'][regime] = {'means': means, 'point_checks': point_checks,
                                     'quiet_underload_cases': int(quiet.sum())}
        if regime != 'delayed_noisy':
            continue
        output['delayed_noisy_seed_drops'] = [
            {'seed': seed, **{policy: float(data[regime, (policy, 'variant')][i::len(seeds), DROP].mean())
                              for policy in ('v4_hybrid', 'capacity_balance', 'v5_full')}}
            for i, seed in enumerate(seeds)]
        effects = {}
        for baseline in ('v4_hybrid', 'capacity_balance'):
            difference = data[regime, (baseline, 'variant')][:, DROP] - full[:, DROP]
            effects[baseline + '_minus_v5_drop'] = {
                'mean': float(difference.mean()),
                'ci95': np.percentile(difference[draws].mean(axis=1), [2.5, 97.5]).tolist()}
            for ix, group in enumerate(GROUPS):
                family_draws = draws[:, ix*len(seeds):(ix+1)*len(seeds)]
                output['delayed_noisy_family_drops'].append({
                    'group': group, 'baseline': baseline,
                    'baseline_minus_v5': float(difference[labels == group].mean()),
                    'ci95': np.percentile(difference[family_draws].mean(axis=1), [2.5, 97.5]).tolist()})
        paired_risk = _cvar_last_axis(risks['v3_full'][draws]) - _cvar_last_axis(risks['v5_full'][draws])
        effects['v3_minus_v5_risk'] = {'difference': means['v3_full']['risk'] - candidate['risk'],
                                       'ci95': np.percentile(paired_risk, [2.5, 97.5]).tolist()}
        output['delayed_noisy_effects'] = effects
    return output


def analyze(directory, seeds, destination):
    if not seeds or tuple(sorted(set(seeds))) != tuple(seeds) or not set(seeds) <= set(SEEDS):
        raise ValueError('Expected unique, ordered seeds from the original development split')
    if destination.resolve() in {(directory / f'episodes-{s}.csv').resolve() for s in SEEDS}:
        raise ValueError('The report destination would overwrite an episode input')
    data = read_cohort(directory, seeds)
    report = summarize(data, seeds)
    report['switching_replays'] = switching_replays(seeds, data)
    report['input_sha256'] = {f'episodes-{s}.csv': hashlib.sha256(
        (directory / f'episodes-{s}.csv').read_bytes()).hexdigest() for s in seeds}
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=destination.parent) as tmp:
        staged = Path(tmp) / destination.name
        staged.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
        staged.replace(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=ROOT / 'local_runs/v5_development')
    parser.add_argument('--seeds', type=int, nargs='+', choices=SEEDS, default=SEEDS)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    analyze(args.input, args.seeds, args.output)
