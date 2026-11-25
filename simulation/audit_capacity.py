"""Independent scalar checks for the v0.2 capacity experiment.

This module deliberately does not import the simulator or policy.  The replay
uses ordinary Python arithmetic and the frozen action catalogue.  It proves
the supplied trace agrees with the specified fluid model, not that the model
describes a deployed service or that a controller is optimal.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import math
from pathlib import Path
from statistics import mean

from abc_service.frozen import catalog

TOL = 1e-6
BASE = (69.0, 69.0, 69.0)
BUDGET = 207.0
WARMUP = 5
DWELL = 8


def require(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def close(left, right):
    return finite(left) and finite(right) and math.isclose(left, right, rel_tol=1e-10, abs_tol=TOL)


def vector_close(left, right):
    return len(left) == len(right) and all(close(float(a), float(b)) for a, b in zip(left, right))


def audit_source_integrity(root):
    root = Path(root)
    results = {}
    for line in (root / "source_checksums.sha256").read_text().splitlines():
        if not line.strip():
            continue
        expected, relative = line.split(maxsplit=1)
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        require(actual == expected, f"Upstream source changed: {relative}")
        results[relative] = actual
    require(len(results) == 11, "Unexpected upstream manifest scope")
    return results


def audit_trace(rep, arrivals, productivity):
    """Replay an episode from its environment and supplied recorded actions.

    The environment is available to the verifier, never to the policy through
    this function.  Counterfactual prediction quality is not checked here.
    """
    row = rep["summary"]
    minutes = rep["minutes"]
    horizon = len(arrivals)
    require(horizon > 0 and len(productivity) == horizon, "Invalid environment horizon")
    require(len(minutes) == horizon, "Trace horizon mismatch")
    permitted = {tuple(float(x) for x in a["delta"]) for a in catalog()["actions"]}
    queue = [0.0, 0.0, 0.0]
    staff = list(BASE)
    completed = 0.0
    arrived = 0.0
    backlogs = []
    previous_transfer = -10**9
    moves = 0
    maximum_mass_error = 0.0
    maximum_budget_error = 0.0
    for t, minute in enumerate(minutes):
        label = f"minute {t}"
        arrival = float(arrivals[t])
        rates = [float(x) for x in productivity[t]]
        require(finite(arrival) and arrival >= 0, f"Invalid arrival at {label}")
        require(len(rates) == 3 and all(finite(x) and x >= 0 for x in rates), f"Invalid rates at {label}")
        require(minute["t"] == t, f"Nonsequential {label}")
        require(close(minute["arrival"], arrival), f"Arrival mismatch at {label}")
        require(vector_close(minute["productivity"], rates), f"Productivity mismatch at {label}")
        queue[0] += arrival
        arrived += arrival
        require(vector_close(minute["observed"], queue), f"Observation mismatch at {label}")
        delta = list(minute["delta"])
        require(len(delta) == 3 and all(finite(x) for x in delta), f"Invalid transfer vector at {label}")
        require(close(sum(delta), 0.0), f"Unbalanced transfer at {label}")
        if any(delta):
            require(row["policy"] != "static", f"Static policy moved at {label}")
            require(tuple(delta) in permitted, f"Transfer outside catalogue at {label}")
            require(t >= WARMUP, f"Warmup violation at {label}")
            require(t - previous_transfer >= DWELL, f"Dwell violation at {label}")
            previous_transfer = t
            moves += 1
        staff = [s + d for s, d in zip(staff, delta)]
        require(min(staff) >= 0.0 and close(sum(staff), BUDGET), f"Budget violation at {label}")
        require(vector_close(minute["staff"], staff), f"Allocation mismatch at {label}")
        flows = []
        for k in range(3):
            service = min(queue[k], staff[k] * rates[k])
            queue[k] -= service
            flows.append(service)
            if k < 2:
                queue[k + 1] += service
            else:
                completed += service
        require(vector_close(minute["flow"], flows), f"Service-flow mismatch at {label}")
        require(vector_close(minute["queue"], queue), f"End-queue mismatch at {label}")
        require(close(minute["completed_cumulative"], completed), f"Completed-work mismatch at {label}")
        require(min(queue) >= -TOL, f"Negative queue at {label}")
        mass_error = abs(arrived - completed - sum(queue))
        budget_error = abs(sum(staff) - BUDGET)
        require(mass_error <= TOL, f"Work conservation failed at {label}")
        maximum_mass_error = max(maximum_mass_error, mass_error)
        maximum_budget_error = max(maximum_budget_error, budget_error)
        backlogs.append(sum(queue))
    expected = {
        "mean_backlog": mean(backlogs),
        "peak_backlog": max(backlogs),
        "end_backlog": backlogs[-1],
        "arrivals": arrived,
        "completed": completed,
        "moves": moves,
    }
    for metric, value in expected.items():
        require(close(row[metric], value), f"Episode summary mismatch: {metric}")
    require(close(row["max_mass_error"], maximum_mass_error), "Maximum mass error mismatch")
    require(close(row["max_budget_error"], maximum_budget_error), "Maximum budget error mismatch")
    return {"minutes_replayed": horizon, "moves_checked": moves,
            "max_mass_error": maximum_mass_error, "max_budget_error": maximum_budget_error}


def audit_rows(rows, scenarios, policy_seeds):
    """Check the complete scope and per-row accounting of an experiment.

    ``policy_seeds`` maps each policy to its exact permitted seed list, e.g.
    {"static": [None], "abc": [11, 29, 47]}. A scenario has group, seed,
    arrivals, productivity attributes. Summary checks do not independently
    replay minutes; that stronger check is ``audit_trace``.
    """
    environments = {(s.group, s.seed): s for s in scenarios}
    require(len(environments) == len(scenarios), "Duplicate scenario identity")
    expected = {(g, seed, policy, ps) for g, seed in environments
                for policy, seeds in policy_seeds.items() for ps in seeds}
    actual = {(r["group"], r["scenario_seed"], r["policy"], r["policy_seed"]) for r in rows}
    require(actual == expected and len(rows) == len(expected), "Missing, duplicated or unexpected episode")
    for row in rows:
        scenario = environments[row["group"], row["scenario_seed"]]
        if "horizon" in row:
            require(row["horizon"] == len(scenario.arrivals), "Row horizon mismatch")
        if "source" in row and hasattr(scenario, "source"):
            require(row["source"] == scenario.source, "Row source mismatch")
        total_arrivals = float(sum(scenario.arrivals))
        require(close(row["arrivals"], total_arrivals), "Row input total mismatch")
        require(close(row["arrivals"], row["completed"] + row["end_backlog"]), "Lost work in summary")
        require(all(finite(row[k]) for k in ("mean_backlog", "peak_backlog", "end_backlog", "completed")), "Nonfinite metric")
        require(0 <= row["mean_backlog"] <= row["peak_backlog"] + TOL, "Mean backlog outside bounds")
        require(0 <= row["end_backlog"] <= row["peak_backlog"] + TOL, "Final backlog outside bounds")
        require(0 <= row["completed"] <= total_arrivals + TOL, "Completed work outside bounds")
        require(finite(row["max_mass_error"]) and 0 <= row["max_mass_error"] <= TOL, "Recorded mass invariant violation")
        require(finite(row["max_budget_error"]) and 0 <= row["max_budget_error"] <= TOL, "Recorded budget invariant violation")
        max_moves = max(0, (len(scenario.arrivals) - 1 - WARMUP) // DWELL + 1)
        require(type(row["moves"]) is int and 0 <= row["moves"] <= max_moves, "Invalid number of moves")
        if row["policy"] == "static":
            require(row["moves"] == 0, "Static policy moved resources")
    return {"scenarios_checked": len(scenarios), "episodes_checked": len(rows),
            "simulated_minutes": sum(len(environments[r["group"], r["scenario_seed"]].arrivals) for r in rows)}


def independent_policy_means(rows):
    """Average policy seeds within case, then cases within each policy."""
    by_case = defaultdict(list)
    for row in rows:
        by_case[row["group"], row["scenario_seed"], row["policy"]].append(row)
    metrics = ("mean_backlog", "peak_backlog", "end_backlog", "completed", "moves")
    per_policy = defaultdict(list)
    for (_, _, policy), items in by_case.items():
        per_policy[policy].append({m: mean(r[m] for r in items) for m in metrics})
    return {policy: {m: mean(c[m] for c in cases) for m in metrics}
            for policy, cases in sorted(per_policy.items())}
