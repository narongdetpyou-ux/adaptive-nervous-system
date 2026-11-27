"""Declared V5 out-of-distribution families (v5_ood_plan.json): cases, physics and episodes.

Every shock is drawn from the family and seed alone, so all policies meet the
same arrivals, productivity, sensor multipliers and transfer-failure windows.
Policies see nothing beyond the unchanged observation interface.
"""
from dataclasses import dataclass, field
import json
from pathlib import Path

import numpy as np

from capacity_scenarios import HORIZON_GENERATED, NOMINAL_PRODUCTIVITY
from v4_environment import Env, EnvironmentConfig, REGIME_CONFIGS
from v4_scenarios import FOLLOWUP_MINUTES, SENSOR_CHANNELS, SENSOR_NOISE_SIGMA, Scenario
from v5_simulation import simulate_case

ROOT = Path(__file__).resolve().parent
PLAN = json.loads((ROOT / 'v5_ood_plan.json').read_text())
DECLARATION = json.loads((ROOT / 'v5_acceptance_predeclared.json').read_text())
FAMILIES = tuple(f['name'] for f in PLAN['ood']['families'])
if FAMILIES != tuple(DECLARATION['ood']['families']):
    raise ValueError('OOD families differ from the declaration')
OOD_SEEDS = tuple(range(DECLARATION['data']['ood_validation_seed_range'][0],
                        DECLARATION['data']['ood_validation_seed_range'][1] + 1))
VALIDATION_SEEDS = tuple(range(DECLARATION['data']['validation_seed_range'][0],
                               DECLARATION['data']['validation_seed_range'][1] + 1))
SMOKE_SEEDS = tuple(range(9000, 9004))
BASE = REGIME_CONFIGS['delayed_noisy']
SMALL_BUFFERS = (30000.0, 20000.0, 20000.0)
TOTAL = HORIZON_GENERATED + FOLLOWUP_MINUTES
SWITCHING_SCHEDULE = ((0, 14, 31), (2, 31, 39), (1, 39, 59), (0, 59, 71))
DEGRADED_SIGMA = 0.20
UNDER_REPORT = 0.8
FAILURE_FRACTION = 0.5
_CONFIGS = {}


def family_config(family):
    """One shared EnvironmentConfig object per family."""
    if family not in FAMILIES:
        raise ValueError(f'Unknown OOD family: {family}')
    if family not in _CONFIGS:
        buffers = SMALL_BUFFERS if family in ('buffer_cascade', 'compound_stress') else BASE.buffer_capacities
        _CONFIGS[family] = EnvironmentConfig(
            f'ood_{family}', transit_delay=BASE.transit_delay, integer_resources=True,
            same_minute_forwarding=False, buffer_capacities=buffers,
            startup_factors=BASE.startup_factors, sensor_lag=BASE.sensor_lag,
            sensor_noise_sigma=BASE.sensor_noise_sigma)
    return _CONFIGS[family]


class TransferFailureEnv(Env):
    """delayed_noisy physics in which moves started inside a window partly fail.

    floor(fraction x units) of each recipient's incoming units rejoin the donors
    at the scheduled arrival minute instead of arriving, split by donor share
    (largest remainder, lower stage first). The committed ledger changes at that
    minute, so committed = active + pending, budget and integrality always hold.
    """

    def __init__(self, config, windows, fraction=FAILURE_FRACTION):
        super().__init__(config)
        if config.transit_delay < 1 or not config.integer_resources:
            raise ValueError('Transfer failure needs transit delay and integer resources')
        self.windows = tuple((int(a), int(b)) for a, b in windows)
        self.fraction = float(fraction)
        self._failure = None
        self.failure_events = []

    def in_window(self, t):
        return any(start <= t < end for start, end in self.windows)

    def _apply(self, t, raw_delta):
        applied = super()._apply(t, raw_delta)
        if np.any(applied) and self.in_window(t):
            incoming = np.maximum(applied, 0.0)
            failed = np.floor(self.fraction * incoming)
            total = float(failed.sum())
            if total > 0:
                donors = np.maximum(-applied, 0.0)
                exact = total * donors / donors.sum()
                back = np.floor(exact)
                order = np.argsort(-(exact - back), kind='stable')
                back[order[:int(round(total - back.sum()))]] += 1.0
                self._failure = (int(t), failed, back)
        return applied

    def _activate(self, t):
        if self._failure is not None and self.ready_at is not None and t >= self.ready_at:
            started, failed, back = self._failure
            self.pending -= failed
            self.committed = self.committed - failed + back
            self.active += back
            self.failure_events.append({'move_t': started, 'arrival_t': int(t),
                                        'failed': failed.tolist(), 'returned': back.tolist()})
            self._failure = None
        super()._activate(t)


@dataclass(frozen=True)
class OODCase:
    family: str
    seed: int
    scenario: Scenario
    windows: tuple = ()
    metadata: dict = field(default_factory=dict)

    @property
    def config(self):
        return family_config(self.family)

    def make_env(self, config):
        if config is not self.config:
            raise ValueError('Environment config does not belong to this case')
        if self.windows:
            return TransferFailureEnv(config, self.windows)
        return Env(config)

    def manifest(self):
        return {**self.scenario.manifest(), 'family': self.family, 'windows': [list(w) for w in self.windows],
                'environment': self.config.as_dict()}


def _streams(family, seed):
    index = FAMILIES.index(family)
    return [np.random.default_rng(np.random.SeedSequence([20261127, 5, index, seed, stream])) for stream in range(4)]


def _sensor(rng, degraded, under_report_stages):
    sigma = SENSOR_NOISE_SIGMA
    result = {}
    for channel in SENSOR_CHANNELS:
        shape = (TOTAL,) if channel == 'arrival' else (TOTAL, 3)
        normal = rng.lognormal(mean=-0.5 * sigma**2, sigma=sigma, size=shape)
        if degraded:
            noisy = rng.lognormal(mean=-0.5 * DEGRADED_SIGMA**2, sigma=DEGRADED_SIGMA, size=shape)
            normal[30:70] = noisy[30:70]
            if channel in ('pre_queue', 'end_queue'):
                for stage in under_report_stages:
                    normal[45:60, stage] *= UNDER_REPORT
        result[channel] = normal
    return result


def _windows(rng):
    first_length = int(rng.integers(12, 21))
    first_start = int(rng.integers(15, 41))
    first_end = first_start + first_length
    second_length = int(rng.integers(12, min(20, 76 - first_end) + 1))
    second_start = int(rng.integers(first_end + 4, 80 - second_length + 1))
    return ((first_start, first_end), (second_start, second_start + second_length))


def _rapid_switching(rate, fault, rng):
    segments, start, previous = [], 12, None
    while start < 84:
        end = min(start + int(rng.integers(5, 10)), 84)
        if previous is None:
            stage = int(rng.integers(3))
        else:
            others = [s for s in range(3) if s != previous]
            stage = others[int(rng.integers(2))]
        rate[start:end, stage] *= fault
        segments.append([stage, start, end])
        previous, start = stage, end
    return segments


def build_case(family, seed, *, allow_reserved=False):
    """Generate one OOD case. Seeds 8000..8063 need allow_reserved (frozen runner only)."""
    if family not in FAMILIES:
        raise ValueError(f'Unknown OOD family: {family}')
    if type(seed) is not int or seed < 0:
        raise ValueError('Seed must be a nonnegative integer')
    if seed in VALIDATION_SEEDS:
        raise ValueError('Validation seeds are never OOD cases')
    if seed in OOD_SEEDS and not allow_reserved:
        raise ValueError('OOD seeds open only through the frozen validation runner')
    arr_rng, prod_rng, shock_rng, sensor_rng = _streams(family, seed)
    t = np.arange(HORIZON_GENERATED)
    arrival_scale = float(arr_rng.uniform(.9, 1.1))
    productivity_scale = prod_rng.uniform(.92, 1.08, 3)
    innovation = prod_rng.normal(0.0, .035, (HORIZON_GENERATED, 3))
    noise = np.zeros_like(innovation)
    for minute in range(HORIZON_GENERATED):
        noise[minute] = .65 * (noise[minute - 1] if minute else 0.0) + innovation[minute]
    rate = NOMINAL_PRODUCTIVITY * productivity_scale * np.clip(1 + noise, .82, 1.18)
    phase = float(arr_rng.uniform(0, 2 * np.pi))
    fault = float(prod_rng.uniform(.42, .62))
    metadata = {'generation': 'v5 OOD family (v5_ood_plan.json)', 'arrival_scale': arrival_scale,
                'stage_productivity_scale': productivity_scale.tolist(), 'fault_factor': fault}
    if family == 'buffer_cascade':
        demand = np.full(HORIZON_GENERATED, 10500.0)
        demand[:12] = 4500.0
        demand[72:] = 5500.0
    else:
        demand = 10000 + 1500 * np.sin(2 * np.pi * t / 37 + phase)
        demand[:10] = 4000.0
        demand[80:] = 5000.0
    windows = ()
    if family in ('rapid_switching', 'compound_stress'):
        metadata['segments'] = _rapid_switching(rate, fault, shock_rng)
    elif family == 'false_recovery':
        stage = int(shock_rng.integers(3))
        rate[14:40, stage] *= fault
        rate[46:76, stage] *= .85 * fault
        metadata['events'] = f'stage {stage}: fault [14,40), recovery [40,46), relapse x0.85 [46,76)'
    elif family == 'buffer_cascade':
        rate[20:44, 2] *= fault
        rate[36:60, 1] *= fault
        metadata['events'] = 'stage 2 fault [20,44), stage 1 fault [36,60); buffers (30000, 20000, 20000)'
    else:
        for stage, start, end in SWITCHING_SCHEDULE:
            rate[start:end, stage] *= fault
        metadata['events'] = 'switching schedule: stage 0 [14,31), 2 [31,39), 1 [39,59), 0 [59,71)'
    if family in ('transfer_failure', 'compound_stress'):
        windows = _windows(shock_rng)
        metadata['failure_windows'] = [list(w) for w in windows]
    degraded = family in ('sensor_degradation', 'compound_stress')
    sensor = _sensor(sensor_rng, degraded, (1,) if family == 'sensor_degradation' else (0, 1, 2))
    minute_factor = arr_rng.lognormal(mean=-.5 * .08**2, sigma=.08, size=HORIZON_GENERATED)
    arrivals = arr_rng.poisson(np.maximum(0, demand * arrival_scale * minute_factor)).astype(float)
    scenario = Scenario(family, seed, arrivals, rate, 'v5_ood', metadata, sensor_noise=sensor)
    return OODCase(family, seed, scenario, windows, metadata)


def run_episode(policy, case, detailed=False):
    """One OOD episode through the unchanged V5 simulation adapter."""
    return simulate_case(policy, case.scenario, environment=case.config,
                         env_factory=case.make_env, detailed=detailed)
