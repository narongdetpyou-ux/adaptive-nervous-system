"""Prespecified nested v0.4 physical environments.

The first two regimes preserve the v0.3 fluid dynamics and resource ledger.
Later regimes add integer resources, admission loss, one-interval forwarding
and resource startup.  This module is simulation truth: it does not implement
sensor delivery or send true productivity to a controller.

Each minute has exactly one begin() followed by one service().  Work rejected
by a finite destination buffer is dropped, never blocked at the upstream
server.  Dropped work remains in the offered-minus-completed deficit.
"""
from dataclasses import dataclass
from numbers import Integral

import numpy as np

from abc_service.frozen import catalog

TOL = 1e-6
STAGES = 3
_CATALOG_DELTAS = frozenset(tuple(item["delta"]) for item in catalog()["actions"])


@dataclass(frozen=True)
class EnvironmentConfig:
    name: str
    transit_delay: int = 0
    integer_resources: bool = False
    same_minute_forwarding: bool = True
    buffer_capacities: tuple = (None, None, None)
    startup_factors: tuple = ()
    sensor_lag: int = 0
    sensor_noise_sigma: float = 0.0
    budget: float = 207.0
    minimum_staff: float = 10.0
    warmup_minutes: int = 5
    dwell_minutes: int = 8

    def __post_init__(self):
        object.__setattr__(self, "buffer_capacities", tuple(self.buffer_capacities))
        object.__setattr__(self, "startup_factors", tuple(self.startup_factors))
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("An environment must have a name")
        for name in ("transit_delay", "sensor_lag", "warmup_minutes", "dwell_minutes"):
            value = getattr(self, name)
            if not isinstance(value, Integral) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if self.dwell_minutes < 1:
            raise ValueError("Dwell must be positive")
        if not isinstance(self.integer_resources, bool) or not isinstance(self.same_minute_forwarding, bool):
            raise ValueError("Resource and forwarding switches must be Boolean")
        if not np.isfinite([self.budget, self.minimum_staff, self.sensor_noise_sigma]).all():
            raise ValueError("Nonfinite environment parameter")
        if self.budget <= 0 or self.minimum_staff < 0 or STAGES * self.minimum_staff > self.budget:
            raise ValueError("Invalid resource budget or minimum")
        if self.integer_resources and (
                self.budget != round(self.budget) or
                self.minimum_staff != round(self.minimum_staff) or
                self.budget / STAGES != round(self.budget / STAGES)):
            raise ValueError("Integer resources need an integer equal initial allocation and minimum")
        if self.sensor_noise_sigma < 0:
            raise ValueError("Sensor noise cannot be negative")
        if len(self.buffer_capacities) != STAGES:
            raise ValueError("Exactly three buffer capacities are required")
        for value in self.buffer_capacities:
            if value is not None and (not np.isfinite(value) or value < 0):
                raise ValueError("Buffer capacity must be nonnegative and finite, or None")
        if any(not np.isfinite(x) or x < 0 or x > 1 for x in self.startup_factors):
            raise ValueError("Startup factors must lie in [0,1]")
        if any(a > b for a, b in zip(self.startup_factors, self.startup_factors[1:])):
            raise ValueError("Startup factors must be nondecreasing")
        # Every move is fully active and out of startup before another move is
        # allowed.  This avoids an undefined choice of which startup cohort a
        # departing donor is taken from.
        if self.transit_delay + len(self.startup_factors) >= self.dwell_minutes:
            raise ValueError("Transit and startup must finish before the dwell interval ends")

    def as_dict(self):
        return {
            "name": self.name,
            "transit_delay": int(self.transit_delay),
            "integer_resources": self.integer_resources,
            "same_minute_forwarding": self.same_minute_forwarding,
            "buffer_capacities": list(self.buffer_capacities),
            "startup_factors": list(self.startup_factors),
            "sensor_lag": int(self.sensor_lag),
            "sensor_noise_sigma": float(self.sensor_noise_sigma),
            "budget": float(self.budget),
            "minimum_staff": float(self.minimum_staff),
            "warmup_minutes": int(self.warmup_minutes),
            "dwell_minutes": int(self.dwell_minutes),
        }


_FINITE_BUFFERS = (60000.0, 40000.0, 40000.0)
REGIME_CONFIGS = {
    "legacy_instant": EnvironmentConfig("legacy_instant"),
    "legacy_transit3": EnvironmentConfig("legacy_transit3", transit_delay=3),
    "integer_transit3": EnvironmentConfig(
        "integer_transit3", transit_delay=3, integer_resources=True),
    "finite_pipeline": EnvironmentConfig(
        "finite_pipeline", transit_delay=3, integer_resources=True,
        same_minute_forwarding=False, buffer_capacities=_FINITE_BUFFERS),
    "startup": EnvironmentConfig(
        "startup", transit_delay=3, integer_resources=True,
        same_minute_forwarding=False, buffer_capacities=_FINITE_BUFFERS,
        startup_factors=(0.5, 0.75)),
    "delayed_noisy": EnvironmentConfig(
        "delayed_noisy", transit_delay=3, integer_resources=True,
        same_minute_forwarding=False, buffer_capacities=_FINITE_BUFFERS,
        startup_factors=(0.5, 0.75), sensor_lag=2, sensor_noise_sigma=0.05),
}
REGIMES = tuple(REGIME_CONFIGS)
REGIME_DELAYS = {name: config.transit_delay for name, config in REGIME_CONFIGS.items()}


def _vector(value, label, nonnegative=False):
    value = np.array(value, dtype=float, copy=True)
    if value.shape != (STAGES,) or not np.isfinite(value).all():
        raise ValueError(f"{label} must be a finite three-vector")
    if nonnegative and np.any(value < 0):
        raise ValueError(f"{label} cannot be negative")
    return value


def project_action(staff, delta, config):
    """Return the applied delta for a raw catalog proposal (or exact hold).

    Raw proposals that would breach the minimum reserve are rejected before
    projection.  Integer projection floors the proposed NEW allocation and
    distributes the remaining units by largest fractional remainder, with
    lower stage index breaking a tie.  The projected delta is an execution
    detail and is not required to be a raw catalog entry.
    """
    if isinstance(config, str):
        config = REGIME_CONFIGS[config]
    if not isinstance(config, EnvironmentConfig):
        raise TypeError("config must be EnvironmentConfig or a regime name")
    staff = _vector(staff, "Committed staff", nonnegative=True)
    delta = _vector(delta, "Raw proposal")
    if abs(float(staff.sum()) - config.budget) > TOL or min(staff) < config.minimum_staff:
        raise ValueError("Invalid committed allocation")
    if abs(float(delta.sum())) > TOL:
        raise ValueError("Raw proposal must conserve resources")
    if np.any(delta) and tuple(delta) not in _CATALOG_DELTAS:
        raise ValueError("Raw proposal must be a frozen catalog action or hold")
    target = staff + delta
    if min(target) < config.minimum_staff:
        raise ValueError("Minimum stage reserve violation")
    if not config.integer_resources:
        # Preserve the original operation in the two legacy regimes.
        return delta.copy()
    if not np.array_equal(staff, np.rint(staff)):
        raise ValueError("Integer regime received fractional committed staff")
    integer_target = np.floor(target)
    remaining = int(round(config.budget - float(integer_target.sum())))
    if remaining < 0 or remaining >= STAGES:
        raise ValueError("Invalid largest-remainder projection")
    ranking = np.argsort(-(target - integer_target), kind="stable")
    integer_target[ranking[:remaining]] += 1.0
    if min(integer_target) < config.minimum_staff or float(integer_target.sum()) != config.budget:
        raise ValueError("Projected allocation violates the resource constraint")
    return integer_target - staff


class Env:
    """Three physical stages with a separately conserved resource ledger."""

    def __init__(self, config):
        if isinstance(config, str):
            config = REGIME_CONFIGS[config]
        if not isinstance(config, EnvironmentConfig):
            raise TypeError("config must be EnvironmentConfig or a regime name")
        self.config = config
        self.committed = np.full(STAGES, config.budget / STAGES)
        self.active = self.committed.copy()
        self.pending = np.zeros(STAGES)
        self.effective_staff = self.active.copy()
        self.queues = np.zeros(STAGES)
        # Work is indexed by its destination stage; element zero is always 0.
        self.inflight_work = np.zeros(STAGES)
        self.cumulative_drops = np.zeros(STAGES)
        self.offered = 0.0
        self.completed = 0.0
        self.ready_at = None
        self.last_move = -10**9
        self.last_start = -1
        self._open = False
        self._startup_cohorts = []
        self._begin_record = None
        self.max_mass_error = 0.0
        self.max_budget_error = 0.0

    def _refresh_effective(self, t):
        self._startup_cohorts = [
            (started, units) for started, units in self._startup_cohorts
            if t - started < len(self.config.startup_factors)
        ]
        self.effective_staff = self.active.copy()
        for started, units in self._startup_cohorts:
            factor = self.config.startup_factors[t - started]
            self.effective_staff -= (1.0 - factor) * units

    def _activate(self, t):
        if self.ready_at is not None and t >= self.ready_at:
            arriving = self.pending.copy()
            self.active += self.pending
            self.pending[:] = 0.0
            self.ready_at = None
            if self.config.startup_factors and np.any(arriving):
                self._startup_cohorts.append((t, arriving))
        self._refresh_effective(t)

    def check(self):
        budget_error = max(
            abs(float(self.committed.sum()) - self.config.budget),
            abs(float(self.active.sum() + self.pending.sum()) - self.config.budget),
            float(np.max(abs(self.committed - self.active - self.pending))),
        )
        retained = float(self.queues.sum() + self.inflight_work.sum())
        mass_error = abs(self.offered - self.completed - retained - float(self.cumulative_drops.sum()))
        self.max_budget_error = max(self.max_budget_error, budget_error)
        self.max_mass_error = max(self.max_mass_error, mass_error)
        if budget_error > TOL or min(self.active) < 0 or min(self.pending) < 0:
            raise ValueError("Resource conservation failed")
        if min(self.committed) < self.config.minimum_staff:
            raise ValueError("Committed allocation violates stage reserve")
        if min(self.effective_staff) < -TOL or np.any(self.effective_staff - self.active > TOL):
            raise ValueError("Invalid startup capacity")
        if min(self.queues) < 0 or min(self.inflight_work) < 0 or min(self.cumulative_drops) < 0 or mass_error > TOL:
            raise ValueError("Work conservation failed")
        if self.inflight_work[0] != 0:
            raise ValueError("No internal work may arrive at the first stage")
        if self.config.same_minute_forwarding and np.any(self.inflight_work):
            raise ValueError("Instant forwarding cannot retain inflight work")
        if self.config.integer_resources and any(
                not np.array_equal(value, np.rint(value))
                for value in (self.committed, self.active, self.pending)):
            raise ValueError("Resource integrality failed")
        for stage, cap in enumerate(self.config.buffer_capacities):
            if cap is not None and self.queues[stage] > cap + TOL:
                raise ValueError("Finite buffer exceeded")
        return {"mass_error": float(mass_error), "budget_error": float(budget_error)}

    def begin(self, t, arrival):
        """Activate resources and admit work, then return pre-service truth.

        Sensor lag/noise are deliberately absent here.  A measurement adapter
        may expose an earlier snapshot, but must keep the commanded resource
        ledger exact and never expose future arrays to a policy.
        """
        if not isinstance(t, Integral) or isinstance(t, bool) or t != self.last_start + 1 or self._open:
            raise ValueError("Each consecutive minute requires begin then service")
        arrival = float(arrival)
        if not np.isfinite(arrival) or arrival < 0:
            raise ValueError("External arrival must be finite and nonnegative")
        self._activate(t)
        inflight_before = self.inflight_work.copy()
        incoming = inflight_before.copy()
        incoming[0] += arrival
        self.inflight_work[:] = 0.0
        admitted = incoming.copy()
        dropped = np.zeros(STAGES)
        for stage, cap in enumerate(self.config.buffer_capacities):
            if cap is not None:
                admitted[stage] = min(incoming[stage], max(0.0, cap - self.queues[stage]))
                dropped[stage] = incoming[stage] - admitted[stage]
            self.queues[stage] += admitted[stage]
        self.cumulative_drops += dropped
        self.offered += arrival
        self.last_start = int(t)
        self._open = True
        self.check()
        self._begin_record = {
            "t": int(t),
            "arrival": arrival,
            "start_queue": self.queues.tolist(),
            "observed": self.queues.tolist(),
            "staff_before": self.committed.tolist(),
            "active_staff_before": self.active.tolist(),
            "pending_staff_before": self.pending.tolist(),
            "effective_staff_before": self.effective_staff.tolist(),
            "inflight_before": inflight_before.tolist(),
            "incoming_by_stage": incoming.tolist(),
            "admitted_by_stage": admitted.tolist(),
            "dropped_by_stage": dropped.tolist(),
        }
        # Return new containers, rather than exposing the record later used by
        # service or mutable physical arrays.
        return {key: value.copy() if isinstance(value, list) else value
                for key, value in self._begin_record.items()}

    def _apply(self, t, raw_delta):
        applied = project_action(self.committed, raw_delta, self.config)
        if not np.any(applied):
            return applied
        if t < self.config.warmup_minutes or t - self.last_move < self.config.dwell_minutes:
            raise ValueError("Warmup/dwell violation")
        if np.any(self.pending) or self.ready_at is not None or self._startup_cohorts:
            raise ValueError("Cannot overlap resource transitions")
        self.committed = self.committed + applied
        self.last_move = int(t)
        if self.config.transit_delay == 0:
            self.active += applied
            incoming = np.maximum(applied, 0.0)
            if self.config.startup_factors and np.any(incoming):
                self._startup_cohorts.append((int(t), incoming))
        else:
            self.active += np.minimum(applied, 0.0)
            self.pending = np.maximum(applied, 0.0)
            self.ready_at = int(t) + self.config.transit_delay
        self._refresh_effective(t)
        return applied

    def service(self, t, proposal_delta, true_productivity):
        """Apply one raw catalog proposal, perform service, and return truth."""
        if not self._open or t != self.last_start:
            raise ValueError("service must follow this minute's begin exactly once")
        rates = _vector(true_productivity, "True productivity", nonnegative=True)
        raw_delta = _vector(proposal_delta, "Raw proposal")
        applied = self._apply(t, raw_delta)
        flow = np.zeros(STAGES)
        if self.config.same_minute_forwarding:
            # Identical scalar operation order to v3_simulation.simulate_case.
            for stage in range(STAGES):
                flow[stage] = min(self.queues[stage], self.effective_staff[stage] * rates[stage])
                self.queues[stage] -= flow[stage]
                if stage < STAGES - 1:
                    self.queues[stage + 1] += flow[stage]
                else:
                    self.completed += flow[stage]
        else:
            # No stage consumes another stage's output in the current interval.
            for stage in range(STAGES):
                flow[stage] = min(self.queues[stage], self.effective_staff[stage] * rates[stage])
                self.queues[stage] -= flow[stage]
            self.inflight_work[1:] = flow[:-1]
            self.completed += flow[-1]
        self._open = False
        errors = self.check()
        retained = float(self.queues.sum() + self.inflight_work.sum())
        pending_minutes = float(self.pending.sum())
        startup_minutes = float((self.active - self.effective_staff).sum())
        result = {
            **self._begin_record,
            "raw_proposal_delta": raw_delta.tolist(),
            "applied_delta": applied.tolist(),
            "delta": applied.tolist(),
            "committed_staff": self.committed.tolist(),
            "active_staff": self.active.tolist(),
            "pending_staff": self.pending.tolist(),
            "effective_staff": self.effective_staff.tolist(),
            "ready_at": self.ready_at,
            "flow": flow.tolist(),
            "end_queue": self.queues.tolist(),
            "queue": self.queues.tolist(),
            "inflight_work": self.inflight_work.tolist(),
            "true_productivity": rates.tolist(),
            "offered_cumulative": float(self.offered),
            "completed_cumulative": float(self.completed),
            "cumulative_drops_by_stage": self.cumulative_drops.tolist(),
            "cumulative_drops": float(self.cumulative_drops.sum()),
            "retained_work": retained,
            "drop_adjusted_deficit": float(self.offered - self.completed),
            "pending_resource_minutes": pending_minutes,
            "startup_capacity_minutes": startup_minutes,
            "max_mass_error": float(self.max_mass_error),
            "max_budget_error": float(self.max_budget_error),
            **errors,
        }
        self._begin_record = None
        return result
