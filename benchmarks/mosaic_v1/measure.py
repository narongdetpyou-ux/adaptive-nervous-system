"""Raw request latency evidence plus pooled gates. Uses independent streams per process."""
import argparse
import concurrent.futures as cf
import gzip
import hashlib
import json
import os
from pathlib import Path
import numpy as np
from make_manifest import ROOT,P,FAMILIES
from queue_engine import replay

RESULT=ROOT/'results'
MANIFEST_SHA='df66565441bb4ee19e853c7500782d65dbba844d1cc3b2adf05afe1d7f87c23a'
def workloads():
    data=(ROOT/'workloads.json.gz').read_bytes()
    if hashlib.sha256(data).hexdigest()!=MANIFEST_SHA:raise ValueError('workload checksum mismatch')
    return json.loads(gzip.decompress(data))

def compute(args):
    key,jobs,arm,profile=args
    return key,replay(jobs,key.split('/')[1],profile,arm)

def measure(split,arm):
    RESULT.mkdir(exist_ok=True)
    profile=json.loads((ROOT/'profile.json').read_text())
    args=[(key,jobs,arm,profile) for key,jobs in workloads().items() if key.startswith(split+'/')]
    with cf.ProcessPoolExecutor(int(os.environ.get('MOSAIC_REPLAY_WORKERS','4'))) as pool:
        records=dict(pool.map(compute,args))
    out=RESULT/f'{split}-{arm}.json.gz'
    out.write_bytes(gzip.compress(json.dumps(records,separators=(',',':')).encode(),mtime=0))
    print(split,arm,len(records),'streams',flush=True)

def read(split,arm):
    return json.loads(gzip.decompress((RESULT/f'{split}-{arm}.json.gz').read_bytes()))

def metrics(data):
    values=np.array(data)
    return dict(mean=float(values.mean()),p95=float(np.percentile(values,95)),p99=float(np.percentile(values,99)))

def summary(data):
    by={f:[] for f in FAMILIES}
    for k,record in data.items():by[k.split('/')[1]].extend(record['latency'])
    return metrics([v for group in by.values() for v in group]),{f:metrics(vals) for f,vals in by.items()}

def report(split,arm,live=False):
    all_stats={};families={}
    for variant in dict.fromkeys(['fixed4','oldest_ready',arm]):
        records=read(split,variant)
        assert len(records)==len(FAMILIES)*len(P[split]['seeds'])
        assert all(len(v['latency'])==P[split]['requests_per_stream'] and min(v['latency'])>0 for v in records.values())
        all_stats[variant],families[variant]=summary(records)
    gates={}
    def check(label,a,b,limit):gates[label]={'ratio':a/b,'max':limit,'pass':bool(a/b<=limit)}
    g=P['gates']
    for ref,terms in [('fixed4',('mean','p95')),('oldest_ready',('mean','p95'))]:
        for term in terms:
            label=f'replay_{term}_vs_{ref}_max'
            check(label,all_stats[arm][term],all_stats[ref][term],g[label])
    for f in FAMILIES:
        for m in ('mean','p99'):
            check(f'{f}_{m}',families[arm][f][m],families['fixed4'][f][m],g[f'each_family_{m}_vs_fixed4_max'])
    live_stats={}
    if live:
        jobs=workloads()['live']
        profile=json.loads((ROOT/'profile.json').read_text())
        expected=[[j,k,x,*profile[x]['reference']] for j,(_,tiles) in enumerate(jobs) for k,x in enumerate(tiles)]
        for variant in dict.fromkeys(['fixed4',arm]):
            d=json.loads((RESULT/f'live-{variant}.json').read_text())
            assert d['checks']==expected and len(d['latency'])==len(jobs) and min(d['latency'])>0
            live_stats[variant]=metrics(d['latency'])
        for m in ('mean','p95'):
            label=f'live_{m}_vs_fixed4_max'
            check(label,live_stats[arm][m],live_stats['fixed4'][m],g[label])
    failed=[k for k,v in gates.items() if not v['pass']]
    excess=sum(max(0,v['ratio']/v['max']-1) for v in gates.values())
    return dict(arm=arm,split=split,replay=all_stats,families=families,live=live_stats,integrity=bool(live) if live else None,gates=gates,rank=[len(failed),excess,all_stats[arm]['mean'],arm],status=('FAIL' if failed else 'PASS' if live and split=='scoring' else 'REPLAY_GATES_MET'),failed=failed)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['measure','report','live-report'])
    p.add_argument('split',choices=['development','scoring'])
    p.add_argument('arm')
    a=p.parse_args()
    if a.action=='measure':measure(a.split,a.arm)
    else:print(json.dumps(report(a.split,a.arm,a.action=='live-report'),indent=2))
