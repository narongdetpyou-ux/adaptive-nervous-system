# V4 development diagnosis of known V3 results

The V3 validation population (352 known family/seed cases) is development
information for this new version. No V4 seed at or above 4000 was used. Four
case/regime pairs were selected retrospectively for diagnosis; twelve missing
full traces were reproduced from frozen V3, and all reported original metrics
matched the already observed rows. These repetitions add no independent cases.

Run `python diagnose_v3.py` to reproduce the diagnosis in
`v4_results/diagnosis/diagnosis.json`. Its `traces/` directory contains the full
four-policy logs. `offline_counterfactuals` are explicitly offline evaluator
calculations, using the known future only to analyze past actions. Their future
inputs never enter a controller.

## Where the downside comes from

Static-relative CVaR90 averages the worst 10% of positive case harms, including
fractional weight at the boundary. The group contributions add to the reported
CVaR, rather than being independent family-specific CVaRs.

| Regime | Pulsed short faults | Switching faults | Sustained overload | Total CVaR90 |
| --- | ---: | ---: | ---: | ---: |
| Instant | 2,251.82 | 1,620.12 | 1,182.04 | 5,053.98 |
| One-minute transfer | 1,433.49 | 3,970.75 | 1,699.14 | 7,103.37 |
| Three-minute transfer | 0.00 | 9,013.32 | 4,593.27 | 13,606.59 |

The three-minute tail is concentrated in switching faults (66.2%) and overload
(33.8%). This motivates targeted development diagnostics while retaining all
families in the fresh experiment.

## 1. Persistent evidence can coexist with a complete response veto

In `generated_switching_fault`, seed 2000, three-minute transfer, V3 full has
mean backlog 51,346.85, versus static 26,173.58, V2 23,845.15 and capacity
balance 16,872.66. The first stage is impaired during minutes [14,31). V2 moves
at 15 and 23; balance moves at 16 and 24. V3 full first moves at 33, after that
fault has ended. It waits for sufficient evidence and then rejects candidates
despite five recent busy samples.

At minute 20, the full policy's observed-rate balancing target is approximately
[92.01,57.49,57.49]. In an eight-minute constant-action counterfactual starting
from the same V3 state, the legal observed-rate balance proposal
[20,-15,-5] produces realized cost 11,460.69, versus holding at 17,942.38.
The cost is mean backlog plus one quarter of terminal backlog. This is a local
post-hoc comparison, not a general guarantee for that proposal.

## 2. Short-horizon queue drainage can sacrifice sustainable upstream service

The same case reallocates [-30,+30,0] at minute 49, producing allocation
[29,93,85]. The observed-rate balance target at that moment is approximately
[53.24,98.06,55.70]. The balance proposal would instead transfer [0,+30,-30],
preserving ingress resources while taking them from the heavily allocated last
stage. The V3 ingress queue reaches about 39,996 by minute 58 and 109,222 by 67.

The eight-minute realized costs of the two moves are very close: 95,314.48 for
the actual V3 move and 95,445.87 for the balance proposal. The actual move is
slightly better in that short window. Therefore, this example does **not** show
that the immediate scoring computation was wrong. It shows why a short-horizon
queue-drain objective can favor a large transfer that leaves poor ongoing
flow balance. A longer paid-transfer horizon, balance-distance tie preference,
or estimated donor-capacity floor can address this mechanism.

## 3. A backlog-relative hurdle creates an inaction region under overload

In `generated_sustained_overload`, seed 2022, three-minute transfer, V3 full
makes one original-horizon move at minute 41 to [54,64,89]. It makes 73
model-rejected holds in the original 96-minute scoring window. Mean backlog is
301,032.26 versus static 268,266.28 and balance 271,979.49. Original-horizon
terminal backlog is 551,491.69, versus static 407,378.48.

At minute 49, the candidate's predicted mean gain is 4,697.97, below its hurdle
of 8,826.43. By minute 72, gain is 5,200.67 but the hurdle has increased to
14,463.72 while ingress backlog reaches roughly 531,097. Potential throughput
gain during an eight-minute horizon is bounded; a threshold proportional to
the entire accumulated backlog can grow without bound. This makes improvement
harder to authorize as the queue grows.

The stage-2 conservative rate anchor remains 127.41 after its earlier slowdown,
although the point estimate reaches 195.81 at minute 72 and 199.63 at minute 95.
Prior reversion is not measured recovery, so the conservative anchor has a
legitimate motivation; retaining it indefinitely combines poorly with the
backlog-relative hurdle. The first corrective move occurs only near the end of
the added drainage follow-up.

The known-future eight-minute diagnostic at minute 72 gives holding cost
720,914.00 and balance-proposal cost 716,002.59. That modest attainable gain
would still be smaller than the original hurdle. A normalized improvement
hurdle, an age-bounded rate uncertainty model and a bounded recovery path should
be evaluated separately from the hypothesis that all uncertainty must shrink.

## 4. Balance responsiveness also needs a risk check

In switching-fault seed 2019 with three-minute transfers, capacity balance has
mean backlog 103,971.17 and terminal backlog 46,130.25. V3 full has 80,373.57 and
1,280.52; static has 57,021.04 and zero. Balance makes 11 original-horizon moves,
versus eight for V3 full. Responsiveness is not a universal improvement when
transfers are costly and bottlenecks change quickly.

The hybrid should evaluate bounded balance proposals with charged transfer and
startup capacity, preserve donor reserves, and use a transparent soft-risk
criterion. It should not assume that the balance target is an oracle or that
returning toward it is free.

## Implications for the next experiment

Keep the candidate generator and the risk gate separable. Log whether a move
comes from the model's catalog ranking, a balance proposal or a recovery
proposal, along with the reason for a veto. Compare the model's gain against a
scale related to achievable service or incremental queue growth, rather than
unbounded accumulated backlog alone. Bound stale uncertainty explicitly and
retain recorded source timestamps under delayed observations.

For the more realistic environments, service-rate inference should use the
known effective resources at the **same source minute** as the observed flow.
Finite-buffer losses must remain in the objective through offered minus
completed work; discarded work must never masquerade as successful clearance.
Resource transfer, pipeline delay and startup loss should be reflected in the
physical model once, without adding a duplicate artificial capacity penalty.

These are development hypotheses grounded in selected known cases. They do not
establish performance on the reserved V4 cases or on real production traffic.
