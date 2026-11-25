"""Recalculate percentiles, gate decisions and audit checks from per-request records."""
from collections import defaultdict
import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics

HERE=Path(__file__).resolve().parent
BENCH=HERE.parent
PLAN=json.loads((HERE/'plan.json').read_text())
INC=PLAN['incumbent']
NEW=PLAN['candidate']


def load(path):
    raw=path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix=='.gz' else raw)


def stats(values):
    ordered=sorted(values)
    if not ordered or any(not math.isfinite(x) or x<0 for x in ordered):
        raise ValueError('Missing or invalid observation')
    def percentile(p):
        pos=(len(ordered)-1)*p
        i=int(pos)
        return ordered[i]+(pos-i)*(ordered[min(i+1,len(ordered)-1)]-ordered[i])
    return dict(mean=statistics.mean(ordered),p95=percentile(.95),p99=percentile(.99))


def summarize(rows,jobs):
    if set(jobs)!=set(rows):
        raise ValueError('Stream keys do not match')
    times=defaultdict(list);waiting=defaultdict(list)
    by_family=defaultdict(list)
    grants=changes=0
    for key,records in rows.items():
        requests=jobs[key]
        start=records['first_wait'];end=records['latency']
        grant=records['eligible_grants'];change=records['changed_dispatches']
        if (len(start)!=len(requests) or len(end)!=len(requests) or
            len(set(grant))!=len(grant) or len(set(change))!=len(change) or not set(change)<=set(grant)):
            raise ValueError(f'Incomplete stream or repeated preference: {key}')
        for j in grant:
            if len(requests[j][1])<4 or start[j]<8:
                raise ValueError('Improper first-dispatch grant')
        for j,(_,tiles) in enumerate(requests):
            if start[j]<0 or end[j]<=0 or start[j]>end[j]:
                raise ValueError('Invalid request timeline')
            for group in ('all','large' if len(tiles)>=4 else 'small'):
                times[group].append(end[j]);waiting[group].append(start[j])
            by_family[key.split('/')[1] if '/' in key else 'live'].append(end[j])
        grants+=len(grant);changes+=len(change)
    return dict(groups={g:dict(n=len(times[g]),first=stats(waiting[g]),final=stats(times[g])) for g in times},
                families={f:stats(v) for f,v in by_family.items()},eligible_grants=grants,changed_dispatches=changes)


def baseline(rows):
    groups=defaultdict(list)
    for key,r in rows.items():
        groups[key.split('/')[1]].extend(r['latency'])
    return stats([v for row in groups.values() for v in row]),{k:stats(v) for k,v in groups.items()}


def gates(path,result,arm):
    spec=load(BENCH/'challenge.json')['gates']
    report=load(path/f'gates-{arm}.json')
    scored=result['scoring'][arm]
    fixed, families=baseline(load(BENCH/'results/scoring-fixed4.json.gz'))
    oldest,_=baseline(load(BENCH/'results/scoring-oldest_ready.json.gz'))
    checks={}
    def add(name,a,b,limit):
        ratio=a/b
        r=report['gates'][name]
        if not math.isclose(ratio,r['ratio'],rel_tol=1e-10) or limit!=r['max'] or r['pass']!=(ratio<=limit):
            raise ValueError(f'Gate discrepancy: {name}')
        checks[name]=ratio<=limit
    for label,reference in [('fixed4',fixed),('oldest_ready',oldest)]:
        for m in ('mean','p95'):
            k=f'replay_{m}_vs_{label}_max'
            add(k,scored['groups']['all']['final'][m],reference[m],spec[k])
    for f,s in scored['families'].items():
        for m in ('mean','p99'):
            add(f'{f}_{m}',s[m],families[f][m],spec[f'each_family_{m}_vs_fixed4_max'])
    for m in ('mean','p95'):
        k=f'live_{m}_vs_fixed4_max'
        add(k,result['live'][arm]['groups']['all']['final'][m],result['live']['fixed4']['groups']['all']['final'][m],spec[k])
    if len(checks)!=22 or set(checks)!=set(report['gates']) or not report['integrity']:
        raise ValueError('Missing gate or integrity evidence')
    return sum(checks.values())


def build(path,include_live):
    import sys
    sys.path.insert(0,str(BENCH))
    from make_manifest import FAMILIES,workload
    pins={'workloads.json.gz':'df66565441bb4ee19e853c7500782d65dbba844d1cc3b2adf05afe1d7f87c23a',
          'profile.json':'0b14aeee08d9327bbb5d65cc9b1c469b9ecafc2f3c1f88a1a2ae942cf8760e99',
          'challenge.json':'d6bf167186a8c73d88f8e460d47a66acd4ba91485e2f86cbdf3b76fb7b84f10d'}
    for name,digest in pins.items():
        if hashlib.sha256((BENCH/name).read_bytes()).hexdigest()!=digest:
            raise ValueError(f'Changed benchmark input: {name}')
    manifest=load(BENCH/'workloads.json.gz')
    official={k:v for k,v in manifest.items() if k.startswith('scoring/')}
    dev={f'development/{f}/{s}':workload(f,s,256) for f in FAMILIES for s in PLAN['development']['seeds']}
    out={}
    for name,jobs in [('development',dev),('scoring',official)]:
        out[name]={}
        for arm in (INC,NEW):
            records=load(path/f'{name}-{arm}.json.gz')
            if name=='scoring' and arm==INC:
                original=load(BENCH/'results'/f'scoring-{arm}.json.gz')
                if not all(all(records[k][field]==v for field,v in r.items()) for k,r in original.items()):
                    raise ValueError('Incumbent differs from recorded reference')
            out[name][arm]=summarize(records,jobs)
    if include_live:
        profile=load(BENCH/'profile.json');jobs=manifest['live']
        want=[[j,k,n,*profile[n]['reference']] for j,(_,tiles) in enumerate(jobs) for k,n in enumerate(tiles)]
        cpus=[]
        out['live']={}
        for arm in ('fixed4',INC,NEW):
            d=load(path/f'live-{arm}.json');start=load(path/f'start-live-{arm}.json')
            if d['checks']!=want or len(want)!=120 or len(jobs)!=48 or d['arm']!=arm:
                raise ValueError('Live science or completion mismatch')
            cpus.append(d['cpus'])
            out['live'][arm]=summarize({'live':{**d,**start}},{'live':jobs})
        if len(set(cpus[0]))!=4 or not all(x==cpus[0] for x in cpus):
            raise ValueError('CPU sets differ')
        out['live_cpus']=cpus[0]
        out['timing_checks_passed']={a:gates(path,out,a) for a in (INC,NEW)}
        a,b=[out['scoring'][arm]['groups'] for arm in (INC,NEW)]
        adopt=(out['timing_checks_passed'][NEW]==22 and b['large']['first']['p99']<a['large']['first']['p99']
               and b['all']['final']['p99']<a['all']['final']['p99']
               and all(b['all']['final'][m]<=1.10*a['all']['final'][m] for m in ('mean','p95')))
        out['decision']='recommend_candidate' if adopt else 'retain_incumbent'
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('path',type=Path)
    p.add_argument('--replay-only',action='store_true')
    args=p.parse_args()
    print(json.dumps(build(args.path,not args.replay_only),indent=2,allow_nan=False))
