"""Freeze v0.3 before new validation; preserve the previously frozen v0.2.

This is a local integrity record, not an external preregistration. Existing
protocols are verified, never silently replaced to accommodate changed code.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from v3_policy import V3Config
from v3_scenarios import scenario_manifest, expected_counts
from freeze_capacity import verify_frozen as verify_parent_frozen

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT/'v3_protocol.json'
HASH_FILE = ROOT/'v3_protocol.sha256'
FILES = (
    'v3_policy.py','v3_simulation.py','v3_scenarios.py','v3_metrics.py',
    'v3_agents.py','run_v3.py','freeze_v3.py','audit_v3.py','test_v3.py',
    'diagnose_v2.py','v3_protocol_draft.json',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {name:sha(ROOT/name) for name in FILES}


def verify_frozen():
    if not PROTOCOL.is_file() or not HASH_FILE.is_file():
        raise ValueError('Validation requires both frozen v3_protocol.json and v3_protocol.sha256')
    protocol = json.loads(PROTOCOL.read_text())
    if sha(PROTOCOL) != HASH_FILE.read_text().split()[0]:
        raise ValueError('v0.3 protocol changed after freezing')
    if protocol['implementation_sha256'] != source_hashes():
        raise ValueError('v0.3 implementation changed after freezing')
    if protocol['controller_configuration'] != V3Config().as_dict():
        raise ValueError('v0.3 controller configuration differs from frozen protocol')
    parent = verify_parent_frozen()
    if protocol['parent_v2_verification'] != parent:
        raise ValueError('Previously frozen v0.2 content changed')
    manifest = scenario_manifest('validation')['manifest_sha256']
    if protocol['validation_manifest_sha256'] != manifest:
        raise ValueError('v0.3 validation environments changed')
    return {
        'protocol_sha256':sha(PROTOCOL),
        'implementation_sha256':protocol['implementation_sha256'],
        'parent_v2_verification':parent,
        'validation_manifest_sha256':manifest,
    }


def freeze():
    if PROTOCOL.exists() or HASH_FILE.exists():
        return verify_frozen()
    draft = ROOT/'v3_protocol_draft.json'
    protocol = json.loads(draft.read_text())
    if not protocol.get('acceptance_criteria'):
        raise ValueError('Draft must specify acceptance_criteria before freezing')
    parent = verify_parent_frozen()
    protocol.update(
        status='frozen locally after known-case development and before new validation; no external registry',
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        controller_configuration=V3Config().as_dict(),
        implementation_sha256=source_hashes(),
        parent_v2_verification=parent,
        validation_manifest_sha256=scenario_manifest('validation')['manifest_sha256'],
        development_manifest_sha256=scenario_manifest('development')['manifest_sha256'],
        expected_development_scope=expected_counts('development'),
        expected_validation_scope=expected_counts('validation'),
        representative_selection='First scenario seed in every family and first policy seed per policy, separately in each of three regimes; 198 complete traces selected before outcomes.',
        workflow='Original NeuronFlow runtime, 18 regime/policy evaluation steps with three workers, descriptor-only artifact exchange, comparison join, first-attempt instant/v3_full transient failure before computation, restart/duplicate reuse, deliberately corrupted static result rejected with dependent blocked and independent branch successful.',
        boundary='All prediction inputs are past/current observations and known transfer duration. Actual environment arrays and terminal schedule stay in the simulator and independent verifier.',
        no_tuning_rule='No policy, configuration, generator, or acceptance changes after inspecting validation outcomes; a subsequent change is a new experiment.',
    )
    evidence = {}
    for relative in (
        'v3_results/diagnosis/diagnosis.json',
        'v3_results/development_initial/summary.json',
        'v3_results/development_initial/configuration.json',
        'v3_results/development_initial/v3_policy_initial.py',
        'v3_results/development/summary.json',
        'V3_DEVELOPMENT_NOTES.md',
    ):
        path = ROOT/relative
        if path.is_file():
            evidence[relative] = sha(path)
    protocol['known_development_evidence_sha256'] = evidence
    with PROTOCOL.open('x',encoding='utf-8') as handle:
        handle.write(json.dumps(protocol,indent=2,ensure_ascii=False,allow_nan=False)+'\n')
    with HASH_FILE.open('x',encoding='utf-8') as handle:
        handle.write(sha(PROTOCOL)+'  v3_protocol.json\n')
    return verify_frozen()


if __name__=='__main__':
    print(json.dumps(freeze(),indent=2))
