# ANS v0.5.0 — Loss-aware adaptive control (research draft)

Acceptance was committed first: 17c0e57726761dbd386887eeb984ebf23e604a49. Do not change the criteria based on results. Keep V4 frozen. No production claim.

## Design
- A: reconstruct delayed post-admission queues using a causal ledger of issued allocations, their projected transfers/startup, delivered demand, and estimated service. No access to true rates, future data, or scenario labels. Reconstruction is approximate; uncertainty scenarios are not calibrated probabilities.
- B: explicit predicted new-drop guard versus hold and balance; prioritize lower predicted losses when every feasible action predicts overflow. No claim of episode-level guarantee.
- C: compare busy-rate evidence and stage pressure; add stress paths where bottlenecks move rather than only constant-rate corners.
- D: independent capacity-balance proposal, evaluate with common forecast and physical constraints. Compare capped versus uncapped action spaces separately.
- E: 8/16/32-minute horizon scoring, preserving 8-minute dwell. Open-loop single action only; multi-action MPC is a later experiment.
- Full: combine flags; single-feature variants and leave-one-out ablations retain V4 physics, observation streams and metric definitions.

## Sequence
1. Replay known 4015/4031/4040/4008/4046 switching cases, inspect per-stage drops and moves. Diagnose rather than infer causes from aggregate metrics.
2. Run unit tests and development smoke, then all declared development variants, selection grid and matched action-space comparison. Retain all rows.
3. Complete OOD adapters (random switching, false recovery, sensor degradation, buffer cascade, transfer failure, compound stress), independent reviewer, evaluator, freeze and reproduction. Transfer failure needs a new physically consistent environment, not merely more sensor noise.
4. Freeze chosen config, declaration, all source and evidence. Only then generate untouched validation/OOD seeds. Re-run original baselines with the frozen selected V4 config.
5. Promotion requires every gate in the declaration. Missing evidence blocks. No merge/release on smoke alone.

## Current scope
Prototype only, now evaluated: **V5 Full FAILS promotion** on the frozen validation and OOD sets with complete evidence. Seeds 6000..6063 and 8000..8063 are used; any later candidate needs a new declaration and new held-out seeds. Any proposed changes to study scope must be committed before data are generated and must not relax existing gates.

## First development slice

After the five published switching cases, run the first original development seed (2000) for all eleven families and all six regimes. Keep all native baselines, all declared variants, and the two additional matched action-space arms. This gives 66 paired family/regime combinations and 1,188 episodes. Report every case. This slice is descriptive and does not select a configuration or open the reserved validation and OOD ranges. The candidate, acceptance thresholds and freeze requirements remain those in `v5_acceptance_predeclared.json`.

[First development results and reproduction commands (Thai)](V5_DEVELOPMENT_RESULTS_TH.md)

## Original development cohort

Run the existing 18 arms for seeds 2001 through 2007 and retain the seed 2000 slice as the reference from round one. Report the seven new seeds on their own and the combined eight-seed cohort. Use paired family-stratified intervals from the original declaration. For the eight-seed cohort, compare five baseline means and static-relative risk with the archived V4 development results in each regime.

For each switching-fault seed in delayed/noisy observations, replay minutes 39 through 58 inclusive. Record the first accepted positive transfer to the middle stage, the staff committed immediately before the switch, and middle-stage dropped work in that window. Record a missing transfer explicitly. Decisions and service activation can occur in different minutes. This timing comparison does not estimate the causal effect of a single move.

Reproduce the sweep from the `simulation/` folder:

```bash
for seed in {2000..2007}; do
  OPENBLAS_NUM_THREADS=1 python v5_development.py --seed "$seed" --workers 2 || exit 1
done
python v5_development_cohort.py --output local_runs/v5_development/cohort-all.json
python v5_development_cohort.py --seeds 2001 2002 2003 2004 2005 2006 2007 --output local_runs/v5_development/cohort-new.json
```

## Middle-stage switch study

Before the selection grid, test from identical states whether V5 Full's extra middle-stage loss comes from move timing or move size. Each arm replaces exactly one V5 Full decision and keeps every other decision. The cases, five arms, metrics and decision rule were committed in `v5_switch_study_plan.json` before any intervention ran. Outcome: neither timing nor size meets the predeclared rule, so the declared variants stay unchanged and the declared development and selection grid comes next. This diagnosis uses development cases only and does not change any acceptance gate.

```bash
OPENBLAS_NUM_THREADS=1 python v5_switch_study.py
```

[Switch study results (Thai)](V5_SWITCH_STUDY_TH.md)

## Development and selection grids

Sequence step 2 ran under `v5_selection_plan.json`, committed before any selection array was generated. All 18 arms ran on the development seeds (2000..2007, 4000..4063; 85,536 episodes) and the selection seeds (5000..5015; 19,008 episodes). The declared rule, applied to the selection seeds only, chooses `v5_full`: 6 of 36 loss/endpoint checks and 3 of 24 risk checks fail, all in end deficit and tail AUC against Capacity Balance with unlimited buffers and in risk against V4 Hybrid. The known V4 validation cases reproduce the archived V4 decision values within 2.6e-9, and a repeated selection seed is byte-identical. Promotion remains NOT_EVALUATED; the next declared steps are OOD adapters, the evaluator and independent reviewer, freeze and reproduction, then validation.

```bash
OPENBLAS_NUM_THREADS=1 python v5_grid.py run --split selection --workers 4
OPENBLAS_NUM_THREADS=1 python v5_grid.py run --split development --workers 4
python v5_grid.py summarize
```

[Development and selection results (Thai)](V5_SELECTION_TH.md)

## Validation and OOD freeze

Sequence steps 3 and 4 follow `v5_ood_plan.json`. It was committed before any OOD adapter, validation array or evaluator output existed, and its one amendment was also made before any code. Five files implement it:

- `v5_ood.py` implements the six OOD families on the delayed_noisy physics, including a transfer-failure environment that keeps the resource ledger exact every minute.
- `v5_evaluate.py` applies every declared gate. Its result is NOT_EVALUATED unless the rows match the declared population exactly.
- `v5_review.py` recomputes every gate number on a separate code path. It also replays the 396 representative traces and their controller decisions, regenerates the scenario manifests, and must reject tampered copies.
- `v5_decide.py` assembles the integrity evidence and writes `v5_results/validation/`.
- `v5_freeze_record.json` holds the SHA-256 of every V5 source and test, the plans, the declaration, the selection result, the imported v0.1–v0.3 modules and data, and the `v5_full` configuration.

`python v5_freeze.py --verify` runs in CI. `v5_validation.py` refuses to open seeds 6000..6063 or 8000..8063 unless the freeze verifies and the tracked tree is clean. The two runs start from the same frozen commit and must match byte for byte.

```bash
python v5_freeze.py --verify
OPENBLAS_NUM_THREADS=1 python v5_validation.py run run_a --workers 2
OPENBLAS_NUM_THREADS=1 python v5_validation.py run run_b --workers 2
python v5_decide.py local_runs/v5_validation/run_a local_runs/v5_validation/run_b
```

## Validation and OOD results

Both official runs started together from frozen commit `b2af839`. Each covered 25,344 validation episodes and 2,304 OOD episodes, and all 261 output files match byte for byte.

**Decision: FAIL.** All integrity evidence passes, so this is a result of the gates, not missing evidence. The integrity evidence covers:

- 135 fresh tests;
- both freezes;
- manifest regeneration;
- accounting within 4.1e-9;
- 396/396 traces and controller decisions replayed exactly;
- zero mismatches between the independent reviewer and the evaluator;
- tamper detection.

| Gate | Result | Detail |
|---|---|---|
| Primary risk vs V3 Full (delayed_noisy) | Pass | lower by 7,338 (95% CI 5,397–9,273) |
| Primary dropped work vs V4 Hybrid (delayed_noisy) | Pass | lower by 4,842 per case, 10.2% (95% CI 4,230–5,508) |
| Mean noninferiority | Pass, 12/12 | |
| Underload | Pass, 384/384 | |
| Risk point non-worsening | Fail, 23/24 | `legacy_instant` vs V4 Hybrid: 337.6 vs 331.2 |
| Loss/endpoint point non-worsening | Fail, 30/36 | End deficit (+2.6–3.0%) and tail AUC (+5.0–5.8%) vs Capacity Balance in the three unlimited-buffer regimes, the same six checks that failed on the selection seeds |
| OOD | Fail | Pooled risk 30,601 vs V4 Hybrid 27,913. Pooled dropped work 84,468 is below both V4 Hybrid (85,162) and Balance (85,045) |

Descriptively, Static loses less work than every adaptive policy in four OOD families: rapid switching, sensor degradation, transfer failure and compound stress.

[Validation and OOD report (Thai)](V5_VALIDATION_TH.md)

End each round's progress report with the next research step and any action the project owner needs to take. State explicitly when no owner action is needed.
