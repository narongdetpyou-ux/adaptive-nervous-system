# M101 mosaic pilot

This Linux pilot processes ten 2MASS J-band images and compares standard Montage to four independent reprojection workers. The final science and area files are produced by the same background matching and coaddition stages. [Results in Thai](RESULTS_TH.md) and [pilot protocol](protocol.txt).

## Reproduce the experiment

Use Linux with the Montage 6.1 command-line programs, Python 3.12 and NumPy. For exact package and input hashes see summary.json in the delivered evidence archive. The measured runs used Debian's montage 6.1+dfsg-1+b1 and libwcstools1t64 3.9.7-1.1+b1. Run from this directory, preferably on a quiet machine with four available logical CPUs. Each run needs a fresh label.

```bash
mkdir -p input work evidence
base=https://irsa.ipac.caltech.edu/Montage/docs/m101Example
curl -fL "$base/tutorial-initial.tar.gz" -o input/tutorial-initial.tar.gz
echo '7f9e1f1406029264431c4184d2c9f83ea9adc25c80920beaa3bffdf5b2946106  input/tutorial-initial.tar.gz' | sha256sum -c -
tar -xzf input/tutorial-initial.tar.gz -C input
curl -fL "$base/final/m101_mosaic.fits" -o input/reference.fits
curl -fL "$base/final/m101_mosaic_area.fits" -o input/reference_area.fits
python bench.py baseline-1 --retain
python bench.py fixed4-warmup --parallel
for i in 1 2 3 4 5; do
  if (( i % 2 )); then
    python bench.py "pair$i-standard" && python bench.py "pair$i-fixed4" --parallel || exit 1
  else
    python bench.py "pair$i-fixed4" --parallel && python bench.py "pair$i-standard" || exit 1
  fi
done
python validate.py
mViewer -gray work/baseline-1/final/m101.fits 0s max gaussian-log -out m101_preview.png
```

Per-run JSON records contain individual command statuses, monotonic wall times, CPU affinity, and both output hashes. The serial and fixed-parallel times in the report exclude the initial familiarization runs. The native V5 cold-start eligibility check refers to research commit `22676ec9848b4b28cb84edd73b61e5eef630bd55`.
