"""Declared V6 development, tuning and selection grids (v6_acceptance_predeclared.json); never validation.

run       python v6_grid.py run --split development|tune|selection [--set NAME] [--workers 4]
          One CSV per seed (original families and OOD families separately) under
          local_runs/v6_grid/<split>/<set>/; a seed whose file exists is skipped,
          so an interrupted run resumes.
tune      python v6_grid.py tune
          Point summary of every tuning set on the tuning seeds, ranked by the
          declared selection rule, with the frozen baselines read from the V5
          validation rows (identical code, identical seeds). Development only.
summarize python v6_grid.py summarize --split development|selection
          Point summaries and the declared ranking of the variants, the
          baseline-reproduction check against the V5 rows, and H1/H2 directions.

Validation seeds 7000..7063 and OOD validation seeds 8100..8163 are refused
here. Selection seeds are refused until v6_selection_plan.json is committed.
"""
import argparse
import csv
import dataclasses
import hashlib
import io
import json
import os
import subprocess
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import v5_grid
import v5_ood
import v6_simulation
from v4_metrics import _row_metric_values
from v4_scenarios import GROUPS, REGIMES
from v6_evaluate import Gates, read_rows
from v6_policy import V6Config

ROOT = Path(__file__).resolve().parent
DECLARATION = json.loads((ROOT / 'v6_acceptance_predeclared.json').read_text())
DATA = DECLARATION['data']
TOL = DECLARATION['tolerance']
OUTPUT = ROOT / 'local_runs/v6_grid'
V5_ROWS = ROOT / 'local_runs/v5_validation/run_a'
SELECTION_PLAN = ROOT / 'v6_selection_plan.json'


def _inclusive(pair):
    return tuple(range(pair[0], pair[1] + 1))


SPLITS = {
    'development': (tuple(s for pair in DATA['development_seed_ranges'] for s in _inclusive(pair)),
                    _inclusive(DATA['development_ood_seed_range'])),
    'tune': (tuple(range(6000, 6032)), tuple(range(8000, 8032))),
    'selection': (_inclusive(DATA['selection_seed_range']), _inclusive(DATA['selection_ood_seed_range'])),
}
CLOSED = (DATA['validation_seed_range'], DATA['ood_validation_seed_range'])

# Development-only parameter sets for the two mechanisms; v6_selection_plan.json fixes one.
TUNING = {
    'drain_w1': ('v6_drain', {'drain_weight': 1.0}),
    'drain_w2': ('v6_drain', {'drain_weight': 2.0}),
    'restraint_mild': ('v6_restraint', {'restraint_hurdle_factor': 3.0, 'restraint_max_transfer': 10.0,
                                        'restraint_loss_fraction': 0.25, 'restraint_memory': 16}),
    'restraint_strong': ('v6_restraint', {'restraint_hurdle_factor': 10.0, 'restraint_max_transfer': 1e9,
                                          'restraint_loss_fraction': 0.5, 'restraint_memory': 16}),
    'restraint_hold16': ('v6_restraint', {'restraint_hurdle_factor': 1e9, 'restraint_max_transfer': 1e9,
                                          'restraint_loss_fraction': 1.0, 'restraint_memory': 16}),
    'restraint_hold8': ('v6_restraint', {'restraint_hurdle_factor': 1e9, 'restraint_max_transfer': 1e9,
                                         'restraint_loss_fraction': 1.0, 'restraint_memory': 8}),
    # Amendment 1: return to the even split while restrained, fast-switching signal.
    'neutral8': ('v6_restraint', {'restraint_neutral': 1.0, 'restraint_memory': 8,
                                  'restraint_neutral_tolerance': 10.0, 'restraint_max_transfer': 1e9}),
    'neutral16': ('v6_restraint', {'restraint_neutral': 1.0, 'restraint_memory': 16,
                                   'restraint_neutral_tolerance': 10.0, 'restraint_max_transfer': 1e9}),
    'full_neutral8': ('v6_full', {'drain_weight': 1.0, 'restraint_neutral': 1.0, 'restraint_memory': 8,
                                  'restraint_neutral_tolerance': 10.0, 'restraint_max_transfer': 1e9}),
    'full_neutral16': ('v6_full', {'drain_weight': 1.0, 'restraint_neutral': 1.0, 'restraint_memory': 16,
                                   'restraint_neutral_tolerance': 10.0, 'restraint_max_transfer': 1e9}),
}


def check_open(seeds):
    for seed in seeds:
        if any(low <= seed <= high for low, high in CLOSED):
            raise ValueError(f'Seed {seed} is a V6 held-out validation or OOD validation seed')
    return tuple(seeds)


def selection_open():
    """The selection plan must exist and be committed unchanged before selection arrays are generated."""
    if not SELECTION_PLAN.is_file():
        return False
    tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', SELECTION_PLAN.name], cwd=ROOT,
                             capture_output=True).returncode == 0
    clean = subprocess.run(['git', 'diff', '--quiet', 'HEAD', '--', SELECTION_PLAN.name], cwd=ROOT).returncode == 0
    return tracked and clean


def selected_config():
    """The parameters fixed by the committed selection plan (default V6Config before it exists)."""
    if not SELECTION_PLAN.is_file():
        return V6Config()
    return V6Config(**json.loads(SELECTION_PLAN.read_text())['parameters'])


def arms(split, name):
    if split == 'tune':
        variant, overrides = TUNING[name]
        return ((variant, dataclasses.replace(V6Config(), **overrides)),)
    config = selected_config()
    return tuple((p, None) for p in v6_simulation.BASELINES) + tuple((v, config) for v in v6_simulation.VARIANTS)


def _cases(seed):
    return {case.group: case for case in v5_grid._build_cases(check_open((seed,)))}


def case_task(task):
    seed, family, regime, arm_list = task
    case = _cases(seed)[family]
    rows = []
    for policy, config in arm_list:
        row, _ = v6_simulation.simulate(policy, case, regime=regime, config=config)
        _row_metric_values(row)
        if row['max_mass_error'] > TOL or row['max_budget_error'] > TOL:
            raise ValueError(f'Accounting error {task[:3]} {policy}')
        rows.append(row)
    return (seed, family, regime), rows


def ood_task(task):
    seed, family, arm_list = task
    case = v5_ood.build_case(family, check_open((seed,))[0], allow_reserved=True)
    rows = []
    for policy, config in arm_list:
        row, _ = v6_simulation.run_ood(policy, case, config=config)
        _row_metric_values(row)
        if row['max_mass_error'] > TOL or row['max_budget_error'] > TOL:
            raise ValueError(f'Accounting error {task[:2]} {policy}')
        rows.append(row)
    return (seed, family), rows


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False, suffix='.part') as handle:
        handle.write(buffer.getvalue().encode())
    os.replace(handle.name, path)


def run(split, name, workers, output=OUTPUT):
    if split == 'selection' and not selection_open():
        raise RuntimeError('Selection seeds stay closed until v6_selection_plan.json is committed')
    seeds, ood_seeds = (check_open(s) for s in SPLITS[split])
    arm_list = arms(split, name)
    base = Path(output) / split / name
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for seed in seeds:
            path = base / 'cases' / f'episodes-{seed}.csv'
            if path.exists():
                continue
            started = time.monotonic()
            tasks = [(seed, f, r, arm_list) for r in REGIMES for f in GROUPS]
            results = dict(pool.map(case_task, tasks))
            _write(path, [row for _, f, r, _ in tasks for row in results[seed, f, r]])
            print(f'{split}/{name} cases {seed} in {time.monotonic() - started:.0f}s', flush=True)
        for seed in ood_seeds:
            path = base / 'ood' / f'episodes-{seed}.csv'
            if path.exists():
                continue
            tasks = [(seed, f, arm_list) for f in v5_ood.FAMILIES]
            results = dict(pool.map(ood_task, tasks))
            _write(path, [row for _, f, _ in tasks for row in results[seed, f]])
            print(f'{split}/{name} ood {seed}', flush=True)


def _rows(folder, seeds, policies):
    paths = [Path(folder) / f'episodes-{s}.csv' for s in seeds]
    missing = [p for p in paths if not p.is_file()]
    if missing:
        raise ValueError(f'Missing {len(missing)} files, first {missing[0]}')
    return read_rows(paths, policies)


def tune(output=OUTPUT, v5_rows=V5_ROWS):
    """Rank every tuning set by the declared selection rule against the frozen baselines (development only)."""
    seeds, ood_seeds = SPLITS['tune']
    baselines = v6_simulation.BASELINES
    base_val = _rows(Path(v5_rows) / 'validation', seeds, baselines)
    base_ood = _rows(Path(v5_rows) / 'ood', ood_seeds, baselines)
    results = []
    for name, (variant, overrides) in TUNING.items():
        folder = Path(output) / 'tune' / name
        if not (folder / 'ood' / f'episodes-{ood_seeds[-1]}.csv').is_file():
            continue
        val = _rows(folder / 'cases', seeds, (variant,))
        ood = _rows(folder / 'ood', ood_seeds, (variant,))
        gates = Gates(DECLARATION, variant, baselines + (variant,))
        summary = gates.point_summary(base_val + val, base_ood + ood)
        results.append({'set': name, 'variant': variant, 'overrides': overrides, **summary})
    results.sort(key=lambda r: (r['variant'], r['failed_point_checks'], r['delayed_noisy_mean_total_dropped'],
                                r['delayed_noisy_risk'], r['set']))
    return results


def summarize(split, output=OUTPUT, v5_rows=V5_ROWS):
    seeds, ood_seeds = SPLITS[split]
    folder = Path(output) / split / 'grid'
    policies = v6_simulation.POLICIES
    val = _rows(folder / 'cases', seeds, policies)
    ood = _rows(folder / 'ood', ood_seeds, policies)
    ranking = []
    for variant in v6_simulation.VARIANTS:
        gates = Gates(DECLARATION, variant, policies)
        ranking.append({'variant': variant, **gates.point_summary(val, ood),
                        'hypotheses_vs_v5_full': gates.hypotheses(val, ood)})
    ranking.sort(key=lambda r: (r['failed_point_checks'], r['delayed_noisy_mean_total_dropped'],
                                r['delayed_noisy_risk'], r['variant']))
    return {'split': split, 'seeds': list(seeds), 'ood_seeds': list(ood_seeds), 'ranking': ranking,
            'chosen_variant': ranking[0]['variant'], 'parameters': dataclasses.asdict(selected_config()),
            'baseline_reproduction': baseline_reproduction(folder, seeds, ood_seeds, v5_rows),
            'episode_files_sha256': {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(folder.rglob('episodes-*.csv'))}}


def baseline_reproduction(folder, seeds, ood_seeds, v5_rows=V5_ROWS):
    """Frozen baseline rows must equal the V5 validation rows wherever the seeds overlap."""
    compared = identical = 0
    for kind, v5_kind, seed_list in (('cases', 'validation', seeds), ('ood', 'ood', ood_seeds)):
        for seed in seed_list:
            theirs = Path(v5_rows) / v5_kind / f'episodes-{seed}.csv'
            if not theirs.is_file():
                continue
            mine = read_rows([Path(folder) / kind / f'episodes-{seed}.csv'], v6_simulation.BASELINES)
            reference = read_rows([theirs], v6_simulation.BASELINES)
            key = lambda r: (r['regime'], r['group'], r['policy'])
            mine = {key(r): r for r in mine}
            for row in reference:
                compared += 1
                identical += mine.get(key(row)) == row
    return {'compared_rows': compared, 'identical_rows': identical, 'all_identical': compared > 0 and compared == identical}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'tune', 'summarize'))
    parser.add_argument('--split', choices=tuple(SPLITS), default='development')
    parser.add_argument('--set', default='grid', help='tuning set name for --split tune')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    if args.action == 'run':
        names = list(TUNING) if args.split == 'tune' and args.set == 'all' else [args.set]
        for name in names:
            run(args.split, name, args.workers)
    elif args.action == 'tune':
        result = tune()
        folder = ROOT / 'v6_results/tuning'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'summary.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
        for r in result:
            print(r['set'], r['failed_point_checks'], r['failed_loss_endpoint_checks'], r['failed_risk_checks'],
                  r['failed_ood_checks'], round(r['delayed_noisy_mean_total_dropped']), round(r['delayed_noisy_risk']),
                  {k: round(v) for k, v in r['ood_pooled'].items()})
    else:
        result = summarize(args.split)
        folder = ROOT / 'v6_results' / f'{args.split}_grid'
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'summary.json').write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + '\n')
        print('chosen variant:', result['chosen_variant'], '| baselines identical:', result['baseline_reproduction'])
