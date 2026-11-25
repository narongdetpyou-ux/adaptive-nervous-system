"""Decompose latency on the long development set after selection. This never chooses a policy."""
import json
from collections import Counter
from pathlib import Path
import sys
import numpy as np
from study import STUDY,BENCH,INCUMBENT,StudyQueue,write
from make_manifest import workload,FAMILIES
from queue_engine import replay


class ObservedQueue(StudyQueue):
    def __init__(self,*args):
        super().__init__(*args)
        self.ready_at={}
        self.first_start={}
        self.first_tile_wait=np.zeros(len(self.jobs))
        self.stage_seconds=np.zeros((len(self.jobs),3,2))

    def enqueue(self,stage,j,frame):
        self.ready_at.setdefault((j,self.tile[j],stage),self.clock)
        super().enqueue(stage,j,frame)

    def pop(self):
        task=super().pop()
        if task is not None:
            self.first_start.setdefault(task[:3],self.clock)
        return task

    def complete(self,task,now):
        j,k,stage,_=task
        if stage!=0 or self.frames_left[j]==1:
            key=(j,k,stage)
            started=self.first_start.pop(key)
            if k==0 and stage==0:
                self.first_tile_wait[j]=started-self.ready_at[key]
            self.stage_seconds[j,stage]+=(started-self.ready_at.pop(key),now-started)
        super().complete(task,now)


def decompose(jobs,family,profile,arm):
    instances=[]
    def observed(*args):
        instance=ObservedQueue(*args)
        instances.append(instance)
        return instance
    data=replay(jobs,family,profile,arm,queue_factory=observed)
    times=instances[0].stage_seconds
    latency=np.array(data['latency'])
    np.testing.assert_allclose(times.sum(axis=(1,2)),latency,rtol=0,atol=1e-6)
    return latency,times,instances[0].first_tile_wait


def main(output):
    plan=json.loads((STUDY/'rescue_plan.json').read_text())
    profile=json.loads((BENCH/'profile.json').read_text())
    rows=[];seconds=[];latencies=[];first_wait=[]
    for family in FAMILIES:
        for seed in plan['development']['seeds']:
            jobs=workload(family,seed,plan['development']['requests_per_stream'])
            latency,times,initial_wait=decompose(jobs,family,profile,INCUMBENT)
            rows.extend((family,len(t)) for _,t in jobs)
            first_wait.extend(initial_wait)
            seconds.extend(times)
            latencies.extend(latency)
    latency=np.array(latencies)
    cutoff=float(np.percentile(latency,99))
    mask=latency>=cutoff
    subset=[row for row,keep in zip(rows,mask) if keep]
    avg=np.asarray(seconds)[mask].mean(axis=0)
    write(output,dict(cohort='incumbent long development jobs at or above pooled P99',total=len(rows),
             mean_initial_projection_wait=float(np.asarray(first_wait)[mask].mean()),
             cutoff_seconds=cutoff,tail_count=len(subset),mean_tail_seconds=float(latency[mask].mean()),
             families=dict(Counter(f for f,_ in subset)),tiles=dict(Counter(n for _,n in subset)),
             stages={name:{'ready_to_first_start':a,'first_start_to_last_completion':b}
                     for name,(a,b) in zip(('projection','background','coadd'),avg)}))


if __name__=='__main__':
    main(Path(sys.argv[1]))
