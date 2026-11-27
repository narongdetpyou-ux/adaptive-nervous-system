"""Evaluate and review a frozen V5 validation run, then write the committed results.

python v5_decide.py <run_a> <run_b>
Integrity evidence comes from fresh tests, both freeze checks, the independent
reviewer and the byte comparison of the two complete runs from one commit. The
evaluator computes every gate on the declared case population, the reviewer
recomputes every number independently, and the final decision uses the
assembled evidence. Nothing here can relax a gate.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import v5_evaluate
import v5_freeze
import v5_review

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / 'v5_results/validation'
COMPARED = ('validation', 'ood', 'traces', 'manifests')


def _ok(command):
    run = subprocess.run([sys.executable, *command], cwd=ROOT, capture_output=True, text=True)
    return run.returncode == 0, (run.stdout + run.stderr)[-2000:]


def _final(run, folder):
    """Completed output files only; an interrupted atomic write leaves a *.part file."""
    return {p.name: p for p in sorted((Path(run) / folder).glob('*')) if p.is_file() and p.suffix != '.part'}


def repeat(run_a, run_b):
    infos = [json.loads((Path(run) / 'run.json').read_text()) for run in (run_a, run_b)]
    files, extra = {}, []
    for folder in COMPARED:
        first, second = _final(run_a, folder), _final(run_b, folder)
        for name, path in first.items():
            data = path.read_bytes()
            files[f'{folder}/{name}'] = {'sha256': hashlib.sha256(data).hexdigest(),
                                         'identical': name in second and second[name].read_bytes() == data}
        extra += [f'{folder}/{name}' for name in second if name not in first]
    same_commit = infos[0]['commit'] == infos[1]['commit']
    return {'runs': infos, 'same_commit': same_commit, 'files': files, 'only_in_second_run': sorted(extra),
            'identical': same_commit and bool(files) and not extra and all(v['identical'] for v in files.values())}


def _table(data, regime, policies, cases=None):
    out = {}
    for policy in policies:
        matrix = data[regime, policy] if cases is None else data[regime, policy][cases]
        harm = v5_evaluate._harm(data, regime, policy)
        harm = harm if cases is None else harm[cases]
        out[policy] = {**dict(zip(v5_evaluate.METRICS, matrix.mean(axis=0).tolist())),
                       'risk': float(v5_evaluate._cvar_last_axis(harm))}
    return out


def summary(validation, ood, decision, reproducibility):
    """Descriptive tables beside the decision: every regime, family and policy."""
    result = {'plan': v5_evaluate.PLAN['version'], 'decision': decision['decision'],
              'failed_gates': decision['failed_gates'], 'missing_evidence': decision['missing_evidence'],
              'rows': {'validation': len(validation), 'ood': len(ood)},
              'commit': reproducibility['runs'][0]['commit']}
    if 'original' not in decision or 'ood' not in decision:
        return result
    policies = v5_evaluate.POLICIES
    data, order, _ = v5_evaluate.matrices(validation, v5_evaluate.REGIMES)
    families = sorted({family for family, _ in order})
    result['validation'] = {
        'cases_per_regime': len(order),
        'means_and_risk': {r: _table(data, r, policies) for r in v5_evaluate.REGIMES},
        'by_family': {r: {f: _table(data, r, policies, [i for i, (g, _) in enumerate(order) if g == f])
                          for f in families} for r in v5_evaluate.REGIMES},
    }
    result['ood'] = {'pooled': decision['ood']['pooled'], 'families': {}}
    for family in v5_evaluate.OOD_FAMILIES:
        regime = f'ood_{family}'
        subset_data, subset_order, _ = v5_evaluate.matrices([r for r in ood if r['group'] == family], (regime,))
        result['ood']['families'][family] = {'cases': len(subset_order),
                                             'means_and_risk': _table(subset_data, regime, policies)}
    return result


def decide(run_a, run_b, destination=RESULTS):
    tests, tests_log = _ok(['-m', 'unittest', 'discover', '-s', '.', '-p', 'test_*.py'])
    v4, v4_log = _ok(['freeze_v4.py', '--verify'])
    try:
        v5_freeze.verify()
        v5, v5_log = True, ''
    except Exception as error:  # recorded as failed evidence, never skipped
        v5, v5_log = False, repr(error)
    reproducibility = repeat(run_a, run_b)
    validation = v5_evaluate.read_rows(sorted(_final(run_a, 'validation').values()))
    ood = v5_evaluate.read_rows(sorted(_final(run_a, 'ood').values()))
    population = v5_evaluate.DECLARED
    draft = v5_evaluate.evaluate(validation, ood, {name: True for name in v5_evaluate.INTEGRITY}, population)
    if 'original' in draft and 'ood' in draft:
        review = v5_review.review(run_a, draft)
        flags = review['flags']
        integrity = {
            'fresh_tests': tests, 'frozen_v4': v4, 'frozen_v5': v5,
            'scenario_identity': flags['scenario_identity'], 'accounting': flags['accounting'],
            'observation_boundary': flags['observation_boundary'],
            'independent_replay': flags['independent_replay'] and flags['statistics_match'],
            'tamper_test': flags['tamper_test'], 'deterministic_repeat': reproducibility['identical'],
        }
    else:
        review, integrity = None, {}
    decision = v5_evaluate.evaluate(validation, ood, integrity, population)
    decision['run'] = reproducibility['runs'][0]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    outputs = {
        'decision.json': decision,
        'independent_review.json': {'review': review, 'fresh_tests_passed': tests, 'fresh_tests_log_tail': tests_log,
                                    'freeze_v4_passed': v4, 'freeze_v4_log_tail': v4_log,
                                    'freeze_v5_passed': v5, 'freeze_v5_error': v5_log},
        'reproducibility.json': reproducibility,
        'summary.json': summary(validation, ood, decision, reproducibility),
    }
    for name, value in outputs.items():
        (destination / name).write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + '\n')
    return decision


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_a', type=Path)
    parser.add_argument('run_b', type=Path)
    args = parser.parse_args()
    result = decide(args.run_a, args.run_b)
    print(result['decision'], result['failed_gates'], result['missing_evidence'])
