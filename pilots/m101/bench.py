import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent
TOOLS = ROOT / "tools/root/usr"
os.environ["PATH"] = str(TOOLS / "bin") + ":" + os.environ["PATH"]
os.environ["LD_LIBRARY_PATH"] = (
    str(TOOLS / "lib/x86_64-linux-gnu") + ":" + os.environ.get("LD_LIBRARY_PATH", "")
)
for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[k] = "1"
CPUS = sorted(os.sched_getaffinity(0))[:4]
os.sched_setaffinity(0, CPUS)


def command(args, cwd):
    p = subprocess.run(
        args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    if p.returncode or 'stat="ERROR"' in p.stdout or 'stat="OK"' not in p.stdout:
        raise RuntimeError((args, p.stdout, p.returncode))
    return p.stdout.strip()


def parallel_project(work):
    lines = (work / "raw.tbl").read_text().splitlines(keepends=True)
    headers = [v for v in lines if v.lstrip().startswith(("|", chr(92)))]
    rows = [v for v in lines if v.strip() and v not in headers]
    if len(rows) != 10:
        raise ValueError("expected ten frame rows")

    def task(pair):
        ix, row = pair
        sub = work / f"project_task_{ix}"
        sub.mkdir()
        (sub / "proj").mkdir()
        (sub / "one.tbl").write_text("".join(headers) + row)
        status = command(
            [
                "mProjExec",
                "-p",
                str(work / "rawdir"),
                "one.tbl",
                str(work / "template.hdr"),
                "proj",
                "status.tbl",
            ],
            sub,
        )
        if "count=1, failed=0, nooverlap=0" not in status:
            raise RuntimeError(status)
        return sub

    with ThreadPoolExecutor(max_workers=4) as pool:
        subs = list(pool.map(task, enumerate(rows)))
    for sub in subs:
        products = list((sub / "proj").glob("*.fits"))
        if len(products) != 2:
            raise ValueError("missing projected flux or area")
        for src in sorted(products):
            src.rename(work / "projdir" / src.name)
    return '[struct stat="OK", count=10, failed=0, nooverlap=0]'


def run(label, retain=False, parallel=False):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", label):
        raise ValueError("Use a simple unique run label")
    (ROOT / "work").mkdir(exist_ok=True)
    (ROOT / "evidence").mkdir(exist_ok=True)
    work = ROOT / "work" / label
    work.mkdir()
    (work / "rawdir").symlink_to(ROOT / "input/m101/rawdir", target_is_directory=True)
    shutil.copy(ROOT / "input/m101/template.hdr", work / "template.hdr")
    for sub in ["projdir", "diffdir", "corrdir", "final"]:
        (work / sub).mkdir()
    commands = [
        ("raw_table", ["mImgtbl", "rawdir", "raw.tbl"]),
        (
            "project",
            [
                "mProjExec",
                "-p",
                "rawdir",
                "raw.tbl",
                "template.hdr",
                "projdir",
                "stats.tbl",
            ],
        ),
        ("projected_table", ["mImgtbl", "projdir", "images.tbl"]),
        ("overlaps", ["mOverlaps", "images.tbl", "diffs.tbl"]),
        (
            "diff",
            ["mDiffExec", "-p", "projdir", "diffs.tbl", "template.hdr", "diffdir"],
        ),
        ("fit", ["mFitExec", "diffs.tbl", "fits.tbl", "diffdir"]),
        ("model", ["mBgModel", "images.tbl", "fits.tbl", "corrections.tbl"]),
        (
            "correct",
            ["mBgExec", "-p", "projdir", "images.tbl", "corrections.tbl", "corrdir"],
        ),
        (
            "coadd",
            ["mAdd", "-p", "corrdir", "images.tbl", "template.hdr", "final/m101.fits"],
        ),
    ]
    start = time.perf_counter()
    records = []
    for stage, args in commands:
        t = time.perf_counter()
        status = (
            parallel_project(work)
            if parallel and stage == "project"
            else command(args, work)
        )
        sec = time.perf_counter() - t
        records.append({"stage": stage, "seconds": sec, "status": status})
    total = time.perf_counter() - start
    for stage in records:
        for key in ["badfits", "failed", "nooverlap", "missing", "nocorrection"]:
            m = re.search(r"\b" + key + r"=(\d+)", stage["status"])
            if m and int(m[1]):
                raise RuntimeError(stage)

    def h(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    result = dict(
        label=label,
        mode="fixed4" if parallel else "standard",
        logical_cpus=CPUS,
        elapsed_s=total,
        stages=records,
        fits_sha256=h(work / "final/m101.fits"),
        area_sha256=h(work / "final/m101_area.fits"),
    )
    (ROOT / "evidence" / (label + ".json")).write_text(
        json.dumps(result, indent=2) + "\n"
    )
    if not retain:
        shutil.rmtree(work)
    print(
        label,
        round(total, 3),
        [(r["stage"], round(r["seconds"], 3)) for r in records],
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("label")
    parser.add_argument("--retain", action="store_true")
    parser.add_argument("--parallel", action="store_true")
    args = parser.parse_args()
    run(args.label, args.retain, args.parallel)
