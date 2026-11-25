"""Opt-in research dispatchers. Normal benchmark runs use the original Queue."""
import heapq
import math
from collections import Counter
from queue_engine import Queue


class StudyQueue(Queue):
    def __init__(self, jobs, frame_counts, arm):
        self.priority_cap=None
        self.rescue_after=None
        self.running=Counter()
        native_arm=arm
        if arm.startswith('tail_'):
            _,cap,weight=arm.split('_')
            self.priority_cap=float(cap)
            if not math.isfinite(self.priority_cap) or self.priority_cap<=0:
                raise ValueError('priority cap must be positive and finite')
            if not math.isfinite(float(weight)) or float(weight)<0:
                raise ValueError('arrival weight must be nonnegative and finite')
            native_arm=f'size_600_{weight}'
        elif arm.startswith('rescue_'):
            _,delay,slots=arm.split('_')
            self.rescue_after=float(delay)
            self.rescue_slots=int(slots)
            if not math.isfinite(self.rescue_after) or self.rescue_after<=0 or not 1<=self.rescue_slots<=4:
                raise ValueError('expected a positive waiting time and one to four workers')
            native_arm='size_600_0.03'
        super().__init__(jobs,frame_counts,native_arm)

    def enqueue(self,stage,j,frame):
        if self.priority_cap is not None and stage==0 and frame==0:
            window,score=self.priorities[j]
            ceiling=round(self.priority_cap+self.age*self.jobs[j][0],6)
            self.priorities[j]=(window,min(score,ceiling))
        super().enqueue(stage,j,frame)

    def overdue_task(self):
        j=self.first
        if (j>=self.next_job or self.clock-self.jobs[j][0]<self.rescue_after
                or self.running[j]>=self.rescue_slots):
            return None
        # An admitted job can have ready work in only one stage of its current tile.
        for stage,heap in enumerate(self.heap):
            matches=((frame,index) for index,(_,job,frame) in enumerate(heap) if job==j)
            best=min(matches,default=None)
            if best is None:
                continue
            frame,index=best
            heap[index]=heap[-1]
            heap.pop()
            if index<len(heap):
                heapq.heapify(heap)
            self.waiting[stage]-=self.unit(j,stage)
            self.busy[stage]+=1
            return j,self.tile[j],stage,frame
        return None

    def pop(self):
        if self.rescue_after is None:
            return super().pop()
        task=self.overdue_task() or super().pop()
        if task is not None:
            self.running[task[0]]+=1
        return task

    def complete(self,task,now):
        if self.rescue_after is not None:
            self.running[task[0]]-=1
        super().complete(task,now)
