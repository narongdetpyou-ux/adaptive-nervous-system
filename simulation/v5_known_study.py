"""Replay V4's five published switching-tail cases with the V5 development arms."""
import argparse
import csv
import json
import math
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

from diagnose_v4_for_v5 import KNOWN_SEEDS, known_cases
from v4_metrics import _row_metric_values
from v5_development import BASELINES, study_arms
from v5_policy import VARIANTS, V5Config
from v5_simulation import simulate_case

ROOT = Path(__file__).resolve().parent
REGIME = 'delayed_noisy'
TRACE_POLICIES = ('static', 'capacity_balance', 'v4_hybrid', 'v5_full')
# Boundaries of the frozen generated_switching_fault schedule, including drain.
BOUNDARIES = (0, 14, 31, 39, 59, 71, 96, 128)


def compare_archive(row, archived, tolerance):
    for key, reference in archived.items():
        if not math.isclose(row[key], reference, abs_tol=tolerance, rel_tol=0):
            raise ValueError(f"V4 replay mismatch: {row['scenario_seed']} {row['policy']} {key}")


def phase_rows(trace, row, tolerance):
    if [record['t'] for record in trace] != list(range(BOUNDARIES[-1])):
        raise ValueError('Incomplete or reordered minute trace')
    phases = []
    for start, stop in zip(BOUNDARIES, BOUNDARIES[1:]):
        window = trace[start:stop]
        stage_drops = np.sum([record['dropped_by_stage'] for record in window], axis=0)
        moves = [record for record in window if any(record['applied_delta'])]
        phases.append({
            'scenario_seed': row['scenario_seed'], 'policy': row['policy'],
            'start_inclusive': start, 'stop_exclusive': stop,
            **{f'dropped_stage_{i}': float(value) for i, value in enumerate(stage_drops)},
            'moves': len(moves),
            'move_minutes_and_deltas': json.dumps([[r['t'], r['applied_delta']] for r in moves], separators=(',', ':')),
        })
    total = sum(phase[f'dropped_stage_{i}'] for phase in phases for i in range(3))
    if abs(total - row['total_dropped']) > tolerance or sum(p['moves'] for p in phases) != row['total_moves']:
        raise ValueError('Phase totals disagree with episode accounting')
    return phases


def write_csv(path, rows):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0], lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def run(output):
    acceptance = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())
    tolerance = acceptance['tolerance']
    archive = json.loads((ROOT / 'v4_results/validation/tail_attribution.json').read_text())
    archived = {r['scenario_seed']: r['paired_metrics'] for r in
                archive['regimes'][REGIME]['five_largest_hybrid_harm_cases']
                if r['group'] == 'generated_switching_fault'}
    if set(archived) != set(KNOWN_SEEDS) or any(set(a) != set(BASELINES) for a in archived.values()):
        raise ValueError('Unexpected population or policies in frozen archive')
    arms = study_arms()
    rows, phases, manifests = [], [], []
    for case in known_cases():
        manifests.append(case.manifest())
        for policy, space in arms:
            row, trace = simulate_case(policy, case, regime=REGIME, action_space=space, detailed=True)
            _row_metric_values(row)
            if row['max_mass_error'] > tolerance or row['max_budget_error'] > tolerance:
                raise ValueError('Physical accounting exceeded tolerance')
            if policy in BASELINES:
                compare_archive(row, archived[case.seed][policy], tolerance)
            if space == 'variant' and policy in TRACE_POLICIES:
                phases.extend(phase_rows(trace, row, tolerance))
            maximum = max(sum(max(x, 0) for x in r['applied_delta']) for r in trace)
            native_cap = policy == 'v4_hybrid' or (policy in VARIANTS and 'balance' not in VARIANTS[policy])
            capped = space == 'capped' or (space == 'variant' and native_cap)
            if capped and maximum > V5Config().maximum_transfer + tolerance:
                raise ValueError('Transfer cap exceeded')
            rows.append(dict(scenario_seed=case.seed, policy=policy, action_space=space,
                             max_applied_transfer=maximum, **{k: v for k, v in row.items()
                             if k not in ('scenario_seed', 'policy')}))
    if Counter((r['policy'], r['action_space']) for r in rows) != {a: len(KNOWN_SEEDS) for a in arms}:
        raise ValueError('Incomplete paired experiment')
    # Only publish tables after the entire fixed cohort and archive checks succeed.
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        tmp = Path(tmp)
        write_csv(tmp / 'episodes.csv', rows)
        write_csv(tmp / 'phases.csv', phases)
        (tmp / 'cases.json').write_text(json.dumps(manifests, sort_keys=True, indent=2, allow_nan=False) + '\n')
        for name in ('episodes.csv', 'phases.csv', 'cases.json'):
            (tmp / name).replace(output / name)
    print(f'{len(rows)} episodes, {len(phases)} phase rows, {len(archived)*len(BASELINES)} matched archived baselines')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'local_runs/v5_known_study')
    run(parser.parse_args().output)
