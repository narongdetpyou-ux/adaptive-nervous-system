# ANS v0.6.0 — Drain and restraint (research draft)

Acceptance was committed first: `04c3afd` ([`v6_acceptance_predeclared.json`](v6_acceptance_predeclared.json)), before any V6 code or V6 episode array. Every gate of `v5_acceptance_predeclared.json` applies unchanged; the round's goal is reported separately as hypotheses H1 (drain) and H2 (restraint) against the frozen V5 Full. The v0.4 chain and the 38 frozen V5 files are never edited. No production claim.

## Diagnosis

Development data only: the V5 validation and OOD seeds, which the V5 round opened. Reproduce with `python v6_diagnose.py --workers 4`; results are in [`v6_results/diagnosis/summary.json`](v6_results/diagnosis/summary.json). True productivity is read for diagnosis only and is never given to a controller.

### Drain: backlog held at the first stage

**One family causes all six failures.** All of V5's end-deficit and tail-AUC loss against Capacity Balance in the unlimited-buffer regimes comes from `generated_sustained_overload`:

- End deficit: +530 to +620 per case.
- Tail AUC: +9,259 to +10,622 per case.
- Every other family contributes about zero.

On the 64 sustained-overload cases of each unlimited regime:

| Regime | Share of retained work at stage 1 at minute 95, V5 / Balance | Stage visits still needed, V5 / Balance | Correlation of visits gap with tail gap |
|---|---|---|---|
| `legacy_instant` | 0.972 / 0.901 | 652,341 / 616,919 | 0.80 |
| `legacy_transit3` | 0.974 / 0.894 | 695,335 / 656,198 | 0.83 |
| `integer_transit3` | 0.974 / 0.882 | 695,499 / 654,636 | 0.79 |

**The placement is the cause, not the follow-up decisions.** If V5 holds through the 32 follow-up minutes, its tail gets worse (`legacy_instant`: 1,955,252 instead of 1,898,664; Balance: 1,795,928). So V5's own follow-up moves help a little; the gap comes from where the backlog sits when arrivals stop.

**Why V5 never pushes work downstream.** The forecast cost `v4_policy.forecast_costs` is `area / h + terminal_weight × (retained + dropped)`. It values retained work equally whatever its stage, so it never prefers moving work downstream.

### Restraint: moves that cannot take effect in time

Every adaptive policy moves as often as the 8-minute dwell allows. On the 64 cases of each OOD family:

| OOD family | Moves reaching the stage still faulted when they take effect, V5 | V5 moves that bounce back | V5 switching flag on | Dropped work, Static / V5 |
|---|---|---|---|---|
| rapid_switching | 10% | 0% | 53% | 41,919 / 76,776 |
| compound_stress | 11% | 32% | 53% | 126,468 / 159,289 |
| sensor_degradation | 36% | 0% | 35% | 65,161 / 83,984 |
| transfer_failure | 36% | 36% | 21% | 63,457 / 82,436 |
| false_recovery | 36% | 0% | 1% | 126,776 / 22,810 |
| buffer_cascade | 32% | 0% | 4% | 118,755 / 81,511 |

Static loses less work than every adaptive policy in the first four families. The switching flag and bounce-backs, both observable, are high exactly there and near zero in the two families where moving helps most.

**Pulsed short faults.** In the 11 original families the switching flag is on for `generated_switching_fault` (20–21% of minutes) and `shifting_bottleneck` (13–15%), and almost never elsewhere. It does not see `generated_pulsed_short_fault`, which drives V5's one failed risk check (`legacy_instant`, 337.6 against V4 Hybrid 331.2). Its faults last three minutes (for example minutes 13–15 at stage 2, 20–22 at stage 1). A move toward a pulse takes effect after the pulse has ended:

- **`delayed_noisy`:** Static has the lowest mean deficit (44,272). V5 is harmed in 59 of 64 cases, with mean harm 5,734 against V4 Hybrid's 3,869.
- **`legacy_instant`:** these cases make up 28 of V5's 71 CVaR90 tail cases. They contribute 27.3 to V5's mean harm over all 704 cases, against 13.6 for V4.

### Consequences for the declared mechanisms

**Drain.** At the forecast horizon's end, count retained work by the stage visits still ahead of it (3, 2 and 1 for work queued at, or travelling to, stages 1, 2 and 3), instead of once. Every candidate still passes the unchanged V5 loss filter first.

**Restraint.** Raise the gain hurdle and cap the transfer size while any of these observable signals is recent:

- the unchanged `RegimeDetector` reports switching;
- a move bounced back: the ledger's committed staff after the move's arrival falls short of the intended allocation;
- a move was wasted: the receiving stage's estimated rate, from lagged observations only, had already recovered when the move took effect. This covers pulses that the switching flag misses.

Expected reach on development data:

- **Signals active:** rapid switching, compound stress, sensor degradation, transfer failure, pulsed short faults, switching faults and shifting bottlenecks.
- **Signals mostly silent:** sustained faults, false recovery, buffer cascade, underload.

The single-change variants `v6_drain` and `v6_restraint` measure what each mechanism adds.
