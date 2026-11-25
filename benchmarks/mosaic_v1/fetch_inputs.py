"""Download the exact 2MASS input catalog pinned alongside the challenge."""
import concurrent.futures as cf
import gzip
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.request import Request,urlopen
from science import DATA,ROOT


def fetch(row):
    path=DATA/row['name']
    for attempt in range(4):
        try:
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']:return
            with urlopen(Request(row['url'],headers={'User-Agent':'ANS research image workload'}),timeout=60) as response:
                body=response.read()
            if body.startswith(b'\x1f\x8b'):body=gzip.decompress(body)
            if hashlib.sha256(body).hexdigest()!=row['sha256']:raise ValueError(row['name'])
            tmp=path.with_suffix('.part')
            tmp.write_bytes(body)
            tmp.replace(path)
            return
        except Exception:
            if attempt==3:raise
            time.sleep(attempt+1)

if __name__=='__main__':
    DATA.mkdir(parents=True,exist_ok=True)
    catalog=(ROOT/'catalog.json').read_bytes()
    with cf.ThreadPoolExecutor(6) as pool:list(pool.map(fetch,json.loads(catalog)))
    (DATA/'catalog.json').write_bytes(catalog)
