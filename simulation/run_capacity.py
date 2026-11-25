"""Execute the capacity-aware experiment through the original Neuron Flow runtime."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import platform
import time

import numpy as np
from neuron_flow import AgentDefinition, Runtime
from capacity_policy import CapacityConfig
from capacity_scenarios import POLICIES, policy_seeds, scenario_manifest, generate_scenarios
from capacity_agents import (ROOT, sha, write_json, atomic_bytes,
                             validate_evaluation, validate_comparison, validate_noop)
from audit_capacity import audit_rows, audit_source_integrity, require


def agents():
    return [
        AgentDefinition('capacity_evaluate','capacity_agents:evaluate_agent',validate_evaluation,version='2'),
        AgentDefinition('capacity_compare','capacity_agents:compare_agent',validate_comparison,version='2'),
        AgentDefinition('capacity_noop','capacity_agents:noop_agent',validate_noop,version='2'),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split',choices=('development','validation'),default='development')
    parser.add_argument('--out',type=Path,required=True,help='Fresh output directory; restart reuse is also checked within this run')
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use a new or empty output directory; existing evidence is preserved')
    output.mkdir(parents=True,exist_ok=True)
    source_hashes = audit_source_integrity(ROOT)
    frozen = None
    config = CapacityConfig()
    if args.split == 'validation':
        from freeze_capacity import verify_frozen
        frozen = verify_frozen()
        protocol = json.loads((ROOT/'capacity_protocol.json').read_text())
        config = CapacityConfig(**protocol['controller_configuration'])
        require(config.as_dict() == protocol['controller_configuration'],'Invalid frozen config')
    manifest = scenario_manifest(args.split)
    write_json(output/'scenario_manifest.json',manifest)
    inputs = {'split':args.split,'out':str(output),'capacity_config':config.as_dict(),
              'freeze_verification':frozen,'scenario_manifest_sha256':manifest['manifest_sha256']}
    spec = {
        'job_id':f'capacity_v020_{args.split}',
        'input':inputs,
        'steps':[
            {'id':policy,'agent':'capacity_evaluate','params':{
                'policy':policy,'inject_retry':policy=='abc_capacity'},
             'timeout_s':600,'max_attempts':2}
            for policy in POLICIES
        ] + [{'id':'comparison','agent':'capacity_compare','depends_on':list(POLICIES),
              'timeout_s':120,'max_attempts':1}],
    }
    workspace = output/'workspace'
    expected_attempts = {policy:2 if policy=='abc_capacity' else 1 for policy in POLICIES}
    expected_attempts['comparison'] = 1
    start = time.perf_counter()
    print(f"Running {args.split}: {manifest['scenario_count']} scenarios, six policies, three workers",flush=True)
    with Runtime(workspace,agents=agents(),max_workers=3) as runtime:
        submission = runtime.submit(spec)
        require(submission['created'] is True,'New output unexpectedly reused a job')
        state = runtime.run(spec['job_id'])
        if state['status'] != 'SUCCEEDED':
            write_json(output/'failed_workflow.json',state)
            raise RuntimeError('Capacity workflow failed; inspect failed_workflow.json')
        attempts = {key:state['steps'][key]['attempts'] for key in expected_attempts}
        require(attempts == expected_attempts,'Unexpected attempts in controlled-retry workflow')
        report = Path(state['receipt']['path'])
        before = (sha(report),report.stat().st_mtime_ns)
        events = runtime.synapse.events_for('monitor',spec['job_id'])
    with Runtime(workspace,agents=agents(),max_workers=3) as runtime:
        duplicate = runtime.submit(spec)
        require(duplicate['created'] is False,'Duplicate job was recreated')
        resumed = runtime.run(spec['job_id'])
        require(resumed['status'] == 'SUCCEEDED','Completed job did not resume successfully')
        require({key:resumed['steps'][key]['attempts'] for key in attempts} == attempts,'Restart repeated a completed step')
        require((sha(report),report.stat().st_mtime_ns) == before,'Restart changed the delivered report')
        negative = {
            'job_id':f'capacity_negative_v020_{args.split}','input':inputs,
            'steps':[
                {'id':'bad','agent':'capacity_evaluate','params':{
                    'policy':'static','reuse_original':True,'corrupt':True},
                 'timeout_s':120,'max_attempts':1},
                {'id':'blocked','agent':'capacity_noop','depends_on':['bad']},
                {'id':'independent','agent':'capacity_noop'},
            ],
        }
        runtime.submit(negative)
        neg = runtime.run(negative['job_id'])
        require(neg['status'] == 'FAILED','Corrupted summary was not rejected')
        neg_status = {key:value['status'] for key,value in neg['steps'].items()}
        require(neg_status == {'bad':'FAILED','blocked':'BLOCKED','independent':'SUCCEEDED'},'Negative-control dependency isolation failed')
        require(neg['steps']['bad']['attempts'] == 1,'Invalid output was unexpectedly retried')
    rows = [row for policy in POLICIES for row in state['steps'][policy]['output']['rows']]
    row_audit = audit_rows(rows,generate_scenarios(args.split),{p:policy_seeds(p) for p in POLICIES})
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer,fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    atomic_bytes(output/'episodes.csv',buffer.getvalue().encode('utf-8'))
    trace_index = {policy:state['steps'][policy]['output']['representative_traces'] for policy in POLICIES}
    write_json(output/'representative_traces_index.json',trace_index)
    write_json(output/'workflow_events.json',events)
    comparison = state['steps']['comparison']['output']
    require(sha(output/'summary.json') == comparison['summary_sha256'],'Final summary checksum changed')
    require(audit_source_integrity(ROOT) == source_hashes,'Upstream files changed during workflow')
    if args.split == 'validation':
        require(verify_frozen() == frozen,'Frozen files changed during validation')
    verification = {
        'split':args.split,'status':state['status'],'steps':attempts,
        'peak_workers':state['peak_workers'],
        'receipt_path':str(report.relative_to(output)),
        'receipt_sha256':before[0],'receipt_bytes':report.stat().st_size,
        'summary_sha256':comparison['summary_sha256'],
        'duplicate_submission_created':duplicate['created'],
        'restart_replay_preserved_attempts':True,'restart_replay_preserved_report':True,
        'negative_control':{'status':neg['status'],'steps':neg_status,
            'bad_error':neg['steps']['bad']['error'],
            'scope':'completed-work count deliberately changed by +1 in a copied static result; no extra policy rollout'},
        'row_audit':row_audit,
        'independent_trace_replay':{
            'traces':sum(item['count'] for item in trace_index.values()),
            'minutes':sum(item['minutes'] for item in trace_index.values()),
            'selection':'first scenario seed per family and first seed per policy, fixed before execution',
            'scope':'scalar arithmetic replay of these traces; all other episodes receive summary and inline invariant checks'},
        'upstream_hashes':source_hashes,'freeze_verification':frozen,
        'scenario_manifest_sha256':manifest['manifest_sha256'],
        'capacity_config':config.as_dict(),
        'python':platform.python_version(),'numpy':np.__version__,
        'elapsed_seconds':round(time.perf_counter()-start,3),
        'timing_scope':'local whole-workflow elapsed time, not a performance benchmark',
    }
    write_json(output/'verification.json',verification)
    print(json.dumps({'status':state['status'],'split':args.split,**comparison['overview'],
                      'independent_trace_replay':verification['independent_trace_replay']},indent=2),flush=True)


if __name__ == '__main__':
    main()
