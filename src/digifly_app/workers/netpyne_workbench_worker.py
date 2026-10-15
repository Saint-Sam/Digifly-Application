"""Standalone local NetPyNE worker for frozen Digifly Workbench v1 bundles.

Only the Python standard library is imported before bundle validation. The
chosen external interpreter must provide NetPyNE and NEURON; it need not have
Digifly installed. This is a serial synthetic-HH lane, not a PDCM worker.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path


WORKFLOW = "netpyne_workbench_v1"
_FROZEN_FILES = {
    "model.json",
    "netpyne_network.json",
    "netpyne_simulation.json",
    "worker_request.json",
    "netpyne_workbench_worker.py",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_bundle(request_path: Path) -> tuple[Path, dict, dict, dict, dict]:
    root = request_path.absolute().parent
    if request_path.name != "worker_request.json" or request_path.is_symlink():
        raise ValueError("Choose a regular Workbench worker_request.json")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if (
        request.get("schema_version") != 1
        or request.get("workflow") != WORKFLOW
        or request.get("run_root") != "."
    ):
        raise ValueError("Unsupported or non-portable Workbench request")
    bundle_path = root / "bundle_manifest.json"
    if not bundle_path.is_file() or bundle_path.is_symlink():
        raise ValueError("Workbench input integrity manifest is missing")
    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    hashes = bundle.get("files_sha256")
    if (
        bundle.get("schema_version") != 1
        or bundle.get("workflow") != WORKFLOW
        or not isinstance(hashes, dict)
        or set(hashes) != _FROZEN_FILES
    ):
        raise ValueError("Invalid Workbench input integrity manifest")
    for name, expected in hashes.items():
        path = root / name
        if not path.is_file() or path.is_symlink() or _sha256(path) != expected:
            raise ValueError(f"Prepared Workbench input changed: {name}")
    model = json.loads((root / "model.json").read_text(encoding="utf-8"))
    network = json.loads((root / "netpyne_network.json").read_text(encoding="utf-8"))
    simulation = json.loads((root / "netpyne_simulation.json").read_text(encoding="utf-8"))
    if model.get("schema_version") != 1 or model.get("model_family") != "synthetic_single_soma_hh":
        raise ValueError("Only the version-1 synthetic single-soma HH family can run")
    if any(item.get("usage") == "model_input" for item in model.get("external_resources", ())):
        raise ValueError("External model inputs are not executable in Workbench v1")
    if not isinstance(network, dict) or not isinstance(simulation, dict):
        raise ValueError("NetPyNE settings must be objects")
    return root, request, model, network, simulation


def _netpyne_specs(network: dict, simulation: dict):
    from netpyne import specs

    params = specs.NetParams()
    allowed_network = {
        "cellParams", "popParams", "synMechParams", "connParams",
        "stimSourceParams", "stimTargetParams", "defaultThreshold",
    }
    if set(network) != allowed_network:
        raise ValueError("Unexpected NetPyNE network settings")
    for key, value in network.items():
        if isinstance(value, dict):
            getattr(params, key).update(value)
        else:
            setattr(params, key, value)
    cfg = specs.SimConfig()
    allowed_simulation = {
        "duration", "dt", "hParams", "seeds", "recordStep", "recordCellsSpikes",
        "recordCells", "recordTraces", "saveJson", "savePickle", "analysis", "verbose",
    }
    if set(simulation) != allowed_simulation:
        raise ValueError("Unexpected NetPyNE simulation settings")
    for key, value in simulation.items():
        setattr(cfg, key, value)
    cfg.saveJson = False
    cfg.savePickle = False
    cfg.analysis = {}
    cfg.verbose = False
    return params, cfg


def _cell_rows(cells: list, expected_counts: dict[str, int]) -> tuple[list[tuple], dict[int, str], int]:
    rows = []
    by_gid = {}
    counts = {pop: 0 for pop in expected_counts}
    contacts = 0
    for cell in cells:
        gid = cell.get("gid")
        tags = cell.get("tags") or {}
        pop = tags.get("pop")
        if type(gid) is not int or gid < 0 or pop not in expected_counts or gid in by_gid:
            raise RuntimeError("NetPyNE returned an invalid or duplicate population cell")
        coordinates = []
        for key in ("x", "y", "z"):
            value = tags.get(key)
            coordinates.append(value if isinstance(value, (float, int)) and math.isfinite(value) else "")
        rows.append((gid, pop, *coordinates))
        by_gid[gid] = pop
        counts[pop] += 1
        contacts += len(cell.get("conns") or ())
    if counts != expected_counts:
        raise RuntimeError(f"NetPyNE cell counts differ from the frozen model: {counts} != {expected_counts}")
    return sorted(rows), by_gid, contacts


def _write_cells(root: Path, rows: list[tuple]) -> None:
    with (root / "cells.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("gid", "population", "x_um", "y_um", "z_um"))
        writer.writerows(rows)


def _write_spikes(root: Path, data: dict, by_gid: dict[int, str], duration_ms: float) -> int:
    times = list(data.get("spkt", ()))
    gids = list(data.get("spkid", ()))
    if len(times) != len(gids):
        raise RuntimeError("NetPyNE spike time and cell-ID arrays differ in length")
    events = []
    for time, gid in zip(times, gids):
        value = float(time)
        cell_id = int(gid)
        if not math.isfinite(value) or not 0 <= value <= duration_ms + 1e-6 or cell_id not in by_gid:
            raise RuntimeError("NetPyNE returned an invalid spike event")
        events.append((value, cell_id, by_gid[cell_id]))
    events.sort(key=lambda row: (row[0], row[1]))
    with (root / "spikes.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("time_ms", "gid", "population"))
        writer.writerows(events)
    return len(events)


def _write_voltage(root: Path, data: dict, by_gid: dict[int, str], record_step: float) -> int:
    traces = data.get("V_soma") or {}
    if not isinstance(traces, dict):
        raise RuntimeError("NetPyNE voltage recordings are not a cell-to-trace mapping")
    if not traces:
        return 0
    times = list(data.get("t") or ())
    output = root / "voltage_traces.csv"
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("condition", "repetition", "neuron_id", "time_ms", "voltage_mV"))
        for key, values in sorted(traces.items()):
            label = str(key)
            if not label.startswith("cell_"):
                raise RuntimeError(f"Unexpected NetPyNE voltage trace key: {label}")
            gid = int(label[5:])
            if gid not in by_gid:
                raise RuntimeError(f"Voltage recording refers to unknown cell {gid}")
            samples = list(values)
            if times and len(times) != len(samples):
                raise RuntimeError(f"Time and voltage samples differ for cell {gid}")
            for index, sample in enumerate(samples):
                time = float(times[index]) if times else index * record_step
                voltage = float(sample)
                if not math.isfinite(time) or not math.isfinite(voltage):
                    raise RuntimeError(f"Non-finite voltage recording for cell {gid}")
                writer.writerow((by_gid[gid], 0, gid, time, voltage))
    return len(traces)


def execute(request_path: Path) -> Path:
    root, request, model, network, simulation = _load_bundle(request_path)
    if (root / "summary.json").exists():
        raise FileExistsError("This Workbench run already has a summary")
    from netpyne import sim
    import netpyne
    import neuron

    params, cfg = _netpyne_specs(network, simulation)
    cfg.saveFolder = str(root)
    cfg.filename = "netpyne_workbench"
    sim.createSimulate(netParams=params, simConfig=cfg)
    if sim.nhosts != 1:
        raise RuntimeError("Workbench v1 local execution supports one NetPyNE process")
    expected_counts = {item["label"]: int(item["count"]) for item in model["populations"]}
    rows, by_gid, contacts = _cell_rows(sim.net.allCells, expected_counts)
    _write_cells(root, rows)
    data = sim.allSimData
    spike_count = _write_spikes(root, data, by_gid, float(simulation["duration"]))
    voltage_count = (
        _write_voltage(root, data, by_gid, float(simulation["recordStep"]))
        if simulation["recordTraces"] else 0
    )
    artifacts = [
        {"kind": "json", "path": "model.json", "label": "Frozen Workbench model"},
        {"kind": "json", "path": "netpyne_network.json", "label": "NetPyNE network settings"},
        {"kind": "json", "path": "netpyne_simulation.json", "label": "NetPyNE simulation settings"},
        {"kind": "json", "path": "bundle_manifest.json", "label": "Input integrity manifest"},
        {"kind": "json", "path": "run_manifest.json", "label": "Run state and runtime"},
        {"kind": "table", "path": "cells.csv", "label": "Population cell IDs and positions"},
        {"kind": "table", "path": "spikes.csv", "label": "Spike events"},
        {"kind": "file", "path": "worker.log", "label": "NetPyNE worker log"},
    ]
    if voltage_count:
        artifacts.append({"kind": "table", "path": "voltage_traces.csv", "label": "Soma voltage traces"})
    summary = {
        "schema_version": 1,
        "workflow": WORKFLOW,
        "status": "completed",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "title": f"NetPyNE Workbench · {request['run_name']}",
        "engine": "netpyne",
        "metadata": {
            "run_name": request["run_name"],
            "model_name": model["name"],
            "model_family": model["model_family"],
            "duration_ms": simulation["duration"],
            "cells": len(rows),
            "spikes": spike_count,
            "recorded_voltage_cells": voltage_count,
            "synaptic_contacts": contacts,
            "netpyne_version": getattr(netpyne, "__version__", "unknown"),
            "neuron_version": getattr(neuron, "__version__", "unknown"),
            "scope": "Version-1 synthetic single-soma HH model; no external model inputs",
        },
        "artifacts": artifacts,
    }
    destination = root / "summary.json"
    _write_json(destination, summary)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a prepared Digifly NetPyNE Workbench model")
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    result = execute(args.request)
    print(f"Digifly NetPyNE Workbench completed: {result}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
