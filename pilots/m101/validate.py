import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parent


def fits(path):
    # Read only the SIMPLE, primary, unscaled, 2-D float images produced for this pilot.
    hdr = {}
    with Path(path).open("rb") as f:
        done = False
        while not done:
            block = f.read(2880)
            if len(block) != 2880:
                raise ValueError("incomplete FITS header")
            for i in range(0, 2880, 80):
                c = block[i : i + 80].decode("ascii")
                key = c[:8].strip()
                if key == "END":
                    done = True
                    break
                if c[8:10] == "= ":
                    hdr[key] = c[10:].split("/")[0].strip().strip("'").strip()
        off = f.tell()
    if (
        hdr["NAXIS"] != "2"
        or hdr["BITPIX"] not in ("-32", "-64")
        or float(hdr.get("BZERO", 0)) != 0
        or float(hdr.get("BSCALE", 1)) != 1
    ):
        raise ValueError("unsupported FITS image")
    return hdr, np.memmap(
        path,
        dtype={"-64": ">f8", "-32": ">f4"}[hdr["BITPIX"]],
        mode="r",
        offset=off,
        shape=(int(hdr["NAXIS2"]), int(hdr["NAXIS1"])),
    )


def compare(a, b, area=False):
    ha, aa = fits(a)
    hb, bb = fits(b)
    for k in ("CTYPE1", "CTYPE2"):
        if ha[k] != hb[k]:
            raise ValueError(k)
    for k in ("CDELT1", "CDELT2", "CRVAL1", "CRVAL2", "CROTA2"):
        if not np.isclose(float(ha[k]), float(hb[k]), rtol=0, atol=1e-9):
            raise ValueError(k)
    offsets = [float(hb[k]) - float(ha[k]) for k in ("CRPIX2", "CRPIX1")]
    if any(abs(x - round(x)) > 1e-8 for x in offsets):
        raise ValueError("subpixel grid difference")

    def slices(n, m, d):
        x = max(0, -d)
        y = min(n, m - d)
        return slice(x, y), slice(x + d, y + d)

    pair = [slices(n, m, round(d)) for n, m, d in zip(aa.shape, bb.shape, offsets)]
    x, y = aa[tuple(p[0] for p in pair)], bb[tuple(p[1] for p in pair)]
    valid = lambda z: np.isfinite(z) & (z > 0) if area else np.isfinite(z)
    mask1, mask2 = valid(x), valid(y)
    overlap = mask1 & mask2
    one, two = x[overlap], y[overlap]
    delta = one - two
    qa, qb = np.percentile(two, [1, 99])
    rms = float(np.sqrt(np.mean(delta**2)))
    matches = int(overlap.sum())
    n1 = int(valid(aa).sum())
    n2 = int(valid(bb).sum())
    return {
        "shape_current": list(aa.shape),
        "shape_reference": list(bb.shape),
        "pixel_grid_offset_yx": offsets,
        "valid_current": n1,
        "valid_reference": n2,
        "common": matches,
        "footprint_union_fraction": matches / (n1 + n2 - matches),
        "median_abs_diff": float(np.median(abs(delta))),
        "p99_abs_diff": float(np.percentile(abs(delta), 99)),
        "max_abs_diff": float(np.max(abs(delta))),
        "rmse": rms,
        "reference_p01_p99": [float(qa), float(qb)],
        "rms_over_p99_minus_p01": rms / (qb - qa) if qb > qa else None,
        "corr": float(np.corrcoef(one, two)[0, 1]),
        "within_local_tolerance": bool(
            n1 == n2 == matches
            and aa.shape == bb.shape
            and all(d == 0 for d in offsets)
            and np.allclose(one, two, rtol=1e-10, atol=1e-8)
        ),
    }


if __name__ == "__main__":
    ref = ROOT / "input"
    out = ROOT / "work/baseline-1/final"
    print(
        json.dumps(
            {
                "external_flux": compare(out / "m101.fits", ref / "reference.fits"),
                "external_area": compare(
                    out / "m101_area.fits", ref / "reference_area.fits", True
                ),
            },
            indent=2,
        )
    )
