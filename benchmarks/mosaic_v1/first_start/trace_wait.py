"""Post-hoc timing decomposition on the development cohort; never used for selection."""
from concurrent.futures import ProcessPoolExecutor
import argparse
import json
import os
from pathlib import Path
import statistics
import numpy as np
from experiment import BENCH,FAMILIES,INC,NEW,PLAN,FirstStartQueue,replay,workload


class Timeline(FirstStartQueue):
    def __init__(self,*args):
        super().__init__(*args)
        self.second=[None]*len(self.jobs)
        self.first_done=[None]*len(self.jobs)
        self.dispatch_counts=[0]*len(self.jobs)

    def pop(self):
        task=super().pop()
        if task is not None:
            j,k,stage,_=task
            if (k,stage)==(0,0):
                self.dispatch_counts[j]+=1
                if self.dispatch_counts[j]==2:
                    self.second[j]=self.clock-self.jobs[j][0]
        return task

    def complete(self,task,now):
        j,k,stage,_=task
        if (k,stage)==(0,2):
            self.first_done[j]=now-self.jobs[j][0]
        super().complete(task,now)


def trace(spec):
    family,seed=spec
    jobs=workload(family,seed,256)
    profile=json.loads((BENCH/'profile.json').read_text())
    by_arm={}
    for arm in (INC,NEW):
        instances=[]
        def factory(*args):
            q=Timeline(*args)
            instances.append(q)
            return q
        data=replay(jobs,family,profile,arm,queue_factory=factory)
        q=instances[0]
        timeline=np.array([q.first_wait,q.second,q.first_done,data['latency']]).T
        segments=np.diff(timeline,axis=1,prepend=0)
        if segments.min() < -1e-7 or not np.allclose(segments.sum(axis=1),data['latency'],rtol=0,atol=1e-7):
            raise ValueError('Invalid milestone timing')
        by_arm[arm]=segments
    return [{"incumbent_total":float(by_arm[INC][i].sum()),
             "large":len(job[1])>=4,**{a:by_arm[a][i].tolist() for a in (INC,NEW)}}
            for i,job in enumerate(jobs)]


def diagnose():
    specs=[(f,s) for f in FAMILIES for s in PLAN['development']['seeds']]
    with ProcessPoolExecutor(int(os.environ.get('MOSAIC_REPLAY_WORKERS','4'))) as pool:
        rows=[r for group in pool.map(trace,specs) for r in group]
    large=[r for r in rows if r['large']]
    cutoff=float(np.percentile([r['incumbent_total'] for r in large],99))
    tail=[r for r in large if r['incumbent_total']>=cutoff]
    labels=('release_to_first_frame','first_to_second_frame_dispatch','second_frame_to_first_mosaic_finish','first_mosaic_finish_to_final')
    out={'cohort':'large development requests at or above incumbent large-request completion P99; identical IDs in both arms',
         'cutoff_seconds':cutoff,'count':len(tail),'large_count':len(large)}
    for arm in (INC,NEW):
        means=[statistics.mean(r[arm][i] for r in tail) for i in range(4)]
        out[arm]=dict(zip(labels,means))
        out[arm]['total']=sum(means)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps(diagnose(),indent=2)+'\n')
