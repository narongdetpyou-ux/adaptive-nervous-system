"""Independent review of a frozen V6 validation run (v6_validation_plan.json).

Separate code paths from v6_evaluate.py. The review covers:
- plain-Python means, CVaR and percentiles;
- a separately written family-stratified bootstrap on the declared stream;
- the round hypotheses H1/H2 against V5 Full;
- exact re-simulation of the representative traces;
- controller replay from recorded observations only;
- minute-level physical invariants and regenerated scenario manifests;
- a negative tamper test.
Version-independent helpers come from the frozen v5_review.
"""
import copy
import csv
import gzip
import json
import math
from pathlib import Path

import numpy as np

import v5_ood
import v6_grid
import v6_validation
from v4_environment import REGIME_CONFIGS
from v4_scenarios import GROUPS
from v5_review import accounting, cvar90, invariants, percentile
from v5_simulation import make_controller as baseline_controller
from v5_validation import canonical
from v6_policy import V6Policy

DECLARATION = v6_grid.DECLARATION
TOL = DECLARATION['tolerance']
REGIMES = tuple(DECLARATION['data']['regimes'])
UNLIMITED = tuple(DECLARATION['data']['unlimited_buffer_regimes'])
OOD_FAMILIES = tuple(DECLARATION['ood']['families'])
BOOT = DECLARATION['metrics']['bootstrap']
METRICS = ('mean_deficit', 'total_dropped', 'end_deficit', 'tail_auc_deficit', 'total_moves')
REFERENCE = 'v5_full'


def load_rows(run, split):
    rows = []
    for path in sorted((Path(run) / split).glob('episodes-*.csv')):
        with path.open(newline='') as handle:
            rows.extend(csv.DictReader(handle))
    return rows


def table(rows):
    cells = {}
    for row in rows:
        cells.setdefault((row['regime'], row['policy']), {})[(row['group'], int(row['scenario_seed']))] = \
            {m: float(row[m]) for m in METRICS}
    order = sorted(next(iter(cells.values())))
    return cells, order


def bootstrap_draws(labels, groups, replicates, seed):
    """Family-stratified resampling written independently of v4_metrics, in the given family order."""
    rng = np.random.default_rng(seed)
    blocks = []
    for family in groups:
        members = [i for i, label in enumerate(labels) if label == family]
        if members:
            picks = rng.integers(0, len(members), (replicates, len(members)))
            blocks.append([[members[j] for j in line] for line in picks.tolist()])
    return [sum((block[r] for block in blocks), []) for r in range(replicates)]


def _mean(values):
    return math.fsum(values) / len(values)


def independent_statistics(rows, candidate):
    cells, order = table(rows)
    harm, out = {}, {'means': {}, 'risk': {}}
    for (regime, policy), cell in cells.items():
        values = [cell[c] for c in order]
        out['means'][f'{regime}|{policy}'] = {m: _mean([v[m] for v in values]) for m in METRICS}
        static = [cells[regime, 'static'][c]['mean_deficit'] for c in order]
        harm[regime, policy] = [max(v['mean_deficit'] - s, 0.0) for v, s in zip(values, static)]
        out['risk'][f'{regime}|{policy}'] = cvar90(harm[regime, policy])
    draws = bootstrap_draws([f for f, _ in order], GROUPS, BOOT['replicates'], BOOT['seed'])
    noisy = 'delayed_noisy'
    v3, cand = harm[noisy, 'v3_full'], harm[noisy, candidate]
    risk = [cvar90([v3[i] for i in d]) - cvar90([cand[i] for i in d]) for d in draws]
    diff = [cells[noisy, 'v4_hybrid'][c]['total_dropped'] - cells[noisy, candidate][c]['total_dropped'] for c in order]
    drops = [_mean([diff[i] for i in d]) for d in draws]
    out['primary_point'] = {'risk_vs_v3_full': out['risk'][f'{noisy}|v3_full'] - out['risk'][f'{noisy}|{candidate}'],
                            'total_dropped_vs_v4_hybrid': _mean(diff)}
    out['primary_ci95'] = {'risk_vs_v3_full': [percentile(risk, .025), percentile(risk, .975)],
                           'total_dropped_vs_v4_hybrid': [percentile(drops, .025), percentile(drops, .975)]}
    out['noninferiority_ci95'] = {}
    allowance = 1.0 + DECLARATION['mean_noninferiority']['margin']
    for regime in REGIMES:
        for baseline in DECLARATION['mean_noninferiority']['baselines']:
            slack = [allowance * cells[regime, baseline][c]['mean_deficit'] - cells[regime, candidate][c]['mean_deficit']
                     for c in order]
            means = [_mean([slack[i] for i in d]) for d in draws]
            out['noninferiority_ci95'][f'{regime}|{baseline}'] = [percentile(means, .025), percentile(means, .975)]
    family = DECLARATION['underload']['family']
    out['underload_violations'] = sorted(
        (regime, seed) for (regime, policy), cell in cells.items() if policy == candidate
        for (group, seed), v in cell.items() if group == family and (v['total_dropped'] > TOL or v['total_moves'] != 0))
    out['underload_episodes'] = sum(1 for (regime, policy), cell in cells.items() if policy == candidate
                                    for group, _ in cell if group == family)
    out['H1'] = {}
    for regime in UNLIMITED:
        for metric in ('end_deficit', 'tail_auc_deficit'):
            gap = [cells[regime, REFERENCE][c][metric] - cells[regime, candidate][c][metric] for c in order]
            boot = [_mean([gap[i] for i in d]) for d in draws]
            out['H1'][f'{regime}|{metric}'] = {'point': _mean(gap), 'ci95': [percentile(boot, .025), percentile(boot, .975)]}
    return out


def independent_ood(rows, candidate):
    cells = {}
    for row in rows:
        cells.setdefault((row['group'], row['policy']), {})[int(row['scenario_seed'])] = \
            (float(row['mean_deficit']), float(row['total_dropped']))
    families = [f for f in OOD_FAMILIES if any(f == g for g, _ in cells)]
    policies = sorted({p for _, p in cells})
    out = {'families': {f: {} for f in families}, 'pooled': {}}
    harm_lists, labels = {}, []
    for policy in policies:
        harm, drops = [], []
        for family in families:
            mine, static = cells[family, policy], cells[family, 'static']
            seeds = sorted(mine)
            family_harm = [max(mine[s][0] - static[s][0], 0.0) for s in seeds]
            family_drops = [mine[s][1] for s in seeds]
            out['families'][family][policy] = {'risk': cvar90(family_harm), 'mean_total_dropped': _mean(family_drops)}
            harm += family_harm
            drops += family_drops
            if policy == policies[0]:
                labels += [family] * len(seeds)
        harm_lists[policy] = harm
        out['pooled'][policy] = {'risk': cvar90(harm), 'mean_total_dropped': _mean(drops)}
    draws = bootstrap_draws(labels, OOD_FAMILIES, BOOT['replicates'], BOOT['seed'])
    ref, cand = harm_lists[REFERENCE], harm_lists[candidate]
    boot = [cvar90([ref[i] for i in d]) - cvar90([cand[i] for i in d]) for d in draws]
    out['H2'] = {'point': cvar90(ref) - cvar90(cand), 'ci95': [percentile(boot, .025), percentile(boot, .975)]}
    return out


def _close(a, b):
    return abs(a - b) <= max(1e-6, 1e-9 * max(abs(a), abs(b)))


def _close_pair(a, b):
    return all(_close(x, y) for x, y in zip(a, b))


def compare_with_decision(stats, decision, candidate):
    """Every validation number the evaluator used, recomputed independently."""
    mismatches = []
    original = decision['original']
    for check in original['loss_endpoint_point_nonworsening']['checks']:
        for policy, key in ((candidate, 'candidate_mean'), (check['baseline'], 'baseline_mean')):
            if not _close(stats['means'][f"{check['regime']}|{policy}"][check['metric']], check[key]):
                mismatches.append(('loss', check['regime'], policy, check['metric']))
    for check in original['risk_point_nonworsening']['checks']:
        for policy, key in ((candidate, 'candidate_risk'), (check['baseline'], 'baseline_risk')):
            if not _close(stats['risk'][f"{check['regime']}|{policy}"], check[key]):
                mismatches.append(('risk', check['regime'], policy))
    for name, gate in original['primary'].items():
        if not _close_pair(stats['primary_ci95'][name], gate['ci95']) or not _close(stats['primary_point'][name], gate['point']):
            mismatches.append(('primary', name))
    for check in original['mean_noninferiority']['checks']:
        if not _close_pair(stats['noninferiority_ci95'][f"{check['regime']}|{check['baseline']}"], check['ci95']):
            mismatches.append(('noninferiority', check['regime'], check['baseline']))
        for policy, key in ((candidate, 'candidate_mean'), (check['baseline'], 'baseline_mean')):
            if not _close(stats['means'][f"{check['regime']}|{policy}"]['mean_deficit'], check[key]):
                mismatches.append(('noninferiority mean', check['regime'], policy))
    underload = original['underload']
    if (stats['underload_episodes'] != underload['episodes_checked'] or
            stats['underload_violations'] != sorted((v['regime'], v['seed']) for v in underload['violations'])):
        mismatches.append(('underload',))
    for check in decision['hypotheses']['H1_drain']['checks']:
        mine = stats['H1'][f"{check['regime']}|{check['metric']}"]
        if not _close(mine['point'], check['point']) or not _close_pair(mine['ci95'], check['ci95']):
            mismatches.append(('H1', check['regime'], check['metric']))
    return mismatches


def compare_ood(stats, decision, candidate):
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
        for policy, key in ((candidate, 'candidate'), (check['baseline'], 'baseline_value')):
            if not _close(stats['pooled'][policy][check['metric']], check[key]):
                mismatches.append(('ood check', check['metric'], policy))
    h2 = decision['hypotheses']['H2_restraint']
    if not _close(stats['H2']['point'], h2['point']) or not _close_pair(stats['H2']['ci95'], h2['ci95']):
        mismatches.append(('H2',))
    return mismatches


def _environment(split, family, regime):
    return REGIME_CONFIGS[regime] if split == 'validation' else v5_ood.family_config(family)


def controller_replay(policy, config, records, candidate):
    """Re-decide every minute from the recorded observation payloads alone."""
    if policy == candidate:
        controller = V6Policy(config, policy, v6_grid.selected_config())
    else:
        controller = baseline_controller(policy, config)
    for rec in records:
        if controller is None:
            raw = np.zeros(3)
        else:
            d = rec['decision_input']
            raw, _ = controller.decide(d['delivery_t'], np.array(d['queues']), [np.array(h) for h in d['history']],
                                       np.array(d['committed_staff']), d['arrival'])
        if not np.allclose(raw, rec['raw_proposal_delta'], rtol=0, atol=1e-12):
            return False
        if controller is not None:
            f = rec['feedback_input']
            controller.observe(rec['t'], np.array(f['effective_staff']), np.array(f['flow']), np.array(f['end_queue']))
    return True


def replay_traces(run, candidate, allow_reserved=True):
    summary = {'traces': 0, 'exact': 0, 'controller_replay': 0, 'invariant_problems': []}
    for path in sorted((Path(run) / 'traces').glob('*.jsonl.gz')):
        for line in gzip.decompress(path.read_bytes()).decode().splitlines():
            item = json.loads(line)
            split, seed, family, regime, policy = (item[k] for k in ('split', 'seed', 'family', 'regime', 'policy'))
            config = _environment(split, family, regime)
            _, trace, _ = v6_validation._episode(policy, (split, seed, family, regime, allow_reserved), True)
            summary['traces'] += 1
            summary['exact'] += canonical(trace) == canonical(item['records'])
            summary['controller_replay'] += controller_replay(policy, config, item['records'], candidate)
            summary['invariant_problems'] += [(split, seed, family, regime, policy, p)
                                              for p in invariants(item['records'], config)]
    return summary


def scenario_identity(run, allow_reserved=True):
    mismatched = []
    for path in sorted((Path(run) / 'manifests').glob('*.json')):
        split, seed = path.stem.split('-')
        stored = json.loads(path.read_text())
        for family, manifest in stored.items():
            if split == 'validation':
                fresh = v6_validation.validation_case(int(seed), family, allow_reserved).manifest()
            else:
                fresh = v6_validation.ood_case(family, int(seed), allow_reserved).manifest()
            if json.loads(json.dumps(fresh, sort_keys=True, default=float)) != manifest:
                mismatched.append((split, seed, family))
    return mismatched


def tamper_detected(rows, ood_rows, decision, candidate):
    """Copies with one candidate value changed must disagree with the evaluator, for both sets."""
    tampered = copy.deepcopy(rows)
    target = next(r for r in tampered if r['policy'] == candidate and r['regime'] == 'delayed_noisy')
    target['total_dropped'] = str(float(target['total_dropped']) + 1000.0)
    tampered_ood = copy.deepcopy(ood_rows)
    target = next(r for r in tampered_ood if r['policy'] == candidate)
    target['total_dropped'] = str(float(target['total_dropped']) + 1000.0)
    return (bool(compare_with_decision(independent_statistics(tampered, candidate), decision, candidate)) and
            bool(compare_ood(independent_ood(tampered_ood, candidate), decision, candidate)))


def review(run, decision, candidate, *, allow_reserved=True):
    validation, ood = load_rows(run, 'validation'), load_rows(run, 'ood')
    mismatches = (compare_with_decision(independent_statistics(validation, candidate), decision, candidate) +
                  compare_ood(independent_ood(ood, candidate), decision, candidate))
    replay = replay_traces(run, candidate, allow_reserved)
    identity = scenario_identity(run, allow_reserved)
    flags = {
        'scenario_identity': not identity,
        'accounting': max(accounting(validation), accounting(ood)) <= TOL,
        'observation_boundary': replay['traces'] > 0 and replay['controller_replay'] == replay['traces']
                                and not any(p[-1][0] == 'observation' for p in replay['invariant_problems']),
        'independent_replay': replay['traces'] > 0 and replay['exact'] == replay['traces'] and not replay['invariant_problems'],
        'statistics_match': not mismatches,
        'tamper_test': tamper_detected(validation, ood, decision, candidate),
    }
    return {'flags': flags, 'statistic_mismatches': mismatches, 'replay': replay,
            'scenario_mismatches': identity, 'rows': {'validation': len(validation), 'ood': len(ood)}}
