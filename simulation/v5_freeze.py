"""Freeze V5 before validation (v5_ood_plan.json).

`--freeze` writes v5_freeze_record.json once: SHA-256 of every V5 source, test,
plan, the declaration, the selection result and the candidate configuration.
`--verify` (and v5_validation.py) refuse to continue if any of them changed or
if the frozen v0.4 chain no longer verifies.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RECORD = ROOT / 'v5_freeze_record.json'
FILES = (
    'v5_acceptance_predeclared.json', 'v5_switch_study_plan.json', 'v5_selection_plan.json', 'v5_ood_plan.json',
    'v5_results/selection/summary.json',
    'v5_policy.py', 'v5_observer.py', 'v5_regime.py', 'v5_forecast.py', 'v5_simulation.py',
    'v5_ood.py', 'v5_validation.py', 'v5_evaluate.py', 'v5_review.py', 'v5_decide.py', 'v5_freeze.py',
    'v5_grid.py', 'v5_development.py', 'v5_development_cohort.py', 'v5_known_study.py', 'v5_smoke.py',
    'v5_intervention.py', 'v5_switch_study.py', 'diagnose_v4_for_v5.py',
    'test_v5.py', 'test_v5_cohort.py', 'test_v5_development.py', 'test_v5_intervention.py', 'test_v5_grid.py',
    'test_v5_ood.py', 'test_v5_evaluate.py', 'test_v5_review.py', 'test_v5_freeze.py',
    # Imported v0.1-v0.3 modules and data outside the v0.4 freeze; also guarded by test_source_layout.py.
    'capacity_policy.py', 'capacity_scenarios.py', 'simulation.py', 'v3_policy.py',
    'data/load_test_summary_per_minute.csv',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def candidate():
    from v5_policy import V5Config
    selection = json.loads((ROOT / 'v5_results/selection/summary.json').read_text())
    if selection['chosen_configuration'] != 'v5_full':
        raise ValueError('Frozen candidate differs from the selection result')
    return json.loads(json.dumps({'policy': 'v5_full', 'action_space': 'variant', 'config': asdict(V5Config())}))


def record():
    return {'version': 'v5-freeze-1', 'candidate': candidate(),
            'files': {name: sha(ROOT / name) for name in FILES}}


def freeze():
    if RECORD.exists():
        raise ValueError('V5 is already frozen; a new freeze needs a new declaration')
    data = record()
    RECORD.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    return data


def verify():
    from freeze_v4 import verify_frozen
    verify_frozen()
    if not RECORD.is_file():
        raise ValueError('V5 has not been frozen')
    stored = json.loads(RECORD.read_text())
    current = record()
    changed = sorted(name for name in set(stored['files']) | set(current['files'])
                     if stored['files'].get(name) != current['files'].get(name))
    if changed or stored['candidate'] != current['candidate']:
        raise ValueError(f'V5 changed after freezing: {changed or "candidate configuration"}')
    return {'verified': True, 'files': len(stored['files']), 'candidate': stored['candidate']['policy']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--freeze', action='store_true')
    group.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    print(json.dumps(freeze() if args.freeze else verify(), indent=2))
