# v0.4 development record

This version addresses harmful inaction, combines observed capacity balance
with finite forecast risk checks, and adds explicit physical and observation
constraints. All development evidence is retained. The previous v0.3 outcome
population is known information, not a new validation population.

## Acceptance and information order

The acceptance declaration was fixed at 2026-11-25T06:10:24.498677+00:00,
before any v0.4 development outcomes. Its SHA256 is
`1105442baf50d0cee923c671ebc2c024d3e8697e90e280843fbdffdd005fb6ef`.
The criteria require a positive paired 95% lower endpoint for the CVaR90
reduction against V3 in the final delayed/noisy regime, plus every declared
point risk, 5% mean-noninferiority, loss/endpoint, integrity and underload gate.
These requirements cannot be relaxed using development or validation results.

Development uses all eleven families with known seeds 2000 through 2007:
88 cases. Final validation reserves seeds 4000 through 4063: 704 cases.
No controller outcomes on those reserved seeds are used for development.
Before final validation the complete implementation, selected configuration,
generator, sensor streams, metrics, criteria and this record are frozen.

## Diagnosis before implementation

`V4_DIAGNOSIS_NOTES.md` and `v4_results/diagnosis/diagnosis.json` retain the
known V3 cases and offline local counterfactuals. The latter can inspect the
known future only as retrospective diagnostics; future environment values
never enter a running controller.

In switching-fault seed 2000 with a three-minute transfer, V3 does not move
during the first fault [14,31), while V2 moves at 15 and 23 and balance at
16 and 24. V3 first moves at 33. Its later transfer at minute 49 leaves
allocation [29,93,85]. That move is marginally better than the alternative
in the short eight-minute offline window, so this is not evidence of a
numerically incorrect immediate calculation. It exposes a horizon and
sustainable-flow problem.

In overload seed 2022 with a three-minute transfer, V3's backlog-proportional
hurdle rises from 8,826 at minute 49 to 14,464 at minute 72, while predicted
attainable gains remain around 4,698 and 5,201. An old conservative rate
anchor persists even as the point estimate reverts toward nominal. This can
block a modest but useful correction precisely when the existing backlog
has become large. Balance is not uniformly safe either: in switching seed
2019 it has substantially worse mean and terminal backlog than V3.

## Implemented hypothesis

The hybrid has one rate estimator and one action clock. It ranks the same
legal catalog proposals with a 16-minute paid forecast, including resource
transfer, startup, pipeline forwarding and finite buffers. A balance-directed
proposal is preferred when its adjusted gain is within 10% of the best gain.
The fixed donor reserve, five-minute warmup and eight-minute action spacing
remain physical constraints.

The score subtracts a finite penalty for the worst quarter of positive
forecast regrets against holding. This forecast penalty is an engineering
stress score; it is not the empirical episode CVaR90 and is not a calibrated
probability distribution. The improvement hurdle scales with recent arrival
flow rather than the unbounded stock of old backlog. Conservative busy-rate
anchors expire after eight source minutes. Persistent observed pressure
reduces the risk weight and hurdle, bounded below by one quarter of their
ordinary values. A separate idle recovery path permits a balance-directed,
model-neutral return only when its forecast checks pass.

The no-risk ablation removes the finite stress-score weight. The no-recovery
ablation jointly removes pressure escape and idle recovery; it does not
isolate either recovery mechanism individually. Both retain physical
constraints. No policy receives hidden productivity, scenario identity,
future measurements, the scoring horizon or an offline static counterfactual.

## Realism and accounting

Six separately analyzed, nested regimes preserve legacy instant and D=3
physics, then add integer resource allocations, one-interval forwarding with
buffers [60000,40000,40000], incoming-resource startup at 50% then 75%, and
two-interval sensor lag with mean-one lognormal multiplicative noise of log
standard deviation 0.05. These numbers are synthetic stress assumptions,
not measurements from a production system or documented platform defaults.
Jobs and service remain a continuous fluid even when resources are integers.

The primary minute deficit is offered minus completed work, equal to retained
queues plus in-flight work plus cumulative dropped work. Discarded work is
never treated as completed or silently removed from the primary score.
Physical drainage uses retained work only, with a fixed 32-minute follow-up;
losses can leave a permanent positive deficit after physical queues drain.
Current commanded allocations are known exactly. Delayed flow is normalized
by effective resources at its own source minute, including known startup.
All policies receive the same declared observation interface and shared
source-indexed sensor noise for a given case and regime.

The original development protocol snapshot described
`peak_service_queue_max_over_full_run` as an individual queue maximum. Before
freeze its prose was corrected to the implemented and independently audited
quantity: max over time of the **sum** of service queues, excluding in-flight
work. No executable metric, outcome, selection rule or acceptance condition
changed in that clarification. The original snapshot is preserved.

The independent pre-freeze design review also corrected three prose-only
descriptions in the draft: idle recovery uses total measured end queue at
most 10 work units, not literal emptiness; the pressure streak permits a
decline of max(10 work units, 2% of the previous queue), rather than requiring
strict growth; and expiry of a stale low anchor can narrow the interval.
Sparse evidence and lag separately widen the point-relative band. The tested
code already implements these behaviors. No executable source, selection
rule, numerical outcome or acceptance condition changed.

## Finite development selection

Six configurations are declared before the grid: risk weight 0.1, 0.3 or 0.6,
crossed with maximum transfer 20 or 30. All other parameters are held fixed.
The shared baselines are Static, frozen V2, Capacity balance and V3 full.
The grid contains 2,112 baseline episodes and 528 candidate episodes for
each of the six configurations, totaling 5,280 episodes. No intervals are
computed for selection; the grid bootstrap draw count is zero.

Selection is lexicographic, in this exact order:

1. Count of the twelve point mean violations of the 5% margin against V2
   and balance in the six regimes.
2. Largest regime ratio of hybrid CVaR90 to the smallest V3/V2/balance
   CVaR90 in that regime, with a denominator floor of one work unit.
3. Sum of the six hybrid CVaR90 values.
4. Mean original-horizon moves across regimes.
5. Smaller risk weight, then smaller transfer cap.

Cross-regime ratios and sums are development ranking devices only, not pooled
inferential results. Every configuration's rows and point summary remain in
`v4_results/development_grid/`. Source snapshots and before/after hashes bind
the execution. Selection is mechanical; there is no manual override.

Four initial smoke episodes on known surge-only seed 2000 were also run:
Static and the constructor-default hybrid in legacy instant and delayed/noisy
physics. They checked basic execution only and did not choose cases or
parameters. The failed surge behavior is not concealed or called solved.

## Verification before validation

The combined actual test run passes 79 tests with no failures, errors or
skips: eight original, fifteen v0.2, nineteen v0.3, twenty-four new physics
and controller tests, and thirteen new statistical/evaluator tests.
`v4_results/unit_tests.json` binds the tested sources and the log; `passed`
and `successful` are explicit aliases of the same observed successful result.
The alias resolves evidence-reader compatibility without changing tests or
evaluation logic. The independently implemented bootstrap reviewer was
checked against an arithmetic fixture and rejected a deliberately corrupted
primary CVaR interval. That check generated no scenario outcomes.

The original ABC service, Neuron Flow runtime, V2 and V3 frozen sources are
preserved. Selected-configuration workflow results and the final ranking are
recorded below before this development record is frozen.

## Completed grid result

The mechanically selected configuration is **risk_0.1_cap_20**. All six configurations have zero point mean-margin violations; the second ranking component therefore determines their order. No override was applied.

| Configuration | Point mean violations /12 | Worst risk ratio | Sum of six risks | Mean moves |
| --- | ---: | ---: | ---: | ---: |
| risk_0.1_cap_20 | 0 | 1.023809768 | 41596.720507 | 6.450758 |
| risk_0.3_cap_20 | 0 | 1.053116035 | 42110.686877 | 6.160985 |
| risk_0.6_cap_20 | 0 | 1.139057224 | 42468.333213 | 5.772727 |
| risk_0.3_cap_30 | 0 | 1.143483811 | 35000.477755 | 6.123106 |
| risk_0.1_cap_30 | 0 | 1.171883351 | 35851.607129 | 6.428030 |
| risk_0.6_cap_30 | 0 | 1.213408957 | 33144.817852 | 5.746212 |

The ratio slightly above one for the selected configuration is a real development miss, not rounding: in integer_transit3, hybrid CVaR90 is about2.38% above V2. It remains lower than V3 in every regime. The grid ranking is not the full advancement test: endpoint/loss gates and interval-based gates are still required in validation.

| Regime | Hybrid mean deficit | Hybrid CVaR90 | V3 CVaR90 | V2 CVaR90 | Balance CVaR90 |
| --- | ---: | ---: | ---: | ---: | ---: |
| legacy_instant | 24867.416582 | 407.181231 | 5316.164969 | 2124.884929 | 1351.283616 |
| legacy_transit3 | 27484.261919 | 3560.006616 | 12919.367311 | 3940.457858 | 4276.546252 |
| integer_transit3 | 27566.211333 | 3543.349295 | 12919.367311 | 3460.944999 | 4267.347523 |
| finite_pipeline | 45408.721696 | 9861.310723 | 23163.812428 | 46244.744424 | 11824.594256 |
| startup | 46106.144177 | 11257.028919 | 23763.238700 | 48421.602690 | 11743.533419 |
| delayed_noisy | 48190.988808 | 12967.843724 | 17468.345592 | 40882.268830 | 14940.553648 |

The selected risk weight is0.1 and maximum transfer is20; the complete selected dataclass is stored in `v4_selected_config.json`. Remaining fixed parameters were not tuned. `selection.json` retains full-precision ranks and source hashes. All5,280 grid episodes completed without a source change.

Selected configuration SHA256: `46f76f0b38f66058d4c8be557a446252f3a93c44178dfbd58fb71e184bb89b11`.
Grid selection SHA256: `256efb10b99ac3fe01db51b2af23deaf92a36afb1e9e3b0e267ed2a560c2aeaf`.

## Completed selected-configuration workflow before freeze

The selected configuration was executed through the original Neuron Flow runtime in all six regimes and all seven policies, including both ablations. The workflow completed successfully:3696 episodes,412608 simulated minutes,462 independently replayed representative traces and51576 observation-only decisions. The representative set is repeated verification of existing episodes, not additional independent cases. Retry, duplicate/restart reuse and the deliberately invalid completed-count result behaved as specified.

All2640 shared rows (30 regime/policy artifact pairs) from the grid and the full selected-development workflow are exactly equal. See `v4_results/development_grid_workflow_match.json`. This confirms that the workflow loaded the selected configuration without changing the observed outcomes.

| Regime | Hybrid CVaR90 | No-risk CVaR90 | No-recovery CVaR90 |
| --- | ---: | ---: | ---: |
| legacy_instant | 407.181231 | 438.169358 | 421.118094 |
| legacy_transit3 | 3560.006616 | 4639.020844 | 4384.449454 |
| integer_transit3 | 3543.349295 | 5015.130254 | 4384.449454 |
| finite_pipeline | 9861.310723 | 9972.466945 | 9921.464540 |
| startup | 11257.028919 | 11400.329681 | 10899.976482 |
| delayed_noisy | 12967.843724 | 15835.984415 | 14124.175399 |

The hybrid has lower development CVaR90 than the no-risk variant in all six regimes and than no-recovery in five. No-recovery is lower in startup; that counterexample is retained. These known-case ablations are descriptive and do not justify claiming that either mechanism is universally beneficial.

Applying the unchanged numerical rules descriptively to the development results yields17/18 point-risk checks,12/12 mean-noninferiority checks, and21/36 loss/endpoint checks passing. All48 hybrid underload episodes have zero full-run drops and moves. The final-regime V3-minus-hybrid risk difference is4500.501868 with a paired95% interval[295.188516,8220.321168] on these known cases. This is not the reserved validation result.

`v4_results/development/decision.json` explicitly keeps frozen_identity_passed false, as expected for a run that precedes the final freeze. It is a development diagnostic, not a production advancement decision. No policy, parameter, generator, metric, acceptance or selected configuration is changed after inspecting these development results.

After the complete development workflow returned, the draft protocol wording was clarified from recipient-increment cap to **maximum total transfer**, the sum of positive deltas per move, with both raw and integer-projected applied moves checked. The pre-grid executable already imposed exactly that rule. The earlier snapshot and development execution-source hashes remain preserved. This documentation correction changes no numerical behavior or selection criterion.

The final freeze will bind the selected configuration, all19 required files, unchanged acceptance declaration, complete parent freeze chain, reserved scenario manifest and the retained development evidence. All reserved validation outcomes are still unopened at the completion of this record.
