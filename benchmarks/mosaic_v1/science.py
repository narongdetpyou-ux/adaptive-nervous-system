"""Real Montage stages. Only immutable observations and input descriptions are shared."""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from make_manifest import PRODUCTS, ROOT

DATA=Path(os.environ.get('MOSAIC_DATA',ROOT/'data')).resolve()
RUNS=Path(os.environ.get('MOSAIC_SCRATCH',ROOT/'runs')).resolve()
PREP=DATA/'prepared'
CENTERS={'M101':(210.8024,54.3491),'M31':(10.6847,41.2688),'M51':(202.47,47.196),'M33':(23.4621,30.66),'M81':(148.8882,69.0653),'M82':(148.9697,69.6797),'M42':(83.822,-5.391),'M17':(275.2,-16.18)}

def init_cpu():
    os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:4])
    for k in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):os.environ[k]='1'

def call(work,*args):
    p=subprocess.run(args,cwd=work,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    if p.returncode or 'stat="OK"' not in p.stdout or 'stat="ERROR"' in p.stdout:
        raise RuntimeError(f'{work}: {args}: {p.stdout}')
    for k in ('failed','badfits','badwcs','missing','nocorrection'):
        m=re.search(r'\b'+k+r'=(\d+)',p.stdout)
        if m and int(m[1]):raise RuntimeError(f'{work}: {args}: {p.stdout}')
    return p.stdout

def parts(path):
    lines=path.read_text().splitlines(keepends=True)
    return ([s for s in lines if s.strip() and s.lstrip()[0] in '|\\'],
            [s for s in lines if s.strip() and s.lstrip()[0] not in '|\\'])

def canonical(table):
    heads,rows=parts(table)
    rows.sort(key=lambda s:s.split()[-1])
    for i,s in enumerate(rows):
        rows[i]=re.sub(r'^\s*\d+',lambda m:str(i).rjust(len(m[0])),s,count=1)
    table.write_text(''.join(heads+rows))

def prepare():
    catalog=json.loads((DATA/'catalog.json').read_text());PREP.mkdir(exist_ok=True)
    for entry in catalog:
        if hashlib.sha256((DATA/entry['name']).read_bytes()).hexdigest()!=entry['sha256']:raise ValueError(entry['name'])
    for name in PRODUCTS:
        field,band,sz=name.split('-');size=int(sz)
        root=PREP/name;root.mkdir(exist_ok=True);raw=root/'raw';raw.mkdir(exist_ok=True)
        for e in catalog:
            if (e['field'],e['band'])==(field,band):
                dst=raw/e['name']
                if not dst.exists():dst.symlink_to(DATA/e['name'])
        call(root,'mImgtbl','raw','all.tbl')
        canonical(root/'all.tbl')
        heads,rows=parts(root/'all.tbl')
        for i,row in enumerate(rows):(root/f'frame{i}.tbl').write_text(''.join(heads)+row)
        ra,dec=CENTERS[field];step=.20/size
        cards=[('SIMPLE','T'),('BITPIX',-64),('NAXIS',2),('NAXIS1',size),('NAXIS2',size),('CTYPE1',"'RA---TAN'"),('CTYPE2',"'DEC--TAN'"),('CRVAL1',ra),('CRVAL2',dec),('CDELT1',-step),('CDELT2',step),('CRPIX1',(size+1)/2),('CRPIX2',(size+1)/2),('CROTA2',0),('EQUINOX',2000)]
        (root/'template.hdr').write_text(''.join(f'{k:8}= {v}\n' for k,v in cards)+'END\n')

def make_work(path):
    path.mkdir(parents=True,exist_ok=False)
    for x in ('proj','diff','corr','final'):(path/x).mkdir()

def project(name,work,i):
    src=PREP/name;sub=work/f'f{i}';sub.mkdir()
    call(sub,'mProjExec','-p',str(src/'raw'),str(src/f'frame{i}.tbl'),str(src/'template.hdr'),str(work/'proj'),'stats.tbl')

def background(name,work):
    src=PREP/name
    call(work,'mImgtbl','proj','images.tbl');canonical(work/'images.tbl')
    call(work,'mOverlaps','images.tbl','overlaps.tbl')
    call(work,'mDiffExec','-p','proj','overlaps.tbl',str(src/'template.hdr'),'diff')
    call(work,'mFitExec','overlaps.tbl','fits.tbl','diff')
    call(work,'mBgModel','images.tbl','fits.tbl','corr.tbl')
    call(work,'mBgExec','-p','proj','images.tbl','corr.tbl','corr')

def finish(name,work):
    call(work,'mAdd','-p','corr','images.tbl',str(PREP/name/'template.hdr'),'final/m.fits')
    result=[hashlib.sha256((work/'final'/f).read_bytes()).hexdigest() for f in ('m.fits','m_area.fits')]
    return result

def calibrate(name):
    RUNS.mkdir(exist_ok=True)
    n=len(list((PREP/name).glob('frame*.tbl')))
    with tempfile.TemporaryDirectory(dir=RUNS) as tmp:
        work=Path(tmp)/'independent';make_work(work);src=PREP/name
        call(work,'mProjExec','-p',str(src/'raw'),str(src/'all.tbl'),str(src/'template.hdr'),'proj','stats.tbl')
        background(name,work);reference=finish(name,work);shutil.rmtree(work)
        work=Path(tmp)/'profile';make_work(work);cost=[]
        for i in range(n):
            t=time.monotonic();project(name,work,i);cost.append(time.monotonic()-t)
        t=time.monotonic();background(name,work);bg=time.monotonic()-t
        t=time.monotonic();output=finish(name,work);end=time.monotonic()-t
        if output!=reference:raise ValueError(f'Reference mismatch: {name}')
    return name,{'frames':cost,'background':bg,'finish':end,'reference':reference}

if __name__=='__main__':
    init_cpu();prepare()
    out={}
    # Isolated single-core service samples. No policy is evaluated here.
    for name in PRODUCTS:
        k,v=calibrate(name);out[k]=v
        (ROOT/'profile.partial.json').write_text(json.dumps(out,sort_keys=True)+'\n')
        print(k,flush=True)
    (ROOT/'profile.json').write_text(json.dumps(out,sort_keys=True,indent=2)+'\n')
