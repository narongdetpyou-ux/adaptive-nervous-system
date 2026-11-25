"""Independent scalar reference for v0.3 transfer delays and physical replay.

This module is a verifier. The production simulator must not use its transition
or queue-service implementation. It intentionally imports no v0.3 simulator or
controller implementation.
"""
from __future__ import annotations

import math
from statistics import mean

from abc_service.frozen import catalog

BASE = (69.0, 69.0, 69.0)
BUDGET = 207.0
WARMUP = 5
DWELL = 8
TOL = 1e-6


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def close(left, right):
    return finite(left) and finite(right) and math.isclose(left, right, rel_tol=1e-10, abs_tol=TOL)


def vector_close(left, right):
    return len(left) == len(right) and all(close(float(a), float(b)) for a, b in zip(left, right))


def valid_vector(values, name, nonnegative=True):
    result = [float(value) for value in values]
    require(len(result) == 3 and all(finite(value) for value in result), "Invalid " + name)
    if nonnegative:
        require(min(result) >= 0, "Negative " + name)
    return result


class ReferenceTransitLedger:
    """Scalar timing oracle for verification of resource accounting.

    A transfer at t with delay D has its positive units unavailable for service
    intervals t through t+D-1. At t+D they activate before the next decision.
    Holds never restart this timer or the last-transfer dwell clock.
    """
    def __init__(self, delay):
        require(type(delay) is int and delay in (0, 1, 3), "Delay must be 0, 1 or 3")
        self.delay = delay
        self.active = list(BASE)
        self.committed = list(BASE)
        self.pending = []
        self.last_minute = -1
        self.last_transfer = -10**9
        self.moved_units = 0.0
        self.resource_minutes_observed = 0.0
        self.moves = 0
        self.permitted = {tuple(float(x) for x in action["delta"]) for action in catalog()["actions"]}

    def step(self, minute, delta):
        require(type(minute) is int and minute == self.last_minute + 1,
                "Replay minutes must be consecutive")
        delta = valid_vector(delta, "transfer", nonnegative=False)
        require(close(sum(delta), 0.0), "Transfer does not conserve committed resources")
        remaining = []
        for ready_at, incoming in self.pending:
            if ready_at <= minute:
                self.active = [a + x for a, x in zip(self.active, incoming)]
            else:
                remaining.append((ready_at, incoming))
        self.pending = remaining
        before_active = self.active.copy()
        before_committed = self.committed.copy()
        if any(delta):
            require(tuple(delta) in self.permitted, "Transfer is outside the frozen catalogue")
            require(minute >= WARMUP and minute - self.last_transfer >= DWELL,
                    "Transfer violates warmup or dwell")
            require(not self.pending, "A new transfer precedes completion of an earlier transfer")
            self.active = [a + min(d, 0.0) for a, d in zip(self.active, delta)]
            self.committed = [a + d for a, d in zip(self.committed, delta)]
            require(min(self.active) >= 0 and min(self.committed) >= 10,
                    "Transfer draws unavailable resources")
            incoming = [max(d, 0.0) for d in delta]
            amount = sum(incoming)
            if self.delay == 0:
                self.active = [a + x for a, x in zip(self.active, incoming)]
            else:
                self.pending.append((minute + self.delay, incoming))
            self.moved_units += amount
            self.moves += 1
            self.last_transfer = minute
        in_transit = [sum(incoming[i] for _, incoming in self.pending) for i in range(3)]
        require(vector_close(self.committed, [a + p for a, p in zip(self.active, in_transit)]),
                "Committed resources differ from active plus incoming")
        require(close(sum(self.active) + sum(in_transit), BUDGET), "Total resources changed")
        loss = sum(in_transit)
        self.resource_minutes_observed += loss
        self.last_minute = minute
        return {
            "active_before_decision": before_active,
            "committed_before_decision": before_committed,
            "active": self.active.copy(),
            "committed": self.committed.copy(),
            "in_transit": in_transit,
            "unavailable_resource_units": loss,
        }

    def accounting(self):
        # Remaining intervals after the last service interval already counted.
        future_exposure = sum(
            max(0, ready_at - self.last_minute - 1) * sum(incoming)
            for ready_at, incoming in self.pending
        )
        scheduled = self.moved_units * self.delay
        require(close(self.resource_minutes_observed + future_exposure, scheduled),
                "Transfer resource-minute accounting disagrees")
        return {
            "moves": self.moves,
            "moved_resource_units": self.moved_units,
            "resource_minutes_in_transit": self.resource_minutes_observed,
            "scheduled_resource_minutes": scheduled,
            "unobserved_future_resource_minutes": future_exposure,
        }


def reference_service(carried_queue, arrival, active, productivity):
    """Scalar same-minute three-stage physical service, for verification only."""
    queue = valid_vector(carried_queue, "carried queue")
    staff = valid_vector(active, "active staff")
    rates = valid_vector(productivity, "productivity")
    require(finite(float(arrival)) and arrival >= 0, "Invalid arrival")
    queue[0] += float(arrival)
    observed = queue.copy()
    flow = []
    completed = 0.0
    for stage in range(3):
        amount = min(queue[stage], staff[stage] * rates[stage])
        queue[stage] -= amount
        flow.append(amount)
        if stage < 2:
            queue[stage + 1] += amount
        else:
            completed += amount
    require(close(sum(carried_queue) + arrival, sum(queue) + completed), "Service loses work")
    return {"observed": observed, "queue": queue, "flow": flow, "completed": completed}


def tail_clearance(backlogs, original_horizon):
    """Report observed zero-arrival drain time and right censoring separately."""
    require(type(original_horizon) is int and 0 < original_horizon <= len(backlogs),
            "Invalid original horizon")
    tail_length = len(backlogs) - original_horizon
    if backlogs[original_horizon - 1] <= TOL:
        return {"clearance_tail_minutes": 0, "clearance_censored": False, "tail_length": tail_length}
    for offset, backlog in enumerate(backlogs[original_horizon:], start=1):
        if backlog <= TOL:
            return {"clearance_tail_minutes": offset, "clearance_censored": False, "tail_length": tail_length}
    return {"clearance_tail_minutes": None, "clearance_censored": True, "tail_length": tail_length}


def audit_trace(rep, scenario, regime):
    """Independently replay all original and follow-up intervals of one trace."""
    from v3_scenarios import REGIME_DELAYS, FOLLOWUP_MINUTES
    require(regime in REGIME_DELAYS, "Unknown resource-transition regime")
    row, minutes = rep["summary"], rep["minutes"]
    horizon = scenario.horizon
    total_horizon = horizon + FOLLOWUP_MINUTES
    require((row["group"],row["scenario_seed"],row["regime"]) == (scenario.group,scenario.seed,regime),
            "Trace identity mismatch")
    require(row["horizon"] == horizon and row["followup_minutes"] == FOLLOWUP_MINUTES,
            "Trace summary horizon mismatch")
    require(len(minutes) == total_horizon, "Incomplete physical trace")
    ledger = ReferenceTransitLedger(REGIME_DELAYS[regime])
    queue = [0.0]*3
    completed = arrived = 0.0
    backlogs, completion, losses, move_times = [], [], [], []
    max_mass_error = max_budget_error = 0.0
    for t, minute in enumerate(minutes):
        label = f"minute {t}"
        arrival = float(scenario.arrivals[t]) if t < horizon else 0.0
        rates = [float(x) for x in (scenario.productivity[t] if t < horizon else scenario.productivity[-1])]
        require(minute["t"] == t, "Nonconsecutive trace minute")
        require(minute["phase"] == ("scoring" if t < horizon else "followup"), "Phase mismatch at " + label)
        require(close(minute["arrival"], arrival), "Arrival mismatch at " + label)
        require(vector_close(minute["productivity"], rates), "Productivity mismatch at " + label)
        delta = minute["delta"]
        state = ledger.step(t, delta)
        if any(delta):
            require(row["policy"] != "static", "Static policy moved resources")
            move_times.append(t)
        fields = {
            "staff_before": "committed_before_decision",
            "committed_staff": "committed", "active_staff": "active", "pending_staff": "in_transit",
        }
        for field, reference in fields.items():
            require(vector_close(minute[field], state[reference]), "Resource mismatch: " + field + " at " + label)
        ready_at = ledger.pending[0][0] if ledger.pending else None
        require(minute["ready_at"] == ready_at, "Activation-time mismatch at " + label)
        require(close(minute["resource_minutes_lost"], state["unavailable_resource_units"]),
                "In-transit resource-minute mismatch at " + label)
        physical = reference_service(queue, arrival, state["active"], rates)
        for field in ("observed", "flow", "queue"):
            require(vector_close(minute[field], physical[field]), "Physical " + field + " mismatch at " + label)
        queue = physical["queue"]
        completed += physical["completed"]
        arrived += arrival
        require(close(minute["completed_cumulative"], completed), "Completed-work mismatch at " + label)
        mass_error = abs(arrived-completed-sum(queue))
        budget_error = max(abs(sum(state["committed"])-BUDGET),
                           abs(sum(state["active"])+sum(state["in_transit"])-BUDGET))
        require(mass_error <= TOL, "Mass-conservation mismatch at " + label)
        max_mass_error = max(max_mass_error, mass_error)
        max_budget_error = max(max_budget_error, budget_error)
        backlogs.append(sum(queue))
        completion.append(completed)
        losses.append(state["unavailable_resource_units"])
    primary, tail = backlogs[:horizon], backlogs[horizon:]
    exposure = ledger.accounting()
    expected = {
        "mean_backlog": mean(primary), "peak_backlog": max(primary), "end_backlog": primary[-1],
        "arrivals": arrived, "completed": completion[horizon-1],
        "moves": sum(t<horizon for t in move_times),
        "tail_mean_backlog": mean(tail), "tail_auc": sum(tail), "tail_end_backlog": tail[-1],
        "tail_completed": completion[-1]-completion[horizon-1], "total_completed": completion[-1],
        "tail_moves": sum(t>=horizon for t in move_times), "total_moves": len(move_times),
        "resource_minutes_lost": sum(losses[:horizon]), "tail_resource_minutes_lost": sum(losses[horizon:]),
        "in_transit_at_end": sum(sum(incoming) for _,incoming in ledger.pending),
        "outstanding_resource_minutes": exposure["unobserved_future_resource_minutes"],
        "max_mass_error": max_mass_error, "max_budget_error": max_budget_error,
    }
    for field, value in expected.items():
        require(close(row[field],value), "Episode summary mismatch: " + field)
    clearance = tail_clearance(backlogs,horizon)
    require(row["drain_time_observed"] == clearance["clearance_tail_minutes"], "Observed clearance time mismatch")
    require(type(row["drain_censored"]) is bool and row["drain_censored"] == clearance["clearance_censored"],
            "Clearance censoring mismatch")
    capped = FOLLOWUP_MINUTES if clearance["clearance_censored"] else clearance["clearance_tail_minutes"]
    require(row["drain_time_capped"] == capped, "Capped clearance time mismatch")
    return {"minutes_replayed": total_horizon, "scoring_minutes": horizon,
            "followup_minutes": FOLLOWUP_MINUTES, "moves_checked": len(move_times),
            "max_mass_error": max_mass_error, "max_budget_error": max_budget_error,
            **exposure}


def audit_rows(rows, scenarios, seeds_by_policy, regimes=None):
    """Check complete scope, conservation, transfer exposure and tail censoring.

    Summary validation does not reconstruct every minute. Complete scalar
    physical reconstruction is performed by audit_trace on prescribed traces.
    """
    from v3_scenarios import REGIME_DELAYS, FOLLOWUP_MINUTES
    regimes = tuple(REGIME_DELAYS) if regimes is None else tuple(regimes)
    require(regimes and len(set(regimes)) == len(regimes) and all(r in REGIME_DELAYS for r in regimes),
            "Invalid regime scope")
    cases = {(s.group,s.seed):s for s in scenarios}
    require(len(cases) == len(scenarios), "Duplicate scenario identity")
    expected = {(regime,g,seed,policy,draw) for regime in regimes for g,seed in cases
                for policy,draws in seeds_by_policy.items() for draw in draws}
    actual = {(r["regime"],r["group"],r["scenario_seed"],r["policy"],r["policy_seed"]) for r in rows}
    require(actual == expected and len(rows) == len(expected), "Missing, duplicated or unexpected episode")
    max_moved_units = max(sum(max(x,0.0) for x in action["delta"]) for action in catalog()["actions"])
    original_minutes = followup_minutes = 0
    numeric_fields = (
        "mean_backlog","peak_backlog","end_backlog","arrivals","completed", "tail_mean_backlog",
        "tail_auc","tail_end_backlog","tail_completed","total_completed","resource_minutes_lost",
        "tail_resource_minutes_lost","in_transit_at_end","outstanding_resource_minutes",
        "max_mass_error","max_budget_error",
    )
    for row in rows:
        scenario = cases[row["group"],row["scenario_seed"]]
        horizon = scenario.horizon
        total_horizon = horizon + FOLLOWUP_MINUTES
        delay = REGIME_DELAYS[row["regime"]]
        require(row["source"] == scenario.source and row["horizon"] == horizon and
                row["followup_minutes"] == FOLLOWUP_MINUTES, "Episode metadata mismatch")
        for field in numeric_fields:
            require(finite(row[field]) and row[field] >= 0, "Invalid numeric summary: " + field)
        arrivals = float(sum(scenario.arrivals))
        require(close(row["arrivals"],arrivals), "Input count mismatch")
        require(close(arrivals,row["completed"]+row["end_backlog"]), "Lost work in primary summary")
        require(close(arrivals,row["total_completed"]+row["tail_end_backlog"]), "Lost work in follow-up summary")
        require(close(row["total_completed"],row["completed"]+row["tail_completed"]), "Completion totals disagree")
        require(0 <= row["mean_backlog"] <= row["peak_backlog"]+TOL and
                0 <= row["end_backlog"] <= row["peak_backlog"]+TOL, "Invalid primary backlog bounds")
        require(row["tail_end_backlog"] <= row["tail_mean_backlog"]+TOL <= row["end_backlog"]+2*TOL,
                "Zero-arrival tail backlog increased")
        require(close(row["tail_auc"],FOLLOWUP_MINUTES*row["tail_mean_backlog"]), "Tail AUC mismatch")
        max_primary_moves = max(0,(horizon-1-WARMUP)//DWELL+1)
        max_total_moves = max(0,(total_horizon-1-WARMUP)//DWELL+1)
        for field, upper in (("moves",max_primary_moves),("tail_moves",(FOLLOWUP_MINUTES-1)//DWELL+1),
                             ("total_moves",max_total_moves)):
            require(type(row[field]) is int and 0 <= row[field] <= upper, "Invalid move count: " + field)
        require(row["total_moves"] == row["moves"]+row["tail_moves"], "Move counts disagree")
        require(row["max_mass_error"] <= TOL and row["max_budget_error"] <= TOL, "Recorded conservation error")
        require(row["in_transit_at_end"] <= max_moved_units+TOL, "Impossible outstanding resource count")
        require(row["outstanding_resource_minutes"] <= max(0,delay-1)*row["in_transit_at_end"]+TOL,
                "Impossible remaining transit exposure")
        exposure = row["resource_minutes_lost"]+row["tail_resource_minutes_lost"]+row["outstanding_resource_minutes"]
        require(exposure <= row["total_moves"]*max_moved_units*delay+TOL, "Transit costs exceed possible moves")
        if delay == 0:
            require(exposure == 0 and row["in_transit_at_end"] == 0, "Instant regime lost resources in transit")
        if row["policy"] == "static":
            require(row["total_moves"] == 0 and exposure == 0 and row["in_transit_at_end"] == 0,
                    "Static policy transferred resources")
        require(type(row["drain_censored"]) is bool, "Censor flag must be boolean")
        require(row["drain_censored"] == (row["tail_end_backlog"]>TOL), "Censor flag disagrees with remaining work")
        if row["drain_censored"]:
            require(row["drain_time_observed"] is None and row["drain_time_capped"] == FOLLOWUP_MINUTES,
                    "Censored clearance must not claim an observed time")
        else:
            require(type(row["drain_time_observed"]) is int and 0 <= row["drain_time_observed"] <= FOLLOWUP_MINUTES,
                    "Invalid observed clearance time")
            require(row["drain_time_capped"] == row["drain_time_observed"], "Uncensored clearance mismatch")
        if row["end_backlog"] <= TOL:
            require(row["drain_time_observed"] == 0 and not row["drain_censored"], "Already-empty queue has delayed clearance")
        original_minutes += horizon
        followup_minutes += FOLLOWUP_MINUTES
    return {"scenarios_checked":len(scenarios),"regimes_checked":len(regimes),
            "scenario_regime_pairs":len(scenarios)*len(regimes),"episodes_checked":len(rows),
            "original_minutes":original_minutes,"followup_minutes":followup_minutes,
            "simulated_minutes":original_minutes+followup_minutes}


def audit_controller_replay(rep, controller):
    """Replay a supplied controller using recorded observations, never rates.

    Instantiate the controller outside this function. Its random stream may be
    reconstructed from declared seeds; no environment rates or future arrivals
    are supplied here. Physical audit_trace must separately validate this log.
    """
    import numpy as np
    history = []
    for minute in rep["minutes"]:
        if controller is None:
            delta, evidence = np.zeros(3), {"branch":"fixed"}
        else:
            delta,evidence = controller.decide(
                minute["t"],np.array(minute["observed"]),[x.copy() for x in history],
                np.array(minute["staff_before"]),minute["arrival"],
            )
        require(np.array_equal(delta,np.array(minute["delta"])), "Observation-only action replay mismatch")
        require(evidence == minute["decision_evidence"], "Observation-only decision evidence mismatch")
        if controller is not None:
            controller.observe(minute["t"],np.array(minute["active_staff"]),
                               np.array(minute["flow"]),np.array(minute["queue"]))
        history = (history+[np.array(minute["queue"])])[-5:]
    return {"decisions_replayed":len(rep["minutes"]),"actions_and_evidence_match":True}
