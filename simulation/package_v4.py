"""Package the completed v0.4 experiment, including unsuccessful performance.

This helper verifies existing evidence and never executes or tunes a policy.
It omits reproducible caches, live runtime databases/locks and layout-preview
files. Receipt JSON files and all original experiment outcomes are retained.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

from freeze_v4 import verify_frozen

ROOT = Path(__file__).resolve().parent
PARENT_BUNDLE_SHA256 = "aaff19b57247227fd3f82139fe45fc72ecc8d1d6a190c161ca7844c7d4d75cf3"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def include(path):
    relative = path.relative_to(ROOT)
    if not path.is_file() or path.is_symlink():
        return False
    if any(part.startswith(".") for part in relative.parts):
        return False
    if any(part in {"__pycache__", ".pytest_cache", ".ipynb_checkpoints", ".git",
                    "qa_pages", "layout_preview"} for part in relative.parts):
        return False
    if path.name == "package_manifest.json" or path.name == "runtime.lock":
        return False
    if path.name.endswith((".pyc", ".pyo", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm")):
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Release output must be outside the project tree")
    identity = verify_frozen()
    results = ROOT / "v4_results/validation"
    verification = json.loads((results / "verification.json").read_text())
    review = json.loads((results / "independent_review.json").read_text())
    decision = json.loads((results / "decision.json").read_text())
    reproduction = json.loads((results / "reproducibility.json").read_text())
    if verification["status"] != "SUCCEEDED" or verification["freeze_verification"] != identity:
        raise ValueError("Completed same-freeze validation is required")
    if not all(review["acceptance_integrity"].values()) or not decision["integrity"]["passed"]:
        raise ValueError("Independent integrity checks must pass before packaging")
    if reproduction["status"] != "PASS" or not reproduction["all_byte_identical"]:
        raise ValueError("Completed deterministic reproduction is required")
    pdf = ROOT / "report_v040/ANS_v040_Report_TH.pdf"
    notebook = ROOT / "START_COLAB_v040.ipynb"
    if not pdf.is_file() or not notebook.is_file():
        raise ValueError("Final report and notebook are required")
    if "<!-- V4_RESULT_SECTION -->" in (ROOT / "README.md").read_text():
        raise ValueError("README result section has not been completed")
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "Adaptive_Nervous_System_v040.zip"
    targets = [destination, output / notebook.name, output / pdf.name]
    if any(path.exists() for path in targets):
        raise ValueError("Refuse to overwrite a previous release; use a fresh output directory")
    members = sorted((p for p in ROOT.rglob("*") if include(p)), key=lambda p: p.relative_to(ROOT).as_posix())
    manifest = {
        "version": "0.4.0", "protocol_sha256": identity["protocol_sha256"],
        "parent_bundle_sha256": PARENT_BUNDLE_SHA256,
        "contents": [{"path": p.relative_to(ROOT).as_posix(), "bytes": p.stat().st_size,
                      "sha256": sha(p)} for p in members],
        "excluded_runtime_state": ["Runtime databases and locks", "Python caches",
                                   "Rendered QA page images", "Clearly marked layout previews"],
        "outcome_policy": "All original outcomes and failed performance gates are retained; workflow integrity does not imply advancement.",
    }
    manifest_path = ROOT / "package_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in members + [manifest_path]:
            archive.write(path, arcname="Adaptive_Nervous_System_v040/" + path.relative_to(ROOT).as_posix())
    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad:
            raise ValueError("ZIP CRC failed: " + bad)
        prefix = "Adaptive_Nervous_System_v040/"
        actual = set(archive.namelist())
        expected = {prefix + item["path"] for item in manifest["contents"]} | {prefix + "package_manifest.json"}
        if actual != expected:
            raise ValueError("Archive inventory differs from manifest")
        for item in manifest["contents"]:
            payload = archive.read(prefix + item["path"])
            if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
                raise ValueError("Archived bytes differ from manifest: " + item["path"])
    shutil.copy2(notebook, output / notebook.name)
    shutil.copy2(pdf, output / pdf.name)
    release = {
        "version": "0.4.0", "files": [{"filename": p.name, "bytes": p.stat().st_size,
                                         "sha256": sha(p)} for p in targets],
        "manifest_file_count": len(members), "archive_crc_and_all_file_hashes_passed": True,
        "protocol_sha256": identity["protocol_sha256"],
        "advancement_passed": decision["advancement_passed"],
    }
    (output / "ANS_v040_release.json").write_text(json.dumps(release, indent=2) + "\n")
    print(json.dumps(release, indent=2))


if __name__ == "__main__":
    main()
