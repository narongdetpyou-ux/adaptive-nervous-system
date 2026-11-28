"""Evaluate and review a frozen V6 validation run, then write the committed results.

python v6_decide.py <run_a> <run_b>

Integrity evidence comes from six sources:
- fresh tests;
- the v0.4, V5 and V6 freezes;
- the independent reviewer;
- the byte comparison of two complete runs from one commit;
- the development check that the frozen baselines reproduce the V5 rows;
- the declared case population.
The evaluator computes every standing gate and the round hypotheses, and the
reviewer recomputes every number independently. The decision uses the
assembled evidence. Nothing here can relax a gate.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import v6_review
import v6_validation
from v4_metrics import _cvar_last_axis
from v5_decide import _final, repeat
from v6_evaluate import Gates, METRICS, read_rows
from v6_grid import DECLARATION

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / 'v6_results/validation'
DEVELOPMENT = ROOT / 'v6_results/development_grid/summary.json'


def _ok(command):
    run = subprocess.run([sys.executable, *command], cwd=ROOT, capture_output=True, text=True)
    return run.returncode == 0, (run.stdout + run.stderr)[-2000:]


def _verified(module):
    try:
        __import__(module).verify()
        return True, ''
    except Exception as error:  # recorded as failed evidence, never skipped
        return False, repr(error)


def _table(gates, data, regime, cases=None):
    out = {}
    for policy in gates.policies:
        matrix = data[regime, policy] if cases is None else data[regime, policy][cases]
        harm = gates.harm(data, regime, policy)
        harm = harm if cases is None else harm[cases]
        out[policy] = {**dict(zip(METRICS, matrix.mean(axis=0).tolist())), 'risk': float(_cvar_last_axis(harm))}
    return out


def summary(gates, validation, ood, decision, reproducibility):
    """Descriptive tables beside the decision: every regime, family and policy."""
    result = {'decision': decision['decision'], 'failed_gates': decision['failed_gates'],
              'missing_evidence': decision['missing_evidence'], 'candidate': gates.candidate,
              'rows': {'validation': len(validation), 'ood': len(ood)},
              'commit': reproducibility['runs'][0]['commit'], 'hypotheses': decision.get('hypotheses')}
    if 'original' not in decision or 'ood' not in decision:
        return result
    data, order, _ = gates.matrices(validation, gates.regimes)
    families = sorted({family for family, _ in order})
    result['validation'] = {
        'cases_per_regime': len(order),
        'means_and_risk': {r: _table(gates, data, r) for r in gates.regimes},
        'by_family': {r: {f: _table(gates, data, r, [i for i, (g, _) in enumerate(order) if g == f]) for f in families}
                      for r in gates.regimes},
    }
    result['ood'] = {'pooled': decision['ood']['pooled'], 'families': {}}
    for family, regime, fdata, forder, _ in gates.ood_tables(ood):
        result['ood']['families'][family] = {'cases': len(forder), 'means_and_risk': _table(gates, fdata, regime)}
    return result


def decide(run_a, run_b, destination=RESULTS):
    tests, tests_log = _ok(['-m', 'unittest', 'discover', '-s', '.', '-p', 'test_*.py'])
    v4, v4_log = _ok(['freeze_v4.py', '--verify'])
    v5, v5_log = _verified('v5_freeze')
    v6, v6_log = _verified('v6_freeze')
    development = json.loads(DEVELOPMENT.read_text()) if DEVELOPMENT.is_file() else {}
    baselines_ok = bool(development.get('baseline_reproduction', {}).get('all_identical'))
    reproducibility = repeat(run_a, run_b)
    candidate = v6_validation.candidate()
    policies = v6_validation.policies()
    gates = Gates(DECLARATION, candidate, policies)
    validation = read_rows(sorted(_final(run_a, 'validation').values()), policies)
    ood = read_rows(sorted(_final(run_a, 'ood').values()), policies)
    population = gates.declared_population()
    all_true = {name: True for name in ('fresh_tests', 'frozen_v4', 'frozen_v5', 'frozen_v6', 'scenario_identity',
                                        'accounting', 'observation_boundary', 'independent_replay', 'tamper_test',
                                        'deterministic_repeat', 'baseline_reproduction')}
    draft = gates.evaluate(validation, ood, all_true, population)
    review, integrity, hypotheses = None, {}, None
    if 'original' in draft and 'ood' in draft:
        hypotheses = gates.hypotheses(validation, ood)
        draft['hypotheses'] = hypotheses
        review = v6_review.review(run_a, draft, candidate)
        flags = review['flags']
        integrity = {
            'fresh_tests': tests, 'frozen_v4': v4, 'frozen_v5': v5, 'frozen_v6': v6,
            'scenario_identity': flags['scenario_identity'], 'accounting': flags['accounting'],
            'observation_boundary': flags['observation_boundary'],
            'independent_replay': flags['independent_replay'] and flags['statistics_match'],
            'tamper_test': flags['tamper_test'], 'deterministic_repeat': reproducibility['identical'],
            'baseline_reproduction': baselines_ok,
        }
    decision = gates.evaluate(validation, ood, integrity, population)
    decision['hypotheses'] = hypotheses
    decision['run'] = reproducibility['runs'][0]
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    outputs = {
        'decision.json': decision,
        'independent_review.json': {'review': review, 'fresh_tests_passed': tests, 'fresh_tests_log_tail': tests_log,
                                    'freeze_v4_passed': v4, 'freeze_v4_log_tail': v4_log,
                                    'freeze_v5_passed': v5, 'freeze_v5_error': v5_log,
                                    'freeze_v6_passed': v6, 'freeze_v6_error': v6_log,
                                    'baseline_reproduction': development.get('baseline_reproduction')},
        'reproducibility.json': reproducibility,
        'summary.json': summary(gates, validation, ood, decision, reproducibility),
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
    print(result['decision'], result['failed_gates'], result['missing_evidence'],
          {k: v['supported'] for k, v in (result['hypotheses'] or {}).items() if isinstance(v, dict) and 'supported' in v})
