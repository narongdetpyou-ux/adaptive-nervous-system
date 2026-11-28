"""Diagnose the two V5 failure mechanisms on V6 development data (v6_acceptance_predeclared.json).

drain  Unlimited-buffer regimes, generated_sustained_overload, seeds 6000..6063:
       where the backlog sits at the end of the original horizon for V5 Full and
       Capacity Balance, how many stage visits it still needs, and V5's follow-up
       when it holds from minute 96 (so its follow-up decisions cannot matter).
moves  OOD families, seeds 8000..8063: how often and how much each policy moves,
       whether a move reaches the stage that is still faulted when it takes
       effect, how many moves bounce back (seen in the committed-staff ledger),
       and how often V5's RegimeDetector reports switching. True productivity is
       read here for diagnosis only; no controller ever receives it.
signal Validation-type families, seeds 6000..6063, delayed_noisy and
       legacy_instant: how often V5's switching flag is on, so restraint can be
       checked against families where moving helps.
risk   legacy_instant: the families in the CVaR90 tail of V5 and V4 harm.

Development data only. Writes v6_results/diagnosis/summary.json.
"""
import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

import v5_grid
import v5_ood
from v4_environment import REGIME_CONFIGS
from v4_metrics import _cvar_last_axis
from v5_policy import V5Policy
from v5_simulation import simulate_case

ROOT = Path(__file__).resolve().parent
DECLARATION = json.loads((ROOT / 'v6_acceptance_predeclared.json').read_text())
H = 96
UNLIMITED = tuple(DECLARATION['data']['unlimited_buffer_regimes'])
DRAIN_SEEDS = tuple(range(6000, 6064))
OOD_SEEDS = tuple(range(8000, 8064))
OOD_POLICIES = ('static', 'capacity_balance', 'v4_hybrid', 'v5_full')
OUTPUT = ROOT / 'v6_results/diagnosis/summary.json'


class HoldFrom(V5Policy):
    """V5 Full that holds from a given minute; its observation-driven state is unchanged."""

    def __init__(self, environment, minute):
        super().__init__(environment, 'v5_full')
        self.hold_from = minute

    def decide(self, t, queues, history, staff, current_arrival):
        ledger_size, previous = len(self.ledger.actions), self.last_change
        raw, note = super().decide(t, queues, history, staff, current_arrival)
        if t < self.hold_from:
            return raw, note
        del self.ledger.actions[ledger_size:]
        self.last_change = previous
        return np.zeros(3), {**note, 'branch': 'diagnostic_hold'}


def _overload_case(seed):
    return next(c for c in v5_grid._build_cases((seed,)) if c.group == 'generated_sustained_overload')


def remaining_visits(record):
    """Stage visits still ahead of the retained work: queued at stage s and in flight to s need 3 - s."""
    work = np.asarray(record['end_queue']) + np.asarray(record['inflight_work'])
    return float(np.dot(work, [3, 2, 1]))


def drain_task(task):
    seed, regime = task
    case = _overload_case(seed)
    out = {'seed': seed, 'regime': regime}
    for policy in ('v5_full', 'capacity_balance'):
        row, trace = simulate_case(policy, case, regime=regime, detailed=True)
        end = trace[H - 1]
        work = np.asarray(end['end_queue']) + np.asarray(end['inflight_work'])
        out[policy] = {'end_deficit': row['end_deficit'], 'tail_auc_deficit': row['tail_auc_deficit'],
                       'stage_share_at_end': (work / max(work.sum(), 1e-9)).tolist(),
                       'remaining_visits_at_end': remaining_visits(end),
                       'followup_moves': sum(1 for r in trace[H:] if np.any(np.abs(r['applied_delta']) > 1e-9))}
    held, _ = simulate_case('v5_full', case, regime=regime, controller=HoldFrom(REGIME_CONFIGS[regime], H))
    out['v5_hold_followup'] = {'end_deficit': held['end_deficit'], 'tail_auc_deficit': held['tail_auc_deficit']}
    return out


def fault_stage(record):
    """The stage whose true productivity is clearly below the others (fault factors are 0.42-0.62), else None."""
    rate = np.asarray(record['true_productivity'])
    return int(np.argmin(rate)) if rate.min() < 0.8 * np.median(rate) else None


def switching_share(trace, warmup=5):
    flags = [bool(r['decision_evidence'].get('switching')) for r in trace if r['t'] >= warmup
             and 'switching' in r['decision_evidence']]
    return sum(flags) / max(1, len(flags))


def moves_task(task):
    seed, family = task
    case = v5_ood.build_case(family, seed, allow_reserved=True)
    lag = case.config.transit_delay + len(case.config.startup_factors)
    out = {'seed': seed, 'family': family}
    for policy in OOD_POLICIES:
        row, trace = v5_ood.run_episode(policy, case, detailed=True)
        moves = [r for r in trace if np.any(np.abs(r['applied_delta']) > 1e-9)]
        reached = unchanged = no_fault = bounced = 0
        for r in moves:
            receiving = int(np.argmax(r['applied_delta']))
            effective = trace[min(r['t'] + lag, len(trace) - 1)]
            before, after = fault_stage(r), fault_stage(effective)
            reached += after == receiving
            unchanged += before is not None and before == after
            no_fault += after is None
            check = min(r['t'] + case.config.transit_delay + 1, len(trace) - 1)
            intended = np.asarray(r['decision_input']['committed_staff']) + np.asarray(r['applied_delta'])
            bounced += np.abs(np.asarray(trace[check]['decision_input']['committed_staff']) - intended).sum() >= 1
        out[policy] = {'total_dropped': row['total_dropped'], 'mean_deficit': row['mean_deficit'],
                       'moves': len(moves), 'units': sum(float(np.maximum(r['applied_delta'], 0).sum()) for r in moves),
                       'reaching_fault': reached, 'fault_unchanged': unchanged, 'no_fault_when_effective': no_fault,
                       'bounced': int(bounced),
                       'switching_share': switching_share(trace) if policy == 'v5_full' else None}
    return out


def signal_task(task):
    seed, regime = task
    out = {'seed': seed, 'regime': regime, 'families': {}}
    for case in v5_grid._build_cases((seed,)):
        _, trace = simulate_case('v5_full', case, regime=regime, detailed=True)
        out['families'][case.group] = switching_share(trace)
    return out


def risk_tail(rows_dir):
    """Families of the CVaR90 tail cases in legacy_instant for V5 Full and V4 Hybrid (V5 validation rows)."""
    md = defaultdict(dict)
    for path in sorted(Path(rows_dir).glob('episodes-*.csv')):
        for row in csv.DictReader(path.open()):
            if row['regime'] == 'legacy_instant':
                md[row['policy']][(row['group'], int(row['scenario_seed']))] = float(row['mean_deficit'])
    if not md:
        return None
    cases = sorted(md['static'])
    out = {}
    for policy in ('v5_full', 'v4_hybrid'):
        harm = np.array([max(md[policy][c] - md['static'][c], 0.0) for c in cases])
        tail = np.argsort(-harm)[:math.ceil(0.1 * len(cases))]
        out[policy] = {'risk': float(_cvar_last_axis(harm)),
                       'tail_families': dict(Counter(cases[i][0] for i in tail).most_common()),
                       'harm_by_family': {f: float(harm[[i for i, c in enumerate(cases) if c[0] == f]].sum() / len(cases))
                                          for f in sorted({c[0] for c in cases})}}
    return out


def summarize(drain, moves, risk, signal):
    result = {'drain': {}, 'moves': {}, 'risk_legacy_instant': risk, 'switching_share_v5_full': {}}
    for regime in ('delayed_noisy', 'legacy_instant'):
        items = [x for x in signal if x['regime'] == regime]
        result['switching_share_v5_full'][regime] = {
            f: float(np.mean([x['families'][f] for x in items])) for f in sorted(items[0]['families'])}
    for regime in UNLIMITED:
        items = [d for d in drain if d['regime'] == regime]
        def mean(path):
            return float(np.mean([path(d) for d in items]))
        result['drain'][regime] = {
            'cases': len(items),
            'stage0_share_at_end': {p: mean(lambda d, p=p: d[p]['stage_share_at_end'][0]) for p in ('v5_full', 'capacity_balance')},
            'remaining_visits_at_end': {p: mean(lambda d, p=p: d[p]['remaining_visits_at_end']) for p in ('v5_full', 'capacity_balance')},
            'end_deficit': {p: mean(lambda d, p=p: d[p]['end_deficit']) for p in ('v5_full', 'capacity_balance')},
            'tail_auc_deficit': {p: mean(lambda d, p=p: d[p]['tail_auc_deficit']) for p in ('v5_full', 'capacity_balance')},
            'v5_hold_followup_tail_auc_deficit': mean(lambda d: d['v5_hold_followup']['tail_auc_deficit']),
            'v5_followup_moves': mean(lambda d: d['v5_full']['followup_moves']),
            'correlation_visits_gap_vs_tail_gap': float(np.corrcoef(
                [d['v5_full']['remaining_visits_at_end'] - d['capacity_balance']['remaining_visits_at_end'] for d in items],
                [d['v5_full']['tail_auc_deficit'] - d['capacity_balance']['tail_auc_deficit'] for d in items])[0, 1]),
        }
    for family in v5_ood.FAMILIES:
        items = [m for m in moves if m['family'] == family]
        def share(p, key):
            return sum(m[p][key] for m in items) / max(1, sum(m[p]['moves'] for m in items))
        result['moves'][family] = {p: {
            'mean_total_dropped': float(np.mean([m[p]['total_dropped'] for m in items])),
            'moves_per_episode': float(np.mean([m[p]['moves'] for m in items])),
            'units_per_episode': float(np.mean([m[p]['units'] for m in items])),
            'share_reaching_fault': share(p, 'reaching_fault'),
            'share_fault_unchanged': share(p, 'fault_unchanged'),
            'share_no_fault_when_effective': share(p, 'no_fault_when_effective'),
            'share_bounced': share(p, 'bounced')}
            for p in OOD_POLICIES}
        result['moves'][family]['v5_full']['switching_share'] = float(np.mean([m['v5_full']['switching_share'] for m in items]))
    return result


def main(workers, rows_dir):
    with ProcessPoolExecutor(max_workers=workers) as pool:
        drain = list(pool.map(drain_task, [(s, r) for r in UNLIMITED for s in DRAIN_SEEDS]))
        moves = list(pool.map(moves_task, [(s, f) for f in v5_ood.FAMILIES for s in OOD_SEEDS]))
        signal = list(pool.map(signal_task, [(s, r) for r in ('delayed_noisy', 'legacy_instant') for s in DRAIN_SEEDS]))
    result = summarize(drain, moves, risk_tail(rows_dir), signal)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--v5-rows', default=str(ROOT / 'local_runs/v5_validation/run_a/validation'),
                        help='V5 validation rows (development data for V6) for the legacy_instant risk tail')
    args = parser.parse_args()
    print(json.dumps(main(args.workers, args.v5_rows), indent=1)[:6000])
