# One early projection for a waiting mosaic request

This directory contains an opt-in experiment on the frozen Mosaic Queue v1 benchmark. A request for at least four mosaics, waiting at least eight seconds with no frame dispatched, gets priority for exactly one raw frame of its first mosaic. When several requests qualify, the oldest submitted request wins, with the admission index breaking ties. All subsequent tasks use the incumbent priorities. The result records both eligible grants and actual changes to the incumbent's next dispatch. The default scheduler is unchanged.

[Predeclared comparison](plan.json) · [Findings in Thai](RESULTS_TH.md)

## Reproduce

Use the [benchmark environment and pinned V5 checkout](../README.md). Set ANS_SOURCE, MOSAIC_DATA and MOSAIC_SCRATCH as specified there; the prepared inputs must exist before a live run. Run from this directory. An empty output directory keeps this run separate from the published baselines. The development seed block was reserved but unused in the previous tail study. One predeclared candidate is tested on both cohorts regardless of development results.

```sh
export OPENBLAS_NUM_THREADS=1
output_dir=$(mktemp -d)
python test_first.py
for split in development scoring; do
  for arm in size_600_0.03 first_projection_once; do
    python experiment.py "$split" "$output_dir" --arm "$arm"
  done
done
python trace_wait.py "$output_dir/diagnosis.json"
for arm in fixed4 size_600_0.03 first_projection_once; do
  python experiment.py live "$output_dir" --arm "$arm"
done
python experiment.py gates "$output_dir"
python summarize.py "$output_dir" > "$output_dir/summary.json"
```

Run the live arms consecutively on the same machine. First dispatch means release to handing a raw frame to a worker. Final latency includes all processing until the last mosaic finishes. Real clocks measure dispatch in the event loop, just before submitting the work. The independent summary checks manifests, replay accounting, equality of incumbent traces, four-CPU affinity and science/coverage hashes. It recalculates all 22 published timing checks from request latencies. For the private evidence archive, unzip its contents to a directory and pass that directory to summarize.py. The post-hoc diagnostic uses the same request IDs in both arms and reports means of additive time intervals in the slowest 1% of large development requests as identified on the incumbent.
