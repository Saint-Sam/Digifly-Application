"""Generic-seed app DC observation, retaining qualified v3 scientific semantics.

This separately authored adapter generalizes the reviewed root27 drive
qualification's source-defined DC transformation, not its historical pilot
admission policy. The builder, initial states, recurrent graph, clock and
independent mechanism remain byte-pinned. A full Poisson *input construction*
is retained as the DC provenance reference; it is never simulated here.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
QUALIFIED_DC_SOURCE_SHA256 = "3ec3d626076a8782183c25daaf15f81e260d54e8592a6c652786f644415dc2c5"
BASE_PINS = {
    "scripts/pdcm_bionet_dc_qualification_v1.py": QUALIFIED_DC_SOURCE_SHA256,
    "scripts/build_pdcm_bionet_primary_v3.py": "d634ac906a95ee33074f01b9976e10603832bf4a186c40c768288c35239bda73",
    "scripts/run_pdcm_bionet_primary_v3.py": "96074aa1db9d2a6badc8bf48a74631f379efd1922e79f79eb13b21ba6e2151dd",
    "scripts/analyze_pdcm_bionet_primary_v3.py": "f2bb135328c3e48174b9469e73b2e6baa83e3269c9b960f7f5f3c2feb17b8f7c",
    "scripts/probe_pdcm_bionet_independent.py": "f70403b8e9865e05a79a6a0a216aa681aa276082ee8fb3b48cac03b1c96faca0",
    "src/digifly_app/pdcm_bionet/event_cell.py": "ffdfcd603910f51be491c5a3e0304034b4afff3a35f4221c78675d5cd797baff",
    "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod": "d25f643ff90083706aadf4f2f992183d2d267289b9e38e542fca7439b74c1ebc",
    "src/digifly_app/workers/pdcm_netpyne_worker.py": "359823360444e9a1d759fe453dd614f7c7318ee276d00e9468b02aabba086f10",
}
EXTERNAL = frozenset(("external_spikes.h5", "network/external_nodes.h5",
    "network/external_node_types.csv", "network/external_cortex_edges.h5",
    "network/external_cortex_edge_types.csv"))


def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def load(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    with Path(path).open("x") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def inventory(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError("DC artifacts must not contain symlinks")
        if path.is_file() and path != Path(root) / "manifest.json":
            result[str(path.relative_to(root))] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    return result


def dependencies():
    for relative, digest in BASE_PINS.items():
        if sha(ROOT / relative) != digest:
            raise ValueError("Approved DC scientific foundation changed: " + relative)
    return (importlib.import_module("scripts.build_pdcm_bionet_primary_v3"),
            importlib.import_module("scripts.run_pdcm_bionet_primary_v3"),
            importlib.import_module("scripts.analyze_pdcm_bionet_primary_v3"))


def extra_current(manifest, inputs):
    """Exact qualified source expression; each input count is source-bound."""
    return [math.sqrt(.01) * float(count) * float(manifest["base_exc_weight_pa"])
            * 8. * .5 * .001 for count in inputs]


def validate_condition(manifest):
    if (manifest.get("scale") != .01 or manifest.get("duration_ms") != 60100.
            or manifest.get("drive") != "balanced_dc"
            or manifest.get("construction_policy") != "released_method_fixed_unique_integer_variance_v3"
            or manifest.get("independent_inputs") is not True
            or manifest.get("matched_reference") is not None
            or manifest.get("cortical_nodes") != 767
            or manifest.get("clock_events") != 2404000
            or manifest.get("clock_nodes") != 1 or manifest.get("clock_contacts") != 767):
        raise ValueError("Outside qualified full-duration 1% DC app condition")
    for key in ("network_seed", "stimulus_seed"):
        if type(manifest.get(key)) is not int or not 0 <= manifest[key] < 2**31:
            raise ValueError("DC observation seed is not a nonnegative 31-bit root")


def reference_path(path, manifest):
    # Inputs stay within one app bundle, including the simulation's copied input.
    path = Path(path).resolve()
    if path.name != "structure" and not (path.name == "input" and path.parent.name == "simulation"):
        raise ValueError("DC structure must use the app-owned source or copied-input route")
    root = path.parents[1] if path.name == "input" and path.parent.name == "simulation" else path.parent
    expected = root / "structure_reference_poisson"
    if manifest.get("paired_poisson_input") != "structure_reference_poisson":
        raise ValueError("DC reference input route changed")
    return expected


def transform(reference, output):
    """Preserve graph, per-cell initialization and clock; replace only drive."""
    builder, runner, _ = dependencies()
    reference, output = Path(reference).resolve(), Path(output).resolve()
    base = runner._verify_structure(reference)
    if base["duration_ms"] != 60100. or base["scale"] != .01:
        raise ValueError("DC input reference is not the full qualified condition")
    if output.exists():
        raise FileExistsError("Preserve every DC input attempt")
    output.mkdir(parents=True)
    for relative in base["files"]:
        if relative in EXTERNAL or relative == "structure_audit.json" or relative.startswith("components/"):
            continue
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(reference / relative, target)
    (output / "components").mkdir()
    added = extra_current(base, builder.INPUTS)
    for index, pop in enumerate(builder.POPS):
        dynamics = load(reference / f"components/{pop}.json")
        dynamics["Iext"] += added[index]
        write_new(output / f"components/{pop}.json", dynamics)
    shutil.copy2(reference / "components/event.json", output / "components/event.json")
    shutil.copy2(SELF, output / "provenance" / SELF.name)
    manifest = dict(base)
    manifest.update(schema="digifly-app-independent-dc-structure-v1", drive="balanced_dc",
        external_nodes=0, external_contacts=0, external_events=0,
        external_rate_hz_by_population=[0.] * 8,
        paired_poisson_input="structure_reference_poisson",
        paired_poisson_manifest_sha256=sha(reference / "manifest.json"),
        adapter_sha256=sha(SELF), source_dependencies_sha256=BASE_PINS,
        qualified_dc_source_sha256=QUALIFIED_DC_SOURCE_SHA256,
        dc_added_mean_drive_pa=added,
        dc_total_current_pa=[float(a) + b for a, b in zip(base["dc_compensation_pa"], added)],
        sampling="Same own graph, recurrent weights/delays, initial state and clock; nominal source-defined DC; no external Poisson nodes/edges/events",
        caveats=base["caveats"] + ["1% DC is outside the paper-active DC grid; silence is an outcome, not technical failure."])
    manifest["files"] = inventory(output)
    write_new(output / "manifest.json", manifest)
    verify_structure(output)
    return manifest


def verify_structure(path):
    import h5py
    import numpy as np
    builder, runner, _ = dependencies()
    path = Path(path).resolve()
    manifest = load(path / "manifest.json")
    validate_condition(manifest)
    reference = reference_path(path, manifest)
    base = runner._verify_structure(reference)
    if sha(reference / "manifest.json") != manifest.get("paired_poisson_manifest_sha256"):
        raise ValueError("Source-bound DC reference manifest changed")
    if (manifest.get("files") != inventory(path)
            or manifest.get("source_dependencies_sha256") != BASE_PINS
            or manifest.get("adapter_sha256") != sha(SELF)
            or manifest.get("qualified_dc_source_sha256") != QUALIFIED_DC_SOURCE_SHA256):
        raise ValueError("DC source/input inventory changed")
    expected_files = (set(base["files"]) - EXTERNAL - {"structure_audit.json"}) | {"provenance/" + SELF.name}
    if set(manifest["files"]) != expected_files or sha(path / "provenance" / SELF.name) != sha(SELF):
        raise ValueError("DC input file set or executed adapter changed")
    allowed = {"schema", "drive", "external_nodes", "external_contacts", "external_events",
               "external_rate_hz_by_population", "sampling", "caveats", "files"}
    for key, value in base.items():
        if key not in allowed and manifest.get(key) != value:
            raise ValueError("DC source construction contract changed: " + key)
    for relative, record in base["files"].items():
        if relative not in EXTERNAL | {"structure_audit.json"} and not relative.startswith("components/"):
            if manifest["files"].get(relative) != record:
                raise ValueError("DC graph, initialization or clock differs: " + relative)
    added = extra_current(base, builder.INPUTS)
    total = [float(a) + b for a, b in zip(base["dc_compensation_pa"], added)]
    if (manifest["external_nodes"] != 0 or manifest["external_contacts"] != 0
            or manifest["external_events"] != 0 or manifest["external_rate_hz_by_population"] != [0.] * 8
            or manifest["dc_added_mean_drive_pa"] != added or manifest["dc_total_current_pa"] != total):
        raise ValueError("DC source mean-current or no-Poisson semantics differ")
    for index, pop in enumerate(builder.POPS):
        expected = load(reference / f"components/{pop}.json")
        expected["Iext"] += added[index]
        if load(path / f"components/{pop}.json") != expected:
            raise ValueError("DC current or initialization component changed")
    if sha(path / "components/event.json") != sha(reference / "components/event.json"):
        raise ValueError("DC signed event delivery changed")
    with h5py.File(path / "clock_spikes.h5") as handle:
        clock = handle["spikes/clock"]
        if (not np.array_equal(clock["timestamps"][:], np.arange(2404000) * .025)
                or not np.all(clock["node_ids"][:] == 0)):
            raise ValueError("DC exact full-window clock or its initial prefix changed")
    return manifest


def config_for(run):
    copied, native = Path(run) / "input", Path(run) / "native_output"
    names = ("cortex", "clock")
    return {"target_simulator": "BioNet",
        "run": {"tstart": 0., "tstop": 60100., "dt": .025, "nsteps_block": 400},
        "conditions": {"v_init": -65., "celsius": 34.},
        "components": {"point_neuron_models_dir": str((copied / "components").resolve()),
                       "synaptic_models_dir": str((copied / "components").resolve()),
                       "mechanisms_dir": str((Path(run) / "mechanisms").resolve())},
        "networks": {
            "nodes": [{"nodes_file": str((copied / f"network/{n}_nodes.h5").resolve()),
                       "node_types_file": str((copied / f"network/{n}_node_types.csv").resolve())} for n in names],
            "edges": [{"edges_file": str((copied / f"network/{n}_cortex_edges.h5").resolve()),
                       "edge_types_file": str((copied / f"network/{n}_cortex_edge_types.csv").resolve())} for n in names]},
        "node_sets": {"clock_input": {"population": "clock"}},
        "inputs": {"clock_input": {"input_type": "spikes", "module": "sonata", "node_set": "clock_input",
                   "input_file": str((copied / "clock_spikes.h5").resolve())}},
        "output": {"output_dir": str(native.resolve()), "spikes_file": str((native / "spikes.h5").resolve()),
                   "spikes_sort_order": "by_time", "overwrite_output_dir": False}}


def run(structure, output):
    import h5py
    import numpy as np
    import bmtk
    import neuron
    from neuron import h
    from bmtk.simulator import bionet
    from bmtk.simulator.bionet.io_tools import io as bio_io
    from bmtk.utils.sonata import File
    _, original, _ = dependencies()
    structure, output = Path(structure).resolve(), Path(output).resolve()
    manifest = verify_structure(structure)
    if output.exists():
        raise FileExistsError("No overwrite or automatic DC retry")
    output.mkdir(parents=True)
    shutil.copytree(structure, output / "input")
    verify_structure(output / "input")
    shutil.copy2(SELF, output / SELF.name)
    for relative in BASE_PINS:
        target = output / "provenance_sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    node_counts, edge_counts = {}, {}
    for name in ("cortex", "clock"):
        network = output / "input/network"
        nodes = File(data_files=str(network / f"{name}_nodes.h5"),
                     data_type_files=str(network / f"{name}_node_types.csv"))
        edges = File(data_files=str(network / f"{name}_cortex_edges.h5"),
                     data_type_files=str(network / f"{name}_cortex_edge_types.csv"))
        node_counts[name] = len(nodes.nodes[name])
        edge_counts[name] = len(edges.edges[f"{name}_to_cortex"])
    if node_counts != {"cortex": 767, "clock": 1} or edge_counts != {"cortex": manifest["recurrent_contacts"], "clock": 767}:
        raise ValueError("BioNet interpreted DC node/edge counts differ")
    probe_spec = importlib.util.spec_from_file_location("app_dc_compile_probe", original.PROBE)
    probe = importlib.util.module_from_spec(probe_spec)
    probe_spec.loader.exec_module(probe)
    mod = probe._mechanisms(output)
    binaries = list(mod.glob("*/libnrnmech.*"))
    if len(binaries) != 1:
        raise ValueError("Expected one independent compiled DC mechanism library")
    neuron.load_mechanisms(str(mod))
    @bionet.cell_model("digifly:DigiflyPDCMEvent", model_type="point_neuron")
    def cell(node, _template, dynamics):
        value = h.DigiflyPDCMEvent()
        value.Iext = float(dynamics["Iext"])
        value.m = float(node["m_init"])
        value.i = 0.
        return value
    write_new(output / "config.json", config_for(output))
    provenance = {"schema": "digifly-app-independent-dc-execution-v1",
        "drive": "balanced_dc", "scale": .01, "duration_ms": 60100.,
        "network_seed": manifest["network_seed"], "stimulus_seed": manifest["stimulus_seed"],
        "construction_policy": manifest["construction_policy"], "primary_target": manifest["primary_target"],
        "normal_parameterization": manifest["normal_parameterization"], "independent_inputs": True,
        "paper_condition": True, "python_executable": sys.executable,
        "bmtk_version": bmtk.__version__, "neuron_version": neuron.__version__,
        "node_counts": node_counts, "edge_counts": edge_counts,
        "adapter_sha256": sha(SELF), "source_dependencies_sha256": BASE_PINS,
        "qualified_dc_source_sha256": QUALIFIED_DC_SOURCE_SHA256,
        "input_manifest_sha256": sha(output / "input/manifest.json"),
        "mechanism_source_sha256": sha(original.MOD), "mechanism_compiled_sha256": sha(binaries[0]),
        "scientific_acceptance": False}
    write_new(output / "provenance.json", provenance)
    with (output / "run.log").open("x") as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), original._capture_bionet_log(bio_io, log):
        cfg = bionet.Config.from_json(str(output / "config.json"))
        cfg.build_env()
        network = bionet.BioNetwork.from_config(cfg)
        started = time.monotonic()
        bionet.BioSimulator.from_config(cfg, network=network).run()
        elapsed = time.monotonic() - started
    result = dict(provenance, state="completed_technical_unreviewed", simulation_walltime_s=elapsed)
    with h5py.File(output / "native_output/spikes.h5") as handle:
        ids = handle["spikes/cortex/node_ids"][:]
        times = handle["spikes/cortex/timestamps"][:]
    _, _, analyzer = dependencies()
    analyzer.validate_spikes(ids, times, 767, 60100.)
    with h5py.File(output / "input/network/cortex_cortex_edges.h5") as handle:
        edges = handle["edges/cortex_to_cortex"]
        sources, delays = edges["source_node_id"][:], edges["0/delay"][:]
    arrivals = ties = 0
    outgoing = {int(node): delays[sources == node].tolist() for node in np.unique(sources)}
    for node, event_time in zip(ids, times):
        for delay in outgoing.get(int(node), ()):
            arrival = float(event_time) + delay
            if arrival > 60100.:
                continue
            arrivals += 1
            grid = round((arrival - .5) / .025)
            ties += int(0 <= grid < 2404000 and abs(arrival - (.5 + grid * .025)) <= 1e-9)
    result.update(cortical_spikes=len(ids), spiking_cells=len(np.unique(ids)),
                  first_spike_ms=float(min(times)) if len(times) else None,
                  last_spike_ms=float(max(times)) if len(times) else None,
                  recurrent_arrivals_within_run=arrivals,
                  recurrent_arrival_clock_grid_ties_1ns_tolerance=ties)
    write_new(output / "technical_result.json", result)
    return result


def analyze(run, output):
    import h5py
    import numpy as np
    builder, _, analyzer = dependencies()
    run, output = Path(run).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("Preserve every DC analysis version")
    artifact = load(run / "artifact_manifest.json")
    actual = {name: record["sha256"] for name, record in inventory(run).items() if name != "artifact_manifest.json"}
    if artifact != {"files": actual}:
        raise ValueError("Immutable DC run inventory changed")
    manifest = verify_structure(run / "input")
    provenance, technical = load(run / "provenance.json"), load(run / "technical_result.json")
    if (technical.get("state") != "completed_technical_unreviewed"
            or any(technical.get(k) != v for k, v in provenance.items())
            or provenance.get("scientific_acceptance") is not False
            or provenance.get("source_dependencies_sha256") != BASE_PINS
            or provenance.get("adapter_sha256") != sha(SELF) or sha(run / SELF.name) != sha(SELF)
            or load(run / "config.json") != config_for(run)):
        raise ValueError("DC executed condition, adapter or framework route differs")
    for key, value in {"scale": .01, "duration_ms": 60100., "drive": "balanced_dc",
                       "network_seed": manifest["network_seed"], "stimulus_seed": manifest["stimulus_seed"],
                       "node_counts": {"cortex": 767, "clock": 1},
                       "edge_counts": {"cortex": manifest["recurrent_contacts"], "clock": 767},
                       "input_manifest_sha256": sha(run / "input/manifest.json"),
                       "qualified_dc_source_sha256": QUALIFIED_DC_SOURCE_SHA256}.items():
        if provenance.get(key) != value:
            raise ValueError("DC framework/provenance claim differs: " + key)
    for relative, digest in BASE_PINS.items():
        if sha(run / "provenance_sources" / relative) != digest:
            raise ValueError("Executed DC foundation changed")
    binaries = list((run / "mechanisms").glob("*/libnrnmech.*"))
    if (len(binaries) != 1 or sha(binaries[0]) != provenance["mechanism_compiled_sha256"]
            or sha(run / "mechanisms/DigiflyPDCMEvent.mod") != BASE_PINS["src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod"]
            or provenance["mechanism_source_sha256"] != BASE_PINS["src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod"]):
        raise ValueError("Executed DC source/compiled mechanism differs")
    if not all(term in (run / "run.log").read_text() for term in ("Building cells", "Running simulation", "Simulation completed")):
        raise ValueError("DC BioNet lifecycle incomplete")
    with h5py.File(run / "native_output/spikes.h5") as handle:
        ids = handle["spikes/cortex/node_ids"][:]
        times = handle["spikes/cortex/timestamps"][:]
    analyzer.validate_spikes(ids, times, 767, 60100.)
    if (technical.get("cortical_spikes") != len(ids)
            or technical.get("spiking_cells") != len(np.unique(ids))
            or technical.get("first_spike_ms") != (float(min(times)) if len(times) else None)
            or technical.get("last_spike_ms") != (float(max(times)) if len(times) else None)):
        raise ValueError("DC recorded spike census differs from actual framework output")
    with h5py.File(run / "input/network/cortex_nodes.h5") as handle:
        initial = handle["nodes/cortex/0/m_init"][:]
    currents = np.repeat(manifest["dc_total_current_pa"], manifest["scaled_counts"])
    predicted = np.flatnonzero(initial + .5 * ((-65. - initial) / 10. + currents / 250.) > -50.)
    if not np.array_equal(np.sort(ids[np.isclose(times, .5, atol=1e-12, rtol=0)]), predicted):
        raise ValueError("DC initial-state first-clock numeric oracle differs")
    with h5py.File(run / "input/network/cortex_cortex_edges.h5") as handle:
        group = handle["edges/cortex_to_cortex"]
        sources, delays = group["source_node_id"][:], group["0/delay"][:]
    arrivals = ties = 0
    for node in np.unique(ids):
        events = times[ids == node, None] + delays[sources == node][None, :]
        within = events <= 60100.
        grid = np.rint((events - .5) / .025)
        arrivals += int(np.count_nonzero(within))
        ties += int(np.count_nonzero(within & (grid >= 0) & (grid < 2404000)
                    & (np.abs(events - (.5 + grid * .025)) <= 1e-9)))
    if (arrivals != technical.get("recurrent_arrivals_within_run")
            or ties != technical.get("recurrent_arrival_clock_grid_ties_1ns_tolerance")):
        raise ValueError("DC independent arrival/clock-tie census differs")
    output.mkdir(parents=True)
    counts = np.asarray(manifest["scaled_counts"])
    ends, begins = np.cumsum(counts), np.r_[0, np.cumsum(counts)[:-1]]
    pops = np.searchsorted(ends, ids, side="right")
    gids = {pop: list(range(int(begins[i]), int(ends[i]))) for i, pop in enumerate(builder.POPS)}
    spikes = [(float(t), int(n), builder.POPS[int(p)]) for n, t, p in zip(ids, times, pops)]
    if not analyzer._paper_statistics(output, gids_by_population=gids, spikes=spikes,
                                      duration_ms=60100., sample_seed=manifest["stimulus_seed"]):
        raise ValueError("DC full-duration paper windows absent")
    with (output / "paper_metrics.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    if [row["population"] for row in rows] != list(builder.POPS):
        raise ValueError("DC paper metrics have incomplete population ownership")
    with (output / "synchrony_metric_comparison.csv").open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("population", "sampled_cells", "population_total_fano_3ms_5s", "population_total_fano_3ms_60s", "per_cell_normalized_3ms_5s", "per_cell_normalized_3ms_60s"))
        for row in rows:
            n = int(row["sampled_cells"])
            values = [float(row[f"synchrony_3ms_{w}"]) if row[f"synchrony_3ms_{w}"] else None for w in ("5s", "60s")]
            if any(v is not None and not math.isfinite(v) for v in values):
                raise ValueError("Nonfinite defined DC synchrony")
            writer.writerow([row["population"], n, *[n * v if v is not None else "" for v in values], *[v if v is not None else "" for v in values]])
    census = []
    for index, pop in enumerate(builder.POPS):
        active = cv = corr = 0
        for node in gids[pop]:
            train = times[(ids == node) & (times >= 100) & (times < 60100)]
            active += int(len(train) > 0)
            cv += int(len(train) >= 3 and float(np.diff(train).mean()) > 0)
            bins = np.histogram(train[train < 5100], bins=100 + np.arange(201) * 25)[0]
            corr += int(np.var(bins) > 0)
        n = len(gids[pop])
        if int(rows[index]["sampled_cells"]) != n or int(rows[index]["correlation_defined_cells"]) != corr:
            raise ValueError("DC metric defined/undefined cell census differs")
        census.append((pop, n, active, cv, n - cv, corr, n - corr))
    with (output / "defined_cell_census.csv").open("x", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(("population", "all_cells", "settled_active_cells", "cv_defined_cells", "cv_undefined_cells", "correlation_defined_cells", "correlation_undefined_cells"))
        writer.writerows(census)
    settled = (times >= 100) & (times < 60100)
    result = {"schema": "digifly-app-independent-dc-paper-window-audit-v1",
        "construction_policy": manifest["construction_policy"], "drive": "balanced_dc",
        "technical_acceptance": True, "scientific_acceptance": False, "scale": .01, "duration_ms": 60100.,
        "network_seed": manifest["network_seed"], "stimulus_seed": manifest["stimulus_seed"],
        "cortical_cells": 767, "cortical_spikes": len(ids), "post_settling_spikes": int(np.count_nonzero(settled)),
        "per_population_spikes": dict(zip(builder.POPS, np.bincount(pops, minlength=8).astype(int).tolist())),
        "startup_spikes_at_0_5ms": len(predicted), "recurrent_arrivals_within_run": arrivals,
        "recurrent_arrival_clock_grid_ties_1ns_tolerance": ties,
        "run_artifact_manifest_sha256": sha(run / "artifact_manifest.json"),
        "input_manifest_sha256": sha(run / "input/manifest.json"),
        "source_h5_sha256": sha(run / "native_output/spikes.h5"),
        "qualified_dc_source_sha256": QUALIFIED_DC_SOURCE_SHA256,
        "analysis_windows_ms": {"settled": [100, 60100], "correlation": [100, 5100]},
        "caveats": ["One independent network observation, not cohort/equivalence or accepted paper replication.",
            "1% DC is below the paper-active DC grid; zero activity and undefined CV/correlation are valid outcomes.",
            "BioNet and NetPyNE share NEURON, not independent solvers.",
            "Both synchrony normalizations are retained; original numerical convention remains ambiguous.",
            "Clock-arrival ties are counted; internal same-time ordering is not inferred."]}
    write_new(output / "audit.json", result)
    write_new(output / "analysis_manifest.json", {
        "source_run": str(run), "source_artifact_manifest_sha256": result["run_artifact_manifest_sha256"],
        "analysis_script_sha256": sha(SELF), "shared_metric_function_sha256": BASE_PINS["src/digifly_app/workers/pdcm_netpyne_worker.py"],
        "files_sha256": {name: record["sha256"] for name, record in inventory(output).items()}})
    return result
