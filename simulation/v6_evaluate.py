"""Apply the standing promotion gates and the V6 round hypotheses (v6_acceptance_predeclared.json).

The gate arithmetic is v5_evaluate.py's, parameterised by a declaration, a
candidate and the policy list instead of module constants. With the V5
declaration, candidate v5_full and the V5 policies it reproduces
v5_evaluate.evaluate exactly (test_v6_evaluate.py). The same functions give the
point summary that the declared selection rule ranks.
"""
import csv
import json
from pathlib import Path

import numpy as np

from v4_metrics import _bootstrap_indices, _cvar_last_axis
from v4_scenarios import GROUPS

ROOT = Path(__file__).resolve().parent
METRICS = ('mean_deficit', 'total_dropped', 'end_deficit', 'tail_auc_deficit', 'total_moves')
MD, DROP = METRICS.index('mean_deficit'), METRICS.index('total_dropped')
INTEGRITY = ('fresh_tests', 'frozen_v4', 'frozen_v5', 'frozen_v6', 'scenario_identity', 'accounting',
             'observation_boundary', 'independent_replay', 'tamper_test', 'deterministic_repeat',
             'baseline_reproduction')


class Incomplete(ValueError):
    """Evidence is missing or unpaired: the decision is NOT_EVALUATED."""


def read_rows(paths, policies):
    rows = []
    for path in paths:
        with Path(path).open(newline='') as handle:
            for row in csv.DictReader(handle):
                if row['policy'] in policies and row.get('action_space', 'variant') == 'variant':
                    rows.append(row)
    return rows


def _seeds(pair):
    return tuple(range(pair[0], pair[1] + 1))


def stratified_indices(labels, groups, draws, seed):
    """v4_metrics._bootstrap_indices over an explicit family order; identical to it for the original GROUPS."""
    rng = np.random.default_rng(seed)
    labels = np.asarray(labels)
    pieces = []
    for group in groups:
        indices = np.flatnonzero(labels == group)
        if len(indices):
            pieces.append(indices[rng.integers(0, len(indices), (draws, len(indices)))])
    if not pieces:
        raise ValueError('Empty bootstrap population')
    return np.concatenate(pieces, axis=1)


class Gates:
    def __init__(self, declaration, candidate, policies):
        self.declaration = declaration
        self.candidate = candidate
        self.policies = tuple(policies)
        if candidate not in self.policies:
            raise ValueError('The candidate must be one of the evaluated policies')
        self.tol = declaration['tolerance']
        self.regimes = tuple(declaration['data']['regimes'])
        self.ood_families = tuple(declaration['ood']['families'])
        boot = declaration['metrics']['bootstrap']
        self.replicates, self.boot_seed = boot['replicates'], boot['seed']

    def declared_population(self):
        data = self.declaration['data']
        return {'validation': tuple(sorted((f, s) for f in GROUPS for s in _seeds(data['validation_seed_range']))),
                'ood': _seeds(data['ood_validation_seed_range'])}

    def matrices(self, rows, regimes):
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
        expected = {(r, p) for r in regimes for p in self.policies}
        if not expected <= set(table):
            raise Incomplete(f'Missing regime/policy cells: {sorted(expected - set(table))}')
        table = {k: v for k, v in table.items() if k in expected}
        order = sorted(next(iter(table.values())))
        for key, cell in table.items():
            if sorted(cell) != order:
                raise Incomplete(f'Unpaired cases for {key}')
        return {k: np.array([v[c] for c in order]) for k, v in table.items()}, order, accounting

    @staticmethod
    def harm(data, regime, policy):
        return np.maximum(data[regime, policy][:, MD] - data[regime, 'static'][:, MD], 0.0)

    def risk(self, data, regime, policy):
        return float(_cvar_last_axis(self.harm(data, regime, policy)))

    @staticmethod
    def interval(values):
        return [float(x) for x in np.percentile(values, [2.5, 97.5])]

    def original_gates(self, rows, population=None):
        d, cand, tol = self.declaration, self.candidate, self.tol
        data, order, accounting = self.matrices(rows, self.regimes)
        if population is not None and tuple(order) != tuple(population):
            raise Incomplete(f'Validation cases differ from the declared population ({len(order)} of {len(population)})')
        labels = np.array([family for family, _ in order])
        draws = _bootstrap_indices(labels, self.replicates, self.boot_seed)
        noisy = 'delayed_noisy'
        risk_boot = (_cvar_last_axis(self.harm(data, noisy, 'v3_full')[draws]) -
                     _cvar_last_axis(self.harm(data, noisy, cand)[draws]))
        drop_diff = data[noisy, 'v4_hybrid'][:, DROP] - data[noisy, cand][:, DROP]
        primary = {
            'risk_vs_v3_full': {'point': self.risk(data, noisy, 'v3_full') - self.risk(data, noisy, cand),
                                'ci95': self.interval(risk_boot)},
            'total_dropped_vs_v4_hybrid': {'point': float(drop_diff.mean()),
                                           'ci95': self.interval(drop_diff[draws].mean(axis=1))},
        }
        for gate in primary.values():
            gate['passed'] = bool(gate['ci95'][0] > 0)
        risk_checks, mean_checks, loss_checks = [], [], []
        for regime in self.regimes:
            mine = self.risk(data, regime, cand)
            for baseline in d['risk_point_nonworsening']['baselines']:
                theirs = self.risk(data, regime, baseline)
                risk_checks.append({'regime': regime, 'baseline': baseline, 'candidate_risk': mine,
                                    'baseline_risk': theirs, 'passed': bool(mine <= theirs + tol)})
            margin = 1 + d['mean_noninferiority']['margin']
            for baseline in d['mean_noninferiority']['baselines']:
                slack = margin * data[regime, baseline][:, MD] - data[regime, cand][:, MD]
                ci = self.interval(slack[draws].mean(axis=1))
                mean_checks.append({'regime': regime, 'baseline': baseline,
                                    'candidate_mean': float(data[regime, cand][:, MD].mean()),
                                    'baseline_mean': float(data[regime, baseline][:, MD].mean()),
                                    'allowance_slack': float(slack.mean()), 'ci95': ci, 'passed': bool(ci[0] >= -tol)})
            for baseline in d['loss_endpoint_point_nonworsening']['baselines']:
                for metric in d['loss_endpoint_point_nonworsening']['metrics']:
                    i = METRICS.index(metric)
                    mine_m, theirs_m = float(data[regime, cand][:, i].mean()), float(data[regime, baseline][:, i].mean())
                    loss_checks.append({'regime': regime, 'baseline': baseline, 'metric': metric, 'candidate_mean': mine_m,
                                        'baseline_mean': theirs_m, 'passed': bool(mine_m <= theirs_m + tol)})
        if len(loss_checks) != d['loss_endpoint_point_nonworsening']['required_checks']:
            raise Incomplete('Wrong number of loss/endpoint checks')
        family = d['underload']['family']
        under = [i for i, (f, _) in enumerate(order) if f == family]
        if not under:
            raise Incomplete('No underload episodes')
        violations = [{'regime': regime, 'seed': order[i][1]} for regime in self.regimes for i in under
                      if data[regime, cand][i, DROP] > tol or data[regime, cand][i, METRICS.index('total_moves')] != 0]
        return {
            'cases_per_regime': len(order), 'max_accounting_error': accounting,
            'primary': primary,
            'risk_point_nonworsening': {'checks': risk_checks, 'passed': all(c['passed'] for c in risk_checks)},
            'mean_noninferiority': {'checks': mean_checks, 'passed': all(c['passed'] for c in mean_checks)},
            'loss_endpoint_point_nonworsening': {'checks': loss_checks, 'passed': all(c['passed'] for c in loss_checks)},
            'underload': {'episodes_checked': len(under) * len(self.regimes), 'violations': violations, 'passed': not violations},
        }

    def ood_tables(self, rows, seeds=None):
        tables = []
        for family in self.ood_families:
            subset = [r for r in rows if r['group'] == family]
            if not subset:
                raise Incomplete(f'No rows for OOD family {family}')
            regime = f'ood_{family}'
            data, order, accounting = self.matrices(subset, (regime,))
            if seeds is not None and tuple(s for _, s in order) != tuple(seeds):
                raise Incomplete(f'OOD family {family} differs from the declared seeds ({len(order)} of {len(seeds)})')
            tables.append((family, regime, data, order, accounting))
        return tables

    def ood_gate(self, rows, seeds=None):
        families, pooled = {}, {}
        tables = self.ood_tables(rows, seeds)
        for family, regime, data, order, accounting in tables:
            families[family] = {'cases': len(order), 'max_accounting_error': accounting,
                                'risk': {p: self.risk(data, regime, p) for p in self.policies},
                                'mean_total_dropped': {p: float(data[regime, p][:, DROP].mean()) for p in self.policies}}
        for policy in self.policies:
            harm = np.concatenate([self.harm(d, r, policy) for _, r, d, _, _ in tables])
            drops = np.concatenate([d[r, policy][:, DROP] for _, r, d, _, _ in tables])
            pooled[policy] = {'risk': float(_cvar_last_axis(harm)), 'mean_total_dropped': float(drops.mean())}
        accounting = max(f['max_accounting_error'] for f in families.values())
        checks = [{'metric': m, 'baseline': b, 'candidate': pooled[self.candidate][m], 'baseline_value': pooled[b][m],
                   'passed': bool(pooled[self.candidate][m] <= pooled[b][m] + self.tol)}
                  for m in ('risk', 'mean_total_dropped') for b in ('v4_hybrid', 'capacity_balance')]
        return {'families': families, 'pooled': pooled, 'checks': checks, 'max_accounting_error': accounting,
                'passed': all(c['passed'] for c in checks) and accounting <= self.tol}

    def evaluate(self, validation_rows, ood_rows, integrity, population=None, integrity_names=INTEGRITY):
        record = {'version': self.declaration['version'], 'candidate': self.candidate, 'tolerance': self.tol,
                  'bootstrap': {'replicates': self.replicates, 'seed': self.boot_seed},
                  'declared_population_checked': population is not None}
        missing = []
        population = population or {}
        try:
            record['original'] = self.original_gates(validation_rows, population.get('validation'))
            if record['original']['max_accounting_error'] > self.tol:
                record['original']['accounting_failed'] = True
        except Incomplete as error:
            missing.append(f'validation: {error}')
        try:
            record['ood'] = self.ood_gate(ood_rows, population.get('ood'))
        except Incomplete as error:
            missing.append(f'ood: {error}')
        flags = {name: integrity.get(name) for name in integrity_names}
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

    def point_summary(self, validation_rows, ood_rows):
        """The quantities the declared selection rule ranks: point checks only, no bootstrap."""
        d, cand, tol = self.declaration, self.candidate, self.tol
        data, order, _ = self.matrices(validation_rows, self.regimes)
        loss, risk = [], []
        for regime in self.regimes:
            for baseline in d['loss_endpoint_point_nonworsening']['baselines']:
                for metric in d['loss_endpoint_point_nonworsening']['metrics']:
                    i = METRICS.index(metric)
                    if data[regime, cand][:, i].mean() > data[regime, baseline][:, i].mean() + tol:
                        loss.append([regime, baseline, metric])
            mine = self.risk(data, regime, cand)
            risk += [[regime, b] for b in d['risk_point_nonworsening']['baselines']
                     if mine > self.risk(data, regime, b) + tol]
        ood = self.ood_gate(ood_rows)
        ood_failed = [[c['metric'], c['baseline']] for c in ood['checks'] if not c['passed']]
        return {'candidate': cand, 'cases_per_regime': len(order),
                'failed_point_checks': len(loss) + len(risk) + len(ood_failed),
                'failed_loss_endpoint_checks': loss, 'failed_risk_checks': risk, 'failed_ood_checks': ood_failed,
                'delayed_noisy_mean_total_dropped': float(data['delayed_noisy', cand][:, DROP].mean()),
                'delayed_noisy_risk': self.risk(data, 'delayed_noisy', cand),
                'ood_pooled': ood['pooled'][cand]}

    def hypotheses(self, validation_rows, ood_rows, reference='v5_full'):
        """H1 (drain) and H2 (restraint) against the frozen V5 Full: paired bootstrap lower95 > 0."""
        data, order, _ = self.matrices(validation_rows, self.regimes)
        labels = np.array([family for family, _ in order])
        draws = _bootstrap_indices(labels, self.replicates, self.boot_seed)
        h1 = []
        for regime in self.declaration['data']['unlimited_buffer_regimes']:
            for metric in ('end_deficit', 'tail_auc_deficit'):
                i = METRICS.index(metric)
                diff = data[regime, reference][:, i] - data[regime, self.candidate][:, i]
                ci = self.interval(diff[draws].mean(axis=1))
                h1.append({'regime': regime, 'metric': metric, 'point': float(diff.mean()), 'ci95': ci,
                           'supported': bool(ci[0] > 0)})
        tables = self.ood_tables(ood_rows)
        harm = {p: np.concatenate([self.harm(d, r, p) for _, r, d, _, _ in tables]) for p in (reference, self.candidate)}
        ood_labels = np.array([family for family, _, _, order, _ in tables for _ in order])
        ood_draws = stratified_indices(ood_labels, self.ood_families, self.replicates, self.boot_seed)
        boot = _cvar_last_axis(harm[reference][ood_draws]) - _cvar_last_axis(harm[self.candidate][ood_draws])
        point = float(_cvar_last_axis(harm[reference]) - _cvar_last_axis(harm[self.candidate]))
        ci = self.interval(boot)
        return {'reference': reference,
                'H1_drain': {'checks': h1, 'supported': all(c['supported'] for c in h1)},
                'H2_restraint': {'point': point, 'ci95': ci, 'supported': bool(ci[0] > 0)}}


def load_declaration(name='v6_acceptance_predeclared.json'):
    return json.loads((ROOT / name).read_text())
