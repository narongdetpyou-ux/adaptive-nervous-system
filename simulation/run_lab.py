"""Run the real Neuron Flow runtime with the real ABC core; no external services."""
import argparse,csv,json,hashlib,sys,time
from pathlib import Path
from neuron_flow import Runtime,AgentDefinition
from simulation import ROOT,load_trace,sha,verify,PROTOCOL,POLICIES
from lab_agents import validate_evaluation,validate_comparison,validate_noop

def write_json(path,value): path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
def agents():
    return [AgentDefinition('evaluate','lab_agents:evaluate_agent',validate_evaluation,version='1'),
            AgentDefinition('compare','lab_agents:compare_agent',validate_comparison,version='1'),
            AgentDefinition('noop','lab_agents:noop_agent',validate_noop,version='1')]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=ROOT/'results');a=parser.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=True); workspace=out/'workspace'
    trace_file=ROOT/'data/load_test_summary_per_minute.csv'; trace=load_trace(trace_file)
    protocol_hash=sha(ROOT/'protocol.json'); frozen=verify()
    if protocol_hash!=(ROOT/'protocol.sha256').read_text().split()[0]: raise ValueError('Protocol changed')
    spec={'job_id':'flash_sale_v010','input':{'trace':trace,'protocol_sha256':protocol_hash,'source_sha256':sha(trace_file)},
          'steps':[{'id':p,'agent':'evaluate','params':{'policy':p,'inject_retry':p=='abc'},'timeout_s':120,'max_attempts':2} for p in POLICIES]+[
          {'id':'comparison','agent':'compare','depends_on':POLICIES,'timeout_s':30}]}
    start=time.perf_counter()
    with Runtime(workspace,agents=agents(),max_workers=3) as rt:
        receipt=rt.submit(spec); print('Submission:',receipt,flush=True)
        state=rt.run(spec['job_id']); print('Workflow status:',state['status'],flush=True)
        if state['status']!='SUCCEEDED': raise RuntimeError(json.dumps(state,default=str)[:8000])
        report=Path(state['receipt']['path']); before=(sha(report),report.stat().st_mtime_ns)
        attempts={p:state['steps'][p]['attempts'] for p in [*POLICIES,'comparison']}
        assert attempts=={'static':1,'maxweight':1,'abc':2,'comparison':1}, attempts
        events=rt.synapse.events_for('monitor',spec['job_id'])
    with Runtime(workspace,agents=agents(),max_workers=3) as rt:
        duplicate=rt.submit(spec); assert duplicate['created'] is False
        resumed=rt.run(spec['job_id']); assert resumed['status']=='SUCCEEDED'
        assert (sha(report),report.stat().st_mtime_ns)==before
        assert {p:resumed['steps'][p]['attempts'] for p in attempts}==attempts
        # Deliberate corrupted output must be rejected, dependent blocked, independent branch succeeds.
        negative={'job_id':'negative_control_v010','input':spec['input'],'steps':[
            {'id':'bad','agent':'evaluate','params':{'policy':'static','corrupt':True},'timeout_s':120},
            {'id':'blocked','agent':'noop','depends_on':['bad']},{'id':'independent','agent':'noop'}]}
        rt.submit(negative); neg=rt.run(negative['job_id'])
        assert neg['status']=='FAILED'
        assert {k:v['status'] for k,v in neg['steps'].items()}=={'bad':'FAILED','blocked':'BLOCKED','independent':'SUCCEEDED'}
        assert neg['steps']['bad']['attempts']==1
    summary=state['steps']['comparison']['output']
    write_json(out/'summary.json',summary)
    rows=[r for p in POLICIES for r in state['steps'][p]['output']['rows']]
    with (out/'episodes.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    traces=[r for p in POLICIES for r in state['steps'][p]['output']['representative']]
    write_json(out/'representative_traces.json',traces)
    write_json(out/'workflow_events.json',events)
    receipt_info={'status':state['status'],'steps':attempts,'peak_workers':state['peak_workers'],
        'receipt_sha256':before[0],'receipt_bytes':report.stat().st_size,'duplicate_submission_created':duplicate['created'],
        'restart_replay_preserved_attempts':True,'restart_replay_preserved_report':True,
        'negative_control':{'status':neg['status'],'steps':{k:v['status'] for k,v in neg['steps'].items()},'bad_error':neg['steps']['bad']['error']},
        'frozen_hashes':frozen,'trace_sha256':sha(trace_file),'protocol_sha256':protocol_hash,'python':sys.version,'elapsed_seconds':round(time.perf_counter()-start,3)}
    write_json(out/'verification.json',receipt_info)
    print(json.dumps({k:v for k,v in summary.items() if k!='case_results'},indent=2));print(json.dumps(receipt_info,indent=2))

if __name__=='__main__': main()
