# ANS v0.3.0 — development record

This record was written before the fresh validation run. It is an engineering
development history, not an external preregistration. The local freeze binds
this record, the final implementation and the validation environment manifest.

## Data separation

The 176 cases already examined in v0.2 are known data. Their move-level replay
is diagnostic only. Seeds 1000–1003 in all eleven existing families are the
44-case v0.3 development set. Fresh seeds 2000–2031 in the same eleven families
form 352 validation cases. There are three separately analyzed transfer-delay
regimes, not three independent copies of each case. No validation policy
outcomes were examined before this record and the final local freeze.

## Diagnosis of the frozen v0.2 controller

`diagnose_v2.py` reproduced all 176 original capacity-aware catalog rows and
independently replayed 14,016 original-horizon minutes. Of 761 resource moves,
752 had a complete eight-minute local comparison window: 595 helped, 99 hurt,
and 58 tied a fixed-allocation counterfactual; nine truncated windows were
reported separately. These are retrospective environment-informed comparisons,
not information available to the controller and not unique causal attribution
for the entire episode.

In surge-only cases, 11 of 21 moves were locally harmful. Nine of those eleven
had at most one previous saturated observation at the recipient. Relative
stage-ranking errors inside a 15% per-stage error band were sufficient to
explain those comparisons; scaling every stage together did not test that
possibility. A separate severe error involved a low-rate donor whose last
saturated observation was sixteen minutes old: prior reversion had raised its
estimate without observed evidence of recovery. Full tables and reproducible
diagnostic code are retained in `v3_results/diagnosis/`.

## Initial variant and retained evidence

One initial variant was evaluated on the known 44 cases in the instant regime
only (352 episodes). Its evidence gate always required capacity separation,
ranked the mean over all 33 forecasts, and required non-regression in every
forecast. It had no separate mean-terminal veto. Files in
`v3_results/development_initial/` preserve its implementation, configuration,
episode rows and summary. This was development, not the final experiment.

The initial full variant had mean backlog 31,866.09, compared with 35,248.99 for
Static, 26,404.00 for V2, and 25,313.50 for the simple capacity-balancing
comparator. It avoided the surge-only penalty in these four seeds but withheld
useful transfers elsewhere. Treating every corner as equally probable was also
an unjustified interpretation of engineering stress cases.

## One pre-validation revision

The final variant retains the original nine V2 design forecasts for ranking and
requires non-regression in each of them. The 24 additional forecasts are the
eight independent low/high stage-rate corners at each of three demand levels;
they are stress assumptions, not a calibrated probability distribution. Each
additional corner permits at most 5% modeled cost regression relative to its
holding cost (denominator floor 1). This 5% stress allowance was introduced
during development and differs from the separately prespecified 5%
noninferiority margin in the statistical acceptance rule.

The recipient needs at least two saturated observations in the last five and a
latest end queue greater than ten. It also needs capacity separation from at
least one donor, or saturated evidence throughout the five-observation window.
The comparison with the largest donor lower bound does not certify every donor.
All donor losses are included in the joint transfer forecast. The full variant
additionally rejects a positive mean terminal-backlog change in the original
nine forecasts. The signal-only ablation excludes both the additional stress
forecasts and modeled transit delay, and omits that terminal veto; its contrast
with the full variant combines these mechanisms. The instant regime removes
the transit-delay difference but not the terminal-veto difference.

No further policy revision was made after the final development run. The final
configuration is serialized in its verification file and the frozen protocol.

## Final known-case development results

These point estimates selected no validation cases and are not held-out evidence.
All values below are means over the same 44 cases in each regime.

| Transfer duration | Policy | Original-H mean backlog | Original-H end backlog | 32-minute tail AUC | Static-relative harm CVaR90 |
|---|---|---:|---:|---:|---:|
| 0 | Static | 35,248.99 | 27,554.92 | 321,050.80 | 0.00 |
| 0 | V2 | 26,404.00 | 25,676.20 | 333,630.88 | 62.79 |
| 0 | Capacity balance | 25,313.50 | 22,729.55 | 247,807.31 | 878.81 |
| 0 | V3 full | 28,630.75 | 26,418.06 | 363,422.77 | 6,485.78 |
| 1 | Static | 35,248.99 | 27,554.92 | 321,050.80 | 0.00 |
| 1 | V2 | 27,241.88 | 24,910.61 | 312,105.14 | 457.14 |
| 1 | Capacity balance | 26,438.65 | 23,038.42 | 252,649.64 | 1,629.78 |
| 1 | V3 full | 29,326.66 | 26,842.49 | 376,143.98 | 5,313.28 |
| 3 | Static | 35,248.99 | 27,554.92 | 321,050.80 | 0.00 |
| 3 | V2 | 29,593.21 | 27,014.73 | 360,357.12 | 6,590.91 |
| 3 | Capacity balance | 28,197.09 | 23,604.61 | 266,362.19 | 2,772.24 |
| 3 | V3 full | 31,893.46 | 32,232.61 | 515,259.11 | 12,967.26 |

The final full variant still avoids the surge-only mean penalty in this small
development set, but is worse than V2 and capacity balance on overall mean,
terminal backlog and tail AUC. Its positive-part harm CVaR90 is also larger.
Fewer moves are therefore not evidence of less harmful outcomes. The fresh
validation will test this fixed candidate honestly; development did not satisfy
the intended advancement conditions, and those conditions remain unchanged.

The final development workflow completed 1,056 episodes (84,096 original and
33,792 follow-up minutes). Independent scalar verification covered 198
preselected complete traces, totaling 22,104 minutes. The original NeuronFlow
runtime exercised controlled retry, restart reuse, and isolation of a corrupt
result. Statistical point estimates and all row invariants were checked.

## Frozen experiment interpretation

All cases are synthetic fluid queues. The total abstract resource budget is
207. A transfer removes donor resources immediately and activates recipients
after 0, 1, or 3 complete service intervals. The 32-minute follow-up uses zero
arrivals and the last original productivity row, an explicit artificial
drainage assumption. The controller is not told the terminal switch. The
original horizon and follow-up are scored separately.

Passing the primary benefit-to-Static comparison alone is insufficient. The
fixed acceptance rule also requires V2 noninferiority, no surge-only mean harm,
lower Static-relative positive-harm CVaR90 in each regime, no larger terminal
backlog or tail AUC than V2 in each regime, and all invariant/test/underload
checks. Any failed requirement means the candidate is not advanced as a V2
replacement. Later ideas require a new version and a new validation design;
this holdout will not be reused to claim another tuned candidate was unseen.
