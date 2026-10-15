"""Fail-closed release artifact audit.

The core distribution must contain application code and small owned resources,
never a local connectome, morphology corpus, simulation output, or developer
machine binding.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import os
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
PROJECT_LICENSE_MARKER = b"MIT License"
PROJECT_LICENSE_SHA256 = (
    "ddff84afdac51697e2bcd237ffe0a363cb2f4b25dfa0d6efa3457ac31613bc5d"
)

# These files are the minimum self-contained bridge between a Digifly release
# and user-managed simulator installations.  Match path suffixes because the
# install root is intentionally different for an sdist, wheel data directory,
# and native application bundle.
REQUIRED_RELEASE_COMPONENTS = {
    "licensing scope notice": "THIRD_PARTY_NOTICES.md",
    "generic experiment worker": "digifly_app/workers/generic_experiment_worker.py",
    "aggregate pair Parquet indexer": "digifly_app/workers/pair_graph_import_worker.py",
    "isolated independent-trial scheduler": "digifly_app/workers/independent_trials.py",
    "Arbor channel translator": "digifly_app/workers/arbor_channel_translation.py",
    "Augustin nat source": "mechanisms/augustin_2019/nat.mod",
    "Augustin nap source": "mechanisms/augustin_2019/nap.mod",
    "Augustin k source": "mechanisms/augustin_2019/k.mod",
    "BMTK BioNet worker": "digifly_app/workers/bmtk_bionet_worker.py",
    "NetPyNE PDCM worker": "digifly_app/workers/pdcm_netpyne_worker.py",
    "independent BioNet app worker": "digifly_app/workers/pdcm_bionet_app_worker.py",
    "independent BioNet scalable app worker": "digifly_app/workers/pdcm_bionet_scalable_app_worker.py",
    "independent BioNet 5% app worker": "digifly_app/workers/pdcm_bionet_5pct_app_worker.py",
    "independent BioNet 5% app controller source": "digifly_app/core/pdcm_bionet_5pct_app.py",
    "independent BioNet saved Results converter": "digifly_app/workers/pdcm_campaign_results.py",
    "NetPyNE Workbench worker": "digifly_app/workers/netpyne_workbench_worker.py",
    "NetPyNE PDCM MPI worker": "digifly_app/workers/pdcm_netpyne_mpi_worker.py",
    "NetPyNE PDCM Athene runner": "scripts/run_pdcm_athene.sh",
    "NetPyNE PDCM Athene layer installer": "scripts/install_pdcm_athene_layer.sh",
    "NEURON MPI diagnostic": "scripts/probe_neuron_mpi.py",
    "NetPyNE PDCM Athene runbook": "docs/PDCM_ATHENE_RUNBOOK.md",
    "NetPyNE workbench plan": "docs/NETPYNE_WORKBENCH_PLAN.md",
    "NEURON Gap source": "mechanisms/neuron_gap_junctions/Gap.mod",
    "NEURON RectGap source": "mechanisms/neuron_gap_junctions/RectGap.mod",
    "NEURON HeteroRectGap source": (
        "mechanisms/neuron_gap_junctions/HeteroRectGap.mod"
    ),
    "NEURON source manifest": (
        "mechanisms/neuron_gap_junctions/source_manifest.json"
    ),
    "NEURON mechanism build helper": "scripts/build_neuron_gap_mechanisms.py",
}
from ..workers.pdcm_bionet_app_worker import SOURCE_PINS, SCAFFOLD

for _source in (*SOURCE_PINS, *SCAFFOLD):
    REQUIRED_RELEASE_COMPONENTS[f"independent BioNet frozen {_source}"] = (
        f"digifly_app/pdcm_bionet/primary_v3/{_source}"
    )
from ..workers.pdcm_bionet_scalable_app_worker import SOURCE_PINS as SCALABLE_SOURCE_PINS, SCAFFOLD as SCALABLE_SCAFFOLD

for _source in (*SCALABLE_SOURCE_PINS, *SCALABLE_SCAFFOLD):
    REQUIRED_RELEASE_COMPONENTS[f"independent BioNet scalable frozen {_source}"] = (
        f"digifly_app/pdcm_bionet/primary_v4/{_source}"
    )
_NEURON_MECHANISM_NAMES = ("Gap", "RectGap", "HeteroRectGap")
_VENDORED_RUNTIME_PACKAGES = frozenset({"arbor", "bmtk", "h5py", "netpyne", "neuron", "numpy"})
_VENDORED_RUNTIME_EXECUTABLES = frozenset(
    {
        "arbor-build-catalogue",
        "bmtk",
        "modlunit",
        "nocmodl",
        "nrniv",
        "nrnivmodl",
        "special",
    }
)
_DIGIFLY_ARCHIVE_RE = re.compile(r"^digifly[-_]workstation(?:[-_.]|$)", re.IGNORECASE)
FORBIDDEN_RELEASE_NAMES = frozenset(
    {
        "libqtvirtualkeyboardplugin.dylib",
        "qtvirtualkeyboard",
        "qtvirtualkeyboardqml",
    }
)
BUNDLED_ARBOR_ALLOW_ENV = "DIGIFLY_ALLOW_BUNDLED_ARBOR"
BUNDLED_ARBOR_MARKER = "DIGIFLY_RUNTIME.json"


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


def _release_kind(path: Path) -> str | None:
    """Classify canonical Digifly release outputs, not arbitrary test dirs."""

    name = path.name
    folded = name.casefold()
    if path.is_dir() and (path / "digifly_windows_package.json").is_file():
        return "windows"
    if _DIGIFLY_ARCHIVE_RE.match(name) and folded.endswith("-windows-x86_64.zip"):
        return "windows"
    if path.is_dir() and folded.endswith(".app"):
        stem = folded.removesuffix(".app")
        if "digifly" in stem and "workstation" in stem:
            return "app"
    if path.is_file() and _DIGIFLY_ARCHIVE_RE.match(name):
        if folded.endswith(".whl"):
            return "wheel"
        if any(
            folded.endswith(suffix)
            for suffix in (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz")
        ):
            return "sdist"
    return None


def _ends_with_path(path: PurePosixPath, suffix: str) -> bool:
    wanted = PurePosixPath(suffix).parts
    return len(path.parts) >= len(wanted) and path.parts[-len(wanted) :] == wanted


def _vendored_runtime_root(path: PurePosixPath) -> tuple[str, str] | None:
    """Return a stable offending root/reason for an obvious simulator payload.

    Exact path-segment matching deliberately permits Digifly's adapters,
    workers, documentation, and source helpers (for example
    ``bmtk_bionet_worker.py`` and ``neuron_gap_junctions``).
    """

    for index, raw_part in enumerate(path.parts):
        part = raw_part.casefold()
        package: str | None = None
        if part in _VENDORED_RUNTIME_PACKAGES:
            package = part
        else:
            for candidate in _VENDORED_RUNTIME_PACKAGES:
                if part in {f"{candidate}.libs", f"{candidate}.data"} or (
                    part.startswith(f"{candidate}-")
                    and part.endswith((".dist-info", ".egg-info"))
                ):
                    package = candidate
                    break
        if package is not None:
            root = PurePosixPath(*path.parts[: index + 1]).as_posix()
            return root, f"Vendored {package} package/runtime content is forbidden."

    filename = path.name.casefold()
    if filename in _VENDORED_RUNTIME_EXECUTABLES:
        return path.as_posix(), f"Bundled simulator executable {path.name!r} is forbidden."
    if path.suffix.casefold() in BINARY_SUFFIXES and re.match(
        r"^(?:lib)?(?:corenrn|nrniv|nrnpython|nrnmech|arbor)(?:[._-]|$)", filename
    ):
        return path.as_posix(), f"Bundled simulator binary {path.name!r} is forbidden."
    if path.suffix.casefold() in BINARY_SUFFIXES and "-catalogue" in filename:
        return (
            path.as_posix(),
            f"Bundled compiled mechanism catalogue {path.name!r} is forbidden.",
        )
    return None


def _validate_neuron_manifest(
    component_entries: dict[str, _Entry],
) -> tuple[ArtifactIssue, ...]:
    manifest_suffix = REQUIRED_RELEASE_COMPONENTS["NEURON source manifest"]
    manifest_entry = component_entries.get(manifest_suffix)
    if manifest_entry is None:
        return ()
    if manifest_entry.data is None:
        return (
            ArtifactIssue(
                "invalid_release_component",
                manifest_entry.name,
                "NEURON source manifest is too large to validate.",
            ),
        )
    try:
        payload = json.loads(manifest_entry.data.decode("utf-8"))
        mechanisms = payload["mechanisms"]
        if payload.get("engine") != "neuron" or not isinstance(mechanisms, dict):
            raise ValueError("manifest does not describe NEURON mechanisms")
    except (KeyError, TypeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        return (
            ArtifactIssue(
                "invalid_release_component",
                manifest_entry.name,
                f"NEURON source manifest is invalid: {exc}.",
            ),
        )

    issues: list[ArtifactIssue] = []
    for mechanism in _NEURON_MECHANISM_NAMES:
        source_suffix = REQUIRED_RELEASE_COMPONENTS[f"NEURON {mechanism} source"]
        source_entry = component_entries.get(source_suffix)
        record = mechanisms.get(mechanism)
        if not isinstance(record, dict):
            issues.append(
                ArtifactIssue(
                    "invalid_release_component",
                    manifest_entry.name,
                    f"NEURON source manifest has no {mechanism} record.",
                )
            )
            continue
        if record.get("source_filename") != PurePosixPath(source_suffix).name:
            issues.append(
                ArtifactIssue(
                    "invalid_release_component",
                    manifest_entry.name,
                    f"NEURON source manifest names the wrong file for {mechanism}.",
                )
            )
        if source_entry is None or source_entry.data is None:
            # Absence has its own missing-component issue; an oversized source
            # has already failed the general size gate.
            continue
        expected = str(record.get("source_sha256") or "").casefold()
        actual = hashlib.sha256(source_entry.data).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", expected) or expected != actual:
            issues.append(
                ArtifactIssue(
                    "release_component_hash_mismatch",
                    source_entry.name,
                    f"{mechanism} source does not match its packaged manifest hash.",
                )
            )
    return tuple(issues)


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


def _inside_approved_arbor_root(path: PurePosixPath) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    wanted = ("contents", "resources", "runtimes", "arbor")
    return any(parts[index : index + len(wanted)] == wanted for index in range(len(parts)))


def audit_artifact(
    artifact: str | Path,
    *,
    max_file_bytes: int = 100 * 1024 * 1024,
) -> ArtifactReport:
    path = Path(artifact).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    issues: list[ArtifactIssue] = []
    release_kind = _release_kind(path)
    artifact_is_app = path.is_dir() and path.suffix.casefold() == ".app"
    component_entries: dict[str, _Entry] = {}
    project_license_entry: _Entry | None = None
    bundled_arbor_marker: _Entry | None = None
    vendored_roots: set[str] = set()
    allow_bundled_arbor = (
        release_kind == "app"
        and os.environ.get(BUNDLED_ARBOR_ALLOW_ENV, "").strip().casefold()
        in {"1", "true", "yes", "on"}
    )
    count = total = 0
    windows_binaries = {}
    approved_neuron_archives = set()
    approved_neuron_entries = set()
    if release_kind == "windows":
        try:
            marker_entry = next(e for e in _entries(path) if e.name == "digifly_windows_package.json")
            marker = json.loads(marker_entry.data.decode("utf-8"))
            if marker["schema"] != "digifly-windows-package-v1":
                raise ValueError("Unknown Windows package schema")
            windows_binaries = {name: record for name, record in marker["files"].items()
                                if PurePosixPath(name).suffix.casefold() in BINARY_SUFFIXES}
            if "Digifly Workstation.exe" not in windows_binaries:
                raise ValueError("Windows application entrypoint absent")
            # One explicit Windows offline-runtime exception, not arbitrary
            # simulator vendoring or a global larger-file waiver.
            offline = next((e for e in _entries(path) if e.name == "installers/neuron/DIGIFLY_NEURON_RUNTIME.json"), None)
            if offline is not None:
                from ..core.windows_neuron_bundle import INSTALLER_SHA256, RUNTIME_ID
                declared = json.loads(offline.data.decode("utf-8"))
                if (declared.get("runtime_id") != RUNTIME_ID or declared.get("neuron_installer_sha256") != INSTALLER_SHA256
                        or declared.get("schema") != "digifly-offline-neuron-v1"
                        or set(declared.get("artifacts", {})) != {"runtime.zip", "NEURON-9.0.2-official-setup.exe"}
                        or declared["artifacts"]["NEURON-9.0.2-official-setup.exe"]["sha256"] != INSTALLER_SHA256):
                    raise ValueError("Unknown bundled NEURON source identity")
                approved_neuron_entries.add(offline.name)
                for name, record in declared["artifacts"].items():
                    relative = "installers/neuron/" + name
                    if relative not in marker["files"] or marker["files"][relative] != record:
                        raise ValueError("Bundled NEURON artifact differs from package inventory")
                    approved_neuron_entries.add(relative)
                    if name == "runtime.zip":
                        approved_neuron_archives.add(relative)
        except (StopIteration, KeyError, ValueError, TypeError, AttributeError):
            issues.append(ArtifactIssue("invalid_windows_package", "digifly_windows_package.json", "Windows binaries require a declared package inventory."))
    for entry in _entries(path):
        count += 1
        total += entry.size
        logical = PurePosixPath(entry.name)
        approved_arbor_entry = allow_bundled_arbor and _inside_approved_arbor_root(logical)
        if approved_arbor_entry and logical.name == BUNDLED_ARBOR_MARKER:
            bundled_arbor_marker = entry
        parts = set(logical.parts)
        suffix = logical.suffix.casefold()
        if release_kind is not None and any(
            part.casefold() in FORBIDDEN_RELEASE_NAMES for part in logical.parts
        ):
            issues.append(
                ArtifactIssue(
                    "incompatible_distribution_component",
                    entry.name,
                    "Qt Virtual Keyboard is not part of the Digifly release boundary.",
                )
            )
        for component_suffix in REQUIRED_RELEASE_COMPONENTS.values():
            converter_module = (component_suffix == "digifly_app/workers/pdcm_campaign_results.py"
                and _ends_with_path(logical, "digifly_app/core/pdcm_campaign_results.py"))
            if _ends_with_path(logical, component_suffix) or converter_module:
                component_entries.setdefault(component_suffix, entry)
        if (
            logical.name.casefold() == "license"
            and entry.data is not None
            and PROJECT_LICENSE_MARKER in entry.data
            and hashlib.sha256(entry.data).hexdigest() == PROJECT_LICENSE_SHA256
        ):
            project_license_entry = entry
        vendored = None if approved_arbor_entry or entry.name in approved_neuron_entries else _vendored_runtime_root(logical)
        if vendored is not None:
            runtime_root, detail = vendored
            if runtime_root not in vendored_roots:
                vendored_roots.add(runtime_root)
                issues.append(
                    ArtifactIssue("bundled_simulator_runtime", runtime_root, detail)
                )
        if logical.is_absolute() or ".." in logical.parts:
            issues.append(ArtifactIssue("unsafe_member_path", entry.name, "Archive member path is unsafe."))
        forbidden = sorted(parts & GENERATED_PARTS) if not approved_arbor_entry else []
        if forbidden:
            issues.append(
                ArtifactIssue(
                    "generated_content",
                    entry.name,
                    f"Generated directory is not releasable: {', '.join(forbidden)}",
                )
            )
        if entry.size > (1024**3 if entry.name in approved_neuron_archives else max_file_bytes):
            issues.append(
                ArtifactIssue(
                    "oversized_file",
                    entry.name,
                    f"{entry.size} bytes exceeds the {max_file_bytes}-byte limit.",
                )
            )
        if suffix in DATASET_SUFFIXES and not approved_arbor_entry:
            issues.append(
                ArtifactIssue(
                    "scientific_dataset",
                    entry.name,
                    f"Dataset-like file type {suffix} is forbidden in the core artifact.",
                )
            )
        declared_windows_binary = (release_kind == "windows" and entry.name in windows_binaries
                                   and windows_binaries[entry.name].get("bytes") == entry.size)
        if suffix in BINARY_SUFFIXES and not declared_windows_binary and not _inside_native_bundle(
            logical, artifact_is_app=artifact_is_app
        ):
            issues.append(
                ArtifactIssue(
                    "undeclared_binary",
                    entry.name,
                    "Compiled binaries are forbidden outside a native application bundle.",
                )
            )
        if entry.data is not None and suffix in TEXT_SUFFIXES and not approved_arbor_entry:
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
    if allow_bundled_arbor:
        try:
            marker = json.loads((bundled_arbor_marker.data if bundled_arbor_marker else b"").decode())
            valid_marker = (
                marker.get("component") == "Arbor"
                and marker.get("version") == "0.12.2"
                and marker.get("policy") == "temporary-bundled-runtime-v1"
            )
        except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
            valid_marker = False
        if not valid_marker:
            issues.append(
                ArtifactIssue(
                    "invalid_bundled_arbor_runtime",
                    "Contents/Resources/runtimes/arbor",
                    "The explicit bundled-Arbor exception requires its valid runtime marker.",
                )
            )
    if release_kind is not None:
        if project_license_entry is None:
            issues.append(
                ArtifactIssue(
                    "missing_release_component",
                    "LICENSE",
                    f"Digifly {release_kind} is missing its exact MIT project license.",
                )
            )
        for label, component_suffix in REQUIRED_RELEASE_COMPONENTS.items():
            if component_suffix not in component_entries:
                issues.append(
                    ArtifactIssue(
                        "missing_release_component",
                        component_suffix,
                        f"Digifly {release_kind} is missing its {label}.",
                    )
                )
        issues.extend(_validate_neuron_manifest(component_entries))
        for version, pins in (("primary_v3", SOURCE_PINS), ("primary_v4", SCALABLE_SOURCE_PINS)):
            for source, expected_hash in pins.items():
                suffix = f"digifly_app/pdcm_bionet/{version}/{source}"
                entry = component_entries.get(suffix)
                if entry is not None and (entry.data is None or hashlib.sha256(entry.data).hexdigest() != expected_hash):
                    issues.append(ArtifactIssue("changed_scientific_source", suffix,
                        "Frozen independent BioNet scientific source does not match its qualified bytes."))
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
