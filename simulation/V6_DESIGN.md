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

## Tuning on development seeds

Parameters were tuned with `python v6_grid.py run --split tune --set all`, then ranked with `python v6_grid.py tune`; the results are in [`v6_results/tuning/summary.json`](v6_results/tuning/summary.json). Each set runs one variant on seeds 6000..6031 (352 cases per regime) and OOD 8000..8031 (192 cases). The frozen baselines come from the V5 validation rows, which use identical code and identical seeds.

Every set is ranked by the declared selection rule. The rule counts the failed standing point checks: 36 loss/endpoint, 24 risk and the 4 pooled OOD checks. Ties are broken by `delayed_noisy` mean dropped work, then risk.

| Set | Failed checks | Loss/endpoint | Risk | OOD | OOD pooled risk |
|---|---:|---:|---:|---:|---:|
| V5 Full (reference) | 9 | 6 | 2 | 1 | 27,783 |
| `drain_w1` (drain weight 1) | **2** | **0** | 1 | 1 | 29,898 |
| `drain_w2` (drain weight 2) | 4 | 0 | 3 | 1 | 29,824 |
| `restraint_strong` (hurdle ×10, no cap) | 10 | 6 | 3 | 1 | 27,686 |
| `restraint_mild` (hurdle ×3, cap 10) | 27 | 15 | 11 | 1 | 27,441 |
| `restraint_hold8` (hold while restrained, memory 8) | 31 | 15 | 12 | 4 | 34,839 |
| `restraint_hold16` (hold while restrained, memory 16) | 32 | 15 | 13 | 4 | 35,805 |

On the same seeds, V4 Hybrid's pooled OOD risk is 25,432.

Three findings:

- **Drain works.** With weight 1 it removes all six end-deficit and tail-AUC failures and lowers `delayed_noisy` risk from 11,243 to 9,580. It does raise OOD risk, mostly in rapid switching (37,482 against V5's 28,749).
- **Holding locks in the wrong allocation.** In the original hurdle mode, holding while restrained freezes whatever allocation earlier moves reached. In rapid switching, restraint started after two moves at `[54, 59, 94]`, and the episode ended worse than both V5 and Static. When restraint started near the even split, it came close to Static.
- **The detector's flag fires too widely.** Its 12-minute two-stage switching flag also fired in slow switching faults, 26–34% of minutes. In `finite_pipeline`, holding there raised dropped work from 45,126 to 79,493.

These findings led to **amendment 1** of the declaration, made before any selection array existed:

- **Neutral mode.** While restrained, the controller returns toward the even split and then holds.
- **Fast-switching signal.** Restraint is triggered by two consecutive intervals between stage changes that are each shorter than transit + startup + dwell. The detector's 12-minute flag is no longer used.
- **Unchanged.** Gates, hypotheses, seeds, the selection rule and the reversal and bounce signals are unchanged. The hurdle mode stays implemented, and its rows still reproduce exactly.

With amendment 1 in place, the four neutral-mode sets ran on the same seeds:

| Set | Failed checks | Loss/endpoint | Risk | OOD | OOD pooled risk |
|---|---:|---:|---:|---:|---:|
| `full_neutral16` (`v6_full`: drain weight 1, neutral restraint, memory 16) | **1** | **0** | 1 | **0** | **17,989** |
| `full_neutral8` (`v6_full`: drain weight 1, neutral restraint, memory 8) | 3 | 0 | 3 | 0 | 21,676 |
| `neutral16` (`v6_restraint`, memory 16) | 6 | 6 | 0 | 0 | 16,750 |
| `neutral8` (`v6_restraint`, memory 8) | 8 | 6 | 2 | 0 | 19,731 |

Returning to the even split brings pooled OOD risk well below V4 Hybrid's 25,432, and OOD dropped work below both V4 Hybrid and Capacity Balance. Restraint alone leaves the six unlimited-buffer end/tail failures, which drain removes. The one check `full_neutral16` still fails is the risk check in `legacy_instant` against V4 Hybrid, which V5 Full also fails.

**Rule for the final parameters.**

- The drain weight is the best drain-only set by the declared ranking.
- The restraint parameters are those of the best drain-plus-restraint set (`v6_full`) by the same ranking.
- The chosen values become the `V6Config` defaults, and `v6_selection_plan.json` fixes them before any selection array exists.

**Chosen parameters**, which are now the `V6Config` defaults:

- **Drain:** `drain_weight` 1, from `drain_w1`.
- **Restraint:** `restraint_neutral` 1, `restraint_memory` 16, `restraint_neutral_tolerance` 10, and no transfer cap. These come from `full_neutral16`.
- **Hurdle-mode fields:** `restraint_hurdle_factor` and `restraint_loss_fraction` apply only in the hurdle mode.
- **Reproducibility:** the tuning rows of every set still reproduce exactly. The hurdle-mode sets now name their mode explicitly.

## Development grid

The grid ran all six baselines and the three variants on every development seed, using the fixed parameters:

- **Seeds:** 2000..2007, 4000..4063 and 6000..6063, which give 1,496 cases per regime, plus OOD 8000..8063 (384 cases).
- **Commands:** `python v6_grid.py run --split development`, then `python v6_grid.py summarize --split development`.
- **Results:** [`v6_results/development_grid/summary.json`](v6_results/development_grid/summary.json).
- **Baseline reproduction:** the frozen baselines reproduce all 27,648 V5 validation rows they share byte for byte (six policies on seeds 6000..6063 and OOD 8000..8063).

| Variant | Failed point checks | Loss/endpoint | Risk | OOD | OOD pooled risk | H1 (drain vs V5 Full) | H2 (OOD risk vs V5 Full) |
|---|---:|---:|---:|---:|---:|---|---|
| `v6_full` | **4** | 3 | 1 | 0 | **19,392** | supported | supported: 11,210 (95% CI 9,007–13,527) |
| `v6_drain` | 6 | 0 | 3 | 3 | 31,508 | supported | not supported |
| `v6_restraint` | 7 | 6 | 1 | 0 | 18,877 | not supported | supported |
| V5 Full | 8 | 6 | 1 | 1 | 30,601 | — | — |
| V4 Hybrid | 16 | 15 | 0 | 1 | 27,913 | — | — |

The single-change variants attribute the gains: drain removes the unlimited-buffer end/tail failures, and restraint the OOD failures.

`v6_full` fails three `finite_pipeline` checks against Capacity Balance and one risk check:

| Check | `v6_full` | Capacity Balance | Gap |
|---|---:|---:|---:|
| Dropped work | 40,115 | 40,030 | +0.21% |
| End deficit | 46,405 | 46,320 | +0.18% |
| Tail AUC | 1,286,814 | 1,284,101 | +0.21% |

Almost all of the gap comes from `generated_switching_fault`, where restraint gives up moves that still pay in that regime. The risk check is `legacy_instant` against V4 Hybrid, which V5 Full also fails.

This grid is descriptive. The parameters stay as fixed by the tuning rule above, and the selection seeds decide among the three variants.
