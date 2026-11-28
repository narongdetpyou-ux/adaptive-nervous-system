"""Frozen V6 validation and OOD validation runner (v6_validation_plan.json).

`python v6_validation.py run <name>` refuses to start unless v6_freeze.py and
v5_freeze.py verify and tracked files are clean; only then may seeds
7000..7063 and 8100..8163 be generated. The candidate and its parameters come
from the committed selection plan and result. Tests use open seeds through
run_seeds(). Every file is written atomically and deterministically, so a
second run can be compared byte for byte.
"""
import argparse
import gzip
import json
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import v5_grid
import v5_ood
import v6_grid
import v6_simulation
from v4_metrics import _row_metric_values
from v4_scenarios import GROUPS, REGIMES
from v5_validation import _csv_bytes, _write_atomic, canonical

ROOT = Path(__file__).resolve().parent
DECLARATION = v6_grid.DECLARATION
TOL = DECLARATION['tolerance']
VALIDATION_SEEDS = v6_grid._inclusive(DECLARATION['data']['validation_seed_range'])
OOD_SEEDS = v6_grid._inclusive(DECLARATION['data']['ood_validation_seed_range'])
TRACE_FAMILIES = ('generated_switching_fault', 'generated_underload', 'generated_sustained_overload',
                  'generated_pulsed_short_fault')
TRACE_VALIDATION_SEEDS = VALIDATION_SEEDS[:3]
TRACE_OOD_SEEDS = OOD_SEEDS[:2]
OUTPUT = ROOT / 'local_runs/v6_validation'
SELECTION_SUMMARY = ROOT / 'v6_results/selection_grid/summary.json'


def candidate():
    """The variant the declared selection rule chose (default v6_full before the selection result exists)."""
    if SELECTION_SUMMARY.is_file():
        return json.loads(SELECTION_SUMMARY.read_text())['chosen_variant']
    return 'v6_full'


def policies():
    return v6_simulation.BASELINES + (candidate(),)


def validation_case(seed, family, allow_reserved):
    builder = v5_grid._build_cases if allow_reserved else (lambda seeds: v5_grid._build_cases(v6_grid.check_open(seeds)))
    cases = [c for c in builder((seed,)) if c.group == family]
    if len(cases) != 1:
        raise ValueError(f'Missing validation case {family} {seed}')
    return cases[0]


def ood_case(family, seed, allow_reserved):
    if not allow_reserved:
        v6_grid.check_open((seed,))
    return v5_ood.build_case(family, seed, allow_reserved=True)


def _episode(policy, task, detailed):
    split, seed, family, regime, allow = task
    config = v6_grid.selected_config() if policy in v6_simulation.VARIANTS else None
    if split == 'validation':
        case = validation_case(seed, family, allow)
        row, trace = v6_simulation.simulate(policy, case, regime=regime, detailed=detailed, config=config)
    else:
        case = ood_case(family, seed, allow)
        row, trace = v6_simulation.run_ood(policy, case, detailed=detailed, config=config)
    _row_metric_values(row)
    if row['max_mass_error'] > TOL or row['max_budget_error'] > TOL:
        raise ValueError(f'Accounting error {task} {policy}')
    return row, trace, case.manifest()


def traced(split, seed, family, trace_validation=TRACE_VALIDATION_SEEDS, trace_ood=TRACE_OOD_SEEDS):
    if split == 'validation':
        return seed in trace_validation and family in TRACE_FAMILIES
    return seed in trace_ood


def run_task(task_and_flags):
    task, detailed, policy_list = task_and_flags
    rows, traces, manifest = [], [], None
    for policy in policy_list:
        row, trace, manifest = _episode(policy, task, detailed)
        rows.append(row)
        if detailed:
            split, seed, family, regime, _ = task
            traces.append({'split': split, 'seed': seed, 'family': family, 'regime': regime,
                           'policy': policy, 'records': trace})
    return task, rows, traces, manifest


def seed_tasks(split, seed, allow, families=None, regimes=None):
    if split == 'validation':
        return [(split, seed, f, r, allow) for r in (regimes or REGIMES) for f in (families or GROUPS)]
    return [(split, seed, f, f'ood_{f}', allow) for f in (families or v5_ood.FAMILIES)]


def run_seeds(split, seeds, output, workers, *, allow_reserved=False, families=None, regimes=None,
              trace_validation=TRACE_VALIDATION_SEEDS, trace_ood=TRACE_OOD_SEEDS, policy_list=None):
    output = Path(output)
    policy_list = tuple(policy_list or policies())
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for seed in seeds:
            rows_path = output / split / f'episodes-{seed}.csv'
            if rows_path.exists():
                print(f'{split} {seed} already complete', flush=True)
                continue
            started = time.monotonic()
            tasks = seed_tasks(split, seed, allow_reserved, families, regimes)
            flags = [(t, traced(split, seed, t[2], trace_validation, trace_ood), policy_list) for t in tasks]
            results = {r[0]: r[1:] for r in pool.map(run_task, flags)}
            rows = [row for t in tasks for row in results[t][0]]
            traces = [tr for t in tasks for tr in results[t][1]]
            manifests = {t[2]: results[t][2] for t in tasks}
            if len(rows) != len(tasks) * len(policy_list):
                raise ValueError(f'Incomplete {split} seed {seed}')
            _write_atomic(output / 'manifests' / f'{split}-{seed}.json',
                          (json.dumps(manifests, sort_keys=True, indent=1, default=float) + '\n').encode())
            if traces:
                lines = ''.join(canonical(tr) + '\n' for tr in traces).encode()
                _write_atomic(output / 'traces' / f'{split}-{seed}.jsonl.gz', gzip.compress(lines, mtime=0))
            _write_atomic(rows_path, _csv_bytes(rows))
            print(f'{split} {seed} done {len(rows)} rows {len(traces)} traces in '
                  f'{time.monotonic() - started:.0f}s {time.strftime("%H:%M:%S", time.gmtime())}', flush=True)


def require_frozen():
    import v5_freeze
    import v6_freeze
    v5_freeze.verify()
    v6_freeze.verify()
    dirty = subprocess.run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT,
                           capture_output=True, text=True, check=True).stdout.strip()
    if dirty:
        raise RuntimeError('Tracked files differ from the frozen commit')
    return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def official(name, workers):
    import numpy
    commit = require_frozen()
    output = OUTPUT / name
    info = output / 'run.json'
    if info.exists():
        if json.loads(info.read_text())['commit'] != commit:
            raise RuntimeError(f'{name} started at another commit; a run never resumes across commits')
    else:
        _write_atomic(info, (json.dumps({'commit': commit, 'python': sys.version.split()[0],
                                         'numpy': numpy.__version__, 'candidate': candidate(),
                                         'started_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())},
                                        indent=1) + '\n').encode())
    run_seeds('validation', VALIDATION_SEEDS, output, workers, allow_reserved=True)
    run_seeds('ood', OOD_SEEDS, output, workers, allow_reserved=True)
    print(f'{name} complete at commit {commit}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run',))
    parser.add_argument('name')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    official(args.name, args.workers)
