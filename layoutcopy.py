#!/usr/bin/env python3
"""Export a directory layout as a hash manifest and apply it to an existing file set.

Python 3.9+. No third-party packages required.
Vibecoded with ChatGPT, with manual review and edits by Aw4yland/bequidox74.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Dict, List, Tuple

CHUNK_SIZE = 1024 * 1024  # 1 MiB
DEFAULT_HASH_ALGORITHM = "blake2b"
SUPPORTED_HASH_ALGORITHMS = hashlib.algorithms_guaranteed  # make sure this thing is truly portable.


def hash_file(path: Path, algorithm: str) -> str:
    """Return the hexadecimal digest of a file using the selected algorithm."""
    if algorithm not in SUPPORTED_HASH_ALGORITHMS:
        raise ValueError(f"Unsupported hash algorithm: {algorithm}")
    h = hashlib.new(algorithm)
    with path.open("rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def scan_tree(root: Path) -> Tuple[List[Path], List[Path]]:
    """Return (directories, files) below root; reject symlinks/non-regular entries."""
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    if root.is_symlink():
        raise ValueError(f"Root must not be a symbolic link: {root}")

    directories: List[Path] = []
    files: List[Path] = []
    # Path.walk is newer than Python 3.9; os.walk works across supported versions.
    import os

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        base = Path(dirpath)
        for name in list(dirnames):
            p = base / name
            if p.is_symlink():
                raise ValueError(f"Symbolic-link directory is not supported: {p}")
            directories.append(p)
        for name in filenames:
            p = base / name
            if p.is_symlink():
                raise ValueError(f"Symbolic-link file is not supported: {p}")
            if not p.is_file():
                raise ValueError(f"Non-regular filesystem entry is not supported: {p}")
            files.append(p)
    return directories, files


def relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def ensure_manifest_outside_root(manifest_path: Path, root: Path) -> None:
    try:
        manifest_path.resolve().relative_to(root.resolve())
    except ValueError:
        return
    raise ValueError("The manifest must be saved outside the directory being exported/applied.")


def export_manifest(root_arg: str, manifest_arg: str, hash_algorithm: str) -> None:
    root = Path(root_arg).expanduser().resolve()
    manifest_path = Path(manifest_arg).expanduser().resolve()
    ensure_manifest_outside_root(manifest_path, root)
    directories, files = scan_tree(root)

    records = []
    for i, path in enumerate(sorted(files, key=lambda p: relative_posix(p, root).casefold()), 1):
        before = path.stat()
        digest = hash_file(path, hash_algorithm)
        after = path.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeError(f"File changed while being hashed; retry with files closed: {path}")
        records.append({
            "path": relative_posix(path, root),
            "size": after.st_size,
            "hash": digest,
        })
        if i % 1000 == 0:
            print(f"Hashed {i:,} files...", file=sys.stderr)

    manifest = {
        "format": "directory-layout-manifest",
        "hash_algorithm": hash_algorithm,
        "directories": sorted(
            (relative_posix(p, root) for p in directories), key=str.casefold
        ),
        "files": records,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(records):,} files and {len(directories):,} directories to:\n{manifest_path}")
    print(f"The manifest contains paths, sizes, and {hash_algorithm.upper()} hashes, not file contents.")


def validate_relative_path(raw: object, description: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"Invalid {description}: expected a non-empty path string")
    if "\\" in raw or raw.startswith("/"):
        raise ValueError(f"Unsafe {description}: {raw!r}")
    parts = raw.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"Unsafe {description}: {raw!r}")
    # These characters and trailing dots/spaces are not valid in ordinary Windows names.
    for part in parts:
        if any(ch in part for ch in '<>:"|?*') or part.endswith((".", " ")):
            raise ValueError(f"Path is not a valid Windows relative path: {raw!r}")
        if part.split(".")[0].upper() in {
            "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }:
            raise ValueError(f"Reserved Windows filename in path: {raw!r}")
    pure = PurePosixPath(raw)
    if pure.is_absolute():
        raise ValueError(f"Unsafe {description}: {raw!r}")
    return raw


def load_and_validate_manifest(path: Path) -> Tuple[str, List[str], List[dict]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != "directory-layout-manifest":
        raise ValueError("Unrecognized manifest format/version: missing format")

    # chatgpt originally wrote some backwards compat code for older manifests
    # that only used sha256. i'm removing this since the script hasn't been used before.
    hash_algorithm = data.get("hash_algorithm")
    digest_field = "hash"
    if hash_algorithm not in SUPPORTED_HASH_ALGORITHMS:
        raise ValueError(f"Unsupported or missing hash algorithm in manifest: {hash_algorithm!r}")
    raw_dirs = data.get("directories")
    raw_files = data.get("files")
    if not isinstance(raw_dirs, list) or not isinstance(raw_files, list):
        raise ValueError("Manifest must contain 'directories' and 'files' lists")

    dirs = [validate_relative_path(x, "directory path") for x in raw_dirs]
    files: List[dict] = []
    for item in raw_files:
        if not isinstance(item, dict):
            raise ValueError("Invalid file entry in manifest")
        rel = validate_relative_path(item.get("path"), "file path")
        size = item.get("size")
        digest = item.get(digest_field)
        if not isinstance(size, int) or size < 0:
            raise ValueError(f"Invalid size for {rel!r}")
        expected_hex_length = hashlib.new(hash_algorithm).digest_size * 2
        if (
            not isinstance(digest, str)
            or len(digest) != expected_hex_length
            or any(c not in "0123456789abcdef" for c in digest.lower())
        ):
            raise ValueError(f"Invalid {hash_algorithm.upper()} hash for {rel!r}")
        files.append({"path": rel, "size": size, "hash": digest.lower()})

    all_paths = dirs + [f["path"] for f in files]
    folded = [p.casefold() for p in all_paths]
    if len(folded) != len(set(folded)):
        raise ValueError("Manifest contains duplicate or case-colliding file/directory paths")
    file_set = {f["path"].casefold() for f in files}
    for rel in all_paths:
        parts = rel.split("/")
        for n in range(1, len(parts)):
            if "/".join(parts[:n]).casefold() in file_set:
                raise ValueError(f"A file is used as a parent directory: {rel!r}")
    return hash_algorithm, dirs, files


def apply_manifest(root_arg: str, manifest_arg: str, execute: bool) -> None:
    root = Path(root_arg).expanduser().resolve()
    manifest_path = Path(manifest_arg).expanduser().resolve()
    ensure_manifest_outside_root(manifest_path, root)
    hash_algorithm, target_dirs, target_files = load_and_validate_manifest(manifest_path)

    old_dirs, old_files = scan_tree(root)
    source_by_key: Dict[Tuple[int, str], List[Path]] = defaultdict(list)
    source_counts: Counter = Counter()
    for i, path in enumerate(old_files, 1):
        before = path.stat()
        digest = hash_file(path, hash_algorithm)
        after = path.stat()
        # goddamn, it went way overboard with these checks.
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            raise RuntimeError(f"File changed while being hashed; retry with files closed: {path}")
        key = (after.st_size, digest)
        source_by_key[key].append(path)
        source_counts[key] += 1
        if i % 1000 == 0:
            print(f"Hashed {i:,} existing files...", file=sys.stderr)

    target_counts = Counter((f["size"], f["hash"]) for f in target_files)
    if source_counts != target_counts:
        missing = target_counts - source_counts
        extra = source_counts - target_counts
        lines = ["The file sets do not match exactly; nothing has been moved."]
        if missing:
            lines.append(f"Missing on A (size, {hash_algorithm.upper()} hash) groups: {sum(missing.values()):,} file(s)")
        if extra:
            lines.append(f"Extra on A (size, {hash_algorithm.upper()} hash) groups: {sum(extra.values()):,} file(s)")
        raise ValueError("\n".join(lines))

    # Build a complete plan before changing anything. Same-content duplicates are interchangeable.
    plan: List[Tuple[Path, Path]] = []
    for item in target_files:
        key = (item["size"], item["hash"])
        source = source_by_key[key].pop()
        destination = root.joinpath(*item["path"].split("/"))
        plan.append((source, destination))

    print(f"Verified exact file match: {len(old_files):,} files.")
    print(f"Target layout: {len(target_dirs):,} directories, {len(target_files):,} files.")
    print("No file contents will be copied over the network; files are moved locally on A.")
    if not execute:
        print("DRY RUN: no changes made. Re-run with --execute to reorganize the tree.")
        for source, destination in plan[:10]:
            print(f"  {relative_posix(source, root)}  ->  {relative_posix(destination, root)}")
        if len(plan) > 10:
            print(f"  ... and {len(plan) - 10:,} more moves")
        return

    # Stage every file outside root but on the same volume, so renames/collisions are safe.
    stage_dir = Path(tempfile.mkdtemp(prefix=f".{root.name}_layout_stage_", dir=str(root.parent)))
    staged: List[Tuple[Path, Path, Path]] = []  # original, staged, final target
    moved_final: List[Tuple[Path, Path]] = []   # staged path, final target
    all_staged = False
    try:
        for i, (source, destination) in enumerate(plan):
            stage_path = stage_dir / f"{i:09d}.dat"
            shutil.move(str(source), str(stage_path))
            staged.append((source, stage_path, destination))
        all_staged = True

        # All files are out of the tree; old directories can now be removed safely.
        for p in sorted(old_dirs, key=lambda p: len(p.parts), reverse=True):
            p.rmdir()

        for rel in sorted(target_dirs, key=lambda s: (s.count("/"), s.casefold())):
            root.joinpath(*rel.split("/")).mkdir(parents=True, exist_ok=True)

        for original, stage_path, destination in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage_path), str(destination))
            moved_final.append((stage_path, destination))

        shutil.rmtree(stage_dir)
        print("Layout applied successfully.")
    except Exception:
        # Best-effort rollback: move final files back to staging, remove new dirs, restore originals.
        rollback_ok = True
        for stage_path, destination in reversed(moved_final):
            try:
                if destination.exists():
                    shutil.move(str(destination), str(stage_path))
            except Exception as e:
                rollback_ok = False
                print(f"ROLLBACK ERROR moving {destination} back to staging: {e}", file=sys.stderr)
        if all_staged:
            try:
                current_dirs = [p for p in root.rglob("*") if p.is_dir() and not p.is_symlink()]
                for p in sorted(current_dirs, key=lambda p: len(p.parts), reverse=True):
                    p.rmdir()
            except Exception as e:
                rollback_ok = False
                print(f"ROLLBACK ERROR removing newly created directories: {e}", file=sys.stderr)
        for original, stage_path, _destination in reversed(staged):
            try:
                if stage_path.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(stage_path), str(original))
            except Exception as e:
                rollback_ok = False
                print(f"ROLLBACK ERROR restoring {original}: {e}", file=sys.stderr)
        if rollback_ok:
            shutil.rmtree(stage_dir, ignore_errors=True)
            print("Operation failed; original layout was restored as far as verified.", file=sys.stderr)
        else:
            print(f"Rollback was incomplete. Do not delete this recovery directory: {stage_dir}", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transfer a directory layout using a path/size/hash manifest, without transferring file contents."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_export = sub.add_parser("export", help="Create a layout manifest on source computer")
    p_export.add_argument("root", help="Directory whose layout should be shared")
    p_export.add_argument("manifest", help="Output JSON path; keep it outside the root")
    p_export.add_argument(
        "--hash-algorithm",
        choices=SUPPORTED_HASH_ALGORITHMS,
        default=DEFAULT_HASH_ALGORITHM,
        help=(
            "Hash algorithm to use (default: blake2b; a strong, generally fast choice). "
            "The selected algorithm is stored in the manifest."
        ),
    )
    p_apply = sub.add_parser("apply", help="Reorganize files on the destination computer to match the source")
    p_apply.add_argument("root", help="Directory on the source computer containing the same files")
    p_apply.add_argument("manifest", help="JSON manifest created by export")
    p_apply.add_argument("--execute", action="store_true", help="Actually move files (default is dry run)")
    args = parser.parse_args()
    try:
        if args.command == "export":
            export_manifest(args.root, args.manifest, args.hash_algorithm)
        else:
            apply_manifest(args.root, args.manifest, args.execute)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
