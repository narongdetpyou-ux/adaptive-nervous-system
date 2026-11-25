#!/usr/bin/env python3
"""Import the reviewed ANS archive without executing its contents.

Default: verify content and build Git objects locally, without a commit or push.
--publish additionally requires the exact GitHub push event, repository, branch,
readiness message and original main head. Only a non-force push is performed.
"""
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import zipfile

REPOSITORY = "narongdetpyou-ux/adaptive-nervous-system"
IMPORT_REF = "refs/heads/ans/import-v040"
READY_MESSAGE = "ANS_IMPORT_READY_V040"
ORIGINAL_MAIN = "c5caf8ca27adffd74a53f1953377ff4c86d5ec6b"
ARCHIVE_SHA256 = "cd473c09b018496ac5d51dd4f52075b3b3508d2bf5859febbe7407f2b0e6f2eb"
ARCHIVE_BYTES = 2325270
FILE_COUNT = 200
TOTAL_BYTES = 24617348
ARCHIVE_ROOT = "Adaptive_Nervous_System_v040/"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def safe_relative(value):
    require(isinstance(value, str) and value, "Empty or invalid path")
    p = PurePosixPath(value)
    require(not p.is_absolute() and str(p) == value, "Noncanonical path")
    require(all(part not in ("", ".", "..", ".git") for part in p.parts), "Unsafe path component")
    require(not any(c in value for c in ("\\", "\x00", "\n", "\r", "\t")), "Unsafe path character")
    return value


def file_bytes(path):
    require(path.is_file() and not path.is_symlink(), "Input is not a regular file")
    return path.read_bytes()


def validated_contents(manifest_path):
    manifest = json.loads(file_bytes(manifest_path))
    require(manifest["repository"] == REPOSITORY, "Repository mismatch")
    require(manifest["import_ref"] == IMPORT_REF, "Import branch mismatch")
    require(manifest["original_main"] == ORIGINAL_MAIN, "Original parent mismatch")
    require(manifest["archive_sha256"] == ARCHIVE_SHA256, "Archive identity mismatch")
    require(manifest["archive_bytes"] == ARCHIVE_BYTES, "Archive size identity mismatch")
    require(manifest["file_count"] == FILE_COUNT and manifest["total_bytes"] == TOTAL_BYTES, "Source totals mismatch")
    expected_files = {}
    for row in manifest["files"]:
        path = safe_relative(row["path"])
        require(path not in expected_files, "Duplicate expected file")
        require(row["mode"] == "100644", "Unexpected source mode")
        require(not path.startswith((".ans-import/", ".github/workflows/")), "Bootstrap workflow in approved source")
        expected_files[path] = row
    require(len(expected_files) == FILE_COUNT, "Expected path count mismatch")
    require(sum(row["bytes"] for row in expected_files.values()) == TOTAL_BYTES, "Expected byte total mismatch")
    chunks = []
    seen_chunks = set()
    for index, row in enumerate(manifest["chunks"]):
        relative = safe_relative(row["path"])
        require(relative == f"chunks/{index:04d}.b64", "Unexpected chunk order/path")
        require(relative not in seen_chunks, "Duplicate chunk")
        seen_chunks.add(relative)
        raw = file_bytes(manifest_path.parent / relative)
        require(len(raw) == row["bytes"] and hashlib.sha256(raw).hexdigest() == row["sha256"], "Chunk hash/size mismatch")
        chunks.append(raw)
    require(seen_chunks == {p.relative_to(manifest_path.parent).as_posix() for p in (manifest_path.parent / "chunks").iterdir()}, "Missing or unexpected chunks")
    archive = base64.b64decode(b"".join(chunks), validate=True)
    require(len(archive) == ARCHIVE_BYTES and hashlib.sha256(archive).hexdigest() == ARCHIVE_SHA256, "Archive hash/size mismatch")
    contents = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        infos = zf.infolist()
        require(len(infos) == FILE_COUNT, "Archive entry count mismatch")
        for info in infos:
            require(info.filename.startswith(ARCHIVE_ROOT), "Unexpected archive root")
            path = safe_relative(info.filename[len(ARCHIVE_ROOT):])
            require(path in expected_files and path not in contents, "Unexpected or duplicate archive file")
            require(not info.is_dir() and not (info.flag_bits & 1), "Directory/encrypted archive entry")
            require(stat.S_IFMT(info.external_attr >> 16) == stat.S_IFREG, "Nonregular archive entry")
            expected = expected_files[path]
            permissions = (info.external_attr >> 16) & 0o777
            require(permissions in (0o600, 0o644) and format(permissions, "04o") == expected["archive_permissions"], "Archive permission mismatch")
            require(info.file_size == expected["bytes"], "Archive declared file size mismatch")
            raw = zf.read(info)
            require(len(raw) == expected["bytes"] and hashlib.sha256(raw).hexdigest() == expected["sha256"], "Source file hash/size mismatch")
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
            require(blob == expected["git_blob_sha1"], "Git blob hash mismatch")
            contents[path] = raw
    require(set(contents) == set(expected_files), "Archive path set mismatch")
    return manifest, contents


def git(repo, args, data=None, auth=False):
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    if auth:
        token = env.pop("ANS_IMPORT_TOKEN", "")
        require(bool(token), "Missing job-scoped GitHub token")
        # Authentication is process-local: no credential file, URL or argument.
        for key in list(env):
            if key == "GIT_CONFIG_COUNT" or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
                env.pop(key)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "http.https://github.com/.extraheader"
        env["GIT_CONFIG_VALUE_0"] = "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode("ascii")
    else:
        env.pop("ANS_IMPORT_TOKEN", None)
    proc = subprocess.run(["git", "-C", str(repo), *args], input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if proc.returncode:
        # Do not echo command input, credentials or environment on error.
        raise RuntimeError("Git operation failed: " + args[0] + "; " + proc.stderr.decode("utf-8", "replace")[:2000])
    return proc.stdout


def make_tree(repo, manifest, contents):
    nodes = {}
    for row in manifest["files"]:
        path = row["path"]
        actual = git(repo, ["hash-object", "-w", "--stdin"], contents[path]).decode().strip()
        require(actual == row["git_blob_sha1"], "Written Git blob differs")
        parts = PurePosixPath(path).parts
        node = nodes
        for directory in parts[:-1]:
            child = node.setdefault(directory, {})
            require(isinstance(child, dict), "File/directory collision")
            node = child
        require(parts[-1] not in node, "Tree entry collision")
        node[parts[-1]] = actual

    def build(node):
        rows = []
        for name, item in node.items():
            if isinstance(item, dict):
                rows.append((name, "040000", "tree", build(item)))
            else:
                rows.append((name, "100644", "blob", item))
        data = b"".join(f"{mode} {kind} {sha}\t{name}".encode("utf-8") + b"\0" for name, mode, kind, sha in rows)
        return git(repo, ["mktree", "-z"], data).decode().strip()

    tree = build(nodes)
    require(tree == manifest["expected_tree_sha1"], "Final tree hash mismatch")
    actual_files = {}
    for record in git(repo, ["ls-tree", "-r", "-z", tree]).split(b"\0"):
        if not record:
            continue
        left, raw_path = record.split(b"\t", 1)
        mode, kind, sha = left.decode("ascii").split()
        require(kind == "blob", "Nonblob in final tree")
        actual_files[raw_path.decode("utf-8")] = (mode, sha)
    expected = {row["path"]: (row["mode"], row["git_blob_sha1"]) for row in manifest["files"]}
    require(actual_files == expected, "Final recursive tree differs from approved source")
    return tree


def require_publish_context(repo):
    require(os.environ.get("GITHUB_ACTIONS") == "true", "Publish is only available inside GitHub Actions")
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Workflow repository mismatch")
    require(os.environ.get("GITHUB_REF") == IMPORT_REF, "Workflow ref mismatch")
    require(os.environ.get("GITHUB_EVENT_NAME") == "push", "Only the reviewed push event is accepted")
    event = json.loads(file_bytes(Path(os.environ["GITHUB_EVENT_PATH"])))
    require(event.get("repository", {}).get("full_name") == REPOSITORY, "Event repository mismatch")
    require(event.get("ref") == IMPORT_REF and not event.get("deleted"), "Event ref mismatch")
    require(event.get("head_commit", {}).get("message") == READY_MESSAGE, "Import is not marked ready")
    actual_head = git(repo, ["rev-parse", "HEAD"]).decode().strip()
    require(actual_head == event.get("after") == os.environ.get("GITHUB_SHA"), "Checked-out commit is not the exact triggering commit")
    remote = git(repo, ["remote", "get-url", "origin"]).decode().strip()
    require(remote in {f"https://github.com/{REPOSITORY}", f"https://github.com/{REPOSITORY}.git"}, "Unexpected origin URL")


def remote_head(repo):
    lines = git(repo, ["ls-remote", "--exit-code", "origin", "refs/heads/main"], auth=True).decode().splitlines()
    require(len(lines) == 1, "Could not identify unique main ref")
    sha, ref = lines[0].split()
    require(ref == "refs/heads/main", "Unexpected remote ref")
    return sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    repo = args.repo.resolve()
    if args.publish:
        require_publish_context(repo)
        require(remote_head(repo) == ORIGINAL_MAIN, "Main has changed; refusing to overwrite newer work")
        git(repo, ["fetch", "--no-tags", "--depth=1", "origin", "refs/heads/main"], auth=True)
        require(git(repo, ["rev-parse", "FETCH_HEAD"]).decode().strip() == ORIGINAL_MAIN, "Main changed while fetching")
    manifest, contents = validated_contents(args.manifest.resolve())
    tree = make_tree(repo, manifest, contents)
    result = {"verified_files": len(contents), "verified_bytes": sum(map(len, contents.values())), "tree_sha": tree, "published": False}
    if args.publish:
        require(remote_head(repo) == ORIGINAL_MAIN, "Main changed before commit; refusing to continue")
        commit = git(repo, ["-c", "user.name=github-actions[bot]", "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit-tree", tree, "-p", ORIGINAL_MAIN, "-m", "Import verified ANS v0.4.0 source and evidence (200 files)"]).decode().strip()
        parent = git(repo, ["rev-parse", commit + "^1"]).decode().strip()
        require(parent == ORIGINAL_MAIN, "Unexpected new commit parent")
        require(remote_head(repo) == ORIGINAL_MAIN, "Main changed before push; refusing to continue")
        git(repo, ["push", "--porcelain", "origin", commit + ":refs/heads/main"], auth=True)
        require(remote_head(repo) == commit, "Could not confirm main after push; inspect before retrying")
        result.update({"published": True, "commit_sha": commit, "parent_sha": parent})
    print(json.dumps(result, sort_keys=True))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("## ANS v0.4.0 source import\n\n")
            out.write(f"Verified {result['verified_files']} files ({result['verified_bytes']} bytes).\n\n")
            out.write(f"Tree: `{tree}`\n\n")
            if result.get("commit_sha"):
                out.write(f"Main commit: `{result['commit_sha']}`\n\n")
            out.write("All source bytes and file modes match the approved inventory. Bootstrap files remain only on the import branch.\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("Import stopped: " + str(exc), file=sys.stderr)
        sys.exit(1)
