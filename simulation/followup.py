"""Post-primary exploratory persistent-backlog guard, frozen before this run."""
from simulation import ROOT,load_trace,scenarios,simulate,ABC_SEEDS,sha,verify,H,GROUPS
from collections import defaultdict
from statistics import mean
import csv,json,argparse

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',default=str(ROOT/'results/followup'));args=ap.parse_args()
    from pathlib import Path
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    proto=ROOT/'followup_protocol.json'; assert sha(proto)==(ROOT/'followup_protocol.sha256').read_text().split()[0]
    p=json.loads(proto.read_text()); trace=load_trace(ROOT/'data/load_test_summary_per_minute.csv');verify()
    rows=[]
    for g,s,a,prod in scenarios(trace,p['scenario_seeds']):
        for label in p['policy_comparison']:
            for ps in ABC_SEEDS if label in ('abc','abc_guard') else [None]:
                row,_=simulate('abc' if label=='abc_guard' else label,g,s,ps,a,prod,persistent_guard=label=='abc_guard')
                row['policy']=label;rows.append(row)
    d=defaultdict(list)
    for r in rows: d[r['group'],r['scenario_seed'],r['policy']].append(r)
    labels=p['policy_comparison']; metrics=['mean_backlog','peak_backlog','end_backlog','completed','moves']
    cases=[{label:{m:mean(r[m] for r in d[g,s,label]) for m in metrics} for label in labels} for g in GROUPS for s in p['scenario_seeds']]
    overall={l:{m:mean(c[l][m] for c in cases) for m in metrics} for l in labels}
    vs={}
    for l in ('static','maxweight','abc'):
        diff=[c[l]['mean_backlog']-c['abc_guard']['mean_backlog'] for c in cases]
        vs[l]={'wins':sum(x>1e-6 for x in diff),'ties':sum(abs(x)<=1e-6 for x in diff),'losses':sum(x<-1e-6 for x in diff), 'reduction_pct':100*(overall[l]['mean_backlog']-overall['abc_guard']['mean_backlog'])/overall[l]['mean_backlog']}
    res={'scenarios':len(cases),'episodes':len(rows),'minutes':len(rows)*H,'overall':overall,'guard_vs':vs,
        'max_mass_error':max(r['max_mass_error'] for r in rows),'max_budget_error':max(r['max_budget_error'] for r in rows),'protocol_sha256':sha(proto),'frozen_hashes':verify()}
    (out/'summary.json').write_text(json.dumps(res,indent=2)+'\n')
    with (out/'episodes.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    print(json.dumps(res,indent=2))
if __name__=='__main__':main()
