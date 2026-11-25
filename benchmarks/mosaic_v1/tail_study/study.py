"""Select on development only. Use the original replay, live runner and scoring functions."""
import argparse
import gzip
import os
from concurrent.futures import ProcessPoolExecutor
import json
import shutil
import sys
from pathlib import Path

STUDY=Path(__file__).resolve().parent
BENCH=STUDY.parent
sys.path.insert(0,str(BENCH))
import measure
import live
from controls import StudyQueue
from queue_engine import replay

PLAN=json.loads((STUDY/'plan.json').read_text())
INCUMBENT=PLAN['incumbent']
REFERENCE=('fixed4','oldest_ready',INCUMBENT)


def write(path,record):
    path.write_text(json.dumps(record,indent=2,allow_nan=False)+'\n')


def use_results(path):
    path.mkdir(parents=True,exist_ok=True)
    for split in ('development','scoring'):
        for arm in REFERENCE:
            name=f'{split}-{arm}.json.gz'
            source=BENCH/'results'/name
            dest=path/name
            if not dest.exists():
                shutil.copyfile(source,dest)
            if dest.read_bytes()!=source.read_bytes():
                raise ValueError(f'Changed reference: {name}')
    measure.RESULT=path


def _one_stream(spec):
    key,jobs,arm,profile=spec
    return key,replay(jobs,key.split('/')[1],profile,arm,queue_factory=StudyQueue)


def measure_arm(split,arm,path):
    profile=json.loads((BENCH/'profile.json').read_text())
    specs=[(key,jobs,arm,profile) for key,jobs in measure.workloads().items() if key.startswith(split+'/')]
    with ProcessPoolExecutor(int(os.getenv('MOSAIC_REPLAY_WORKERS','4'))) as pool:
        rows=dict(pool.map(_one_stream,specs))
    content=json.dumps(rows,separators=(',',':')).encode()
    (path/f'{split}-{arm}.json.gz').write_bytes(gzip.compress(content,mtime=0))


def select(path):
    base,_=measure.summary(measure.read('development',INCUMBENT))
    rows=[]
    grid=PLAN['challengers']
    for cap in grid['priority_cap']:
        for weight in grid['arrival_weight']:
            arm=f'tail_{cap}_{weight}'
            measure_arm('development',arm,path)
            r=measure.report('development',arm)
            stats=r['replay'][arm]
            eligible=(not r['failed'] and stats['p99']<base['p99'] and
                      all(stats[m]<=1.10*base[m] for m in ('mean','p95')))
            rows.append(dict(arm=arm,stats=stats,eligible=eligible,rank=r['rank']))
    candidates=[r for r in rows if r['eligible']]
    winner=INCUMBENT
    if candidates:
        cutoff=1.02*min(r['stats']['p99'] for r in candidates)
        shortlist=[r for r in candidates if r['stats']['p99']<=cutoff]
        winner=min(shortlist,key=lambda r:r['rank'])['arm']
    write(path/'selection.json',dict(incumbent=base,winner=winner,candidates=rows))
    print(winner,flush=True)


def selected(path):
    return json.loads((path/'selection.json').read_text())['winner']


def evaluate(path):
    winner=selected(path)
    if winner==INCUMBENT:
        raise SystemExit('No eligible challenger; retain incumbent.')
    measure_arm('scoring',winner,path)
    for arm in (INCUMBENT,winner):
        write(path/f'official-{arm}.json',measure.report('scoring',arm,True))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('select','live','evaluate'))
    p.add_argument('output',type=Path)
    p.add_argument('--arm',choices=('fixed4','incumbent','selected'),default='selected')
    args=p.parse_args()
    output=args.output.resolve()
    if output==(BENCH/'results').resolve():
        p.error('Choose a new results directory to preserve the original measurements.')
    use_results(output)
    if args.action=='select':
        select(output)
    elif args.action=='evaluate':
        evaluate(output)
    else:
        arm={'fixed4':'fixed4','incumbent':INCUMBENT}.get(args.arm)
        live.run(arm or selected(output),output,queue_factory=StudyQueue)
