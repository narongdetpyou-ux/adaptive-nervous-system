"""Freeze v0.4 after known-case selection and before fresh validation.

This is a local integrity record, not an external preregistration.  The
acceptance declaration was fixed separately before development outcomes.
Parent v0.3/v0.2 content is verified and never replaced.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from v4_policy import V4Config
from v4_scenarios import (
    scenario_manifest, expected_counts, REGIME_CONFIGS, POLICIES, SPLIT_SEEDS,
)
from freeze_v3 import verify_frozen as verify_parent_frozen

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / "v4_protocol.json"
HASH_FILE = ROOT / "v4_protocol.sha256"
SELECTED_CONFIG = ROOT / "v4_selected_config.json"
DECLARATION_SHA256 = "1105442baf50d0cee923c671ebc2c024d3e8697e90e280843fbdffdd005fb6ef"
CORE_FILES = (
    "v4_environment.py", "v4_scenarios.py", "v4_policy.py", "v4_simulation.py",
    "v4_metrics.py", "v4_agents.py", "run_v4.py", "freeze_v4.py",
    "audit_v4.py", "test_v4.py", "develop_v4.py",
)
FILES = CORE_FILES + (
    "evaluate_v4.py", "diagnose_v3.py", "test_v4_metrics.py",
    "v4_selected_config.json", "v4_protocol_draft.json",
    "v4_acceptance_predeclared.json", "v4_acceptance_predeclared.sha256",
    "V4_DEVELOPMENT_NOTES.md",
)
# These files are required by every actual run, including early development.
# Selection notes/configuration and independent test files need not yet exist
# during an explicitly configured development run.
EXECUTION_FILES = (
    "v4_environment.py", "v4_scenarios.py", "v4_policy.py", "v4_simulation.py",
    "v4_metrics.py", "v4_agents.py", "run_v4.py", "freeze_v4.py", "audit_v4.py",
    "v4_protocol_draft.json", "v4_acceptance_predeclared.json",
    "v4_acceptance_predeclared.sha256",
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_hashes():
    return {name: sha(ROOT / name) for name in FILES}


def execution_source_hashes():
    return {name: sha(ROOT / name) for name in EXECUTION_FILES}


def verify_predeclared_acceptance():
    path = ROOT / "v4_acceptance_predeclared.json"
    recorded = (ROOT / "v4_acceptance_predeclared.sha256").read_text().split()[0]
    if recorded != DECLARATION_SHA256 or sha(path) != DECLARATION_SHA256:
        raise ValueError("The acceptance rule changed from its predevelopment declaration")
    return json.loads(path.read_text())


def load_selected_config():
    if not SELECTED_CONFIG.is_file():
        raise ValueError("Freeze requires v4_selected_config.json from documented development")
    selected = json.loads(SELECTED_CONFIG.read_text())
    if not isinstance(selected, dict):
        raise ValueError("Selected configuration must be a JSON object of V4Config fields")
    return V4Config(**selected)


def verify_frozen():
    if not PROTOCOL.is_file() or not HASH_FILE.is_file():
        raise ValueError("Validation requires frozen v4_protocol.json and v4_protocol.sha256")
    protocol = json.loads(PROTOCOL.read_text())
    if sha(PROTOCOL) != HASH_FILE.read_text().split()[0]:
        raise ValueError("v0.4 protocol changed after freezing")
    if protocol.get("version") != "0.4.0":
        raise ValueError("Unexpected frozen protocol version")
    if protocol["implementation_sha256"] != source_hashes():
        raise ValueError("v0.4 implementation or selection evidence changed after freezing")
    declared = verify_predeclared_acceptance()
    if protocol["acceptance_criteria"] != declared:
        raise ValueError("Frozen acceptance differs from the predevelopment declaration")
    if protocol["acceptance_declaration_sha256"] != DECLARATION_SHA256:
        raise ValueError("Frozen declaration identity differs")
    if protocol["controller_configuration"] != load_selected_config().as_dict():
        raise ValueError("Selected controller configuration differs from frozen protocol")
    regimes = {name: config.as_dict() for name, config in REGIME_CONFIGS.items()}
    if protocol["regime_configurations"] != regimes or protocol["policy_names"] != list(POLICIES):
        raise ValueError("Frozen regime or policy specification differs")
    if protocol["split_seeds"] != {name: list(seeds) for name, seeds in SPLIT_SEEDS.items()}:
        raise ValueError("Frozen scenario split differs")
    parent = verify_parent_frozen()
    if protocol["parent_v3_verification"] != parent:
        raise ValueError("Previously frozen v0.3 content changed")
    for relative, expected in protocol.get("known_development_evidence_sha256", {}).items():
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file() or sha(path) != expected:
            raise ValueError("Known development evidence changed: " + relative)
    manifest = scenario_manifest("validation")["manifest_sha256"]
    if protocol["validation_manifest_sha256"] != manifest:
        raise ValueError("v0.4 held-out environments or common sensor draws changed")
    return {
        "protocol_sha256": sha(PROTOCOL),
        "implementation_sha256": protocol["implementation_sha256"],
        "parent_v3_verification": parent,
        "validation_manifest_sha256": manifest,
        "acceptance_declaration_sha256": DECLARATION_SHA256,
        "controller_configuration": protocol["controller_configuration"],
    }


def freeze():
    if PROTOCOL.exists() or HASH_FILE.exists():
        return verify_frozen()
    protocol = json.loads((ROOT / "v4_protocol_draft.json").read_text())
    declared = verify_predeclared_acceptance()
    if protocol.get("acceptance_criteria") != declared:
        raise ValueError("Draft acceptance must exactly match the predevelopment declaration")
    parent = verify_parent_frozen()
    selected = load_selected_config()
    implementation = source_hashes()  # Refuse missing tests, notes or selector.
    protocol.update(
        status="frozen locally after known-case development and before new validation; no external registry",
        frozen_at_utc=datetime.now(timezone.utc).isoformat(),
        controller_configuration=selected.as_dict(),
        selected_configuration_sha256=sha(SELECTED_CONFIG),
        implementation_sha256=implementation,
        acceptance_declaration_sha256=DECLARATION_SHA256,
        parent_v3_verification=parent,
        regime_configurations={name: config.as_dict() for name, config in REGIME_CONFIGS.items()},
        policy_names=list(POLICIES),
        split_seeds={name: list(seeds) for name, seeds in SPLIT_SEEDS.items()},
        validation_manifest_sha256=scenario_manifest("validation")["manifest_sha256"],
        development_manifest_sha256=scenario_manifest("development")["manifest_sha256"],
        expected_development_scope=expected_counts("development"),
        expected_validation_scope=expected_counts("validation"),
        representative_selection=(
            "First scenario seed in every family, for every deterministic policy and each of six regimes; "
            "462 complete traces and 51576 original-plus-followup minutes selected before outcomes."
        ),
        workflow=(
            "Original Neuron Flow runtime; 42 regime/policy evaluation steps with three workers and a comparison join; "
            "descriptor-only artifact exchange; first-attempt legacy_instant/v4_hybrid transient failure before "
            "policy computation; restart/duplicate reuse; separate completed+1 static corruption rejected with "
            "dependent branch blocked and independent branch successful."
        ),
        boundary=(
            "Policy inputs contain current commanded resources and timestamped observations; declared sensor lag "
            "and startup use historical same-source effective resources. True environment arrays, common future "
            "noise and terminal schedule remain outside the controller."
        ),
        no_tuning_rule=(
            "No controller, configuration, generator, sensor, metric or acceptance changes after inspecting "
            "validation outcomes. A subsequent change is a new experiment."
        ),
    )
    evidence = {}
    for relative in (
        "v4_results/diagnosis/diagnosis.json",
        "v4_results/development_grid/selection.json",
        "v4_results/development_grid/scores.json",
        "v4_results/development_selection.json",
        "v4_results/development/summary.json",
        "v4_results/development/verification.json",
        "V4_DIAGNOSIS_NOTES.md",
    ):
        path = ROOT / relative
        if path.is_file():
            evidence[relative] = sha(path)
    protocol["known_development_evidence_sha256"] = evidence
    with PROTOCOL.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(protocol, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    with HASH_FILE.open("x", encoding="utf-8") as handle:
        handle.write(sha(PROTOCOL) + "  v4_protocol.json\n")
    return verify_frozen()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify an existing freeze without creating one")
    args = parser.parse_args()
    print(json.dumps(verify_frozen() if args.verify else freeze(), indent=2))
