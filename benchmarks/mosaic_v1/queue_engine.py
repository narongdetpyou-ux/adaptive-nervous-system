"""A common nonpreemptive dispatcher for virtual time and actual Montage workers."""
import heapq
import math
import os
import sys
from collections import Counter
from pathlib import Path
import numpy as np
from make_manifest import ROOT

sys.path.insert(0, os.environ.get('ANS_SOURCE',str(ROOT.parent.parent)))
from v5_simulation import make_controller
from v4_environment import project_action


class Queue:
    def __init__(self, jobs, frame_counts, arm):
        self.jobs=jobs
        self.frame_counts=frame_counts
        self.arm=arm
        self.ctrl=None if arm in ('fixed4','oldest_ready') else make_controller('v5_full','legacy_instant')
        self.window=None
        self.priority_cap=None
        self.rescue_after=None
        self.running_by_job=Counter()
        if arm.startswith('tail_'):
            _,cap,weight=arm.split('_')
            self.priority_cap=float(cap)
            self.age=float(weight)
            if not math.isfinite(self.priority_cap) or self.priority_cap<=0:
                raise ValueError('priority cap must be positive and finite')
            if not math.isfinite(self.age) or self.age<0:
                raise ValueError('arrival weight must be nonnegative and finite')
            self.window=600.
        elif arm.startswith('rescue_'):
            _,delay,limit=arm.split('_')
            self.rescue_after=float(delay)
            self.rescue_slots=int(limit)
            if not math.isfinite(self.rescue_after) or self.rescue_after<=0 or not 1<=self.rescue_slots<=4:
                raise ValueError('expected a positive waiting time and one to four workers')
            self.window=600.
            self.age=0.03
        elif arm.startswith('size_'):
            _,window,age=arm.split('_')
            self.window=float(window)
            self.age=float(age)
        self.heap=[[],[],[]]
        self.tile={}
        self.frames_left={}
        self.priorities={}
        self.ends={}
        self.next_job=0
        self.first=0
        self.shares=np.array([69.,69.,69.])
        self.busy=[0,0,0]
        self.virtual=[0.,0.,0.]
        self.busy_time=[0.,0.,0.]
        self.waiting=[0.,0.,0.]
        self.flow=[0.,0.,0.]
        self.history=[]
        self.arrivals=0
        self.tick=0
        self.clock=0.
        self.moves=[]
        self.branches=Counter()

    def advance(self,now):
        dt=now-self.clock
        for s in range(3):
            used=self.busy[s]*dt
            self.virtual[s]+=used/self.shares[s]
            self.busy_time[s]+=used
        self.clock=now

    def enqueue(self,stage,j,frame):
        if not self.heap[stage] and not self.busy[stage]:
            active=[self.virtual[s] for s in range(3) if self.busy[s] or self.heap[s]]
            if active:self.virtual[stage]=max(self.virtual[stage],min(active))
        heapq.heappush(self.heap[stage],(self.priorities[j],j,frame))
        self.waiting[stage]+=self.unit(j,stage)

    def unit(self,j,stage):
        if stage==0:return 1./self.frame_counts[self.jobs[j][1][self.tile[j]]]
        return 1.

    def start_tile(self,j,k):
        self.tile[j]=k
        at,tiles=self.jobs[j]
        if self.window is None:self.priorities[j]=j
        else:
            # Only declared dimensions, frame counts and admitted job metadata.
            remaining=sum((0.45+(int(x.rsplit('-',1)[1])/640.)**1.4)*self.frame_counts[x]/6 for x in tiles[k:])
            if self.priority_cap is not None:
                remaining=min(remaining,self.priority_cap)
            self.priorities[j]=(int(at//self.window),round(remaining+self.age*at,6))
        n=self.frame_counts[tiles[k]]
        self.frames_left[j]=n
        for frame in range(n):self.enqueue(0,j,frame)

    def admit(self,now):
        while self.next_job<len(self.jobs) and self.jobs[self.next_job][0]<=now:
            j=self.next_job
            self.next_job+=1
            self.arrivals+=len(self.jobs[j][1])
            self.start_tile(j,0)

    def sample(self):
        q=np.maximum(self.waiting,0.)*256
        if self.ctrl is not None:
            if self.tick:
                self.ctrl.observe(self.tick-1,np.asarray(self.busy_time)*207/240,np.asarray(self.flow)*256,q.copy())
                self.history=(self.history+[q.copy()])[-5:]
            raw,note=self.ctrl.decide(self.tick,q,self.history,self.shares,self.arrivals*256.)
            self.shares+=project_action(self.shares,raw,self.ctrl.environment)
            self.branches[note['branch']]+=1
            if np.any(raw):self.moves.append([self.tick,self.shares.tolist()])
        self.arrivals=0
        self.flow=[0.,0.,0.]
        self.busy_time=[0.,0.,0.]
        self.tick+=1

    def overdue_task(self,eligible):
        # A request has ready work in at most one stage of its current mosaic.
        j=self.first
        if (j>=self.next_job or self.clock-self.jobs[j][0]<self.rescue_after
                or self.running_by_job[j]>=self.rescue_slots):
            return None
        for stage in eligible:
            heap=self.heap[stage]
            best=None
            for index,(_,job,frame) in enumerate(heap):
                if job==j and (best is None or frame<best[0]):
                    best=(frame,index)
            if best is not None:
                frame,index=best
                heap[index]=heap[-1]
                heap.pop()
                if index<len(heap):
                    heapq.heapify(heap)
                return stage,j,frame
        return None

    def pop(self):
        eligible=[s for s in range(3) if self.heap[s]]
        if self.arm=='fixed4':
            eligible=[s for s in eligible if self.heap[s][0][1]==self.first]
        if not eligible:return None
        def rank(s):
            priority,j,_=self.heap[s][0]
            fair=self.virtual[s]+self.busy[s]*0.02/self.shares[s]
            if self.arm=='current_v5':return (fair,j,s)
            if self.window is not None:return (priority,fair,j,s)
            return (j,s)
        override=self.overdue_task(eligible) if self.rescue_after is not None else None
        if override is None:
            stage=min(eligible,key=rank)
            _,j,frame=heapq.heappop(self.heap[stage])
        else:
            stage,j,frame=override
        if self.rescue_after is not None:
            self.running_by_job[j]+=1
        self.waiting[stage]-=self.unit(j,stage)
        self.busy[stage]+=1
        return j,self.tile[j],stage,frame

    def complete(self,task,now):
        j,k,stage,_=task
        self.busy[stage]-=1
        if self.rescue_after is not None:
            self.running_by_job[j]-=1
        self.flow[stage]+=self.unit(j,stage)
        if stage==0:
            self.frames_left[j]-=1
            if self.frames_left[j]==0:self.enqueue(1,j,0)
        elif stage==1:self.enqueue(2,j,0)
        elif k+1<len(self.jobs[j][1]):self.start_tile(j,k+1)
        else:
            self.ends[j]=now
            while self.first in self.ends:self.first+=1

    def times(self):
        if len(self.ends)!=len(self.jobs):raise RuntimeError('Incomplete stream')
        return [round(self.ends[j]-job[0],8) for j,job in enumerate(self.jobs)]


def capacity(family,now):
    return 2 if family in ('capacity_dip','compound') and 35<=now%180<95 else 4


def service(profile,name,stage,frame,family,now):
    p=profile[name]
    seconds=p['frames'][frame] if stage==0 else p['background' if stage==1 else 'finish']
    phase=now%180
    if family=='project_slow' and stage==0 and phase<75:seconds*=3
    if family=='background_slow' and stage==1 and 50<=phase<135:seconds*=5
    if family=='compound' and stage==int(phase//60):seconds*=(3,5,4)[stage]
    return seconds


def replay(jobs,family,profile,arm):
    q=Queue(jobs,{k:len(v['frames']) for k,v in profile.items()},arm)
    events=[]
    now=0.
    while len(q.ends)<len(jobs):
        q.advance(now)
        while events and events[0][0]<=now:
            _,task=heapq.heappop(events)
            q.complete(task,now)
        q.admit(now)
        if now>=60*q.tick:q.sample()
        while len(events)<capacity(family,now):
            task=q.pop()
            if task is None:break
            j,k,s,i=task
            due=now+service(profile,jobs[j][1][k],s,i,family,now)
            heapq.heappush(events,(due,task))
        if len(q.ends)==len(jobs):break
        next_times=[60*q.tick]
        if events:next_times.append(events[0][0])
        if q.next_job<len(jobs):next_times.append(jobs[q.next_job][0])
        if family in ('capacity_dip','compound'):
            phase=now%180
            next_times.append(now-phase+(35 if phase<35 else 95 if phase<95 else 215))
        now=min(next_times)
    return {'latency':q.times(),'moves':q.moves,'branches':dict(q.branches)}
