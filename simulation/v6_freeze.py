"""Freeze V6 before validation (v6_validation_plan.json).

`--freeze` writes v6_freeze_record.json once. It holds the SHA-256 of:
- every V6 source and test;
- the declaration and plans;
- the diagnosis, tuning, development and selection results;
- the chosen candidate with its configuration.
`--verify` (and v6_validation.py) refuse to continue if any of them changed, if
the frozen V5 files changed, or if the frozen v0.4 chain no longer verifies.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RECORD = ROOT / 'v6_freeze_record.json'
FILES = (
    'v6_acceptance_predeclared.json', 'v6_selection_plan.json', 'v6_validation_plan.json',
    'v6_results/diagnosis/summary.json', 'v6_results/tuning/summary.json',
    'v6_results/development_grid/summary.json', 'v6_results/selection_grid/summary.json',
    'v6_forecast.py', 'v6_restraint.py', 'v6_policy.py', 'v6_simulation.py', 'v6_evaluate.py', 'v6_grid.py',
    'v6_diagnose.py', 'v6_validation.py', 'v6_review.py', 'v6_decide.py', 'v6_freeze.py',
    'test_v6.py', 'test_v6_evaluate.py', 'test_v6_review.py',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidate():
    import v6_grid
    selection = json.loads((ROOT / 'v6_results/selection_grid/summary.json').read_text())
    plan = json.loads((ROOT / 'v6_selection_plan.json').read_text())
    config = asdict(v6_grid.selected_config())
    if selection['parameters'] != config or plan['parameters'] != config:
        raise ValueError('Frozen parameters differ between the selection plan, the selection result and V6Config')
    return json.loads(json.dumps({'policy': selection['chosen_variant'], 'config': config}))


def record():
    return {'version': 'v6-freeze-1', 'candidate': candidate(),
            'files': {name: sha(ROOT / name) for name in FILES}}


def freeze():
    if RECORD.exists():
        raise ValueError('V6 is already frozen; a new freeze needs a new declaration')
    data = record()
    RECORD.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    return data


def verify():
    from freeze_v4 import verify_frozen
    import v5_freeze
    verify_frozen()
    v5_freeze.verify()
    if not RECORD.is_file():
        raise ValueError('V6 has not been frozen')
    stored = json.loads(RECORD.read_text())
    current = record()
    changed = sorted(name for name in set(stored['files']) | set(current['files'])
                     if stored['files'].get(name) != current['files'].get(name))
    if changed or stored['candidate'] != current['candidate']:
        raise ValueError(f'V6 changed after freezing: {changed or "candidate configuration"}')
    return {'verified': True, 'files': len(stored['files']), 'candidate': stored['candidate']['policy']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--freeze', action='store_true')
    group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    print(json.dumps(freeze() if args.freeze else verify(), indent=2))
