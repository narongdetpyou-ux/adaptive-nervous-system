# v0.2.0 development record

The five legacy and six new synthetic scenario families, seed split, and policy
comparison were specified before new-policy benchmark results. Four seeds per
family (300-303) form development; sixteen (1000-1015) form validation. The latter
was not evaluated until the final local protocol and implementation hashes were
written. This is local experiment discipline, not an external preregistration.

## Design choices before development outcomes

- An eight-minute prediction horizon covers the eight-minute allocation lock.
- Estimate productivity from saturated observed flow; light-load flow is a
  lower bound. Use a declared nominal prior and 10% prior reversion for censored
  recovery. First saturated sample updates fully; later samples use weight0.7.
- Nine fixed prediction models combine demand factors0.8/1/1.2 with lower
  rate0.85, point rate, and linear recovery to nominal200.
- Retain hold, require mean predicted gain above both10 and2% of hold cost,
  and reject any action worse than hold in a modeled scenario. A 10-unit stage
  reserve is stricter than the original baselines' nonnegative-only rule.
- Compare two variants: veto-only originalABC and full-catalog model ranking.
  The latter is a new selector; source-identical ABC is not credited for it.

## Implementation verification before freezing

Independent review caught a potential integer-dtype truncation when users pass
an integer nominal rate. The estimator now explicitly uses floating-point
storage. Configuration now rejects nonfinite values and noninteger time/window
settings. These are defensive correctness fixes, not outcome-based tuning.
Both were applied before the development benchmark. No original upstream
source was changed.

A one-case smoke run (legacy surge_only, seed300, one policy seed each) checked
all six adapters. Then all23 tests (8 existing and15 new) passed. The first
workflow launch correctly refused its output directory because shell redirection
had placed a log inside it; the log was moved outside and the run used the empty
directory. No policy episode ran during that refused launch.

## Development result and decision

Full development completed528 benchmark rollouts across44 scenarios and42048
simulated minutes. The capacity_aware mean backlog was28357.1530 versus
static36033.9931, a21.3044% relative reduction. Its mean terminal backlog was
27044.4431 versusstatic26115.7743, a drawback despite the lower time average.
The surge-only family was9.9124% worse on mean backlog. These limitations were
retained and no policy parameters were adjusted after inspection.

The frozen validation criterion concerns mean backlog. Terminal backlog, every
family result, reserve differences, and modeled-uncertainty limits must be
reported even if the criterion passes. A positive validation result supports
further synthetic/telemetry study, not immediate live control.

## Reproducibility boundary

Development verification used Python3.12.14 and NumPy2.3.5. Repeating identical
seeded experiments tests reproducibility; it does not add independent evidence.
The experiment counts exclude unit-test fixtures, smoke runs and integrity
replays. No validation-informed retuning is permitted within this version.
