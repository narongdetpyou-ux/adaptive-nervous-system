"""Audit recorded evidence without NumPy, V5 or Montage. Operates on repository files."""
from collections import defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import zipfile

STUDY=Path(__file__).resolve().parent
BENCH=STUDY.parent
INC='size_600_0.03'
CHALLENGER='tail_18_0.16'


def load(path):
    b=path.read_bytes()
    return json.loads(gzip.decompress(b) if path.suffix=='.gz' else b)


def stats(values):
    ordered=sorted(values)
    assert ordered and all(math.isfinite(v) and v>0 for v in ordered)
    def percentile(q):
        position=(len(ordered)-1)*q
        i=int(position)
        return ordered[i]+(position-i)*(ordered[min(i+1,len(ordered)-1)]-ordered[i])
    return dict(mean=statistics.mean(ordered),p95=percentile(.95),p99=percentile(.99))


def compare(a,b):
    for key,value in a.items():
        assert math.isclose(value,b[key],rel_tol=1e-10,abs_tol=1e-9),(key,value,b[key])


def replay_stats(records,long=False):
    groups=defaultdict(list)
    for key,record in records.items():
        family=key.split('/')[1]
        values=record if long else record['latency']
        groups[family].extend(values)
    return stats([v for values in groups.values() for v in values]),{f:stats(v) for f,v in groups.items()}


def timing_ratios(summary,fixed,oldest):
    overall,families=summary
    ratio={}
    for name,(r,_) in (('fixed4',fixed),('oldest_ready',oldest)):
        for term in ('mean','p95'):
            ratio[f'replay_{term}_vs_{name}_max']=overall[term]/r[term]
    for family,metrics in families.items():
        for term in ('mean','p99'):
            ratio[f'{family}_{term}']=metrics[term]/fixed[1][family][term]
    return ratio


def bound(gates,key):
    if key in gates:return gates[key]
    term=key.rsplit('_',1)[1]
    return gates[f'each_family_{term}_vs_fixed4_max']


def verify(path):
    spec=load(BENCH/'challenge.json')
    gates=spec['gates']
    profile=load(BENCH/'profile.json')
    workloads=load(BENCH/'workloads.json.gz')
    pins={'challenge.json':'d6bf167186a8c73d88f8e460d47a66acd4ba91485e2f86cbdf3b76fb7b84f10d',
          'profile.json':'0b14aeee08d9327bbb5d65cc9b1c469b9ecafc2f3c1f88a1a2ae942cf8760e99',
          'workloads.json.gz':'df66565441bb4ee19e853c7500782d65dbba844d1cc3b2adf05afe1d7f87c23a'}
    for name,digest in pins.items():
        assert hashlib.sha256((BENCH/name).read_bytes()).hexdigest()==digest,name
    index=load(path/'development-index.json') if (path/'development-index.json').exists() else None

    @lru_cache(maxsize=5)
    def dev(name):
        if index is None:
            return load(path/name)
        item=index[name]
        with zipfile.ZipFile(path/item['archive']) as z:raw=z.read(name)
        assert hashlib.sha256(raw).hexdigest()==item['sha256'],name
        d=json.loads(gzip.decompress(raw))
        return d

    short=lambda arm:load(BENCH/'results'/f'development-{arm}.json.gz')
    inc_short=replay_stats(short(INC))[0]
    selection=load(path/'selection.json')
    short_fixed,short_oldest=[replay_stats(short(a)) for a in ('fixed4','oldest_ready')]
    choices=[]
    for row in selection['candidates']:
        arm=row['arm']
        raw=dev(f'development-{arm}.json.gz')
        assert len(raw)==16 and all(len(v['latency'])==96 for v in raw.values())
        summary=replay_stats(raw)
        compare(summary[0],row['stats'])
        ratios=timing_ratios(summary,short_fixed,short_oldest)
        failures=sum(v>bound(gates,k) for k,v in ratios.items())
        excess=sum(max(0,v/bound(gates,k)-1) for k,v in ratios.items())
        ok=not failures and summary[0]['p99']<inc_short['p99'] and all(summary[0][m]<=1.10*inc_short[m] for m in ('mean','p95'))
        assert row['eligible']==ok and row['rank'][0]==failures
        if ok:choices.append((summary[0]['p99'],failures,excess,summary[0]['mean'],arm))
    cutoff=1.02*min(x[0] for x in choices)
    assert min(x[1:] for x in choices if x[0]<=cutoff)[-1]==selection['winner']==CHALLENGER
    long=lambda arm:dev(f'development-long-{arm}.json.gz')
    inc_long=replay_stats(long(INC),True)[0]
    long_fixed,long_oldest=[replay_stats(long(a),True) for a in ('fixed4','oldest_ready')]
    for label,expected_count in [('rescue',21),('tuning',29)]:
        record=load(path/f'{label}-development.json')
        assert len(record['candidates'])==expected_count and record['winner'] is None
        compare(inc_long,record['incumbent'])
        for arm,row in record['candidates'].items():
            d=long(arm)
            assert d.keys()==long(INC).keys() and len(d)==32 and all(len(v)==256 for v in d.values())
            value=replay_stats(d,True)
            compare(value[0],row['stats'])
            ratio=timing_ratios(value,long_fixed,long_oldest)
            passed=all(v<=bound(gates,k) for k,v in ratio.items())
            assert passed==row['replay_checks']
            eligible=passed and value[0]['p99']<inc_long['p99'] and all(value[0][m]<=1.10*inc_long[m] for m in ('mean','p95'))
            assert not eligible and not row['eligible'],arm

    refs={a:replay_stats(load(BENCH/'results'/f'scoring-{a}.json.gz')) for a in ('fixed4','oldest_ready',INC)}
    scoring=load(path/f'scoring-{CHALLENGER}.json.gz')
    keys={k for k in workloads if k.startswith('scoring/')}
    assert keys==scoring.keys() and len(keys)==96 and all(len(scoring[k]['latency'])==len(workloads[k])==256 for k in keys)
    refs[CHALLENGER]=replay_stats(scoring)
    expected=[[j,k,t,*profile[t]['reference']] for j,(_,tiles) in enumerate(workloads['live']) for k,t in enumerate(tiles)]
    live={}
    cpus=[]
    for arm in ('fixed4',INC,CHALLENGER):
        d=load(path/f'live-{arm}.json')
        assert d['checks']==expected and len(expected)==120 and len(d['latency'])==48 and d['arm']==arm
        cpus.append(d['cpus'])
        live[arm]=stats(d['latency'])
    assert len(cpus[0])==4 and cpus[0]==cpus[1]==cpus[2]
    for arm in (INC,CHALLENGER):
        official=load(path/f'official-{arm}.json')
        ratios=timing_ratios(refs[arm],refs['fixed4'],refs['oldest_ready'])
        for m in ('mean','p95'):
            ratios[f'live_{m}_vs_fixed4_max']=live[arm][m]/live['fixed4'][m]
        assert len(ratios)==len(official['gates'])==22
        for k,v in ratios.items():
            check=official['gates'][k]
            compare({'ratio':v,'max':bound(gates,k)},check)
            assert check['pass']==(v<=bound(gates,k))
        failed=[k for k,v in ratios.items() if v>bound(gates,k)]
        assert set(failed)==set(official['failed']) and official['integrity']
        assert official['status']==('FAIL' if failed else 'PASS')
        print(arm,official['status'],22-len(failed),'of 22 timing checks; 120/120 image pairs')
    print('Verified all 70 candidate records, two development sizes, 24,576 scored requests and 360 live mosaic pairs.')


if __name__=='__main__':
    verify(Path(sys.argv[1]) if len(sys.argv)>1 else STUDY/'evidence')
