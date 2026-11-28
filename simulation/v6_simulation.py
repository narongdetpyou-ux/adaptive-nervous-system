"""Run V6 variants beside the frozen baselines (v6_acceptance_predeclared.json).

Baselines call the frozen v5_simulation.simulate_case unchanged. A V6 variant is
a fresh V6Policy passed as a supplied controller; simulate_case accepts those
only under a V5 label, so the row is relabelled with the V6 variant afterwards.
Every V6 policy therefore runs through the same environment, observation and
accounting code as the baselines.
"""
import json
from pathlib import Path

from v4_environment import REGIME_CONFIGS
from v5_simulation import simulate_case
from v6_policy import V6_VARIANTS, V6Policy

ROOT = Path(__file__).resolve().parent
DECLARATION = json.loads((ROOT / 'v6_acceptance_predeclared.json').read_text())
BASELINES = tuple(DECLARATION['policies']['baselines'])
VARIANTS = tuple(DECLARATION['policies']['variants'])
POLICIES = BASELINES + VARIANTS
SUPPLIED_LABEL = 'v5_full'

if not set(VARIANTS) <= set(V6_VARIANTS):
    raise ValueError('Declared V6 variants are not implemented')


def make_controller(policy, environment, config=None):
    if policy not in V6_VARIANTS:
        raise ValueError(f'Not a V6 variant: {policy}')
    return V6Policy(environment, policy, config)


def simulate(policy, case, *, regime='legacy_instant', detailed=False, config=None, environment=None, env_factory=None):
    if policy in V6_VARIANTS:
        physics = REGIME_CONFIGS[regime] if environment is None else environment
        row, trace = simulate_case(SUPPLIED_LABEL, case, regime=regime, detailed=detailed,
                                   controller=make_controller(policy, physics, config),
                                   environment=environment, env_factory=env_factory)
        row['policy'] = policy
        return row, trace
    if policy not in BASELINES:
        raise ValueError(f'Unknown policy: {policy}')
    if config is not None:
        raise ValueError('Baselines run with their frozen configuration')
    return simulate_case(policy, case, regime=regime, detailed=detailed, environment=environment,
                         env_factory=env_factory)


def run_ood(policy, ood_case, detailed=False, config=None):
    """An OOD episode (v5_ood.OODCase) under the declared OOD physics."""
    return simulate(policy, ood_case.scenario, environment=ood_case.config, env_factory=ood_case.make_env,
                    detailed=detailed, config=config)
