"""Run one original-development seed across every family, regime and ablation."""
import argparse
import csv
import json
import tempfile
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from pathlib import Path

from v4_metrics import _row_metric_values
from v4_scenarios import GROUPS, REGIMES, SPLIT_SEEDS, generate_scenarios
from v5_simulation import simulate_case

ROOT = Path(__file__).resolve().parent
BASELINES = ('static', 'capacity_v2', 'capacity_balance', 'v3_full', 'v4_hybrid')


@cache
def study_arms():
    declaration = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())
    policies = dict.fromkeys((*BASELINES, *declaration['development']['variants']))
    # Native full already uses catalog; native no_balance already uses capped.
    return tuple((p, 'variant') for p in policies) + (('v5_full', 'capped'), ('v5_no_balance', 'catalog'))


@cache
def development_cases():
    frozen = json.loads((ROOT / 'v4_results/development/scenario_manifest.json').read_text())
    references = {(r['scenario_seed'], r['group']): r for r in frozen['cases']}
    cases = {(s.seed, s.group): s for s in generate_scenarios('development')}
    if cases.keys() != references.keys():
        raise ValueError('Original development population differs from the frozen manifest')
    for key, case in cases.items():
        manifest = case.manifest()
        for field in ('arrivals_sha256', 'productivity_sha256', 'sensor_noise_sha256'):
            if manifest[field] != references[key][field]:
                raise ValueError(f'Frozen development input mismatch for {key}, {field}')
    return cases


def run_pair(task):
    seed, group, regime = task
    case = development_cases()[seed, group]
    rows = []
    for policy, space in study_arms():
        row, _ = simulate_case(policy, case, regime=regime, action_space=space)
        _row_metric_values(row)
        rows.append({**row, 'action_space': space})
    return rows


def run(seed, workers, output):
    if seed not in SPLIT_SEEDS['development']:
        raise ValueError('This runner accepts only original development seeds 2000..2007')
    if workers < 1:
        raise ValueError('At least one worker is required')
    tasks = [(seed, group, regime) for regime in REGIMES for group in GROUPS]
    output.mkdir(parents=True, exist_ok=True)
    # Concurrent repeats use unique temporary files. Publish only a complete seed.
    with tempfile.TemporaryDirectory(dir=output) as tmp:
        path = Path(tmp) / f'episodes-{seed}.csv'
        count = 0
        with ProcessPoolExecutor(max_workers=workers) as pool, path.open('w', newline='') as handle:
            writer = None
            for rows in pool.map(run_pair, tasks):
                if writer is None:
                    writer = csv.DictWriter(handle, fieldnames=rows[0], lineterminator='\n')
                    writer.writeheader()
                writer.writerows(rows)
                count += len(rows)
        if count != len(tasks) * len(study_arms()):
            raise ValueError('Incomplete development slice')
        path.replace(output / path.name)
    print(f'Seed {seed}: {count} episodes across {len(tasks)} family/regime pairs')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, choices=SPLIT_SEEDS['development'], required=True)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--output', type=Path, default=ROOT / 'local_runs/v5_development')
    args = parser.parse_args()
    run(args.seed, args.workers, args.output)
