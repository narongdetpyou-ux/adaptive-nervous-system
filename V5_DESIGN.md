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
Prototype only. OOD execution, complete development grid, independent replay, frozen validation, and full acceptance results are not yet established. Do not run reserved seed ranges from this draft. Any proposed changes to study scope must be committed before data are generated and must not relax existing gates.

## First development slice

After the five published switching cases, run the first original development seed (2000) for all eleven families and all six regimes. Keep all native baselines, all declared variants, and the two additional matched action-space arms. This gives 66 paired family/regime combinations and 1,188 episodes. Report every case. This slice is descriptive and does not select a configuration or open the reserved validation and OOD ranges. The candidate, acceptance thresholds and freeze requirements remain those in `v5_acceptance_predeclared.json`.

[First development results and reproduction commands (Thai)](V5_DEVELOPMENT_RESULTS_TH.md)
