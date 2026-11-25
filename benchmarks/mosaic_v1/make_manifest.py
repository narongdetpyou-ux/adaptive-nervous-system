import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parent
P=json.loads((ROOT/'challenge.json').read_text())
FAMILIES=P['families']
PRODUCTS=[f'{field}-{band}-{size}' for field in P['science']['fields'] for band in P['science']['bands'] for size in P['science']['width_pixels']]

def workload(family,seed,count,live=False):
    fi=0 if live else FAMILIES.index(family)
    rng=np.random.default_rng(np.random.SeedSequence([29317,seed,fi,int(live)]))
    jobs=[]
    for i in range(count):
        if live: at=66*(i//8)
        elif family=='flash': at=90*(i//64)
        elif family=='sustained': at=1.35*i
        elif family=='short_pulses': at=(i//16)*27+(i%16)*.12
        elif family=='size_shift': at=(i//32)*50+(i%32)*.32
        elif family=='capacity_dip':at=i*1.3
        elif family=='compound':at=(i//32)*48+(i%32)*.1
        else:at=(i//32)*50+(i%32)*.25
        length=int(rng.choice(P['request_mix']['tiles_per_request']))
        items=[]
        for _ in range(length):
            size_probs=([.72,.22,.06] if (i//32)%2==0 else [.06,.22,.72]) if family in ('size_shift','compound') else [1/3]*3
            si=int(rng.choice(3,p=size_probs));product=PRODUCTS[int(rng.integers(len(PRODUCTS)//3))*3+si]
            items.append(product)
        jobs.append([float(at),items])
    return jobs

def main():
    out={}
    for split in ('development','scoring'):
        for f in FAMILIES:
            for seed in P[split]['seeds']:
                out[f'{split}/{f}/{seed}']=workload(f,seed,P[split]['requests_per_stream'])
    out['live']=workload('live',P['live']['seed'],48,True)
    import gzip
    (ROOT/'workloads.json.gz').write_bytes(gzip.compress(json.dumps(out,separators=(',',':')).encode(),mtime=0))
    print('scoring requests',sum(len(v) for k,v in out.items() if k.startswith('scoring/')),'scoring mosaics',sum(len(tiles) for k,v in out.items() if k.startswith('scoring/') for _,tiles in v),'live mosaics',sum(len(t) for _,t in out['live']))
if __name__=='__main__': main()
