"""Offline diagnosis of the now-known v0.2 validation set.

True future arrivals/productivity are used ONLY by this evaluator to contrast
holding with an already-recorded move over its locked dwell interval. Nothing
from these counterfactuals is passed back to a controller. Local move regret is
not the causal effect of that move on the full episode or of a deployment.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean

import numpy as np

from capacity_policy import CapacityConfig
from capacity_scenarios import generate_scenarios
from capacity_simulation import simulate_case
from audit_capacity import audit_trace, audit_source_integrity

ROOT = Path(__file__).resolve().parent
TOL = 1e-6


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n', encoding='utf-8')


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key:json.dumps(value,separators=(',',':')) if isinstance(value,(list,dict)) else value
                          for key,value in row.items()} for row in rows)


def replay_locked(observed, staff, arrivals, productivity, terminal_weight):
    """Independent scalar service replay; current arrival is in observed already."""
    q = list(map(float,observed))
    allocation = list(map(float,staff))
    queues, flows, totals = [], [], []
    for h in range(len(arrivals)):
        if h:
            q[0] += float(arrivals[h])
        served = []
        for i in range(3):
            flow = min(q[i],allocation[i]*float(productivity[h,i]))
            q[i] -= flow
            served.append(flow)
            if i < 2:
                q[i+1] += flow
        queues.append(q.copy())
        flows.append(served)
        totals.append(sum(q))
    return {'cost':mean(totals)+terminal_weight*totals[-1],
            'mean_backlog':mean(totals),'terminal_backlog':totals[-1],
            'queues':queues,'flows':flows}


def _result_summary(moves):
    full = [move for move in moves if not move['truncated']]
    harmed = [move for move in full if move['actual_gain'] < -TOL]
    neutral = [move for move in full if abs(move['actual_gain']) <= TOL]
    improved = [move for move in full if move['actual_gain'] > TOL]
    return {
        'moves':len(moves),'full_dwell_moves':len(full),'truncated_moves':len(moves)-len(full),
        'local_harmful_moves':len(harmed),'local_neutral_moves':len(neutral),'local_beneficial_moves':len(improved),
        'local_harm_fraction':len(harmed)/len(full) if full else None,
        'mean_predicted_gain':mean(m['predicted_mean_gain'] for m in full) if full else None,
        'mean_realized_local_gain':mean(m['actual_gain'] for m in full) if full else None,
        'harmful_move_flags':{
            'future_mean_demand_below_low_forecast':sum(m['future_mean_demand_below_low_forecast'] for m in harmed),
            'donor_rate_overestimated_by_over_15pct':sum(m['donor_rate_overestimated_by_over_15pct'] for m in harmed),
            'recipient_rate_underestimated_by_over_15pct':sum(m['recipient_rate_underestimated_by_over_15pct'] for m in harmed),
            'donor_queue_mean_increased':sum(m['donor_queue_mean_increased'] for m in harmed),
            'recipient_at_most_one_saturated_observation':sum(m['recipient_at_most_one_saturated_observation'] for m in harmed),
        },
        'worst_local_losses':[{
            k:m[k] for k in ('group','scenario_seed','t','delta','predicted_mean_gain','predicted_worst_gain',
                             'actual_gain','forecast_demand','actual_future_demand_mean','estimated_rates',
                             'actual_mean_rates','saturated_observations_before_move')}
            for m in sorted(full,key=lambda item:item['actual_gain'])[:10]],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=ROOT/'v3_results/diagnosis')
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty diagnosis output to preserve previous evidence')
    output.mkdir(parents=True,exist_ok=True)
    protocol = json.loads((ROOT/'capacity_protocol.json').read_text())
    config = CapacityConfig(**protocol['controller_configuration'])
    source_hashes = audit_source_integrity(ROOT)
    original_path = ROOT/'capacity_results/validation/rows/capacity_aware.json'
    original = json.loads(original_path.read_text())['rows']
    expected = {(row['group'],row['scenario_seed']):row for row in original}
    static_rows = json.loads((ROOT/'capacity_results/validation/rows/static.json').read_text())['rows']
    static = {(row['group'],row['scenario_seed']):row for row in static_rows}
    cases = generate_scenarios('validation')
    if len(expected) != len(cases):
        raise ValueError('Original validation scope mismatch')
    all_moves, case_rows, replayed_rows = [], [], []
    replayed_minutes = 0
    for case in cases:
        row, trace = simulate_case('capacity_aware',case,detailed=True,config=config)
        if row != expected[case.group,case.seed]:
            raise ValueError(f'v0.2 replay row differs: {(case.group,case.seed)}')
        audit_trace({'summary':row,'minutes':trace},case.arrivals,case.productivity)
        replayed_minutes += len(trace)
        replayed_rows.append(row)
        baseline = static[case.group,case.seed]
        case_info = {
            'group':case.group,'scenario_seed':case.seed,'source':case.source,
            'capacity_mean_backlog':row['mean_backlog'],'static_mean_backlog':baseline['mean_backlog'],
            'mean_backlog_excess':row['mean_backlog']-baseline['mean_backlog'],
            'mean_backlog_regression':row['mean_backlog']>baseline['mean_backlog']+TOL,
            'capacity_end_backlog':row['end_backlog'],'static_end_backlog':baseline['end_backlog'],
            'end_backlog_excess':row['end_backlog']-baseline['end_backlog'],
            'terminal_regression':row['end_backlog']>baseline['end_backlog']+TOL,
            'moves':row['moves'],
        }
        saturated_count = [0,0,0]
        case_moves = []
        for minute in trace:
            t = minute['t']
            delta = np.array(minute['delta'])
            if np.any(delta):
                after = np.array(minute['staff'])
                before = after-delta
                remaining = min(config.dwell,case.horizon-t)
                arrivals = case.arrivals[t:t+remaining]
                rates = case.productivity[t:t+remaining]
                hold = replay_locked(minute['observed'],before,arrivals,rates,config.terminal_weight)
                applied = replay_locked(minute['observed'],after,arrivals,rates,config.terminal_weight)
                observed_actual_path = np.array([item['queue'] for item in trace[t:t+remaining]])
                if not np.allclose(applied['queues'],observed_actual_path,rtol=1e-10,atol=TOL):
                    raise ValueError('Applied-action counterfactual does not match locked actual trajectory')
                evidence = minute['decision_evidence']
                estimated_rates = np.array(evidence['estimator']['rate'])
                actual_mean_rates = rates.mean(axis=0)
                rate_error = estimated_rates-actual_mean_rates
                relative_rate_error = rate_error/actual_mean_rates
                donor_indices = np.flatnonzero(delta<0).tolist()
                recipient_indices = np.flatnonzero(delta>0).tolist()
                forecast = evidence['forecast_demand']
                next_arrival_mean = float(np.mean(arrivals[1:])) if remaining>1 else None
                donor_queue_gaps = (np.mean(applied['queues'],axis=0)-np.mean(hold['queues'],axis=0))
                move = {
                    'group':case.group,'scenario_seed':case.seed,'source':case.source,'t':t,
                    'episode_mean_regression':case_info['mean_backlog_regression'],
                    'episode_terminal_regression':case_info['terminal_regression'],
                    'delta':delta.tolist(),'staff_before':before.tolist(),'staff_after':after.tolist(),
                    'donor_indices':donor_indices,'recipient_indices':recipient_indices,
                    'window_minutes':remaining,'truncated':remaining<config.dwell,
                    'predicted_mean_gain':evidence['predicted_mean_gain'],
                    'predicted_worst_gain':evidence['predicted_worst_gain'],
                    'actual_gain':hold['cost']-applied['cost'],
                    'actual_hold_cost':hold['cost'],'actual_applied_cost':applied['cost'],
                    'actual_mean_backlog_gain':hold['mean_backlog']-applied['mean_backlog'],
                    'actual_terminal_backlog_gain':hold['terminal_backlog']-applied['terminal_backlog'],
                    'forecast_demand':forecast,'actual_future_demand_mean':next_arrival_mean,
                    'future_demand_overprediction':None if next_arrival_mean is None else forecast-next_arrival_mean,
                    'future_mean_demand_below_low_forecast':next_arrival_mean is not None and next_arrival_mean<forecast*(1-config.demand_spread),
                    'estimated_rates':estimated_rates.tolist(),'actual_mean_rates':actual_mean_rates.tolist(),
                    'rate_estimate_minus_actual_mean':rate_error.tolist(),
                    'rate_relative_error':relative_rate_error.tolist(),
                    'donor_rate_overestimated_by_over_15pct':any(relative_rate_error[i]>.15 for i in donor_indices),
                    'recipient_rate_underestimated_by_over_15pct':any(relative_rate_error[i]<-.15 for i in recipient_indices),
                    'saturated_observations_before_move':saturated_count.copy(),
                    'recipient_at_most_one_saturated_observation':any(saturated_count[i]<=1 for i in recipient_indices),
                    'stage_mean_queue_gap_applied_minus_hold':donor_queue_gaps.tolist(),
                    'donor_queue_mean_increased':any(donor_queue_gaps[i]>TOL for i in donor_indices),
                    'observed_queue':minute['observed'],'estimator_evidence':evidence['estimator'],
                    'hold_queue_path':hold['queues'],'applied_queue_path':applied['queues'],
                }
                case_moves.append(move)
                all_moves.append(move)
            for i in range(3):
                if minute['queue'][i]>config.tolerance and minute['staff'][i]>0:
                    saturated_count[i] += 1
        case_info['harmful_full_dwell_moves'] = sum(m['actual_gain'] < -TOL and not m['truncated'] for m in case_moves)
        case_info['beneficial_full_dwell_moves'] = sum(m['actual_gain'] > TOL and not m['truncated'] for m in case_moves)
        case_rows.append(case_info)
    by_group = {group:_result_summary([m for m in all_moves if m['group']==group]) for group in dict.fromkeys(r['group'] for r in case_rows)}
    diagnosis = {
        'scope':'offline diagnosis of the already-known v0.2 validation set; development evidence for v0.3',
        'causal_boundary':'actual future rates and arrivals are evaluator-only; local hold counterfactual covers one locked dwell and does not identify full-episode causal effect',
        'counterfactual':'same observed current queue and actual future environment; compare current allocation held with recorded applied allocation held; no subsequent actions within window',
        'cost':'mean total end-minute backlog over dwell plus 0.25 times terminal backlog; truncated final windows flagged and excluded from prediction-error summaries',
        'arrival_error_definition':'recorded constant forecast minus actual mean of subsequent minutes; current known arrival excluded',
        'rate_error_definition':'recorded point estimate before decision minus actual mean productivity over the evaluated dwell',
        'flag_limit':'flags overlap and are descriptive; they do not prove a unique mechanism or identify the contribution of each error',
        'episodes_reproduced':len(replayed_rows),'episode_rows_exact_match':True,'minutes_independently_replayed':replayed_minutes,
        'mean_backlog_regressions_vs_static':sum(r['mean_backlog_regression'] for r in case_rows),
        'terminal_regressions_vs_static':sum(r['terminal_regression'] for r in case_rows),
        'all_moves':_result_summary(all_moves),'by_group':by_group,
        'mean_regression_cases':_result_summary([m for m in all_moves if m['episode_mean_regression']]),
        'terminal_regression_cases':_result_summary([m for m in all_moves if m['episode_terminal_regression']]),
        'original_rows_sha256':digest(original_path),'diagnostic_code_sha256':digest(__file__),
        'upstream_hashes':source_hashes,'config':config.as_dict(),
    }
    write_json(output/'diagnosis.json',diagnosis)
    write_json(output/'moves.json',all_moves)
    write_csv(output/'moves.csv',all_moves)
    write_csv(output/'cases.csv',case_rows)
    write_csv(output/'replayed_episodes.csv',replayed_rows)
    all_stats = diagnosis['all_moves']
    surge = by_group['surge_only']
    text = [
        '# Offline diagnosis of known v0.2 validation results',
        '',
        f"Reproduced all {len(replayed_rows)} capacity-aware episode rows exactly and independently replayed {replayed_minutes:,} minutes.",
        f"Compared with stored static outcomes: {diagnosis['mean_backlog_regressions_vs_static']} mean-backlog regressions and {diagnosis['terminal_regressions_vs_static']} terminal-backlog regressions.",
        '',
        '## Local move outcomes',
        '',
        f"Among {all_stats['full_dwell_moves']} full-dwell moves, {all_stats['local_harmful_moves']} increased the actual eight-minute objective relative to holding, {all_stats['local_beneficial_moves']} reduced it, and {all_stats['local_neutral_moves']} tied. {all_stats['truncated_moves']} end-window moves were separately flagged.",
        f"Surge-only: {surge['full_dwell_moves']} moves, {surge['local_harmful_moves']} locally harmful, {surge['local_beneficial_moves']} beneficial, {surge['local_neutral_moves']} neutral.",
        '',
        '## Interpretation boundaries',
        '',
        'This is a development diagnostic on a previously inspected synthetic set. Future truth is used only for offline evaluation and must never be supplied to the online controller. A one-dwell counterfactual is not the effect of removing a move from an otherwise fully recomputed episode. Flags overlap and do not establish a unique cause.',
        '',
        'The original forecast ensemble lowers all stages together or recovers them together toward a nominal prior. It does not independently vary uncertainty at each stage. Surge-only sign reversals can occur even when average subsequent demand remains inside the ±20% demand band; demand extrapolation alone is therefore insufficient to explain every observed failure.',
        '',
        'See diagnosis.json for full group/flag counts, moves.json or moves.csv for every move and counterfactual queue path, cases.csv for all paired episode regressions, and replayed_episodes.csv for exact replayed outcomes.',
    ]
    (output/'DIAGNOSIS.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(json.dumps({k:diagnosis[k] for k in ('episodes_reproduced','episode_rows_exact_match','minutes_independently_replayed','mean_backlog_regressions_vs_static','terminal_regressions_vs_static')},indent=2))
    print(json.dumps({'all':all_stats,'surge_only':surge},indent=2))


if __name__=='__main__':
    main()
