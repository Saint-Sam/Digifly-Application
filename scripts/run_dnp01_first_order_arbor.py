#!/usr/bin/env python3
"""Prepare and run the full first-order MANC DNp01 chemical neighborhood.

This is a reproducible stress-run entry point for Digifly Workstation. It reads
the canonical local MANC edge index without modifying it, reuses the fullest
local MANC morphology archive, downloads only absent public skeletons, and
creates a normal Experiment Builder run that Results can discover.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from typing import Any, Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen

from digifly_app.core.circuit import (
    CircuitSpec,
    ConnectomeRef,
    GapJunctionPolicy,
    NeuronQuery,
)
from digifly_app.core.connectomes import ConnectomeCatalog, NeuronRecord
from digifly_app.core.experiment import (
    ConditionSpec,
    ExperimentSpec,
    RecordingSpec,
    StimulusSpec,
)
from digifly_app.core.morphology import load_swc
from digifly_app.core.process_environment import (
    external_runtime_launcher,
    sanitized_external_environment,
)
from digifly_app.core.resource_profile import (
    PROFILE_PATH_ENV,
    ResourceKind,
    ResourceProfile,
    default_profile_path,
)
from digifly_app.engines.generic_experiment import (
    GenericExperimentAdapter,
    update_run_manifest_state,
)


DATASET = "manc:v1.2.1"
EDGE_ROOT_ENV = "DIGIFLY_MANC_EDGE_ROOT"
MORPHOLOGY_ROOT_ENV = "DIGIFLY_MANC_MORPHOLOGY_ROOT"
METADATA_ENV = "DIGIFLY_MANC_METADATA"
WORKSPACE_ENV = "DIGIFLY_WORKSTATION_WORKSPACE"
ARBOR_RUNTIME_ENV = "DIGIFLY_ARBOR_RUNTIME"
PUBLIC_ROOT_ENV = "DIGIFLY_PUBLIC_ROOT"


@dataclass(frozen=True)
class RunPaths:
    """Machine-local paths resolved from arguments, environment, or a profile."""

    workspace_root: Path
    output_root: Path
    managed_data_root: Path
    edge_root: Path
    morphology_root: Path
    metadata: Path | None
    arbor_runtime: Path
    digifly_public_root: Path | None
    profile_path: Path | None


def _optional_path(value: Path | None, environment_name: str) -> Path | None:
    if value is not None:
        return value.expanduser().resolve()
    configured = os.environ.get(environment_name, "").strip()
    return Path(configured).expanduser().resolve() if configured else None


def _load_profile(requested: Path | None) -> tuple[ResourceProfile | None, Path | None]:
    path = requested.expanduser().resolve() if requested is not None else default_profile_path()
    if path.is_file():
        return ResourceProfile.load(path), path
    if requested is not None or os.environ.get(PROFILE_PATH_ENV, "").strip():
        raise FileNotFoundError(
            f"Digifly resource profile does not exist: {path}. "
            f"Choose an existing profile with --profile or unset {PROFILE_PATH_ENV}."
        )
    return None, None


def _profile_source_roots(profile: ResourceProfile | None) -> tuple[Path, ...]:
    if profile is None:
        return ()
    roots: list[Path] = []
    for kind in (
        ResourceKind.MORPHOLOGY_SOURCE,
        ResourceKind.CONNECTOME_SOURCE,
        ResourceKind.DATA_SOURCE,
    ):
        roots.extend(binding.resolved_path for binding in profile.bindings(kind))
    return tuple(dict.fromkeys(roots))


def _content_root_candidates(
    profile: ResourceProfile | None,
    source_roots: Iterable[Path],
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    for root in source_roots:
        candidates.extend((root, root / "export_swc", root / "source" / "export_swc"))
    if profile is not None:
        public = profile.workspace_root
        candidates.extend(
            (
                public / "Phase 1" / "manc_v1.2.1" / "export_swc",
                public / "Phase 2" / "data" / "export_swc",
                public / "Phase 2_Arbor_staging" / "data" / "export_swc",
            )
        )
    return tuple(dict.fromkeys(path.expanduser().resolve() for path in candidates))


def _discover_edge_root(candidates: Iterable[Path]) -> Path | None:
    for candidate in candidates:
        if (candidate / "edges" / "master_edges_cache.sqlite").is_file():
            return candidate
    return None


def _discover_metadata(
    profile: ResourceProfile | None,
    content_roots: Iterable[Path],
) -> Path | None:
    filename = "all_neurons_neuroncriteria_template.csv"
    candidates: list[Path] = []
    for root in content_roots:
        candidates.extend((root / filename, root.parent / filename))
    if profile is not None:
        public = profile.workspace_root
        candidates.extend(
            (
                public / "Phase 2" / "data" / filename,
                public / "Phase 2_Arbor_staging" / "data" / filename,
                public / "Phase 2_NEURON_archive_2026-07-04" / "data" / filename,
            )
        )
    return next((path for path in dict.fromkeys(candidates) if path.is_file()), None)


def _resolve_paths(args: argparse.Namespace) -> RunPaths:
    profile, profile_path = _load_profile(args.profile)
    source_roots = _profile_source_roots(profile)
    content_roots = _content_root_candidates(profile, source_roots)

    workspace = _optional_path(args.workspace, WORKSPACE_ENV)
    if workspace is not None:
        output_root = workspace / "runs"
        managed_data_root = workspace / "data"
    elif profile is not None:
        output_root = profile.output_root
        managed_data_root = profile.managed_data_root
        workspace = output_root.parent
    else:
        workspace = (Path.home() / "Digifly Workstation Workspace").resolve()
        output_root = workspace / "runs"
        managed_data_root = workspace / "data"

    edge_root = _optional_path(args.edge_root, EDGE_ROOT_ENV)
    if edge_root is None:
        edge_root = _discover_edge_root(content_roots)
    if edge_root is None:
        raise FileNotFoundError(
            "No full MANC edge archive is configured. Provide --edge-root, set "
            f"{EDGE_ROOT_ENV}, or register a morphology/connectome source that contains "
            "edges/master_edges_cache.sqlite in the Digifly Data Library."
        )
    edge_db = edge_root / "edges" / "master_edges_cache.sqlite"
    if not edge_db.is_file():
        raise FileNotFoundError(
            f"The selected MANC edge root lacks edges/master_edges_cache.sqlite: {edge_root}"
        )

    morphology_root = _optional_path(args.morphology_root, MORPHOLOGY_ROOT_ENV)
    if morphology_root is None:
        morphology_root = edge_root
    if not morphology_root.is_dir():
        raise FileNotFoundError(
            f"The MANC morphology root does not exist: {morphology_root}. "
            f"Provide --morphology-root or set {MORPHOLOGY_ROOT_ENV}."
        )

    metadata = _optional_path(args.metadata, METADATA_ENV)
    if metadata is None:
        metadata = _discover_metadata(profile, (*content_roots, edge_root, morphology_root))
    if metadata is not None and not metadata.is_file():
        raise FileNotFoundError(
            f"The selected MANC metadata CSV does not exist: {metadata}. "
            f"Provide a valid --metadata path or unset {METADATA_ENV}."
        )

    runtime = _optional_path(args.runtime, ARBOR_RUNTIME_ENV)
    if runtime is None and profile is not None:
        runtime = profile.runtime_path(ResourceKind.ARBOR_RUNTIME)
    if runtime is None:
        raise FileNotFoundError(
            "No Arbor Python runtime is configured. Choose one in the Workstation, "
            f"provide --runtime, or set {ARBOR_RUNTIME_ENV}."
        )
    runtime = external_runtime_launcher(runtime)
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise FileNotFoundError(
            f"The selected Arbor Python runtime is missing or not executable: {runtime}"
        )

    public_root = _optional_path(args.digifly_public_root, PUBLIC_ROOT_ENV)
    if public_root is None and profile is not None and profile.workspace_root.is_dir():
        public_root = profile.workspace_root
    if public_root is not None and not public_root.is_dir():
        raise FileNotFoundError(
            f"The selected legacy Digifly workspace does not exist: {public_root}"
        )

    return RunPaths(
        workspace_root=workspace,
        output_root=output_root,
        managed_data_root=managed_data_root,
        edge_root=edge_root,
        morphology_root=morphology_root,
        metadata=metadata,
        arbor_runtime=runtime,
        digifly_public_root=public_root,
        profile_path=profile_path,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _edge_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)


def _network_scope(edge_db: Path, center: str) -> tuple[tuple[str, ...], set[tuple[str, str]], dict[str, int]]:
    with _edge_connection(edge_db) as connection:
        incoming = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT pre_id FROM edges WHERE post_id=? AND pre_id<>?",
                (center, center),
            )
        }
        outgoing = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT post_id FROM edges WHERE pre_id=? AND post_id<>?",
                (center, center),
            )
        }
        counts = connection.execute(
            "SELECT "
            "SUM(CASE WHEN post_id=? THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN pre_id=? THEN 1 ELSE 0 END) "
            "FROM edges WHERE pre_id=? OR post_id=?",
            (center, center, center, center),
        ).fetchone()
        selected = tuple(
            sorted({center, *incoming, *outgoing}, key=lambda value: int(value))
        )
        placeholders = ",".join("?" for _ in selected)
        rows = connection.execute(
            "SELECT DISTINCT pre_id, post_id FROM edges "
            f"WHERE pre_id IN ({placeholders}) AND post_id IN ({placeholders})",
            (*selected, *selected),
        )
        noncenter_pairs = {
            tuple(sorted((str(pre), str(post)), key=lambda value: int(value)))
            for pre, post in rows
            if str(pre) != str(post) and center not in {str(pre), str(post)}
        }
    return selected, noncenter_pairs, {
        "presynaptic_neurons": len(incoming),
        "postsynaptic_neurons": len(outgoing),
        "bidirectional_neighbors": len(incoming & outgoing),
        "unique_neighbors": len(incoming | outgoing),
        "incoming_contacts": int(counts[0] or 0),
        "outgoing_contacts": int(counts[1] or 0),
        "direct_contacts": int(counts[0] or 0) + int(counts[1] or 0),
    }


def _metadata(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {
            str(row.get("bodyId") or "").strip(): dict(row)
            for row in csv.DictReader(handle)
            if str(row.get("bodyId") or "").strip()
        }


def _family(row: dict[str, str]) -> str:
    prefix = str(row.get("prefix") or "").strip().upper()
    if prefix in {"AN", "DN", "IN", "MN", "SN"}:
        return prefix
    class_name = str(row.get("class") or row.get("class_") or "").casefold()
    for phrase, family in (
        ("descending", "DN"),
        ("ascending", "AN"),
        ("motor", "MN"),
        ("sensory", "SN"),
        ("interneuron", "IN"),
    ):
        if phrase in class_name:
            return family
    return "UNKNOWN"


def _scale_swc(raw_path: Path, destination: Path) -> float:
    parsed: list[tuple[str, ...] | str] = []
    maximum_coordinate = 0.0
    with raw_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                parsed.append(line.rstrip("\n"))
                continue
            fields = tuple(stripped.split())
            if len(fields) < 7:
                raise ValueError(f"Malformed public SWC row in {raw_path}")
            maximum_coordinate = max(
                maximum_coordinate, *(abs(float(fields[index])) for index in (2, 3, 4))
            )
            parsed.append(fields)
    scale = 0.001 if maximum_coordinate >= 1000.0 else 1.0
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(f"# Digifly coordinate scale applied: {scale:g}\n")
        handle.write(f"# Source: {raw_path}\n")
        for value in parsed:
            if isinstance(value, str):
                handle.write(value + "\n")
                continue
            fields = list(value)
            for index in (2, 3, 4, 5):
                fields[index] = f"{float(fields[index]) * scale:.9g}"
            handle.write(" ".join(fields) + "\n")
    os.replace(temporary, destination)
    return scale


def _download_one(neuron_id: str, raw_dir: Path, scaled_dir: Path) -> dict[str, Any]:
    encoded_dataset = quote(DATASET, safe=":")
    url = (
        f"https://neuprint.janelia.org/api/skeletons/skeleton/"
        f"{encoded_dataset}/{quote(neuron_id, safe='')}?format=swc"
    )
    raw_path = raw_dir / f"{neuron_id}_{DATASET.replace(':', '_')}_raw.swc"
    scaled_path = scaled_dir / f"{neuron_id}_{DATASET.replace(':', '_')}_um.swc"
    if not raw_path.is_file():
        request = Request(url, headers={"User-Agent": "Digifly-Workstation/0.1"})
        with urlopen(request, timeout=120) as response:
            payload = response.read(250 * 1024 * 1024 + 1)
        if len(payload) > 250 * 1024 * 1024:
            raise ValueError(f"Public SWC for {neuron_id} exceeds the 250 MB safety limit")
        if not payload.lstrip().startswith((b"#", b"1 ", b"1\t")):
            raise ValueError(f"neuPrint did not return SWC text for {neuron_id}")
        raw_dir.mkdir(parents=True, exist_ok=True)
        partial = raw_path.with_suffix(raw_path.suffix + ".partial")
        partial.write_bytes(payload)
        os.replace(partial, raw_path)
    if not scaled_path.is_file():
        scale = _scale_swc(raw_path, scaled_path)
    else:
        scale = 0.001
    return {
        "neuron_id": neuron_id,
        "url": url,
        "raw_path": str(raw_path),
        "raw_sha256": _sha256(raw_path),
        "scaled_path": str(scaled_path),
        "scaled_sha256": _sha256(scaled_path),
        "coordinate_scale": scale,
    }


def _download_missing(
    neuron_ids: Iterable[str], raw_dir: Path, scaled_dir: Path
) -> list[dict[str, Any]]:
    ordered = tuple(sorted(set(neuron_ids), key=lambda value: int(value)))
    if not ordered:
        return []
    print(f"Downloading {len(ordered)} missing public MANC skeletons…", flush=True)
    records: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(8, len(ordered))) as pool:
        jobs = {
            pool.submit(_download_one, neuron_id, raw_dir, scaled_dir): neuron_id
            for neuron_id in ordered
        }
        for completed, future in enumerate(as_completed(jobs), start=1):
            neuron_id = jobs[future]
            records.append(future.result())
            print(f"  [{completed}/{len(ordered)}] body {neuron_id}", flush=True)
    return sorted(records, key=lambda item: int(item["neuron_id"]))


def _root_count(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            fields = line.strip().split()
            if fields and not fields[0].startswith("#") and len(fields) >= 7:
                count += int(float(fields[6])) == -1
    return count


def _heal_one_with_neuprint(source: Path, destination: Path) -> None:
    """Run the same unbounded fragment-healing rule used by Digifly Phase 1."""

    import numpy as np
    import pandas as pd
    from neuprint.skeleton import heal_skeleton

    frame = pd.read_csv(
        source,
        sep=r"\s+",
        comment="#",
        header=None,
        names=("rowId", "structure", "x", "y", "z", "radius", "link"),
    )
    healed = heal_skeleton(frame, max_distance=np.inf, root_parent=-1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write("# Digifly fragment healing: neuprint.skeleton.heal_skeleton\n")
        handle.write("# max_distance=inf; raw and scaled source files remain unchanged\n")
        for row in healed.itertuples(index=False):
            handle.write(
                f"{int(row.rowId)} {int(row.structure)} "
                f"{float(row.x):.9g} {float(row.y):.9g} {float(row.z):.9g} "
                f"{float(row.radius):.9g} {int(row.link)}\n"
            )
    os.replace(temporary, destination)


def _heal_downloaded_forests(
    records: list[dict[str, Any]], runtime: Path, script_path: Path
) -> list[dict[str, Any]]:
    for record in records:
        scaled_path = Path(str(record["scaled_path"]))
        roots = _root_count(scaled_path)
        record["root_count_before_healing"] = roots
        record["simulation_path"] = str(scaled_path)
        if roots <= 1:
            continue
        healed_path = scaled_path.parent.parent / "healed" / scaled_path.name.replace(
            "_um.swc", "_healed_um.swc"
        )
        if not healed_path.is_file():
            subprocess.run(
                (
                    str(runtime),
                    "-B",
                    str(script_path),
                    "--heal-input",
                    str(scaled_path),
                    "--heal-output",
                    str(healed_path),
                ),
                check=True,
                env=sanitized_external_environment(
                    {
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONPATH": str(script_path.parent.parent / "src"),
                    }
                ),
            )
        healed_roots = _root_count(healed_path)
        if healed_roots != 1:
            raise ValueError(
                f"Fragment healing left {healed_roots} roots in {healed_path}"
            )
        record.update(
            {
                "healing_algorithm": "neuprint.skeleton.heal_skeleton(max_distance=inf)",
                "healed_path": str(healed_path),
                "healed_sha256": _sha256(healed_path),
                "root_count_after_healing": healed_roots,
                "simulation_path": str(healed_path),
            }
        )
    return records


def _records(
    selected: tuple[str, ...],
    catalog: ConnectomeCatalog,
    downloaded: list[dict[str, Any]],
    metadata: dict[str, dict[str, str]],
) -> tuple[NeuronRecord, ...]:
    downloaded_by_id = {item["neuron_id"]: item for item in downloaded}
    output: list[NeuronRecord] = []
    for neuron_id in selected:
        existing = catalog.by_id.get(neuron_id)
        if existing is not None:
            output.append(existing)
            continue
        item = downloaded_by_id[neuron_id]
        row = metadata.get(neuron_id, {})
        neuron_type = str(row.get("type") or "").strip() or "Unknown"
        output.append(
            NeuronRecord(
                neuron_id=neuron_id,
                family=_family(row),
                neuron_type=neuron_type,
                swc_path=str(item["simulation_path"]),
                connectome_key="manc:v1.2.1:full-local",
            )
        )
    return tuple(output)


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--center", default="10000")
    parser.add_argument(
        "--profile",
        type=Path,
        help=(
            "Workstation resource profile. Defaults to the profile selected by "
            f"{PROFILE_PATH_ENV}, then the normal per-user profile."
        ),
    )
    parser.add_argument(
        "--edge-root",
        type=Path,
        help=(
            "Full MANC export_swc root containing the edge cache. Otherwise uses "
            f"{EDGE_ROOT_ENV} or a registered profile source."
        ),
    )
    parser.add_argument(
        "--morphology-root",
        type=Path,
        help=(
            "Full MANC morphology root. Otherwise uses "
            f"{MORPHOLOGY_ROOT_ENV} or the resolved edge root."
        ),
    )
    parser.add_argument(
        "--metadata",
        type=Path,
        help=(
            "Optional all-neuron metadata CSV. Otherwise uses "
            f"{METADATA_ENV} or a profile-relative copy when available."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help=(
            "Writable Workstation root. Otherwise uses "
            f"{WORKSPACE_ENV}, the profile output/data roots, or the standard per-user root."
        ),
    )
    parser.add_argument(
        "--runtime",
        type=Path,
        help=(
            "Arbor-capable Python executable. Otherwise uses "
            f"{ARBOR_RUNTIME_ENV} or the profile's Arbor runtime."
        ),
    )
    parser.add_argument(
        "--digifly-public-root",
        type=Path,
        help=(
            "Optional legacy Digifly workspace used for compatible edge lookups. "
            f"Otherwise uses {PUBLIC_ROOT_ENV} or the profile workspace binding."
        ),
    )
    parser.add_argument("--workers", type=int, default=22)
    parser.add_argument("--duration-ms", type=float, default=15.0)
    parser.add_argument("--sample-dt-ms", type=float, default=0.1)
    parser.add_argument("--name", default="")
    parser.add_argument("--heal-input", type=Path)
    parser.add_argument("--heal-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.heal_input or args.heal_output:
        if args.heal_input is None or args.heal_output is None:
            raise ValueError("--heal-input and --heal-output must be provided together")
        _heal_one_with_neuprint(args.heal_input.resolve(), args.heal_output.resolve())
        return 0
    paths = _resolve_paths(args)
    edge_root = paths.edge_root
    edge_db = edge_root / "edges" / "master_edges_cache.sqlite"
    selected, noncenter_pairs, scope = _network_scope(edge_db, str(args.center))
    print(json.dumps({"selected_cells": len(selected), **scope}, sort_keys=True), flush=True)

    connectome = ConnectomeRef(
        key="manc:v1.2.1:full-local",
        label="MANC full",
        root=str(edge_root),
        dataset="manc_v1.2.1",
    )
    morphology_source = replace(connectome, root=str(paths.morphology_root))
    catalog = ConnectomeCatalog.scan(morphology_source)
    missing = tuple(neuron_id for neuron_id in selected if neuron_id not in catalog.by_id)
    cache_root = (
        paths.managed_data_root
        / "stress-inputs"
        / "manc-v1.2.1"
        / f"dnp01-{args.center}-first-order"
    )
    downloaded = _download_missing(missing, cache_root / "raw", cache_root / "um")
    downloaded = _heal_downloaded_forests(
        downloaded,
        paths.arbor_runtime,
        Path(__file__).resolve(),
    )
    if missing and paths.metadata is None:
        print(
            "No MANC metadata CSV was found; downloaded cells will use Unknown "
            f"type/family labels. Provide --metadata or set {METADATA_ENV} to retain labels.",
            file=sys.stderr,
            flush=True,
        )
    metadata = _metadata(paths.metadata) if paths.metadata is not None else {}
    records = _records(selected, catalog, downloaded, metadata)

    print(f"Loading and validating {len(records)} morphologies…", flush=True)
    morphologies = tuple(load_swc(record) for record in records)
    node_count = sum(len(item.nodes) for item in morphologies)
    segment_count = sum(len(item.segments) for item in morphologies)
    source_manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": DATASET,
        "center_neuron_id": str(args.center),
        "scope": scope,
        "selected_neuron_ids": list(selected),
        "source_edge_database": str(edge_db),
        "source_edge_database_size_bytes": edge_db.stat().st_size,
        "local_morphology_archive": str(paths.morphology_root),
        "metadata_csv": str(paths.metadata) if paths.metadata is not None else None,
        "resource_profile": str(paths.profile_path) if paths.profile_path is not None else None,
        "downloaded_public_skeletons": downloaded,
        "loaded_nodes": node_count,
        "loaded_segments": segment_count,
        "selection_policy": (
            "DNp01 plus every unique direct chemical presynaptic and postsynaptic "
            "partner; chemical execution retains only center-partner contacts."
        ),
        "gap_junctions": "disabled",
    }
    _atomic_json(cache_root / "source_manifest.json", source_manifest)
    print(f"Loaded {node_count:,} nodes and {segment_count:,} segments.", flush=True)

    reference_path = (
        paths.output_root
        / "experiments"
        / "dnp01-ttmn-full-chemical-and-gap-validation-ebbec88541"
        / "circuit.json"
    )
    if reference_path.is_file():
        circuit = CircuitSpec.from_dict(json.loads(reference_path.read_text(encoding="utf-8")))
    else:
        circuit = CircuitSpec()
    circuit.connectome = connectome
    circuit.query = NeuronQuery(
        expression=f"DNp01 {args.center} direct pre/post chemical neighborhood",
        max_neurons=len(selected),
    )
    circuit.neuron_ids = selected
    circuit.morphology_sha256 = {
        record.neuron_id: _sha256(Path(record.swc_path)) for record in records
    }
    circuit.connection_overrides.clear()
    for neuron_a, neuron_b in sorted(
        noncenter_pairs, key=lambda pair: (int(pair[0]), int(pair[1]))
    ):
        circuit.set_connection_class_enabled(
            neuron_a, neuron_b, "chemical", False
        )
    circuit.gap_junction_policy = GapJunctionPolicy(mode="none")

    timestamp = datetime.now().strftime("%Y-%m-%d %H%M%S")
    name = (
        args.name.strip()
        or f"DNp01 {args.center} Full First-Order Chemical Stress {timestamp}"
    )
    experiment = ExperimentSpec(
        name=name,
        template_key="blank",
        engine="arbor",
        duration_ms=float(args.duration_ms),
        integration_dt_ms=0.025,
        initial_voltage_mV=-65.0,
        temperature_C=6.3,
        random_seed=10342,
        repetitions=1,
        workers=int(args.workers),
        stimuli=(
            StimulusSpec(
                name="DNp01 soma step",
                enabled=True,
                target_neuron_ids=(str(args.center),),
                target_region="soma",
                waveform="step",
                amplitude_nA=0.9,
                delay_ms=2.0,
                pulse_width_ms=2.0,
                frequency_hz=100.0,
                pulse_count=1,
            ),
        ),
        conditions=(
            ConditionSpec(
                name="Chemical only",
                enabled=True,
                gap_junctions_enabled=False,
                chemical_synapses_enabled=True,
            ),
        ),
        recording=RecordingSpec(
            target_neuron_ids=selected,
            target_region="soma",
            record_voltage=True,
            detect_spikes=True,
            sample_dt_ms=float(args.sample_dt_ms),
            spike_threshold_mV=-20.0,
            make_plots=False,
        ),
    )

    output_root = paths.output_root
    output_root.mkdir(parents=True, exist_ok=True)
    adapter = GenericExperimentAdapter(
        {"arbor": paths.arbor_runtime},
        digifly_public_root=paths.digifly_public_root,
    )
    print("Materializing the exact chemical-contact manifest…", flush=True)
    report = adapter.validate(circuit, experiment, morphologies, output_root=output_root)
    if not report.ok:
        failures = [f"{item.title}: {item.detail}" for item in report.failures]
        raise RuntimeError("Preflight failed:\n" + "\n".join(failures))
    payload = adapter.request_payload(
        circuit, experiment, morphologies, report, output_root=output_root
    )
    chemical_count = len(payload["edge_manifest"]["chemical_edges"])
    if chemical_count != scope["direct_contacts"]:
        raise RuntimeError(
            f"Expected {scope['direct_contacts']} direct contacts, materialized {chemical_count}"
        )
    if payload["edge_manifest"]["electrical_edges"]:
        raise RuntimeError("Electrical contacts were materialized despite gap mode none")
    plan = adapter.plan(circuit, experiment, output_root=output_root)
    request_path = adapter.write_request(
        Path(plan.working_directory) / "worker_request.json", payload
    )
    print(
        f"Running Arbor: {len(selected)} cells, {chemical_count:,} chemical contacts, "
        f"0 gap contacts, {args.workers} CPU threads",
        flush=True,
    )
    log_path = request_path.parent / "launcher.log"
    environment = sanitized_external_environment(plan.environment)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            (plan.program, *plan.arguments),
            cwd=plan.working_directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
        return_code = process.wait()
    if return_code:
        update_run_manifest_state(
            request_path.parent,
            "failed",
            exit_code=return_code,
            launcher_log=str(log_path),
        )
        raise RuntimeError(f"Arbor worker exited with status {return_code}; see {log_path}")
    summary_path = request_path.parent / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(
        json.dumps(
            {
                "status": summary.get("status"),
                "summary_path": str(summary_path),
                "cells": len(selected),
                "chemical_contacts": chemical_count,
                "gap_contacts": 0,
                "recorded_samples": summary.get("metadata", {}).get("Recorded samples"),
                "detected_spikes": summary.get("metadata", {}).get("Detected spikes"),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
