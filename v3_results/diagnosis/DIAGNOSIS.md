# Offline diagnosis of known v0.2 validation results

Reproduced all 176 capacity-aware episode rows exactly and independently replayed 14,016 minutes.
Compared with stored static outcomes: 20 mean-backlog regressions and 8 terminal-backlog regressions.

## Local move outcomes

Among 752 full-dwell moves, 99 increased the actual eight-minute objective relative to holding, 595 reduced it, and 58 tied. 9 end-window moves were separately flagged.
Surge-only: 21 moves, 11 locally harmful, 8 beneficial, 2 neutral.

## Interpretation boundaries

This is a development diagnostic on a previously inspected synthetic set. Future truth is used only for offline evaluation and must never be supplied to the online controller. A one-dwell counterfactual is not the effect of removing a move from an otherwise fully recomputed episode. Flags overlap and do not establish a unique cause.

The original forecast ensemble lowers all stages together or recovers them together toward a nominal prior. It does not independently vary uncertainty at each stage. Surge-only sign reversals can occur even when average subsequent demand remains inside the ±20% demand band; demand extrapolation alone is therefore insufficient to explain every observed failure.

See diagnosis.json for full group/flag counts, moves.json or moves.csv for every move and counterfactual queue path, cases.csv for all paired episode regressions, and replayed_episodes.csv for exact replayed outcomes.

## Mechanisms supported by inspected examples

The following observations motivate development hypotheses. They are not an attribution of all episode-level harm to one cause.

### A single saturated observation can produce an unreliable stage ranking

In surge-only seed 1013 at minute 11, the controller moved seven resources from stage 0 to stage 2. Its rate estimates were [216.32, 188.43, 180.82], based on one saturated observation at each stage. Actual mean rates during the next eight minutes were [200.06, 202.05, 205.58]. Predicted mean gain was +2,897.59 and predicted worst-model gain was +1,233.02, but the offline locked-interval gain was -7,027.55. Mean subsequent demand was only 4.52% below the point forecast and inside its lower demand band.

Nine of eleven harmful surge-only moves had at most one prior saturated observation at a recipient. Only two had subsequent mean demand below the forecast's 20% lower band. None needed a greater-than-15% error in the donor or recipient mean-rate estimate to lose: modest errors that reverse the relative capacity ordering can be enough. Independent adverse uncertainty at different stages is absent from the original nine-model ensemble.

### Upward prior reversion can make a still-faulty donor appear capable

In generated_sustained_processing_fault seed 1004 at minute 49, stage 1's estimate was 182.97 while its actual next-eight-minute mean was 127.59. Its last saturated observation was sixteen minutes old; the most recent flow per resource was only a censored lower bound of 117.15. The policy removed thirty resources from stage 1. Predicted mean gain was +721.95, but actual local gain was -14,288.08. The source estimator explicitly reverts underloaded observations upward toward the nominal prior; this is an assumption about recovery, not measured recovery.

Seed 1008 at minute 25 exhibits a similar pattern: donor stage 1 was estimated at 163.57, its next-window mean was 119.52, and the last saturated observation was eight minutes old. This supports testing a conservative donor bound that retains the last observed slow rate while its recovery is unobserved.

### Abrupt future changes remain fundamentally unobserved

In generated_switching_fault seed 1013 at minute 31, a new stage-2 fault begins while stage 0 recovers. The controller reallocates toward the old stage-0 bottleneck using observations through minute 30. A counterfactual using future truth shows harm, but this does not mean the policy could have known the new fault before observing it. A robust veto may limit exposure at a cost to responsiveness; it cannot remove every unpredictable transition.

## Regression distribution

| Family | Harmful full-dwell moves | Full-dwell moves | Mean-regression episodes | Terminal-regression episodes |
| --- | ---: | ---: | ---: | ---: |
| surge_only | 11 | 21 | 11 | 0 |
| ingress_slow | 0 | 35 | 0 | 0 |
| processing_slow | 0 | 32 | 0 | 0 |
| delivery_slow | 0 | 32 | 0 | 0 |
| shifting_bottleneck | 0 | 46 | 0 | 0 |
| generated_underload | 0 | 0 | 0 | 0 |
| generated_sustained_processing_fault | 14 | 94 | 0 | 0 |
| generated_switching_fault | 11 | 148 | 3 | 1 |
| generated_pulsed_short_fault | 27 | 127 | 3 | 0 |
| generated_asymmetric_recovery | 23 | 118 | 3 | 0 |
| generated_sustained_overload | 13 | 99 | 0 | 7 |

Local move harm and episode regression are different quantities. An episode can contain a harmful move and still outperform static overall. Conversely, changing an early move can change later observations and all subsequent decisions; the present local counterfactual does not evaluate that full alternate trajectory.
