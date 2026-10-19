#!/usr/bin/env python3
"""Bind a packaged build to its staged original source inputs, not a Git state."""
import argparse
import hashlib
import json
from pathlib import Path


def real_ancestors(path):
    """Reject dangling links too; do not resolve away an unsafe ancestor."""
    path = Path(path).absolute()
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("Source receipt refuses symbolic-link paths or ancestors")
    return path


def inventory(root):
    root = real_ancestors(root).resolve()
    files = [root / name for name in ("main.py", "pyproject.toml", "digifly_build.json", "scripts/build_macos.sh", "scripts/stage_build_source_manifest.py")]
    files.extend(sorted((root / "src/digifly_app").rglob("*")))
    result = {}
    for path in files:
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        real_ancestors(path)
        if path.is_dir():
            continue
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("Missing or escaping staged source input")
        data = path.read_bytes()
        result[path.relative_to(root).as_posix()] = {
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)
        }
    if not any(name.startswith("src/digifly_app/core/") for name in result):
        raise ValueError("Compiled core source input is absent")
    return result


def freeze(root, output, build):
    output = real_ancestors(output)
    if output.exists():
        raise ValueError("Refusing to replace a source freeze")
    manifest = {"schema": "digifly-staged-build-source-v1", "build": build,
                "files": inventory(root),
                "scope": "Exact listed staged original source inputs and build identity. PySide may rewrite its deployment configuration; compiler command/configuration and third-party runtime inventory are outside this receipt. Not a proof of which inputs the compiler consumed, binary reproducibility, or scientific acceptance."}
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with output.open("xb") as handle:
        handle.write(data)
    return hashlib.sha256(data).hexdigest()


def attach(root, freeze_path, bundle, expected_sha256):
    freeze_path = real_ancestors(freeze_path)
    data = freeze_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Precompile source freeze identity changed")
    manifest = json.loads(data)
    if manifest.get("schema") != "digifly-staged-build-source-v1" or manifest["files"] != inventory(root):
        raise ValueError("Staged source inputs drifted after the precompile freeze")
    destination = real_ancestors(Path(bundle) / "Contents/Resources/digifly_build_sources.json")
    if destination.exists():
        raise ValueError("Refusing to replace a build-source receipt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    real_ancestors(destination)
    with destination.open("xb") as handle:
        handle.write(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "attach"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--build")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--freeze-sha256")
    args = parser.parse_args()
    if args.mode == "freeze":
        if not args.build:
            parser.error("freeze requires --build")
        print(freeze(args.source, args.freeze, args.build))
    else:
        if not args.bundle or not args.freeze_sha256:
            parser.error("attach requires --bundle and --freeze-sha256")
        attach(args.source, args.freeze, args.bundle, args.freeze_sha256)


if __name__ == "__main__":
    main()
