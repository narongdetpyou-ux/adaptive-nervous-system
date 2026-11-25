"""Run v0.3's three separate transport regimes using the original Neuron Flow."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import platform
import time

import numpy as np
from neuron_flow import Runtime,AgentDefinition
from capacity_agents import atomic_bytes,write_json,sha
from audit_capacity import audit_source_integrity
from audit_v3 import require
from v3_policy import V3Config
from v3_scenarios import REGIMES,POLICIES,policy_seeds,scenario_manifest,generate_scenarios,expected_counts
from v3_agents import (ROOT,step_name,all_rows,validate_evaluation,validate_comparison,validate_noop)


def agents():
    return [
        AgentDefinition('v3_evaluate','v3_agents:evaluate_agent',validate_evaluation,version='3'),
        AgentDefinition('v3_compare','v3_agents:compare_agent',validate_comparison,version='3'),
        AgentDefinition('v3_noop','v3_agents:noop_agent',validate_noop,version='3'),
    ]


def main():
    from audit_v3 import audit_rows
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split',choices=('development','validation'),default='development')
    parser.add_argument('--out',type=Path,required=True,help='New or empty output directory')
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty output directory; existing experiment evidence is preserved')
    output.mkdir(parents=True,exist_ok=True)
    frozen = None
    config = V3Config()
    if args.split=='validation':
        from freeze_v3 import verify_frozen
        frozen = verify_frozen()
        protocol = json.loads((ROOT/'v3_protocol.json').read_text())
        config = V3Config(**protocol['controller_configuration'])
    upstream = audit_source_integrity(ROOT)
    manifest = scenario_manifest(args.split)
    write_json(output/'scenario_manifest.json',manifest)
    inputs = {'split':args.split,'out':str(output),'controller_configuration':config.as_dict(),
              'freeze_verification':frozen,'scenario_manifest_sha256':manifest['manifest_sha256']}
    names = [step_name(regime,policy) for regime in REGIMES for policy in POLICIES]
    spec = {
        'job_id':f'v3_{args.split}','input':inputs,
        'steps':[
            {'id':step_name(regime,policy),'agent':'v3_evaluate',
             'params':{'regime':regime,'policy':policy,'inject_retry':regime=='instant' and policy=='v3_full'},
             'timeout_s':600,'max_attempts':2}
            for regime in REGIMES for policy in POLICIES
        ]+[{'id':'comparison','agent':'v3_compare','depends_on':names,'timeout_s':600,'max_attempts':1}],
    }
    workspace = output/'workspace'
    expected_attempts = {name:2 if name=='instant__v3_full' else 1 for name in names}
    expected_attempts['comparison'] = 1
    started = time.perf_counter()
    print(json.dumps({'split':args.split,'scope':expected_counts(args.split),'workers':3}),flush=True)
    with Runtime(workspace,agents=agents(),max_workers=3) as runtime:
        submitted = runtime.submit(spec)
        require(submitted['created'] is True,'Fresh workspace unexpectedly reused a job')
        state = runtime.run(spec['job_id'])
        if state['status']!='SUCCEEDED':
            write_json(output/'failed_workflow.json',state)
            raise RuntimeError('v0.3 workflow failed; inspect failed_workflow.json')
        attempts = {key:state['steps'][key]['attempts'] for key in expected_attempts}
        require(attempts==expected_attempts,'Unexpected step attempts')
        report = Path(state['receipt']['path'])
        before = (sha(report),report.stat().st_mtime_ns)
        events = runtime.synapse.events_for('monitor',spec['job_id'])
    with Runtime(workspace,agents=agents(),max_workers=3) as runtime:
        duplicate = runtime.submit(spec)
        require(duplicate['created'] is False,'Duplicate submission created a new job')
        reused = runtime.run(spec['job_id'])
        require(reused['status']=='SUCCEEDED','Completed workflow failed to resume')
        require({key:reused['steps'][key]['attempts'] for key in attempts}==attempts,'Restart reran a completed step')
        require((sha(report),report.stat().st_mtime_ns)==before,'Restart altered the report')
        negative = {
            'job_id':f'v3_negative_{args.split}','input':inputs,'steps':[
                {'id':'bad','agent':'v3_evaluate','params':{
                    'regime':'instant','policy':'static','reuse_original':True},
                 'timeout_s':120,'max_attempts':1},
                {'id':'blocked','agent':'v3_noop','depends_on':['bad']},
                {'id':'independent','agent':'v3_noop'},
            ],
        }
        runtime.submit(negative)
        neg = runtime.run(negative['job_id'])
        neg_status = {name:step['status'] for name,step in neg['steps'].items()}
        require(neg['status']=='FAILED' and neg_status=={
            'bad':'FAILED','blocked':'BLOCKED','independent':'SUCCEEDED'},'Negative-control rejection/isolation failed')
        require(neg['steps']['bad']['attempts']==1,'Invalid result was unexpectedly retried')
    dependencies = {name:state['steps'][name]['output'] for name in names}
    rows = all_rows(output,dependencies)
    row_audit = audit_rows(rows,generate_scenarios(args.split),{p:policy_seeds(p) for p in POLICIES},regimes=REGIMES)
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer,fieldnames=list(rows[0]))
    writer.writeheader();writer.writerows(rows)
    atomic_bytes(output/'episodes.csv',buffer.getvalue().encode('utf-8'))
    trace_index = {name:result['traces'] for name,result in dependencies.items()}
    write_json(output/'representative_traces_index.json',trace_index)
    write_json(output/'workflow_events.json',events)
    compare = state['steps']['comparison']['output']
    require(sha(output/'summary.json')==compare['summary']['sha256'],'Final summary changed')
    require(audit_source_integrity(ROOT)==upstream,'Upstream source changed during experiment')
    if args.split=='validation':
        require(verify_frozen()==frozen,'Frozen implementation changed during validation')
    verification = {
        'version':'0.3.0','split':args.split,'status':state['status'],
        'steps':attempts,'peak_workers':state['peak_workers'],
        'receipt_path':str(report.relative_to(output)),'receipt_sha256':before[0],
        'receipt_bytes':report.stat().st_size,'summary_sha256':compare['summary']['sha256'],
        'artifact_exchange':'descriptors only; complete rows and traces checksum-verified from atomic local files',
        'duplicate_submission_created':duplicate['created'],
        'restart_replay_preserved_attempts':True,'restart_replay_preserved_report':True,
        'negative_control':{'status':neg['status'],'steps':neg_status,'bad_error':neg['steps']['bad']['error'],
            'scope':'completed-work count deliberately increased by one in a separate copy of static instant rows; no additional rollout'},
        'row_audit':row_audit,
        'independent_trace_replay':{
            'traces':sum(info['count'] for info in trace_index.values()),
            'minutes':sum(info['minutes'] for info in trace_index.values()),
            'selection':'first scenario seed per family and first policy seed in each regime; selected before outcomes',
            'scope':'independent scalar resource-transit and service replay; all episodes also have inline and summary invariants'},
        'independent_statistics_check':'all point means, paired mean differences, wins/losses and static-relative harm counts/magnitudes; confidence intervals computed by the separately tested frozen statistical module',
        'expected_scope':expected_counts(args.split),'upstream_sha256':upstream,
        'freeze_verification':frozen,'scenario_manifest_sha256':manifest['manifest_sha256'],
        'controller_configuration':config.as_dict(),
        'python':platform.python_version(),'numpy':np.__version__,
        'elapsed_seconds':round(time.perf_counter()-started,3),
        'timing_scope':'local experiment wall time, not a runtime performance benchmark',
    }
    write_json(output/'verification.json',verification)
    print(json.dumps({'status':state['status'],'split':args.split,**compare['overview'],
                      'independent_trace_replay':verification['independent_trace_replay']},indent=2),flush=True)


if __name__=='__main__':
    main()
