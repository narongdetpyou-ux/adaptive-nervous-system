"""Independent scalar verification of v0.4 physics and observation delivery.

Production code must not call this reference transition.  The verifier may
read environment truth to check recorded evidence; a controller replay receives
only the recorded past/current measurements and its exact resource ledger.
"""
from __future__ import annotations

import math
from statistics import mean

from abc_service.frozen import catalog

TOL = 1e-6
PERMITTED = frozenset(tuple(float(x) for x in a["delta"]) for a in catalog()["actions"])


def require(condition, message):
    if not condition:
        raise ValueError(message)


def close(a, b):
    return math.isfinite(float(a)) and math.isfinite(float(b)) and math.isclose(
        float(a), float(b), rel_tol=1e-10, abs_tol=TOL)


def vector_close(a, b):
    return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))


def vector(values, label, nonnegative=True):
    values = [float(x) for x in values]
    require(len(values) == 3 and all(math.isfinite(x) for x in values), "Invalid " + label)
    require(not nonnegative or min(values) >= 0, "Negative " + label)
    return values


def reference_projection(staff, raw, config):
    """Largest remainder, lower stage index first on an exact remainder tie."""
    staff, raw = vector(staff, "staff"), vector(raw, "proposal", False)
    require(close(sum(staff), config["budget"]), "Invalid resource budget")
    require(min(staff) >= config["minimum_staff"], "Invalid initial reserve")
    require(close(sum(raw), 0), "Nonconserving raw proposal")
    require(not any(raw) or tuple(raw) in PERMITTED, "Raw proposal is not in catalogue")
    proposed = [s + d for s, d in zip(staff, raw)]
    require(min(proposed) >= config["minimum_staff"], "Raw target reserve violation")
    if not config["integer_resources"]:
        return raw
    require(all(x == round(x) for x in staff), "Fractional integer-regime staff")
    target = [math.floor(x) for x in proposed]
    remaining = int(round(config["budget"] - sum(target)))
    require(0 <= remaining < 3, "Invalid projection remainder")
    order = sorted(range(3), key=lambda i: (-(proposed[i] - target[i]), i))
    for i in order[:remaining]:
        target[i] += 1
    require(sum(target) == config["budget"] and min(target) >= config["minimum_staff"],
            "Invalid projected resource allocation")
    return [float(target[i] - staff[i]) for i in range(3)]


class ReferenceEnvironment:
    """Scalar reference with independently implemented resource/work ledgers."""
    def __init__(self, config):
        self.config = config.as_dict() if hasattr(config, "as_dict") else dict(config)
        self.committed = [self.config["budget"] / 3] * 3
        self.active = self.committed.copy()
        self.pending = []  # (activation time, destination vector)
        self.startup = []  # (first active time, destination vector)
        self.queue = [0.0] * 3
        self.inflight = [0.0] * 3
        self.drops = [0.0] * 3
        self.offered = self.completed = 0.0
        self.last_minute = -1
        self.last_move = -10**9
        self.open = False
        self.moves = []
        self.observed_pending_loss = self.observed_startup_loss = 0.0
        self.max_mass_error = self.max_budget_error = 0.0

    def pending_units(self):
        return [sum(units[i] for _, units in self.pending) for i in range(3)]

    def effective(self, t):
        factors = self.config["startup_factors"]
        return [self.active[i] - sum(
            units[i] * (1 - factors[t - start])
            for start, units in self.startup if 0 <= t - start < len(factors)
        ) for i in range(3)]

    def check(self, t):
        pending, effective = self.pending_units(), self.effective(t)
        budget = max(abs(sum(self.committed) - self.config["budget"]),
                     abs(sum(self.active) + sum(pending) - self.config["budget"]),
                     max(abs(self.committed[i] - self.active[i] - pending[i]) for i in range(3)))
        mass = abs(self.offered - self.completed - sum(self.queue) - sum(self.inflight) - sum(self.drops))
        require(budget <= TOL and min(self.active) >= 0 and min(pending) >= 0, "Resource conservation")
        require(min(self.committed) >= self.config["minimum_staff"], "Resource reserve")
        require(all(-TOL <= effective[i] <= self.active[i] + TOL for i in range(3)), "Startup capacity")
        require(mass <= TOL and min(self.queue + self.inflight + self.drops) >= 0, "Work conservation")
        require(self.inflight[0] == 0, "Invalid inflight destination")
        for i, cap in enumerate(self.config["buffer_capacities"]):
            require(cap is None or self.queue[i] <= cap + TOL, "Buffer limit")
        if self.config["integer_resources"]:
            require(all(x == round(x) for x in self.committed + self.active + pending), "Resource integrality")
        self.max_mass_error = max(self.max_mass_error, mass)
        self.max_budget_error = max(self.max_budget_error, budget)
        return mass, budget

    def begin(self, t, arrival):
        require(type(t) is int and t == self.last_minute + 1 and not self.open,
                "Reference minutes must be consecutive begin/service pairs")
        arrival = float(arrival)
        require(math.isfinite(arrival) and arrival >= 0, "Invalid arrival")
        remaining = []
        for ready, units in self.pending:
            if ready <= t:
                self.active = [a + x for a, x in zip(self.active, units)]
                if self.config["startup_factors"]:
                    self.startup.append((t, units.copy()))
            else:
                remaining.append((ready, units))
        self.pending = remaining
        self.startup = [(s, u) for s, u in self.startup if t - s < len(self.config["startup_factors"])]
        inflight_before = self.inflight.copy()
        incoming = [arrival, inflight_before[1], inflight_before[2]]
        admitted, dropped = [], []
        for i, amount in enumerate(incoming):
            cap = self.config["buffer_capacities"][i]
            accepted = amount if cap is None else min(amount, max(0.0, cap - self.queue[i]))
            self.queue[i] += accepted
            self.drops[i] += amount - accepted
            admitted.append(accepted)
            dropped.append(amount - accepted)
        self.inflight = [0.0] * 3
        self.offered += arrival
        self.last_minute, self.open = t, True
        self.check(t)
        self.begin_record = {
            "t": t, "arrival": arrival, "start_queue": self.queue.copy(),
            "observed": self.queue.copy(), "staff_before": self.committed.copy(),
            "active_staff_before": self.active.copy(), "pending_staff_before": self.pending_units(),
            "effective_staff_before": self.effective(t), "inflight_before": inflight_before,
            "incoming_by_stage": incoming, "admitted_by_stage": admitted, "dropped_by_stage": dropped,
        }
        return self.begin_record.copy()

    def service(self, t, raw, rates):
        require(self.open and t == self.last_minute, "Service without current begin")
        rates, raw = vector(rates, "rates"), vector(raw, "raw proposal", False)
        delta = reference_projection(self.committed, raw, self.config)
        if any(delta):
            require(t >= self.config["warmup_minutes"] and t - self.last_move >= self.config["dwell_minutes"],
                    "Warmup/dwell violation")
            require(not self.pending and not self.startup, "Overlapping resource transition")
            self.last_move = t
            self.committed = [s + d for s, d in zip(self.committed, delta)]
            self.active = [s + min(d, 0) for s, d in zip(self.active, delta)]
            incoming = [max(d, 0) for d in delta]
            if self.config["transit_delay"]:
                self.pending.append((t + self.config["transit_delay"], incoming))
            else:
                self.active = [s + d for s, d in zip(self.active, incoming)]
                if self.config["startup_factors"]:
                    self.startup.append((t, incoming.copy()))
            self.moves.append((t, sum(incoming)))
        effective = self.effective(t)
        flow = []
        for i in range(3):
            served = min(self.queue[i], effective[i] * rates[i])
            self.queue[i] -= served
            flow.append(served)
            if i < 2 and self.config["same_minute_forwarding"]:
                self.queue[i + 1] += served
        if not self.config["same_minute_forwarding"]:
            self.inflight = [0.0, flow[0], flow[1]]
        self.completed += flow[2]
        self.open = False
        mass, budget = self.check(t)
        pending_loss = sum(self.pending_units())
        startup_loss = sum(a - e for a, e in zip(self.active, effective))
        self.observed_pending_loss += pending_loss
        self.observed_startup_loss += startup_loss
        require(close(self.config["budget"] - sum(effective), pending_loss + startup_loss),
                "Effective capacity loss double counted")
        return {
            **self.begin_record,
            "raw_proposal_delta": raw, "applied_delta": delta, "delta": delta,
            "committed_staff": self.committed.copy(), "active_staff": self.active.copy(),
            "pending_staff": self.pending_units(), "effective_staff": effective,
            "ready_at": self.pending[0][0] if self.pending else None,
            "flow": flow, "end_queue": self.queue.copy(), "queue": self.queue.copy(),
            "inflight_work": self.inflight.copy(), "true_productivity": rates,
            "offered_cumulative": self.offered, "completed_cumulative": self.completed,
            "cumulative_drops_by_stage": self.drops.copy(), "cumulative_drops": sum(self.drops),
            "retained_work": sum(self.queue) + sum(self.inflight),
            "drop_adjusted_deficit": self.offered - self.completed,
            "pending_resource_minutes": pending_loss, "startup_capacity_minutes": startup_loss,
            "mass_error": mass, "budget_error": budget,
            "max_mass_error": self.max_mass_error, "max_budget_error": self.max_budget_error,
        }

    def exposure(self):
        """Observed and still-unobserved costs add to the exact scheduled cost."""
        moved = sum(amount for _, amount in self.moves)
        factors = self.config["startup_factors"]
        pending_future = sum(max(0, ready - self.last_minute - 1) * sum(units)
                             for ready, units in self.pending)
        startup_future = sum(sum(units) * sum(1 - x for x in factors) for _, units in self.pending)
        startup_future += sum(sum(units) * sum(1 - x for x in factors[self.last_minute - started + 1:])
                              for started, units in self.startup)
        require(close(self.observed_pending_loss + pending_future, moved * self.config["transit_delay"]),
                "Transit resource-minute accounting mismatch")
        require(close(self.observed_startup_loss + startup_future, moved * sum(1 - x for x in factors)),
                "Startup capacity-minute accounting mismatch")
        return {"moved_units": moved, "moves": len(self.moves),
                "observed_pending_resource_minutes": self.observed_pending_loss,
                "future_pending_resource_minutes": pending_future,
                "observed_startup_capacity_minutes": self.observed_startup_loss,
                "future_startup_capacity_minutes": startup_future}


def audit_physical_records(minutes, config, arrivals=None, rates=None):
    """Replay independent scalar physics and compare every recorded truth field."""
    ref = ReferenceEnvironment(config)
    for t, minute in enumerate(minutes):
        arrival = float(arrivals[t]) if arrivals is not None else minute["arrival"]
        productivity = rates[t] if rates is not None else minute["true_productivity"]
        ref.begin(t, arrival)
        expected = ref.service(t, minute["raw_proposal_delta"], productivity)
        for field, value in expected.items():
            require(field in minute, f"Missing physical field {field} at {t}")
            actual = minute[field]
            if value is None:
                ok = actual is None
            elif isinstance(value, list):
                ok = vector_close(actual, value)
            else:
                ok = close(actual, value)
            require(ok, f"Physical mismatch {field} at {t}")
    return {"minutes": len(minutes), **ref.exposure()}


def retained_clearance(retained, horizon):
    """Drops remain in deficit; physical clearance uses retained work only."""
    require(0 < horizon <= len(retained), "Invalid clearance horizon")
    if retained[horizon - 1] <= TOL:
        return 0, False
    for offset, value in enumerate(retained[horizon:], 1):
        if value <= TOL:
            return offset, False
    return None, True


def audit_observation_delivery(minutes, scenario, config):
    """Verify source times, common noise and same-source resource exposure.

    This is an offline data-boundary audit, not controller input construction.
    A decision at t can see pre-service source t-lag and post-service feedback
    only through source t-1-lag. Cold samples are explicit zeros.
    """
    config = config.as_dict() if hasattr(config, "as_dict") else dict(config)
    history = []
    for t, row in enumerate(minutes):
        source = t - config["sensor_lag"]
        expected_source = None if source < 0 else source
        age = None if source < 0 else t - source
        d, f = row["decision_input"], row["feedback_input"]
        for sample in (d, f):
            require(sample["delivery_t"] == t and sample["source_t"] == expected_source and
                    sample["observation_age"] == age, f"Incorrect sensor timestamp at {t}")
        require(vector_close(d["committed_staff"], row["staff_before"]), "Noised or delayed command ledger")
        require(len(d["history"]) == len(history) and
                all(vector_close(x, y) for x, y in zip(d["history"], history)),
                f"Feedback history contains missing or future samples at {t}")
        if source < 0:
            queues, arrival, flow, end, staff = [0.] * 3, 0., [0.] * 3, [0.] * 3, [config["budget"] / 3] * 3
        else:
            physical = minutes[source]
            def sample(channel, value):
                if not config["sensor_noise_sigma"]:
                    return value
                noise = scenario.sensor_noise[channel][source]
                if isinstance(value, list):
                    return [float(x) * float(y) for x, y in zip(value, noise)]
                return float(value) * float(noise)
            queues = sample("pre_queue", physical["start_queue"])
            arrival = sample("arrival", physical["arrival"])
            flow = sample("flow", physical["flow"])
            end = sample("end_queue", physical["end_queue"])
            staff = physical["effective_staff"]
        for actual, expected, field in ((d["queues"], queues, "pre_queue"), (f["flow"], flow, "flow"),
                                        (f["end_queue"], end, "end_queue"),
                                        (f["effective_staff"], staff, "same-source effective staff")):
            require(vector_close(actual, expected), f"Incorrect measured {field} at {t}")
        require(close(d["arrival"], arrival), f"Incorrect measured arrival at {t}")
        require(f["resource_normalization"] == "same-source known effective service units, including startup",
                "Missing resource-normalization declaration")
        history = (history + [list(f["end_queue"])])[-5:]
    return {"sensor_minutes_checked": len(minutes), "causal_source_times_match": True,
            "common_source_noise_matches": True, "same_source_effective_staff_matches": True}


def audit_controller_replay(rep, controller):
    """Action/evidence replay with observation payloads only, never truth arrays.

    Instantiate the controller outside this function. The replay supplies only
    each recorded measured queue/arrival, past delivered feedback, and the exact
    commanded allocation. It does not read productivity, phase or horizon.
    """
    import numpy as np
    history = []
    for row in rep["minutes"]:
        decision, feedback = row["decision_input"], row["feedback_input"]
        t = decision["delivery_t"]
        require(len(history) == len(decision["history"]) and
                all(np.array_equal(x, np.array(y)) for x, y in zip(history, decision["history"])),
                "Controller replay history mismatch")
        if controller is None:
            delta, evidence = np.zeros(3), {"branch": "fixed"}
        else:
            delta, evidence = controller.decide(
                t, np.array(decision["queues"]), [x.copy() for x in history],
                np.array(decision["committed_staff"]), decision["arrival"],
            )
        require(np.array_equal(delta, np.array(row["raw_proposal_delta"])),
                f"Observation-only action replay mismatch at {t}")
        require(evidence == row["decision_evidence"], f"Observation-only evidence replay mismatch at {t}")
        require(evidence["branch"] == row["branch"], "Recorded branch mismatch")
        if controller is not None:
            controller.observe(feedback["delivery_t"], np.array(feedback["effective_staff"]),
                               np.array(feedback["flow"]), np.array(feedback["end_queue"]))
        history = (history + [np.array(feedback["end_queue"])])[-5:]
    return {"decisions_replayed": len(rep["minutes"]), "actions_and_evidence_match": True}


def audit_trace(rep, scenario, regime):
    """Full physical, observation delivery and aggregate replay of one trace."""
    from v4_environment import REGIME_CONFIGS
    from v4_scenarios import FOLLOWUP_MINUTES
    require(regime in REGIME_CONFIGS, "Unknown physical regime")
    row, minutes = rep["summary"], rep["minutes"]
    horizon, total = scenario.horizon, scenario.horizon + FOLLOWUP_MINUTES
    require((row["group"], row["scenario_seed"], row["regime"]) == (scenario.group, scenario.seed, regime),
            "Trace identity mismatch")
    require(row["horizon"] == horizon and row["followup_minutes"] == FOLLOWUP_MINUTES,
            "Trace horizon mismatch")
    require(len(minutes) == total, "Incomplete physical trace")
    arrivals = [float(x) for x in scenario.arrivals] + [0.] * FOLLOWUP_MINUTES
    rates = [[float(x) for x in p] for p in scenario.productivity]
    rates.extend([rates[-1].copy() for _ in range(FOLLOWUP_MINUTES)])
    exposure = audit_physical_records(minutes, REGIME_CONFIGS[regime], arrivals, rates)
    sensor = audit_observation_delivery(minutes, scenario, REGIME_CONFIGS[regime])
    for t, minute in enumerate(minutes):
        require(minute["phase"] == ("primary" if t < horizon else "followup"), "Trace phase mismatch")
        if row["policy"] == "static":
            require(not any(minute["applied_delta"]), "Static policy transferred resources")
    deficit = [m["drop_adjusted_deficit"] for m in minutes]
    retained = [m["retained_work"] for m in minutes]
    completed = [m["completed_cumulative"] for m in minutes]
    dropped = [m["cumulative_drops"] for m in minutes]
    pending = [m["pending_resource_minutes"] for m in minutes]
    startup = [m["startup_capacity_minutes"] for m in minutes]
    move_times = [t for t, m in enumerate(minutes) if any(m["applied_delta"])]
    expected = {
        "mean_deficit": mean(deficit[:horizon]), "peak_deficit": max(deficit[:horizon]),
        "end_deficit": deficit[horizon - 1], "tail_mean_deficit": mean(deficit[horizon:]),
        "tail_auc_deficit": sum(deficit[horizon:]), "tail_end_deficit": deficit[-1],
        "mean_queue": mean(retained[:horizon]), "peak_queue": max(retained[:horizon]),
        "end_queue": retained[horizon - 1], "tail_mean_queue": mean(retained[horizon:]),
        "tail_auc_queue": sum(retained[horizon:]), "tail_end_queue": retained[-1],
        "offered": sum(arrivals), "completed": completed[horizon - 1],
        "tail_completed": completed[-1] - completed[horizon - 1], "total_completed": completed[-1],
        "dropped": dropped[horizon - 1], "tail_dropped": dropped[-1] - dropped[horizon - 1],
        "total_dropped": dropped[-1], "moves": sum(t < horizon for t in move_times),
        "tail_moves": sum(t >= horizon for t in move_times), "total_moves": len(move_times),
        "resource_minutes_lost": sum(pending[:horizon]), "tail_resource_minutes_lost": sum(pending[horizon:]),
        "startup_capacity_minutes_lost": sum(startup[:horizon]),
        "tail_startup_capacity_minutes_lost": sum(startup[horizon:]),
        "end_inflight_work": sum(minutes[horizon - 1]["inflight_work"]),
        "tail_end_inflight_work": sum(minutes[-1]["inflight_work"]),
        "peak_service_queue_max_over_full_run": max(sum(m["end_queue"]) for m in minutes),
        "in_transit_at_end": sum(minutes[-1]["pending_staff"]),
        "outstanding_resource_minutes": exposure["future_pending_resource_minutes"],
        "max_mass_error": max(m["max_mass_error"] for m in minutes),
        "max_budget_error": max(m["max_budget_error"] for m in minutes),
        "balance_preferred_moves": sum(m["branch"] == "balance_preferred" for m in minutes),
        "idle_recovery_moves": sum(m["branch"] == "idle_balance_recovery" for m in minutes),
        "soft_risk_holds": sum(m["branch"] == "soft_risk_hold" for m in minutes),
    }
    for field, value in expected.items():
        require(close(row[field], value), "Episode summary mismatch: " + field)
    clear, censored = retained_clearance(retained, horizon)
    require(row["drain_time_observed"] == clear and type(row["drain_censored"]) is bool and
            row["drain_censored"] == censored and row["drain_time_capped"] == (FOLLOWUP_MINUTES if censored else clear),
            "Physical drainage/censoring mismatch")
    return {"minutes_replayed": total, "scoring_minutes": horizon,
            "followup_minutes": FOLLOWUP_MINUTES, "moves_checked": len(move_times),
            "max_mass_error": expected["max_mass_error"], "max_budget_error": expected["max_budget_error"],
            **exposure, **sensor}


def audit_rows(rows, scenarios, seeds_by_policy=None, regimes=None):
    """Full row scope and accounting audit, distinct from minute-level replay."""
    from v4_environment import REGIME_CONFIGS
    from v4_scenarios import FOLLOWUP_MINUTES, DEFAULT_SEEDS_BY_POLICY
    seeds_by_policy = DEFAULT_SEEDS_BY_POLICY if seeds_by_policy is None else seeds_by_policy
    require(seeds_by_policy and all(tuple(seeds) == (None,) for seeds in seeds_by_policy.values()),
            "Unexpected policy replicate selection")
    require(all(p in DEFAULT_SEEDS_BY_POLICY for p in seeds_by_policy), "Unknown policy selection")
    regimes = tuple(REGIME_CONFIGS) if regimes is None else tuple(regimes)
    require(regimes and len(set(regimes)) == len(regimes) and all(r in REGIME_CONFIGS for r in regimes),
            "Invalid regime scope")
    cases = {(s.group, s.seed): s for s in scenarios}
    require(len(cases) == len(scenarios), "Duplicate case identity")
    expected = {(regime, group, seed, policy, None) for regime in regimes for group, seed in cases
                for policy in seeds_by_policy}
    actual = {(r["regime"], r["group"], r["scenario_seed"], r["policy"], r["policy_seed"]) for r in rows}
    require(actual == expected and len(rows) == len(expected), "Missing, duplicate or unexpected episode")
    numeric = (
        "mean_deficit", "peak_deficit", "end_deficit", "tail_mean_deficit", "tail_auc_deficit", "tail_end_deficit",
        "mean_queue", "peak_queue", "end_queue", "tail_mean_queue", "tail_auc_queue", "tail_end_queue",
        "offered", "completed", "tail_completed", "total_completed", "dropped", "tail_dropped", "total_dropped",
        "resource_minutes_lost", "tail_resource_minutes_lost", "startup_capacity_minutes_lost",
        "tail_startup_capacity_minutes_lost", "end_inflight_work", "tail_end_inflight_work",
        "peak_service_queue_max_over_full_run", "in_transit_at_end", "outstanding_resource_minutes",
        "max_mass_error", "max_budget_error",
    )
    max_transfer = max(sum(max(d, 0) for d in action) for action in PERMITTED)
    original_minutes = 0
    for row in rows:
        scenario = cases[row["group"], row["scenario_seed"]]
        cfg = REGIME_CONFIGS[row["regime"]]
        h, total = scenario.horizon, scenario.horizon + FOLLOWUP_MINUTES
        require(row["source"] == scenario.source and row["horizon"] == h and
                row["followup_minutes"] == FOLLOWUP_MINUTES, "Row metadata mismatch")
        for field in numeric:
            require(isinstance(row[field], (int, float)) and not isinstance(row[field], bool) and
                    math.isfinite(row[field]) and row[field] >= -TOL,
                    "Invalid numeric row field: " + field)
        require(close(row["offered"], sum(float(x) for x in scenario.arrivals)), "Offered work mismatch")
        identities = {
            "total_completed": row["completed"] + row["tail_completed"],
            "total_dropped": row["dropped"] + row["tail_dropped"],
            "end_deficit": row["offered"] - row["completed"],
            "tail_end_deficit": row["offered"] - row["total_completed"],
            "end_queue": row["offered"] - row["completed"] - row["dropped"],
            "tail_end_queue": row["offered"] - row["total_completed"] - row["total_dropped"],
            "tail_auc_deficit": FOLLOWUP_MINUTES * row["tail_mean_deficit"],
            "tail_auc_queue": FOLLOWUP_MINUTES * row["tail_mean_queue"],
        }
        for field, expected_value in identities.items():
            require(close(row[field], expected_value), "Row accounting failed: " + field)
        for smaller, larger in (("mean_deficit", "peak_deficit"), ("end_deficit", "peak_deficit"),
                                ("mean_queue", "peak_queue"), ("end_queue", "peak_queue"),
                                ("mean_queue", "mean_deficit"), ("peak_queue", "peak_deficit"),
                                ("end_queue", "end_deficit"), ("tail_end_queue", "tail_end_deficit"),
                                ("end_inflight_work", "end_queue"), ("tail_end_inflight_work", "tail_end_queue")):
            require(row[smaller] <= row[larger] + TOL, f"Invalid ordering: {smaller}/{larger}")
        require(row["mean_deficit"] - row["mean_queue"] <= row["dropped"] + TOL,
                "Mean deficit contains unexplained loss")
        for suffix in ("deficit", "queue"):
            require(row["tail_end_" + suffix] <= row["tail_mean_" + suffix] + TOL <= row["end_" + suffix] + 2 * TOL,
                    "Zero-arrival follow-up quantity increases: " + suffix)
        bounds = (("moves", max(0, (h - 1 - cfg.warmup_minutes) // cfg.dwell_minutes + 1)),
                  ("tail_moves", (FOLLOWUP_MINUTES - 1) // cfg.dwell_minutes + 1),
                  ("total_moves", max(0, (total - 1 - cfg.warmup_minutes) // cfg.dwell_minutes + 1)))
        for field, upper in bounds:
            require(type(row[field]) is int and 0 <= row[field] <= upper, "Invalid move count: " + field)
        require(row["total_moves"] == row["moves"] + row["tail_moves"], "Move total mismatch")
        require(row["max_mass_error"] <= TOL and row["max_budget_error"] <= TOL, "Recorded conservation failure")
        require(row["in_transit_at_end"] <= max_transfer + TOL, "Excess outstanding resources")
        require(row["outstanding_resource_minutes"] <= max(0, cfg.transit_delay - 1) * row["in_transit_at_end"] + TOL,
                "Impossible future pending exposure")
        transit = row["resource_minutes_lost"] + row["tail_resource_minutes_lost"] + row["outstanding_resource_minutes"]
        startup = row["startup_capacity_minutes_lost"] + row["tail_startup_capacity_minutes_lost"]
        require(transit <= row["total_moves"] * max_transfer * cfg.transit_delay + TOL, "Excess pending cost")
        require(startup <= row["total_moves"] * max_transfer * sum(1 - f for f in cfg.startup_factors) + TOL,
                "Excess startup cost")
        if not cfg.transit_delay:
            require(transit == 0 and row["in_transit_at_end"] == 0, "Instant regime has pending exposure")
        if not cfg.startup_factors:
            require(startup == 0, "No-startup regime charged startup cost")
        if cfg.same_minute_forwarding:
            require(row["end_inflight_work"] == 0 and row["tail_end_inflight_work"] == 0, "Instant forwarding has work in flight")
        if all(cap is None for cap in cfg.buffer_capacities):
            require(row["total_dropped"] == 0, "Unlimited buffers dropped work")
        else:
            require(row["peak_service_queue_max_over_full_run"] <= sum(cfg.buffer_capacities) + TOL,
                    "Service queue exceeds finite buffers")
        if row["policy"] == "static":
            require(row["total_moves"] == 0 and transit == 0 and startup == 0, "Static policy paid transfer cost")
        for field in ("balance_preferred_moves", "idle_recovery_moves", "soft_risk_holds"):
            require(type(row[field]) is int and 0 <= row[field] <= total, "Invalid branch count")
        require(row["balance_preferred_moves"] + row["idle_recovery_moves"] <= row["total_moves"],
                "More selected branches than actual moves")
        require(type(row["drain_censored"]) is bool and row["drain_censored"] == (row["tail_end_queue"] > TOL),
                "Physical clearance censoring mismatch")
        if row["drain_censored"]:
            require(row["drain_time_observed"] is None and row["drain_time_capped"] == FOLLOWUP_MINUTES,
                    "Censored time must not be called observed")
        else:
            clear = row["drain_time_observed"]
            require(type(clear) is int and 0 <= clear <= FOLLOWUP_MINUTES and row["drain_time_capped"] == clear,
                    "Invalid observed clearance time")
        if row["end_queue"] <= TOL:
            require(row["drain_time_observed"] == 0 and not row["drain_censored"], "Empty system clears late")
        elif not row["drain_censored"]:
            require(row["drain_time_observed"] >= 1, "Nonempty system claims immediate clearance")
        original_minutes += h
    followup_minutes = FOLLOWUP_MINUTES * len(rows)
    return {"scenarios_checked": len(scenarios), "regimes_checked": len(regimes),
            "scenario_regime_pairs": len(scenarios) * len(regimes), "episodes_checked": len(rows),
            "original_minutes": original_minutes, "followup_minutes": followup_minutes,
            "simulated_minutes": original_minutes + followup_minutes}
