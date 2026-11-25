"""Independent checks of both official decisions from raw records (Python standard library)."""
from hashlib import sha256
import gzip
import json
from pathlib import Path
from statistics import mean

ROOT=Path(__file__).resolve().parent
RESULT=ROOT/'results'
EXPECTED={'challenge.json':'d6bf167186a8c73d88f8e460d47a66acd4ba91485e2f86cbdf3b76fb7b84f10d',
          'catalog.json':'ed6dd7e6117917411bb90cdb408c775e22a21dcb6c25e907ff9f0de8ae38df89',
          'profile.json':'0b14aeee08d9327bbb5d65cc9b1c469b9ecafc2f3c1f88a1a2ae942cf8760e99',
          'workloads.json.gz':'df66565441bb4ee19e853c7500782d65dbba844d1cc3b2adf05afe1d7f87c23a'}

def unpack(path):
    contents=path.read_bytes()
    return json.loads(gzip.decompress(contents) if path.suffix=='.gz' else contents)

def percentile(values,p):
    s=sorted(values)
    pos=(len(s)-1)*p
    a=int(pos)
    return s[a]+(s[min(a+1,len(s)-1)]-s[a])*(pos-a)

def stats(v):return {'mean':mean(v),'p95':percentile(v,.95),'p99':percentile(v,.99)}

def verify():
    for name,digest in EXPECTED.items():assert sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    spec=unpack(ROOT/'challenge.json')
    tasks=unpack(ROOT/'workloads.json.gz')
    catalog=unpack(ROOT/'catalog.json')
    profile=unpack(ROOT/'profile.json')
    assert len(catalog)==len({r['name'] for r in catalog})==132
    assert len(profile)==72
    for name,p in profile.items():
        f,b,_=name.split('-')
        assert len(p['frames'])==sum(r['field']==f and r['band']==b for r in catalog)
        assert min([*p['frames'],p['background'],p['finish']])>0
    official={k:v for k,v in tasks.items() if k.startswith('scoring/')}
    assert len(official)==96 and sum(len(v) for v in official.values())==24576
    assert sum(len(t) for jobs in official.values() for _,t in jobs)==67994
    live=tasks['live']
    assert len(live)==48 and sum(len(t) for _,t in live)==120
    selection=unpack(RESULT/'selection.json')
    assert all(r['split']=='development' for r in selection['candidates'])
    winner=min(selection['candidates'],key=lambda r:r['rank'])['arm']
    assert winner==selection['winner']
    families={}
    pooled={}
    for arm in ('fixed4','oldest_ready','current_v5',winner):
        raw=unpack(RESULT/f'scoring-{arm}.json.gz')
        assert raw.keys()==official.keys()
        family={f:[] for f in spec['families']}
        for k,record in raw.items():
            v=record['latency']
            assert len(v)==len(official[k]) and min(v)>0
            family[k.split('/')[1]].extend(v)
        families[arm]={f:stats(v) for f,v in family.items()}
        pooled[arm]=stats([x for vs in family.values() for x in vs])
    expected=[[j,k,name,*profile[name]['reference']] for j,(_,ts) in enumerate(live) for k,name in enumerate(ts)]
    actual={}
    for arm in ('fixed4','current_v5',winner):
        data=unpack(RESULT/f'live-{arm}.json')
        assert len(data['latency'])==48 and min(data['latency'])>0
        assert data['arm']==arm and data['checks']==expected
        assert data['cpus']==unpack(RESULT/'live-fixed4.json')['cpus'] and len(set(data['cpus']))==4
        actual[arm]=stats(data['latency'])
    g=spec['gates']
    for arm in ('current_v5',winner):
        ratios={}
        for ref in ('fixed4','oldest_ready'):
            for m in ('mean','p95'):
                key=f'replay_{m}_vs_{ref}_max'
                ratios[key]=(pooled[arm][m]/pooled[ref][m],g[key])
        for f in spec['families']:
            for m in ('mean','p99'):
                ratios[f'{f}_{m}']=(families[arm][f][m]/families['fixed4'][f][m],g[f'each_family_{m}_vs_fixed4_max'])
        for m in ('mean','p95'):
            key=f'live_{m}_vs_fixed4_max'
            ratios[key]=(actual[arm][m]/actual['fixed4'][m],g[key])
        report=unpack(RESULT/f'official-{arm}.json')
        assert len(ratios)==22 and ratios.keys()==report['gates'].keys()
        for key,(ratio,limit) in ratios.items():
            assert abs(ratio-report['gates'][key]['ratio'])<1e-10,key
            assert report['gates'][key]['max']==limit
        failed={k for k,(ratio,limit) in ratios.items() if ratio>limit}
        assert report['integrity'] and failed==set(report['failed'])
        assert report['status']==('FAIL' if failed else 'PASS')
        print(arm,report['status'],len(ratios)-len(failed),'of 22 timing checks; integrity passed')
    print('Verified 132 inputs, 72 profile entries, 96 paired replay streams and 360 live mosaics.')

if __name__=='__main__':verify()
