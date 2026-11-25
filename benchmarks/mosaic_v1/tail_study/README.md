# Mosaic queue tail study

The incumbent is the published size_600_0.03 policy. The study investigates end-to-end request latency at the 99th percentile. The Mosaic Queue v1 manifest, timing thresholds, FITS hashes and scoring functions are shared with the original experiment. Changes to experimental ordering are opt-in through controls.py. The regular queue entry point rejects experimental policy IDs; the two runners accept an optional queue factory.

## Read and verify

See [the Thai result](RESULTS_TH.md), [the recorded evidence](evidence/) and the three dated-in-git plans, [first candidate](plan.json), [oldest-job intervention](rescue_plan.json) and [existing-parameter comparison](tuning_plan.json). For the archived run, extract the owner's private evidence ZIP into an empty directory and run `python verify.py /absolute/path/to/extracted-evidence` from this directory. GitHub holds the methods and aggregate reports; the raw per-request files are supplied separately. The verifier uses the Python standard library, checks the frozen inputs, re-evaluates selection eligibility from raw traces, and recalculates the 22 timing checks and verifies the 120 science/coverage pairs for each real execution. Inside the private evidence package the development archive index lists each raw member's SHA-256. The current session's live results are kept separately from prior official results.

## Reproduce

Use the Python, NumPy, Montage, 2MASS inputs, prepared metadata and pinned V5 checkout described in [the benchmark README](../README.md). Set ANS_SOURCE to the absolute path of that V5 checkout, MOSAIC_DATA to the prepared data directory, and MOSAIC_SCRATCH to an empty existing working directory. Start from the tail_study directory and use a new output directory. Keep the three live runs sequential on the same four logical CPUs. The replay can use disjoint CPU affinity. Wall-clock measurements vary across hosts.

```sh
export OPENBLAS_NUM_THREADS=1
python test_priority.py
python study.py select new-results
python rescue.py development new-results --study rescue
python rescue.py development new-results --study tuning
python diagnose.py new-results/diagnosis.json
python study.py live new-results --arm fixed4
python study.py live new-results --arm incumbent
python study.py live new-results --arm selected
python study.py evaluate new-results
python verify.py new-results
```

The initial study selects from 20 capped size-priority configurations using 16 development streams of 96 requests. It sends one candidate to the 96 public scored streams and to a separate real workload. Subsequent development uses 32 new streams of 256 requests, seeded 31, 37, 41 and 43 across all eight families. The oldest-job grid contains 21 configurations. The parameter-only grid contains 29 alternatives on this same development set. Both stop at development under their plans. Confirmation seeds 47, 53, 59 and 61 would be used only for an eligible winner. The development screening limits apply to these experiments only. All 22 timing checks and the FITS comparison retain their original thresholds.
