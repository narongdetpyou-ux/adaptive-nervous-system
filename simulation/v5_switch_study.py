"""Same-state single-decision study of V5 Full at the middle-stage switch (development only).

Runs the arms fixed in v5_switch_study_plan.json. Each arm replaces exactly one
V5 Full decision and keeps every other decision, so its trace must equal the
reference V5 Full trace at every minute before the replaced one.
"""
import argparse
import csv
import json
import tempfile
from pathlib import Path

import numpy as np

from diagnose_v4_for_v5 import KNOWN_SEEDS, known_cases
from v4_environment import REGIME_CONFIGS
from v4_metrics import _row_metric_values
from v5_development import development_cases
from v5_intervention import FREE_BRANCHES, InterventionPolicy, NotApplicable
from v5_simulation import simulate_case

ROOT = Path(__file__).resolve().parent
PLAN = json.loads((ROOT / 'v5_switch_study_plan.json').read_text())
DECLARATION = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())
TOL = PLAN['tolerance']
REGIME = PLAN['regime']
FAMILY = PLAN['family']
START = PLAN['window']['start_inclusive']
STOP = PLAN['window']['stop_exclusive']
ARMS = tuple(arm['id'] for arm in PLAN['arms'])
KINDS = {arm['id']: arm['kind'] for arm in PLAN['arms']}
METRICS = ('total_dropped', 'end_deficit', 'tail_auc_deficit', 'mean_deficit')
# Ranges that this diagnosis must never generate or evaluate.
CLOSED_RANGES = tuple(tuple(DECLARATION['data'][key]) for key in
                      ('development_selection_seed_range', 'validation_seed_range', 'ood_validation_seed_range'))


def check_seeds(seeds):
    for seed in seeds:
        if any(low <= seed <= high for low, high in CLOSED_RANGES):
            raise ValueError(f'Seed {seed} belongs to a range this study must not open')
    return tuple(seeds)


def study_cases():
    development = check_seeds(PLAN['cases']['original_development_seeds'])
    known = check_seeds(PLAN['cases']['known_v4_switching_seeds'])
    if tuple(known) != tuple(KNOWN_SEEDS):
        raise ValueError('Declared known cases differ from the published switching cases')
    loaded = development_cases()
    cases = [('development', loaded[seed, FAMILY]) for seed in development]
    cases += [('known_v4', case) for case in known_cases()]
    if any(case.group != FAMILY for _, case in cases) or [c.seed for _, c in cases] != [*development, *known]:
        raise ValueError('Study population differs from the declaration')
    return cases


def canonical(record):
    # Used only to compare traces; NaN estimates serialize identically.
    return json.dumps(record, sort_keys=True,
                      default=lambda x: x.tolist() if hasattr(x, 'tolist') else float(x))


def window(trace):
    part = trace[START:STOP]
    if [r['t'] for r in part] != list(range(START, STOP)):
        raise ValueError('Incomplete switch window')
    return part


def middle_drop(trace):
    return float(sum(r['dropped_by_stage'][1] for r in window(trace)))


def first_middle(trace):
    return next((r['t'] for r in window(trace) if r['applied_delta'][1] > TOL), None)


def key_minutes(v5_trace, balance_trace):
    """m2, m1 and b exactly as defined in the plan."""
    m2 = first_middle(v5_trace)
    m1 = next((r['t'] for r in window(v5_trace)
               if (m2 is None or r['t'] < m2)
               and any(abs(x) > TOL for x in r['applied_delta']) and r['applied_delta'][1] <= TOL), None)
    return {'m1': m1, 'm2': m2, 'b': first_middle(balance_trace)}


def same_pattern(moves, action):
    """Same-pattern alternatives with the largest and smallest middle-stage gain, lowest catalog index on ties."""
    pattern = np.sign(action)
    gains = [(float(move[1]), i) for i, move in enumerate(moves) if np.array_equal(np.sign(move), pattern)]
    larger = sorted((-gain, i) for gain, i in gains if gain > action[1] + TOL)
    smaller = sorted((gain, i) for gain, i in gains if gain < action[1] - TOL)
    return {'middle_max@m2': larger[0][1] if larger else None,
            'middle_min@m2': smaller[0][1] if smaller else None}


def declared_arms(minutes, v5_trace, moves):
    """Return {arm: (minute, catalog index or None for hold) or reason it does not apply}."""
    m1, m2, b = minutes['m1'], minutes['m2'], minutes['b']
    own = None if m2 is None else v5_trace[m2]['decision_evidence']['selected_action_id']
    arms = {}
    arms['hold@m1'] = (m1, None) if m1 is not None else 'no m1'
    arms['own_middle@m1'] = (m1, own) if m1 is not None and own is not None else 'no m1 or m2'
    if b is None or own is None:
        arms['own_middle@b'] = 'no b or m2'
    elif not b < m2:
        arms['own_middle@b'] = 'b is not before m2'
    elif b == m1:
        arms['own_middle@b'] = 'b equals m1'
    elif v5_trace[b]['branch'] not in FREE_BRANCHES:
        arms['own_middle@b'] = f"V5 not free at b ({v5_trace[b]['branch']})"
    else:
        arms['own_middle@b'] = (b, own)
    alternatives = same_pattern(moves, moves[own]) if own is not None else {}
    for arm in ('middle_max@m2', 'middle_min@m2'):
        if own is None:
            arms[arm] = 'no m2'
        elif alternatives[arm] is None:
            arms[arm] = 'no same-pattern alternative'
        else:
            arms[arm] = (m2, alternatives[arm])
    if set(arms) != set(ARMS):
        raise ValueError('Arms differ from the declaration')
    return arms


def checked(row):
    _row_metric_values(row)
    if row['max_mass_error'] > TOL or row['max_budget_error'] > TOL:
        raise ValueError('Physical accounting exceeded tolerance')
    return row


def run_case(source, case):
    environment = REGIME_CONFIGS[REGIME]
    v5_row, v5_trace = simulate_case('v5_full', case, regime=REGIME, detailed=True)
    bal_row, bal_trace = simulate_case('capacity_balance', case, regime=REGIME, detailed=True)
    plain = InterventionPolicy(environment, 'v5_full')
    plain_row, plain_trace = simulate_case('v5_full', case, regime=REGIME, detailed=True, controller=plain)
    if plain_row != v5_row or [canonical(r) for r in plain_trace] != [canonical(r) for r in v5_trace]:
        raise ValueError(f'Intervention controller without a replacement differs from V5 Full: {case.seed}')
    minutes = key_minutes(v5_trace, bal_trace)
    reference = [canonical(r) for r in v5_trace]
    result = {'seed': case.seed, 'source': source, **minutes,
              'v5_branch_at_b': None if minutes['b'] is None else v5_trace[minutes['b']]['branch'],
              'references': {}, 'arms': {}}
    for name, (row, trace) in (('v5_full', (v5_row, v5_trace)), ('capacity_balance', (bal_row, bal_trace))):
        checked(row)
        result['references'][name] = {'W': middle_drop(trace), 'first_middle': first_middle(trace),
                                       **{k: row[k] for k in METRICS}}
    for arm, spec in declared_arms(minutes, v5_trace, plain.moves).items():
        if isinstance(spec, str):
            result['arms'][arm] = {'applicable': False, 'reason': spec}
            continue
        minute, action_id = spec
        action = None if action_id is None else plain.moves[action_id]
        controller = InterventionPolicy(environment, 'v5_full', minute=minute, action=action)
        try:
            row, trace = simulate_case('v5_full', case, regime=REGIME, detailed=True, controller=controller)
        except NotApplicable as error:
            result['arms'][arm] = {'applicable': False, 'reason': str(error), 'minute': minute, 'action_id': action_id}
            continue
        checked(row)
        if controller.replacement is None or controller.replacement['t'] != minute:
            raise ValueError(f'Replacement did not happen: {case.seed} {arm}')
        if [canonical(r) for r in trace[:minute]] != reference[:minute]:
            raise ValueError(f'Trace diverged before the replaced minute: {case.seed} {arm}')
        if not trace[minute]['branch'].startswith('intervention_'):
            raise ValueError(f'Replaced minute not marked: {case.seed} {arm}')
        result['arms'][arm] = {'applicable': True, 'minute': minute, 'action_id': action_id,
                               'action': controller.replacement['action'],
                               'replaced_action_id': controller.replacement['replaced_action_id'],
                               'W': middle_drop(trace), 'first_middle': first_middle(trace),
                               **{k: row[k] for k in METRICS}}
    return result, case.manifest()


def decide(results):
    diagnosed = [r for r in results if r['references']['v5_full']['W'] - r['references']['capacity_balance']['W'] > TOL]
    support = {}
    for arm in ARMS:
        usable = [r for r in diagnosed if r['arms'][arm]['applicable']]
        gaps = [(r['references']['v5_full']['W'] - r['arms'][arm]['W']) /
                (r['references']['v5_full']['W'] - r['references']['capacity_balance']['W']) for r in usable]
        needed = -(-2 * len(usable) // 3)
        hits = sum(g >= 0.5 for g in gaps)
        arm_total = float(np.mean([r['arms'][arm]['total_dropped'] for r in usable])) if usable else None
        v5_total = float(np.mean([r['references']['v5_full']['total_dropped'] for r in usable])) if usable else None
        support[arm] = {'kind': KINDS[arm], 'applicable_cases': [r['seed'] for r in usable],
                        'gap_closed': dict(zip([r['seed'] for r in usable], gaps)),
                        'cases_closing_half': hits, 'required': needed,
                        'mean_total_dropped': arm_total, 'v5_mean_total_dropped': v5_total,
                        'supports': len(usable) >= 2 and hits >= needed and arm_total < v5_total}
    timing = any(s['supports'] for s in support.values() if s['kind'] == 'timing')
    size = any(s['supports'] for s in support.values() if s['kind'] == 'size')
    conclusion = 'both' if timing and size else 'timing' if timing else 'size' if size else 'neither'
    controls = {}
    for arm in ARMS:
        usable = [r for r in results if r not in diagnosed and r['arms'][arm]['applicable']]
        controls[arm] = {'cases': [r['seed'] for r in usable],
                         'mean_total_dropped_change': float(np.mean([r['arms'][arm]['total_dropped'] -
                                                                     r['references']['v5_full']['total_dropped']
                                                                     for r in usable])) if usable else None}
    mapping = PLAN['decision']['next_step']
    next_step = mapping['timing_or_both'] if timing else mapping['size_only'] if size else mapping['neither']
    return {'diagnosed_cases': [r['seed'] for r in diagnosed], 'support': support, 'conclusion': conclusion,
            'next_step': next_step, 'controls': controls, 'promotion': 'NOT_EVALUATED'}


def episode_rows(results):
    rows = []
    for r in results:
        base = {'seed': r['seed'], 'source': r['source'], 'm1': r['m1'], 'm2': r['m2'], 'b': r['b']}
        for name, ref in r['references'].items():
            rows.append({**base, 'arm': name, 'applicable': True, 'minute': None, 'action_id': None,
                         'reason': None, 'W': ref['W'], 'first_middle': ref['first_middle'],
                         **{k: ref[k] for k in METRICS}})
        for arm in ARMS:
            a = r['arms'][arm]
            rows.append({**base, 'arm': arm, 'applicable': a['applicable'], 'minute': a.get('minute'),
                         'action_id': a.get('action_id'), 'reason': a.get('reason'), 'W': a.get('W'),
                         'first_middle': a.get('first_middle'), **{k: a.get(k) for k in METRICS}})
    return rows


def run(output):
    results, manifests = [], []
    for source, case in study_cases():
        result, manifest = run_case(source, case)
        results.append(result)
        manifests.append(manifest)
    summary = {'plan': PLAN['version'], 'regime': REGIME, 'family': FAMILY, 'cases': results,
               'decision': decide(results), 'case_manifests': manifests}
    rows = episode_rows(results)
    output.mkdir(parents=True, exist_ok=True)
    # Publish only after every case and check has succeeded.
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        tmp = Path(tmp)
        with (tmp / 'episodes.csv').open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows(rows)
        (tmp / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + '\n')
        for name in ('episodes.csv', 'summary.json'):
            (tmp / name).replace(output / name)
    decision = summary['decision']
    print(f"{len(results)} cases, {len(rows)} rows; diagnosed {decision['diagnosed_cases']}; conclusion: {decision['conclusion']}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'local_runs/v5_switch_study')
    run(parser.parse_args().output)
