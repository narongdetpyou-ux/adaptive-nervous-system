# Mosaic Queue v1

This fixed benchmark times astronomical image-processing requests from submission to completion. See challenge.json for the preregistered workload, selection rule and gates.

Use Linux, Python 3.11+, NumPy and the Montage 6.1 executables in PATH. If the Montage binaries use private shared libraries, supply LD_LIBRARY_PATH. Check out the repository a second time at 22676ec9848b4b28cb84edd73b61e5eef630bd55 and set ANS_SOURCE to the absolute path of that checkout. It supplies the frozen V5 policy and dependencies. The benchmark branch extends the M101 pilot. Its default CPU affinity for actual image processing is the first four available logical CPUs. Run the live arms sequentially on the same machine.

From this directory, run the following commands. The checked-in profile is the common reference for the scored replay. Recalibrating with science.py creates a different machine profile and is a separate experiment. prepare() verifies all raw FITS hashes and builds the immutable metadata used in live tests.

```sh
export OPENBLAS_NUM_THREADS=1
python make_manifest.py
python fetch_inputs.py
python -c 'import science; science.init_cpu(); science.prepare(); science.RUNS.mkdir(exist_ok=True)'
python select_policy.py
python measure.py measure scoring fixed4
python measure.py measure scoring oldest_ready
python measure.py measure scoring current_v5
winner=$(python -c 'import json; print(json.load(open("results/selection.json"))["winner"])')
python measure.py measure scoring "$winner"
python live.py fixed4
python live.py current_v5
python live.py "$winner"
python measure.py live-report scoring current_v5
python measure.py live-report scoring "$winner"
```

Raw source inputs are full 2MASS All-Sky Atlas FITS frames from NASA/IPAC IRSA. The immutable catalog records the direct archive URLs and SHA-256 of the decompressed frames. Survey data acknowledgment: 2MASS is a joint project of the University of Massachusetts and IPAC/Caltech, funded by NASA and the NSF. All product templates cover 0.20 degrees centered on their named field. The calibration reference runs all frames in one standard mProjExec invocation. All live runs independently recompute each occurrence and must reproduce both reference FITS hashes byte for byte.

The fixed4 reference admits work from only the oldest unfinished request. The oldest_ready reference fills otherwise unused slots from later requests. The current_v5 arm uses the unmodified V5 minute-level control and measured service to weight stage selection. The development candidates keep the V5 control loop and choose ready work using declared request size and submission order within time windows. They never inspect the measured service profile to make a scheduling decision. Candidate suffixes specify window seconds and aging coefficient. Only the replay uses the frozen host timings to generate completion events. Shocks and worker dips are defined in queue_engine.py; they act on wall-clock phases across policies. Family results and all nine gate types are computed in measure.py. A passed result is a decision about this astronomy queue on this host.
