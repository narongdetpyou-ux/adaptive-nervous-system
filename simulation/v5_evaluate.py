"""Apply every gate of v5_acceptance_predeclared.json as fixed in v5_ood_plan.json.

evaluate(validation_rows, ood_rows, integrity) returns the decision record:
PASS only if every gate passes, NOT_EVALUATED if any evidence is missing or
incomplete, otherwise FAIL. Gates are never relaxed after results are seen.
"""
import csv
import json
from pathlib import Path

import numpy as np

from v4_metrics import _bootstrap_indices, _cvar_last_axis
from v4_scenarios import GROUPS

ROOT = Path(__file__).resolve().parent
DECLARATION = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())
PLAN = json.loads((ROOT / 'v5_ood_plan.json').read_text())
TOL = DECLARATION['tolerance']
CANDIDATE = 'v5_full'
POLICIES = tuple(PLAN['policies'])
REGIMES = tuple(DECLARATION['data']['regimes'])
OOD_FAMILIES = tuple(DECLARATION['ood']['families'])
REPLICATES = DECLARATION['metrics']['bootstrap']['replicates']
BOOT_SEED = DECLARATION['metrics']['bootstrap']['seed']
METRICS = ('mean_deficit', 'total_dropped', 'end_deficit', 'tail_auc_deficit', 'total_moves')
MD, DROP = METRICS.index('mean_deficit'), METRICS.index('total_dropped')
INTEGRITY = ('fresh_tests', 'frozen_v4', 'frozen_v5', 'scenario_identity', 'accounting',
             'observation_boundary', 'independent_replay', 'tamper_test', 'deterministic_repeat')


def _seeds(pair):
    return tuple(range(pair[0], pair[1] + 1))


# The declared case populations: an official decision is NOT_EVALUATED unless the rows match exactly.
DECLARED = {
    'validation': tuple(sorted((family, seed) for family in GROUPS
                               for seed in _seeds(DECLARATION['data']['validation_seed_range']))),
    'ood': _seeds(DECLARATION['data']['ood_validation_seed_range']),
}
if len(DECLARED['validation']) != DECLARATION['data']['validation_count']:
    raise ValueError('Declared validation population differs from the declared count')


class Incomplete(ValueError):
    """Evidence is missing or unpaired: the decision is NOT_EVALUATED."""


def read_rows(paths, policies=POLICIES):
    rows = []
    for path in paths:
        with Path(path).open(newline='') as handle:
            for row in csv.DictReader(handle):
                if row['policy'] in policies and row.get('action_space', 'variant') == 'variant':
                    rows.append(row)
    return rows


def matrices(rows, regimes):
    """{(regime, policy): cases x METRICS}, identical case order (family, seed) for every key."""
    table, accounting = {}, 0.0
    for row in rows:
        key = (row['regime'], row['policy'])
        case = (row['group'], int(row['scenario_seed']))
        cell = table.setdefault(key, {})
        if case in cell:
            raise Incomplete(f'Duplicate row {key} {case}')
        cell[case] = [float(row[m]) for m in METRICS]
        accounting = max(accounting, float(row['max_mass_error']), float(row['max_budget_error']))
    expected = {(r, p) for r in regimes for p in POLICIES}
    if set(table) != expected:
        raise Incomplete(f'Missing regime/policy cells: {sorted(expected - set(table))}')
    order = sorted(next(iter(table.values())))
    for key, cell in table.items():
        if sorted(cell) != order:
            raise Incomplete(f'Unpaired cases for {key}')
    return {k: np.array([v[c] for c in order]) for k, v in table.items()}, order, accounting


def _harm(data, regime, policy):
    return np.maximum(data[regime, policy][:, MD] - data[regime, 'static'][:, MD], 0.0)


def _risk(data, regime, policy):
    return float(_cvar_last_axis(_harm(data, regime, policy)))


def _interval(values):
    return [float(x) for x in np.percentile(values, [2.5, 97.5])]


def original_gates(rows, population=None):
    data, order, accounting = matrices(rows, REGIMES)
    if population is not None and tuple(order) != tuple(population):
        raise Incomplete(f'Validation cases differ from the declared population ({len(order)} of {len(population)})')
    labels = np.array([family for family, _ in order])
    draws = _bootstrap_indices(labels, REPLICATES, BOOT_SEED)
    noisy = 'delayed_noisy'
    risk_boot = (_cvar_last_axis(_harm(data, noisy, 'v3_full')[draws]) -
                 _cvar_last_axis(_harm(data, noisy, CANDIDATE)[draws]))
    drop_diff = data[noisy, 'v4_hybrid'][:, DROP] - data[noisy, CANDIDATE][:, DROP]
    primary = {
        'risk_vs_v3_full': {'point': _risk(data, noisy, 'v3_full') - _risk(data, noisy, CANDIDATE),
                            'ci95': _interval(risk_boot)},
        'total_dropped_vs_v4_hybrid': {'point': float(drop_diff.mean()),
                                       'ci95': _interval(drop_diff[draws].mean(axis=1))},
    }
    for gate in primary.values():
        gate['passed'] = bool(gate['ci95'][0] > 0)
    risk_checks, mean_checks, loss_checks = [], [], []
    for regime in REGIMES:
        mine = _risk(data, regime, CANDIDATE)
        for baseline in DECLARATION['risk_point_nonworsening']['baselines']:
            theirs = _risk(data, regime, baseline)
            risk_checks.append({'regime': regime, 'baseline': baseline, 'candidate_risk': mine,
                                'baseline_risk': theirs, 'passed': bool(mine <= theirs + TOL)})
        margin = 1 + DECLARATION['mean_noninferiority']['margin']
        for baseline in DECLARATION['mean_noninferiority']['baselines']:
            slack = margin * data[regime, baseline][:, MD] - data[regime, CANDIDATE][:, MD]
            ci = _interval(slack[draws].mean(axis=1))
            mean_checks.append({'regime': regime, 'baseline': baseline,
                                'candidate_mean': float(data[regime, CANDIDATE][:, MD].mean()),
                                'baseline_mean': float(data[regime, baseline][:, MD].mean()),
                                'allowance_slack': float(slack.mean()), 'ci95': ci, 'passed': bool(ci[0] >= -TOL)})
        for baseline in DECLARATION['loss_endpoint_point_nonworsening']['baselines']:
            for metric in DECLARATION['loss_endpoint_point_nonworsening']['metrics']:
                i = METRICS.index(metric)
                mine_m, theirs_m = float(data[regime, CANDIDATE][:, i].mean()), float(data[regime, baseline][:, i].mean())
                loss_checks.append({'regime': regime, 'baseline': baseline, 'metric': metric, 'candidate_mean': mine_m,
                                    'baseline_mean': theirs_m, 'passed': bool(mine_m <= theirs_m + TOL)})
    if len(loss_checks) != DECLARATION['loss_endpoint_point_nonworsening']['required_checks']:
        raise Incomplete('Wrong number of loss/endpoint checks')
    family = DECLARATION['underload']['family']
    under = [i for i, (f, _) in enumerate(order) if f == family]
    if not under:
        raise Incomplete('No underload episodes')
    violations = [{'regime': regime, 'seed': order[i][1]} for regime in REGIMES for i in under
                  if data[regime, CANDIDATE][i, DROP] > TOL or data[regime, CANDIDATE][i, METRICS.index('total_moves')] != 0]
    return {
        'cases_per_regime': len(order), 'max_accounting_error': accounting,
        'primary': primary,
        'risk_point_nonworsening': {'checks': risk_checks, 'passed': all(c['passed'] for c in risk_checks)},
        'mean_noninferiority': {'checks': mean_checks, 'passed': all(c['passed'] for c in mean_checks)},
        'loss_endpoint_point_nonworsening': {'checks': loss_checks, 'passed': all(c['passed'] for c in loss_checks)},
        'underload': {'episodes_checked': len(under) * len(REGIMES), 'violations': violations, 'passed': not violations},
    }


def ood_gate(rows, seeds=None):
    families = {}
    pooled_rows = []
    for family in OOD_FAMILIES:
        subset = [r for r in rows if r['group'] == family]
        if not subset:
            raise Incomplete(f'No rows for OOD family {family}')
        data, order, accounting = matrices(subset, (f'ood_{family}',))
        if seeds is not None and tuple(s for _, s in order) != tuple(seeds):
            raise Incomplete(f'OOD family {family} differs from the declared seeds ({len(order)} of {len(seeds)})')
        regime = f'ood_{family}'
        families[family] = {'cases': len(order), 'max_accounting_error': accounting,
                            'risk': {p: _risk(data, regime, p) for p in POLICIES},
                            'mean_total_dropped': {p: float(data[regime, p][:, DROP].mean()) for p in POLICIES}}
        pooled_rows.append((data, regime))
    pooled = {}
    for policy in POLICIES:
        harm = np.concatenate([_harm(d, r, policy) for d, r in pooled_rows])
        drops = np.concatenate([d[r, policy][:, DROP] for d, r in pooled_rows])
        pooled[policy] = {'risk': float(_cvar_last_axis(harm)), 'mean_total_dropped': float(drops.mean())}
    accounting = max(f['max_accounting_error'] for f in families.values())
    checks = [{'metric': m, 'baseline': b, 'candidate': pooled[CANDIDATE][m], 'baseline_value': pooled[b][m],
               'passed': bool(pooled[CANDIDATE][m] <= pooled[b][m] + TOL)}
              for m in ('risk', 'mean_total_dropped') for b in ('v4_hybrid', 'capacity_balance')]
    return {'families': families, 'pooled': pooled, 'checks': checks, 'max_accounting_error': accounting,
            'passed': all(c['passed'] for c in checks) and accounting <= TOL}


def evaluate(validation_rows, ood_rows, integrity, population=None):
    """population=DECLARED for the official decision; None only for synthetic and development checks."""
    record = {'version': PLAN['version'], 'candidate': CANDIDATE, 'tolerance': TOL,
              'bootstrap': {'replicates': REPLICATES, 'seed': BOOT_SEED},
              'declared_population_checked': population is not None}
    missing = []
    population = population or {}
    try:
        record['original'] = original_gates(validation_rows, population.get('validation'))
        if record['original']['max_accounting_error'] > TOL:
            record['original']['accounting_failed'] = True
    except Incomplete as error:
        missing.append(f'validation: {error}')
    try:
        record['ood'] = ood_gate(ood_rows, population.get('ood'))
    except Incomplete as error:
        missing.append(f'ood: {error}')
    flags = {name: integrity.get(name) for name in INTEGRITY}
    missing += [f'integrity: {name}' for name, value in flags.items() if value is None]
    record['integrity'] = {'flags': flags, 'passed': all(v is True for v in flags.values())}
    failed = []
    if 'original' in record:
        o = record['original']
        failed += [f'primary {k}' for k, g in o['primary'].items() if not g['passed']]
        failed += [k for k in ('risk_point_nonworsening', 'mean_noninferiority',
                               'loss_endpoint_point_nonworsening', 'underload') if not o[k]['passed']]
        if o.get('accounting_failed'):
            failed.append('validation accounting')
    if 'ood' in record and not record['ood']['passed']:
        failed.append('ood')
    failed += [f'integrity {name}' for name, value in flags.items() if value is False]
    record['missing_evidence'] = missing
    record['failed_gates'] = failed
    record['decision'] = 'NOT_EVALUATED' if missing else ('PASS' if not failed else 'FAIL')
    return record
