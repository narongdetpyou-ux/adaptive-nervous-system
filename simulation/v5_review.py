"""Independent review of a frozen V5 validation run (v5_ood_plan.json).

Separate code paths from v5_evaluate.py: plain-Python means, CVaR and
percentiles, a separately written family-stratified bootstrap on the declared
random stream, per-family and pooled OOD risk and dropped work, exact re-simulation of the representative traces, controller
replay from recorded observation payloads only, minute-level physical
invariants, regenerated scenario manifests and a negative tamper test.
"""
import copy
import csv
import gzip
import json
import math
from pathlib import Path

import numpy as np

import v5_grid
import v5_ood
from v4_environment import REGIME_CONFIGS
from v4_scenarios import GROUPS
from v5_simulation import make_controller, simulate_case
from v5_validation import POLICIES, canonical, validation_case

ROOT = Path(__file__).resolve().parent
DECLARATION = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())
TOL = DECLARATION['tolerance']
CANDIDATE = 'v5_full'
REGIMES = tuple(DECLARATION['data']['regimes'])
BOOT = DECLARATION['metrics']['bootstrap']
METRICS = ('mean_deficit', 'total_dropped', 'end_deficit', 'tail_auc_deficit', 'total_moves')


def load_rows(run, split):
    rows = []
    for path in sorted((Path(run) / split).glob('episodes-*.csv')):
        with path.open(newline='') as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def cvar90(values):
    ordered = sorted(values, reverse=True)
    mass = 0.1 * len(ordered)
    whole = math.floor(mass + 1e-12)
    total = math.fsum(ordered[:whole])
    if mass - whole > 1e-12 and whole < len(ordered):
        total += (mass - whole) * ordered[whole]
    return total / mass


def percentile(values, q):
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def table(rows):
    cells = {}
    for row in rows:
        cells.setdefault((row['regime'], row['policy']), {})[(row['group'], int(row['scenario_seed']))] = \
            {m: float(row[m]) for m in METRICS}
    order = sorted(next(iter(cells.values())))
    return cells, order


def bootstrap_draws(labels, replicates, seed):
    """Family-stratified resampling written independently of v4_metrics."""
    rng = np.random.default_rng(seed)
    columns = []
    for family in GROUPS:
        members = [i for i, label in enumerate(labels) if label == family]
        if members:
            picks = rng.integers(0, len(members), (replicates, len(members)))
            columns.append([[members[j] for j in line] for line in picks.tolist()])
    return [sum((block[r] for block in columns), []) for r in range(replicates)]


def independent_statistics(rows):
    cells, order = table(rows)
    harm = {}
    out = {'means': {}, 'risk': {}}
    for (regime, policy), cell in cells.items():
        values = [cell[c] for c in order]
        out['means'][f'{regime}|{policy}'] = {m: math.fsum(v[m] for v in values) / len(values) for m in METRICS}
        static = [cells[regime, 'static'][c]['mean_deficit'] for c in order]
        harm[regime, policy] = [max(v['mean_deficit'] - s, 0.0) for v, s in zip(values, static)]
        out['risk'][f'{regime}|{policy}'] = cvar90(harm[regime, policy])
    if 'delayed_noisy' in {r for r, _ in cells}:
        draws = bootstrap_draws([f for f, _ in order], BOOT['replicates'], BOOT['seed'])
        noisy = 'delayed_noisy'
        v3, cand = harm[noisy, 'v3_full'], harm[noisy, CANDIDATE]
        risk = [cvar90([v3[i] for i in d]) - cvar90([cand[i] for i in d]) for d in draws]
        diff = [cells[noisy, 'v4_hybrid'][c]['total_dropped'] - cells[noisy, CANDIDATE][c]['total_dropped'] for c in order]
        drops = [math.fsum(diff[i] for i in d) / len(d) for d in draws]
        out['primary_point'] = {
            'risk_vs_v3_full': out['risk'][f'{noisy}|v3_full'] - out['risk'][f'{noisy}|{CANDIDATE}'],
            'total_dropped_vs_v4_hybrid': math.fsum(diff) / len(diff)}
        out['primary_ci95'] = {'risk_vs_v3_full': [percentile(risk, .025), percentile(risk, .975)],
                               'total_dropped_vs_v4_hybrid': [percentile(drops, .025), percentile(drops, .975)]}
        out['noninferiority_ci95'] = {}
        allowance = 1.0 + DECLARATION['mean_noninferiority']['margin']
        for regime in REGIMES:
            for baseline in DECLARATION['mean_noninferiority']['baselines']:
                slack = [allowance * cells[regime, baseline][c]['mean_deficit'] - cells[regime, CANDIDATE][c]['mean_deficit']
                         for c in order]
                means = [math.fsum(slack[i] for i in d) / len(d) for d in draws]
                out['noninferiority_ci95'][f'{regime}|{baseline}'] = [percentile(means, .025), percentile(means, .975)]
    family = DECLARATION['underload']['family']
    out['underload_violations'] = sorted(
        (regime, seed) for (regime, policy), cell in cells.items() if policy == CANDIDATE
        for (group, seed), v in cell.items()
        if group == family and (v['total_dropped'] > TOL or v['total_moves'] != 0))
    out['underload_episodes'] = sum(1 for (regime, policy), cell in cells.items() if policy == CANDIDATE
                                    for group, _ in cell if group == family)
    return out


def independent_ood(rows):
    """Per-family and pooled OOD risk and mean dropped work, harm paired with static by family and seed."""
    cells = {}
    for row in rows:
        cells.setdefault((row['group'], row['policy']), {})[int(row['scenario_seed'])] = \
            (float(row['mean_deficit']), float(row['total_dropped']))
    families = sorted({family for family, _ in cells})
    policies = sorted({policy for _, policy in cells})
    out = {'families': {f: {} for f in families}, 'pooled': {}}
    for policy in policies:
        harm, drops = [], []
        for family in families:
            mine, static = cells[family, policy], cells[family, 'static']
            seeds = sorted(mine)
            family_harm = [max(mine[s][0] - static[s][0], 0.0) for s in seeds]
            family_drops = [mine[s][1] for s in seeds]
            out['families'][family][policy] = {'risk': cvar90(family_harm),
                                               'mean_total_dropped': math.fsum(family_drops) / len(family_drops)}
            harm += family_harm
            drops += family_drops
        out['pooled'][policy] = {'risk': cvar90(harm), 'mean_total_dropped': math.fsum(drops) / len(drops)}
    return out


def compare_ood(stats, decision):
    mismatches = []
    ood = decision['ood']
    for policy, values in ood['pooled'].items():
        for metric, value in values.items():
            if not _close(stats['pooled'][policy][metric], value):
                mismatches.append(('ood pooled', policy, metric))
    for family, report in ood['families'].items():
        for policy in report['risk']:
            if not _close(stats['families'][family][policy]['risk'], report['risk'][policy]):
                mismatches.append(('ood family', family, policy, 'risk'))
            if not _close(stats['families'][family][policy]['mean_total_dropped'], report['mean_total_dropped'][policy]):
                mismatches.append(('ood family', family, policy, 'mean_total_dropped'))
    for check in ood['checks']:
        for policy, key in ((CANDIDATE, 'candidate'), (check['baseline'], 'baseline_value')):
            if not _close(stats['pooled'][policy][check['metric']], check[key]):
                mismatches.append(('ood check', check['metric'], policy))
    return mismatches


def _close(a, b):
    return abs(a - b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))


def compare_with_decision(stats, decision):
    """Every number the evaluator used, recomputed independently."""
    mismatches = []
    original = decision['original']
    for check in original['loss_endpoint_point_nonworsening']['checks']:
        for policy, key in ((CANDIDATE, 'candidate_mean'), (check['baseline'], 'baseline_mean')):
            mine = stats['means'][f"{check['regime']}|{policy}"][check['metric']]
            if not _close(mine, check[key]):
                mismatches.append(('loss', check['regime'], policy, check['metric']))
    for check in original['risk_point_nonworsening']['checks']:
        for policy, key in ((CANDIDATE, 'candidate_risk'), (check['baseline'], 'baseline_risk')):
            if not _close(stats['risk'][f"{check['regime']}|{policy}"], check[key]):
                mismatches.append(('risk', check['regime'], policy))
    for name, gate in original['primary'].items():
        if not all(_close(a, b) for a, b in zip(stats['primary_ci95'][name], gate['ci95'])):
            mismatches.append(('primary', name))
        if not _close(stats['primary_point'][name], gate['point']):
            mismatches.append(('primary point', name))
    for check in original['mean_noninferiority']['checks']:
        if not all(_close(a, b) for a, b in zip(stats['noninferiority_ci95'][f"{check['regime']}|{check['baseline']}"], check['ci95'])):
            mismatches.append(('noninferiority', check['regime'], check['baseline']))
        for policy, key in ((CANDIDATE, 'candidate_mean'), (check['baseline'], 'baseline_mean')):
            if not _close(stats['means'][f"{check['regime']}|{policy}"]['mean_deficit'], check[key]):
                mismatches.append(('noninferiority mean', check['regime'], policy))
    underload = original['underload']
    if (stats['underload_episodes'] != underload['episodes_checked'] or
            stats['underload_violations'] != sorted((v['regime'], v['seed']) for v in underload['violations'])):
        mismatches.append(('underload',))
    return mismatches


def accounting(rows):
    return max((max(float(r['max_mass_error']), float(r['max_budget_error'])) for r in rows), default=math.inf)


def _environment(split, family, regime):
    return REGIME_CONFIGS[regime] if split == 'validation' else v5_ood.family_config(family)


def invariants(records, config):
    problems = []
    for rec in records:
        staff = rec['committed_staff']
        if abs(math.fsum(staff) - config.budget) > TOL or min(staff) < config.minimum_staff - TOL:
            problems.append(('budget', rec['t']))
        if config.integer_resources and any(x != round(x) for x in staff):
            problems.append(('integer', rec['t']))
        for stage, cap in enumerate(config.buffer_capacities):
            if cap is not None and rec['end_queue'][stage] > cap + TOL:
                problems.append(('buffer', rec['t']))
        mass = rec['offered_cumulative'] - rec['completed_cumulative'] - rec['retained_work'] - rec['cumulative_drops']
        if abs(mass) > TOL:
            problems.append(('mass', rec['t']))
        decision = rec['decision_input']
        if decision['source_t'] is not None and (decision['observation_age'] != config.sensor_lag or
                                                 decision['source_t'] != rec['t'] - config.sensor_lag):
            problems.append(('observation', rec['t']))
    return problems


def controller_replay(policy, config, records):
    """Re-decide every minute from the recorded observation payloads alone."""
    controller = make_controller(policy, config)
    for rec in records:
        if controller is None:
            raw = np.zeros(3)
        else:
            d, f = rec['decision_input'], rec['feedback_input']
            raw, _ = controller.decide(d['delivery_t'], np.array(d['queues']), [np.array(h) for h in d['history']],
                                       np.array(d['committed_staff']), d['arrival'])
        if not np.allclose(raw, rec['raw_proposal_delta'], rtol=0, atol=1e-12):
            return False
        if controller is not None:
            f = rec['feedback_input']
            controller.observe(rec['t'], np.array(f['effective_staff']), np.array(f['flow']), np.array(f['end_queue']))
    return True


def replay_traces(run, allow_reserved=True):
    summary = {'traces': 0, 'exact': 0, 'controller_replay': 0, 'invariant_problems': []}
    for path in sorted((Path(run) / 'traces').glob('*.jsonl.gz')):
        for line in gzip.decompress(path.read_bytes()).decode().splitlines():
            item = json.loads(line)
            split, seed, family, regime, policy = (item[k] for k in ('split', 'seed', 'family', 'regime', 'policy'))
            config = _environment(split, family, regime)
            if split == 'validation':
                _, trace = simulate_case(policy, validation_case(seed, family, allow_reserved), regime=regime, detailed=True)
            else:
                _, trace = v5_ood.run_episode(policy, v5_ood.build_case(family, seed, allow_reserved=allow_reserved), detailed=True)
            summary['traces'] += 1
            summary['exact'] += canonical(trace) == canonical(item['records'])
            summary['controller_replay'] += controller_replay(policy, config, item['records'])
            summary['invariant_problems'] += [(split, seed, family, regime, policy, p) for p in invariants(item['records'], config)]
    return summary


def scenario_identity(run, allow_reserved=True):
    mismatched = []
    for path in sorted((Path(run) / 'manifests').glob('*.json')):
        split, seed = path.stem.split('-')
        stored = json.loads(path.read_text())
        for family, manifest in stored.items():
            if split == 'validation':
                fresh = validation_case(int(seed), family, allow_reserved).manifest()
            else:
                fresh = v5_ood.build_case(family, int(seed), allow_reserved=allow_reserved).manifest()
            if json.loads(json.dumps(fresh, sort_keys=True, default=float)) != manifest:
                mismatched.append((split, seed, family))
    return mismatched


def tamper_detected(rows, ood_rows, decision):
    """Copies with one candidate value changed must disagree with the evaluator, for both sets."""
    tampered = copy.deepcopy(rows)
    target = next(r for r in tampered if r['policy'] == CANDIDATE and r['regime'] == 'delayed_noisy')
    target['total_dropped'] = str(float(target['total_dropped']) + 1000.0)
    tampered_ood = copy.deepcopy(ood_rows)
    target = next(r for r in tampered_ood if r['policy'] == CANDIDATE)
    target['total_dropped'] = str(float(target['total_dropped']) + 1000.0)
    return (bool(compare_with_decision(independent_statistics(tampered), decision)) and
            bool(compare_ood(independent_ood(tampered_ood), decision)))


def review(run, decision, *, allow_reserved=True):
    validation, ood = load_rows(run, 'validation'), load_rows(run, 'ood')
    stats = independent_statistics(validation)
    mismatches = compare_with_decision(stats, decision) + compare_ood(independent_ood(ood), decision)
    replay = replay_traces(run, allow_reserved)
    identity = scenario_identity(run, allow_reserved)
    flags = {
        'scenario_identity': not identity,
        'accounting': max(accounting(validation), accounting(ood)) <= TOL,
        'observation_boundary': replay['traces'] > 0 and replay['controller_replay'] == replay['traces']
                                and not any(p[-1][0] == 'observation' for p in replay['invariant_problems']),
        'independent_replay': replay['traces'] > 0 and replay['exact'] == replay['traces'] and not replay['invariant_problems'],
        'statistics_match': not mismatches,
        'tamper_test': tamper_detected(validation, ood, decision),
    }
    return {'flags': flags, 'statistic_mismatches': mismatches, 'replay': replay,
            'scenario_mismatches': identity, 'rows': {'validation': len(validation), 'ood': len(ood)}}
