"""Select and confirm the oldest-request intervention on separate long streams."""
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import gzip
import json
import os
import sys
import numpy as np
from study import STUDY,BENCH,INCUMBENT,write,use_results,StudyQueue,measure_arm
import measure
from make_manifest import FAMILIES,P,workload
from queue_engine import replay

PLANS={name:json.loads((STUDY/f'{name}_plan.json').read_text()) for name in ('rescue','tuning')}


def evaluate_stream(spec):
    seed,family,arm=spec
    jobs=workload(family,seed,256)
    profile=json.loads((BENCH/'profile.json').read_text())
    result=replay(jobs,family,profile,arm,queue_factory=StudyQueue)
    return (seed,family),result['latency']


def stats(streams,keys):
    return measure.metrics(np.concatenate([streams[k] for k in keys]))


def describe(data,base,fixed,oldest,seeds):
    keys=list(data)
    pooled=stats(data,keys)
    reference=stats(base,keys)
    ratio={m:pooled[m]/reference[m] for m in pooled}
    by_seed={seed:stats(data,[k for k in keys if k[0]==seed])['p99']/
             stats(base,[k for k in keys if k[0]==seed])['p99'] for seed in seeds}
    gates=P['gates']
    passed=True
    for label,ref in [('fixed4',fixed),('oldest_ready',oldest)]:
        comp=stats(ref,keys)
        for m in ('mean','p95'):
            passed &= pooled[m]/comp[m]<=gates[f'replay_{m}_vs_{label}_max']
    for f in FAMILIES:
        ks=[k for k in keys if k[1]==f]
        a,b=stats(data,ks),stats(fixed,ks)
        for m in ('mean','p99'):
            passed &= a[m]/b[m]<=gates[f'each_family_{m}_vs_fixed4_max']
    return dict(stats=pooled,ratios=ratio,seed_p99=by_seed,replay_checks=bool(passed),
                eligible=bool(passed and ratio['p99']<1 and max(ratio['mean'],ratio['p95'])<=1.10))


def run(split,path,study):
    plan=PLANS[study]
    seeds=plan[split]['seeds']
    if split=='development':
        grid=plan['grid']
        if study=='rescue':
            candidates=[f'rescue_{age}_{n}' for age in grid['age_seconds'] for n in grid['worker_limit']]
        else:
            candidates=[f'size_{window}_{weight}' for window in grid['window_seconds']
                        for weight in grid['arrival_weight'] if f'size_{window}_{weight}'!=INCUMBENT]
    else:
        winner=json.loads((path/f'{study}-development.json').read_text())['winner']
        if not winner:raise SystemExit('No eligible development candidate.')
        candidates=[winner]
    references=('fixed4','oldest_ready',INCUMBENT)
    baseline={}
    reports={}
    with ProcessPoolExecutor(int(os.getenv('MOSAIC_REPLAY_WORKERS','4'))) as pool:
        for arm in (*references,*candidates):
            inputs=[(s,f,arm) for s in seeds for f in FAMILIES]
            streams=dict(pool.map(evaluate_stream,inputs))
            if arm in references:
                baseline[arm]=streams
            else:
                reports[arm]=describe(streams,baseline[INCUMBENT],baseline['fixed4'],baseline['oldest_ready'],seeds)
            records={f'{s}/{f}':v for (s,f),v in streams.items()}
            data=json.dumps(records,separators=(',',':')).encode()
            (path/f'{split}-long-{arm}.json.gz').write_bytes(gzip.compress(data,mtime=0))
            print(split,arm,flush=True)
    eligible=[a for a in candidates if reports[a]['eligible']]
    winner=min(eligible,key=lambda a:(max(reports[a]['seed_p99'].values()),reports[a]['stats']['p99'],reports[a]['stats']['mean'],a)) if eligible else None
    write(path/f'{study}-{split}.json',dict(winner=winner,incumbent=stats(baseline[INCUMBENT],list(baseline[INCUMBENT])),candidates=reports))


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('split',choices=('development','confirmation','scoring','live'))
    parser.add_argument('output',type=Path)
    parser.add_argument('--study',choices=tuple(PLANS),default='rescue')
    args=parser.parse_args()
    path=args.output.resolve()
    if path==(BENCH/'results').resolve():parser.error('Use a separate results directory.')
    use_results(path)
    if args.split in ('development','confirmation'):
        run(args.split,path,args.study)
    else:
        winner=json.loads((path/f'{args.study}-confirmation.json').read_text())['winner']
        if not winner:raise SystemExit('Confirmation did not support promotion to scoring.')
        if args.split=='scoring':measure_arm('scoring',winner,path)
        else:
            import live
            live.run(winner,path,queue_factory=StudyQueue)
            write(path/f'official-{winner}.json',measure.report('scoring',winner,True))
