"""Frozen V5 validation and OOD runner (v5_ood_plan.json).

The official run (`python v5_validation.py run <name>`) refuses to start unless
v5_freeze.py verifies and the working tree is clean; only then may seeds
6000..6063 and 8000..8063 be generated. Tests use open seeds through
run_seeds(). Every output file is written atomically and deterministically so a
second run can be compared byte for byte.
"""
import argparse
import csv
import gzip
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import v5_grid
import v5_ood
from v4_scenarios import GROUPS, REGIMES
from v4_metrics import _row_metric_values
from v5_simulation import simulate_case

ROOT = Path(__file__).resolve().parent
PLAN = json.loads((ROOT / 'v5_ood_plan.json').read_text())
POLICIES = tuple(PLAN['policies'])
TOL = v5_grid.TOL
VALIDATION_SEEDS = v5_ood.VALIDATION_SEEDS
OOD_SEEDS = v5_ood.OOD_SEEDS
TRACE_FAMILIES = ('generated_switching_fault', 'generated_underload', 'generated_sustained_overload')
TRACE_VALIDATION_SEEDS = VALIDATION_SEEDS[:3]
TRACE_OOD_SEEDS = OOD_SEEDS[:2]
OUTPUT = ROOT / 'local_runs/v5_validation'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      default=lambda x: x.tolist() if hasattr(x, 'tolist') else float(x))


def validation_case(seed, family, allow_reserved):
    builder = v5_grid._build_cases if allow_reserved else v5_grid.explicit_cases
    cases = [c for c in builder((seed,)) if c.group == family]
    if len(cases) != 1:
        raise ValueError(f'Missing validation case {family} {seed}')
    return cases[0]


def _episode(policy, task, detailed):
    split, seed, family, regime, allow = task
    if split == 'validation':
        case = validation_case(seed, family, allow)
        row, trace = simulate_case(policy, case, regime=regime, detailed=detailed)
        manifest = case.manifest()
    else:
        case = v5_ood.build_case(family, seed, allow_reserved=allow)
        row, trace = v5_ood.run_episode(policy, case, detailed=detailed)
        manifest = case.manifest()
    _row_metric_values(row)
    if row['max_mass_error'] > TOL or row['max_budget_error'] > TOL:
        raise ValueError(f'Accounting error {task} {policy}')
    return row, trace, manifest


def traced(split, seed, family, trace_validation=TRACE_VALIDATION_SEEDS, trace_ood=TRACE_OOD_SEEDS):
    if split == 'validation':
        return seed in trace_validation and family in TRACE_FAMILIES
    return seed in trace_ood


def run_task(task_and_flags):
    task, detailed = task_and_flags
    rows, traces, manifest = [], [], None
    for policy in POLICIES:
        row, trace, manifest = _episode(policy, task, detailed)
        rows.append(row)
        if detailed:
            split, seed, family, regime, _ = task
            traces.append({'split': split, 'seed': seed, 'family': family, 'regime': regime,
                           'policy': policy, 'records': trace})
    return task, rows, traces, manifest


def _write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False, suffix='.part') as handle:
        handle.write(data)
    os.replace(handle.name, path)


def _csv_bytes(rows):
    import io
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def seed_tasks(split, seed, allow, families=None, regimes=None):
    if split == 'validation':
        families = families or GROUPS
        return [(split, seed, f, r, allow) for r in (regimes or REGIMES) for f in families]
    return [(split, seed, f, f'ood_{f}', allow) for f in (families or v5_ood.FAMILIES)]


def run_seeds(split, seeds, output, workers, *, allow_reserved=False, families=None, regimes=None,
              trace_validation=TRACE_VALIDATION_SEEDS, trace_ood=TRACE_OOD_SEEDS):
    output = Path(output)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for seed in seeds:
            rows_path = output / split / f'episodes-{seed}.csv'
            if rows_path.exists():
                print(f'{split} {seed} already complete', flush=True)
                continue
            started = time.monotonic()
            tasks = seed_tasks(split, seed, allow_reserved, families, regimes)
            flags = [(t, traced(split, seed, t[2], trace_validation, trace_ood)) for t in tasks]
            results = {r[0]: r[1:] for r in pool.map(run_task, flags)}
            rows = [row for t in tasks for row in results[t][0]]
            traces = [tr for t in tasks for tr in results[t][1]]
            manifests = {t[2]: results[t][2] for t in tasks}
            if len(rows) != len(tasks) * len(POLICIES):
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
    v5_freeze.verify()
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
                                         'numpy': numpy.__version__,
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
