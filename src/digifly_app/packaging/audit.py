"""Fail-closed release artifact audit.

The core distribution must contain application code and small owned resources,
never a local connectome, morphology corpus, simulation output, or developer
machine binding.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Iterable, Iterator
import zipfile


DATASET_SUFFIXES = frozenset(
    {
        ".swc",
        ".csv",
        ".tsv",
        ".parquet",
        ".feather",
        ".h5",
        ".hdf5",
        ".sqlite",
        ".sqlite3",
        ".db",
        ".npy",
        ".npz",
        ".pkl",
        ".pickle",
    }
)
GENERATED_PARTS = frozenset(
    {
        ".git",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "deployment",
        "dist",
        "outputs",
        "workspace",
    }
)
BINARY_SUFFIXES = frozenset({".dll", ".dylib", ".exe", ".pyd", ".so"})
TEXT_SUFFIXES = frozenset(
    {
        "",
        ".cfg",
        ".ini",
        ".json",
        ".md",
        ".mod",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)
MACHINE_PATH_PATTERNS = (
    re.compile(rb"/Users/[A-Za-z0-9._-]+/"),
    re.compile(rb"/home/[A-Za-z0-9._-]+/"),
    re.compile(rb"[A-Za-z]:\\\\Users\\\\[^\\\r\n]+\\\\"),
)


@dataclass(frozen=True)
class ArtifactIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class ArtifactReport:
    artifact: str
    file_count: int
    total_bytes: int
    issues: tuple[ArtifactIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact": self.artifact,
            "ok": self.ok,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "issues": [asdict(issue) for issue in self.issues],
        }


@dataclass(frozen=True)
class _Entry:
    name: str
    size: int
    data: bytes | None


def _directory_entries(root: Path) -> Iterator[_Entry]:
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        data = path.read_bytes() if size <= 2 * 1024 * 1024 else None
        yield _Entry(relative, size, data)


def _zip_entries(path: Path) -> Iterator[_Entry]:
    with zipfile.ZipFile(path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            data = archive.read(info) if info.file_size <= 2 * 1024 * 1024 else None
            yield _Entry(info.filename, info.file_size, data)


def _tar_entries(path: Path) -> Iterator[_Entry]:
    with tarfile.open(path, "r:*") as archive:
        for info in sorted(archive.getmembers(), key=lambda item: item.name):
            if not info.isfile():
                continue
            stream = archive.extractfile(info) if info.size <= 2 * 1024 * 1024 else None
            data = stream.read() if stream is not None else None
            yield _Entry(info.name, info.size, data)


def _entries(path: Path) -> Iterable[_Entry]:
    if path.is_dir():
        return _directory_entries(path)
    if zipfile.is_zipfile(path):
        return _zip_entries(path)
    if tarfile.is_tarfile(path):
        return _tar_entries(path)
    raise ValueError(f"Unsupported artifact type: {path}")


def _inside_native_bundle(path: PurePosixPath, *, artifact_is_app: bool) -> bool:
    parts = path.parts
    if artifact_is_app:
        return len(parts) > 1 and parts[0] == "Contents"
    try:
        app_index = next(index for index, part in enumerate(parts) if part.endswith(".app"))
    except StopIteration:
        return False
    return len(parts) > app_index + 2 and parts[app_index + 1] == "Contents"


def audit_artifact(
    artifact: str | Path,
    *,
    max_file_bytes: int = 100 * 1024 * 1024,
) -> ArtifactReport:
    path = Path(artifact).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    issues: list[ArtifactIssue] = []
    artifact_is_app = path.is_dir() and path.suffix.casefold() == ".app"
    count = total = 0
    for entry in _entries(path):
        count += 1
        total += entry.size
        logical = PurePosixPath(entry.name)
        parts = set(logical.parts)
        suffix = logical.suffix.casefold()
        if logical.is_absolute() or ".." in logical.parts:
            issues.append(ArtifactIssue("unsafe_member_path", entry.name, "Archive member path is unsafe."))
        forbidden = sorted(parts & GENERATED_PARTS)
        if forbidden:
            issues.append(
                ArtifactIssue(
                    "generated_content",
                    entry.name,
                    f"Generated directory is not releasable: {', '.join(forbidden)}",
                )
            )
        if entry.size > max_file_bytes:
            issues.append(
                ArtifactIssue(
                    "oversized_file",
                    entry.name,
                    f"{entry.size} bytes exceeds the {max_file_bytes}-byte limit.",
                )
            )
        if suffix in DATASET_SUFFIXES:
            issues.append(
                ArtifactIssue(
                    "scientific_dataset",
                    entry.name,
                    f"Dataset-like file type {suffix} is forbidden in the core artifact.",
                )
            )
        if suffix in BINARY_SUFFIXES and not _inside_native_bundle(
            logical, artifact_is_app=artifact_is_app
        ):
            issues.append(
                ArtifactIssue(
                    "undeclared_binary",
                    entry.name,
                    "Compiled binaries are forbidden outside a native application bundle.",
                )
            )
        if entry.data is not None and suffix in TEXT_SUFFIXES:
            for pattern in MACHINE_PATH_PATTERNS:
                if pattern.search(entry.data):
                    issues.append(
                        ArtifactIssue(
                            "developer_machine_path",
                            entry.name,
                            "Text contains an absolute developer home-directory path.",
                        )
                    )
                    break
    return ArtifactReport(str(path), count, total, tuple(issues))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digifly-package-audit",
        description="Reject datasets and machine-local state from Digifly release artifacts.",
    )
    parser.add_argument("artifacts", nargs="+", help="Wheel, sdist, app bundle, or artifact directory")
    parser.add_argument("--max-file-size-mb", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reports = [
        audit_artifact(path, max_file_bytes=args.max_file_size_mb * 1024 * 1024)
        for path in args.artifacts
    ]
    if args.json:
        print(json.dumps([report.to_dict() for report in reports], indent=2))
    else:
        for report in reports:
            state = "PASS" if report.ok else "FAIL"
            print(
                f"[{state}] {report.artifact}: {report.file_count} files, "
                f"{report.total_bytes / (1024 * 1024):.1f} MiB"
            )
            for issue in report.issues:
                print(f"  [{issue.code}] {issue.path}: {issue.detail}")
    return 0 if all(report.ok for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
