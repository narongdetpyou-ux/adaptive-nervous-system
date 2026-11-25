# Adaptive Artificial Nervous System | v0.1.0 + exploratory guard

A reproducible CPU prototype combining the actual frozen ABC Causal Service 0.2.0 policy, the actual Neuron Flow 0.1.0 workflow runtime, and a 30-minute synthetic Flash Sale trace from your Dropbox. The project is a local simulation and not an operational autoscaler, biological neural model, or new trained LLM.

## Quick start (Colab or local computer)

- **Phone/Colab:** open `START_COLAB.ipynb` in Google Colab, upload `Adaptive_Nervous_System.zip` when requested, then run all cells. A GPU, API key and external service are not required. The local subprocess command sequence was verified; the Colab UI itself was not tested.
- **Local:** unzip, enter the `Adaptive_Nervous_System` folder, use Python 3.11+ (tested 3.13.5) and run:

```sh
python -m pip install -r requirements.txt
python -m unittest -v test_lab
python run_lab.py --out fresh_results
python followup.py --out fresh_results/followup
```

Use a NEW output directory for a fresh evaluation. Repeating the exact job inside the same workspace checks reuse/idempotent delivery rather than rerunning the expensive steps. The packaged `results/` is the original evidence, not a mock or prefilled run result.

## How the components work together

`data/load_test_summary_per_minute.csv` -> Dendrite (validate job/spec) -> Soma (three evaluation workers, bounded retries) -> Axon (registered simulation agents: static, MaxWeight-like, ABC) -> Synapse (accounting and representative trace verification) -> comparison agent -> AxonTerminal (durable JSON receipt + SHA-256). Frozen ABC sees current queues and at most five previous end-of-minute observations; it selects one legal resource transfer from at most three candidates. The selected action is applied only in the synthetic simulator. All flows, resource conservation and dwell intervals are checked.

The original ABC code is in `abc_service/_frozen/` and its original loader in `abc_service/frozen.py`. The five Neuron Flow source files are byte-identical to the matching files from its Dropbox checksum manifest. These are selected portions of the two upstream packages; their HTTP/Redis/LLM components and CLI are not bundled or tested here. All new simulator/agent files are outside those directories. `source_checksums.sha256` validates the upstream files and the copied CSV.

## Primary experiment

`protocol.json` was defined before the primary run. We used the original 30 minute request counts (including retries/bots, not unique transactions), appended 30 minutes at a baseline count, and generated 40 scenarios across five disturbance families. Static, MaxWeight-like and ABC shared the same arrivals, productivity, initial 69/69/69 allocation and 207-unit budget. For the adaptive policies the same legal catalog and 8-minute minimum dwell apply. ABC used seeds 11,29,47. Each scenario counted equally, averaging ABC seeds first. This gives 200 actual rollouts / 12,000 simulated minutes. Mean end-of-minute unfinished work is the primary measure. The MaxWeight-like baseline is a greedy queue-weighted catalog heuristic, not a general implementation of the academic MaxWeight algorithm.

| Policy | Mean backlog | Peak backlog (mean of peaks) | End backlog | Mean transfers |
| --- | ---: | ---: | ---: | ---: |
| Static | 8,575.47 | 44,457.85 | 0.00 | 0 |
| MaxWeight-like | 15,906.42 | 61,543.91 | 97.61 | 7 |
| ABC | 12,493.24 | 55,794.57 | 507.97 | 7 |

ABC beats the MaxWeight-like heuristic on 38/40 paired scenarios (21.46% reduction of overall means), but loses to static allocation on 37/40 (45.69% more work waiting). It is not a successful optimization overall. The original threshold of 10 receives unnormalized request counts, and a queue before processing can contain incoming work even when capacity is sufficient. In the representative scenario all three ABC seeds make a first move at t=5 while all previous end-of-minute queues were zero. They also move after recovery. This suggests a mismatch between the signal and the new domain; it is not a proof of universal causation.

## Exploratory follow-up: Persistent Backlog Gate

After seeing the primary result we froze `followup_protocol.json` and tested new seeds 200..207 (same source trace and generator family). A small wrapper calls original ABC only when at least one stage had more than 10 units of unfinished work at both preceding end-of-minute observations. Otherwise it holds resources. The core is unchanged. All four policies are tested on identical scenario conditions. The comparison is run directly on CPU (not a second Neuron Flow workflow), 40 paired scenarios and 320 rollouts / 19,200 minutes.

| Policy | Mean backlog | Peak backlog (mean of peaks) | End backlog | Mean transfers |
| --- | ---: | ---: | ---: | ---: |
| Static | 8,371.47 | 44,339.23 | 0.00 | 0 |
| MaxWeight-like | 15,077.70 | 59,283.28 | 314.23 | 7 |
| ABC | 12,504.11 | 54,455.46 | 568.83 | 7 |
| ABC + guard | 8,669.15 | 42,212.69 | 41.88 | 2.675 |

The guard reduces mean backlog by 30.67% versus original ABC (35/40 paired wins) and by 42.50% versus the MaxWeight-like baseline. It reduces mean transfers from 7 to 2.675. It still has 3.56% more mean backlog than static (20 wins, 1 tie, 19 losses). It has lower mean peak backlog than static, but worse final backlog. Guard is promising, not deployment-ready. New seeds do not equal independent real data; this is exploratory, not a confirmatory generalization result.

## Verification & evidence

- Frozen ABC hashes: match original expected hashes.
- Neuron Flow files: match upstream checksum manifest (five files).
- Main workflow: `SUCCEEDED`; ABC fails intentionally on attempt 1 (controlled transient exception), succeeds on attempt 2; other steps finish on first attempts.
- Restart: reopen workspace, submit same spec; no new job, no extra attempt, receipt hash and report modification time unchanged.
- Negative control: deliberately corrupt a completed-work count. Validator rejects it, dependent step is blocked, independent step succeeds; overall negative-control job fails as intended.
- Work conservation every simulated minute: worst floating-point error 1.203e-10 (primary), 9.914e-11 (follow-up); maximum resource budget error 0 in both runs. Representative minute traces independently replayed using ordinary Python arithmetic; the output validators also check summary conservation and scope of all episodes. This does not independently replay all minute traces; every simulation does perform inline invariants.
- Fresh repeat: primary summary, CSV, and representative trace bytes exactly match.
- Eight local unittest cases pass. See logs.

## Files

- `simulation.py`: original policy adapter, scenario generator and minute simulator.
- `lab_agents.py`, `run_lab.py`: registered Neuron Flow agents, validation, durable orchestration, retry and negative control.
- `followup.py`: exploratory guard comparison.
- `protocol.json`, `followup_protocol.json`: experiment definitions and hashes.
- `results/summary.json`, `results/episodes.csv`: primary aggregate and each of the 200 actual episode summaries.
- `results/representative_traces.json`: five minute-level traces, including actual ABC shortlist/action IDs and selection draws (selection probability is not probability of optimality).
- `results/verification.json`, `results/workflow_events.json`, `results/workspace/reports/`: actual workflow receipts, checks and events.
- `results/followup/`: follow-up summary and all 320 episode rows.
- `results/unit_tests.log`, `results/source_verification.log`, `results/reproducibility.txt`: verification evidence.
- `START_COLAB.ipynb`: executable quick-start upload workflow.

## Boundaries and next research step

The only source load data used is the minute-level synthetic CSV. The mapping from one request to one abstract work unit and 200 work units/minute/resource unit, the faults, and the equal initial split are modeling assumptions, not measured service parameters. There is no dropped work or finite buffer; each stage can process and route work within one minute. The model does not predict HTTP errors, real p95 latency, autoscaling costs, deployment reliability, or LLM performance. The injected fault is an exception, not a machine outage. External side effects are not tested. No Dropbox files were modified.

A worthwhile next milestone is a capacity-aware decision gate using independently measured queue/throughput observations, with a preregistered comparison against balanced static allocation and the simple persistent gate. Test on separately generated arrival shapes and real service telemetry if available. Do not deploy the current original ABC as a controller on these results.
