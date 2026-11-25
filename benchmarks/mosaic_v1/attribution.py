"""Secondary paired replay: preserve the selected ordering, hold stage shares at 69 each."""
import gzip
import json
import os
from pathlib import Path
import numpy as np
import queue_engine
import measure

if __name__=='__main__':
    winner=json.loads((measure.RESULT/'selection.json').read_text())['winner']
    # This diagnostic changes the controller hook only, in a separate process.
    queue_engine.make_controller=lambda *args:None
    import concurrent.futures as cf
    jobs=measure.workloads()
    profile=json.loads((measure.ROOT/'profile.json').read_text())
    result={}
    for arm in ('current_v5',winner):
        args=[(k,v,arm,profile) for k,v in jobs.items() if k.startswith('scoring/')]
        with cf.ProcessPoolExecutor(4) as pool:static=dict(pool.map(measure.compute,args))
        (measure.RESULT/f'diagnostic-equal-shares-{arm}.json.gz').write_bytes(gzip.compress(json.dumps(static,separators=(',',':')).encode(),mtime=0))
        native=measure.read('scoring',arm)
        a=np.concatenate([native[k]['latency'] for k in sorted(native)])
        b=np.concatenate([static[k]['latency'] for k in sorted(native)])
        result[arm]={'with_v5':measure.metrics(a),'equal_shares':measure.metrics(b),'identical_request_times':int(np.sum(a==b)),'requests':len(a),'streams_with_v5_moves':sum(bool(r['moves']) for r in native.values()),'total_native_v5_moves':sum(len(r['moves']) for r in native.values())}
    (measure.RESULT/'attribution.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
