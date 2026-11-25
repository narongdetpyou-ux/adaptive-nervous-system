"""One-off first-frame priority, using the existing replay and live dispatchers."""
import argparse
from collections import deque
from concurrent.futures import ProcessPoolExecutor
import gzip
import heapq
import json
import os
from pathlib import Path
import shutil
import sys

HERE=Path(__file__).resolve().parent
BENCH=HERE.parent
sys.path.insert(0,str(BENCH))
from queue_engine import Queue,replay
from make_manifest import FAMILIES,workload
import measure
import live

PLAN=json.loads((HERE/'plan.json').read_text())
INC=PLAN['incumbent']
NEW=PLAN['candidate']


class FirstStartQueue(Queue):
    def __init__(self,jobs,frame_counts,arm):
        self.enabled=arm==NEW
        super().__init__(jobs,frame_counts,INC if self.enabled else arm)
        self.not_started=deque()
        self.first_wait=[None]*len(jobs)
        self.eligible_grants=[]
        self.changed_dispatches=[]

    def start_tile(self,j,k):
        super().start_tile(j,k)
        if self.enabled and k==0 and len(self.jobs[j][1])>=4:
            self.not_started.append(j)

    def replace_dispatch(self,normal,j):
        # The base dispatcher already removed and charged its normal choice.
        # Swap one ready task, preserving its original ordering and accounting.
        previous,k,stage,frame=normal
        self.busy[stage]-=1
        self.waiting[stage]+=self.unit(previous,stage)
        heapq.heappush(self.heap[stage],(self.priorities[previous],previous,frame))
        heap=self.heap[0]
        index=heap.index((self.priorities[j],j,0))
        heap[index]=heap[-1]
        heap.pop()
        if index<len(heap):
            heapq.heapify(heap)
        self.busy[0]+=1
        self.waiting[0]-=self.unit(j,0)
        return j,0,0,0

    def pop(self):
        task=super().pop()
        if task is None:
            return None
        while self.not_started and self.first_wait[self.not_started[0]] is not None:
            self.not_started.popleft()
        if self.not_started:
            j=self.not_started[0]
            if self.clock-self.jobs[j][0]>=8.0:
                self.not_started.popleft()
                self.eligible_grants.append(j)
                if task!=(j,0,0,0):
                    task=self.replace_dispatch(task,j)
                    self.changed_dispatches.append(j)
        j,k,stage,_=task
        if k==0 and stage==0 and self.first_wait[j] is None:
            self.first_wait[j]=round(self.clock-self.jobs[j][0],8)
        return task

    def observations(self):
        if any(t is None or t<0 for t in self.first_wait):
            raise ValueError('Missing or invalid first dispatch')
        return dict(first_wait=self.first_wait,eligible_grants=self.eligible_grants,
                    changed_dispatches=self.changed_dispatches)


def measured(invoke):
    queues=[]
    def create(*args):
        queue=FirstStartQueue(*args)
        queues.append(queue)
        return queue
    result=invoke(create)
    if len(queues)!=1:
        raise ValueError('Expected one queue per stream')
    return result,queues[0].observations()


def stream(spec):
    key,jobs,arm=spec
    family=key.split('/')[1]
    profile=json.loads((BENCH/'profile.json').read_text())
    result,starts=measured(lambda factory:replay(jobs,family,profile,arm,queue_factory=factory))
    return key,{**result,**starts}


def run_replay(split,arm,path):
    if split=='scoring':
        streams={k:j for k,j in measure.workloads().items() if k.startswith('scoring/')}
    else:
        streams={f'development/{f}/{s}':workload(f,s,256) for f in FAMILIES
                 for s in PLAN['development']['seeds']}
    with ProcessPoolExecutor(int(os.environ.get('MOSAIC_REPLAY_WORKERS','4'))) as pool:
        rows=dict(pool.map(stream,((k,j,arm) for k,j in streams.items())))
    if arm==INC and split=='scoring':
        original=measure.read(split,arm)
        for key,record in original.items():
            if any(rows[key][field]!=value for field,value in record.items()):
                raise ValueError(f'Incumbent recording changed original result: {key}')
    (path/f'{split}-{arm}.json.gz').write_bytes(gzip.compress(json.dumps(rows,separators=(',',':')).encode(),mtime=0))


def run_live(arm,path):
    _,starts=measured(lambda factory:live.run(arm,path,queue_factory=factory))
    (path/f'start-live-{arm}.json').write_text(json.dumps(starts)+'\n')


def gates(path):
    for arm in ('fixed4','oldest_ready'):
        name=f'scoring-{arm}.json.gz'
        shutil.copyfile(BENCH/'results'/name,path/name)
    measure.RESULT=path
    for arm in (INC,NEW):
        report=measure.report('scoring',arm,True)
        (path/f'gates-{arm}.json').write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('development','scoring','live','gates'))
    parser.add_argument('output',type=Path)
    parser.add_argument('--arm',choices=('fixed4',INC,NEW))
    args=parser.parse_args()
    path=args.output.resolve()
    if path==(BENCH/'results').resolve():
        parser.error('Use a separate output directory.')
    path.mkdir(parents=True,exist_ok=True)
    if args.action=='gates':
        gates(path)
    elif not args.arm:
        parser.error('An arm is required for a run.')
    elif args.action=='live':
        run_live(args.arm,path)
    else:
        run_replay(args.action,args.arm,path)
