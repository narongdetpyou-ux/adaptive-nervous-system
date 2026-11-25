"""Freeze the local experiment before validation; this is not a public registry.

Run once after development. A later code change is a new experiment and cannot
silently rewrite this protocol. The packaged protocol is already frozen.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from capacity_policy import CapacityConfig
from capacity_scenarios import scenario_manifest
from audit_capacity import audit_source_integrity

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT/'capacity_protocol.json'
HASH_FILE = ROOT/'capacity_protocol.sha256'
FILES = (
    'capacity_policy.py','capacity_simulation.py','capacity_scenarios.py',
    'capacity_metrics.py','capacity_agents.py','run_capacity.py',
    'audit_capacity.py','test_capacity.py','freeze_capacity.py',
    'simulation.py','protocol.json','protocol.sha256',
    'source_checksums.sha256','requirements.txt',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {name:sha(ROOT/name) for name in FILES}


def verify_frozen():
    if not PROTOCOL.is_file() or not HASH_FILE.is_file():
        raise ValueError('Validation requires the frozen capacity protocol')
    p = json.loads(PROTOCOL.read_text())
    if sha(PROTOCOL) != HASH_FILE.read_text().split()[0]:
        raise ValueError('Capacity protocol changed')
    if p['implementation_sha256'] != source_hashes():
        raise ValueError('Implementation changed after the capacity protocol was frozen')
    if p['controller_configuration'] != CapacityConfig().as_dict():
        raise ValueError('Controller parameters differ from the frozen protocol')
    if p['upstream_sha256'] != audit_source_integrity(ROOT):
        raise ValueError('Upstream content changed')
    if p['validation_manifest_sha256'] != scenario_manifest('validation')['manifest_sha256']:
        raise ValueError('Validation environments changed')
    return {'protocol_sha256':sha(PROTOCOL),
            'implementation_sha256':p['implementation_sha256'],
            'upstream_sha256':p['upstream_sha256'],
            'validation_manifest_sha256':p['validation_manifest_sha256']}


def freeze():
    if PROTOCOL.exists() or HASH_FILE.exists():
        return verify_frozen()
    p = json.loads((ROOT/'capacity_protocol_draft.json').read_text())
    p.update(
        status='frozen locally after development and before validation; no external preregistration',
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        controller_configuration=CapacityConfig().as_dict(),
        implementation_sha256=source_hashes(),
        upstream_sha256=audit_source_integrity(ROOT),
        validation_manifest_sha256=scenario_manifest('validation')['manifest_sha256'],
        development_manifest_sha256=scenario_manifest('development')['manifest_sha256'],
        controller_modes={
            'abc_capacity':'Deep-copy unchanged ABC, evaluate only its one proposed legal action against hold, commit the copied core only if accepted.',
            'capacity_aware':'A separate deterministic selector ranks every feasible original-catalog action against hold. It does not call ABC to choose its action.',
        },
        capacity_estimation='Only past allocated resources, processed flow and end queues. Positive end queue reveals a saturated rate; unsaturated service per resource is a lower bound. Explicit 10% reversion toward nominal200 is a prior assumption, not measured recovery.',
        prediction='Eight-minute constant-action full-path fluid replay; nine fixed models combine demand multipliers0.8/1/1.2 with lower rate0.85*estimate, point estimate, and linear recovery to nominal200. Mean predicted end backlog plus0.25*terminal backlog. Current arrival is already in observed queue; future demand is last3 observed arrivals mean.',
        acceptance='Mean predicted gain greater than max(10 work units,2% of mean hold cost) AND no model predicts a regression, within1e-7. Hold wins ties; subsequent ties prefer lower transferred volume then action ID.',
        extra_reserve='Both new policies require >=10 resource units at every stage. Original baselines retain their original nonnegative-only constraint. All actual nonzero transfers come from the same catalog; reserve is an additional conservative restriction.',
        primary_comparison='capacity_aware versus static: positive lower endpoint of paired95% CI for absolute mean-backlog improvement on the complete validation mixture. This is a conditional synthetic comparison.',
        advancement_criterion={
            'effect':'Positive lower95% paired-CI bound against BOTH static and abc_guard, separately in legacy_flash and generated_synthetic slices.',
            'constraints':'All work/resource/catalog/warmup/dwell checks pass; all generated_underload capacity_aware cases make zero moves and leave zero backlog.',
            'meaning':'Passing supports broader synthetic testing only, not deployment readiness, formal safety, or optimality.',
        },
        representative_traces='First scenario seed in each of11 groups; first policy seed for stochastic policies, all6 policies:66 complete traces, chosen before outcomes. All episodes have inline invariants and row accounting. These66 traces also get independent scalar replay.',
        workflow='Actual NeuronFlow0.1.0, six policy evaluation agents, three workers, comparison join, first-attempt abc_capacity transient exception before simulation, restart/duplicate reuse, and deliberate corrupted static output rejected with dependent blocked and independent branch successful.',
        no_tuning_rule='No policy/configuration/generator changes after validation outcomes. Any subsequent change requires a new experiment/version and new validation design.',
    )
    dev = ROOT/'capacity_results/development/summary.json'
    p['development_summary_sha256'] = sha(dev) if dev.is_file() else None
    p['development_choice_log'] = 'One smoke case, unit checks, and a full development split were inspected. The nine-model gate parameters were selected from design reasoning before development; no outcome-based parameter search was performed. Any implementation fixes are recorded in DEVELOPMENT_NOTES.md.'
    PROTOCOL.write_text(json.dumps(p,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    HASH_FILE.write_text(sha(PROTOCOL)+'  capacity_protocol.json\n')
    return verify_frozen()


if __name__=='__main__':
    print(json.dumps(freeze(),indent=2))
