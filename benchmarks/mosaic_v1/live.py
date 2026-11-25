"""Run all 48 scheduled requests, compare every pair of FITS files to its reference."""
import concurrent.futures as cf
import gzip
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from queue_engine import Queue
from make_manifest import ROOT
import science


def run(arm, result_dir=None):
    science.init_cpu()
    profile=json.loads((ROOT/'profile.json').read_text())
    jobs=json.loads(gzip.decompress((ROOT/'workloads.json.gz').read_bytes()))['live']
    counts={k:len(v['frames']) for k,v in profile.items()}
    q=Queue(jobs,counts,arm)
    checks=[]
    futures={}
    with tempfile.TemporaryDirectory(dir=science.RUNS) as raw,cf.ThreadPoolExecutor(4) as pool:
        root=Path(raw)
        started=time.monotonic()
        def execute(task):
            j,k,s,i=task
            name=jobs[j][1][k]
            path=root/f'{j}-{k}'
            if s==0:science.project(name,path,i)
            elif s==1:science.background(name,path)
            else:
                hashes=science.finish(name,path)
                if hashes!=profile[name]['reference']:raise RuntimeError(f'FITS mismatch: {task} {hashes}')
                shutil.rmtree(path)
                return [j,k,name,*hashes]
        while len(q.ends)<len(jobs):
            now=time.monotonic()-started
            q.advance(now)
            for fut in tuple(futures):
                if fut.done():
                    check=fut.result()
                    if check:checks.append(check)
                    q.complete(futures.pop(fut),now)
            before=q.next_job
            q.admit(now)
            if before!=q.next_job:print(f'{arm} t={now:.1f}s released={q.next_job} done={len(q.ends)}',flush=True)
            if now>=60*q.tick:q.sample()
            while len(futures)<4:
                task=q.pop()
                if task is None:break
                j,k,s,i=task
                if s==0 and i==0:science.make_work(root/f'{j}-{k}')
                futures[pool.submit(execute,task)]=task
            if len(q.ends)==len(jobs):break
            wake=60*q.tick
            if q.next_job<len(jobs):wake=min(wake,jobs[q.next_job][0])
            delay=max(0,wake-(time.monotonic()-started))
            if futures:cf.wait(futures,timeout=delay,return_when=cf.FIRST_COMPLETED)
            else:time.sleep(delay)
    assert len(checks)==sum(len(tiles) for _,tiles in jobs)
    result={'arm':arm,'latency':q.times(),'checks':sorted(checks),'moves':q.moves,'branches':dict(q.branches),'cpus':sorted(os.sched_getaffinity(0))}
    output=ROOT/'results' if result_dir is None else Path(result_dir)
    output.mkdir(parents=True,exist_ok=True)
    (output/f'live-{arm}.json').write_text(json.dumps(result,separators=(',',':'))+'\n')
    print(arm,'complete',len(checks),flush=True)

if __name__=='__main__':run(sys.argv[1])
