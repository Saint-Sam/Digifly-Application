"""Tiny SONATA/BioNet test of Digifly's independently authored PDCM cell.

This checks one artificial cell and explicit 40 kHz zero-weight input events.
It is not an eight-population network or a paper-condition result.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

from digifly_app.pdcm_bionet.event_cell import EventCellParameters, EventCellState


ROOT = Path(__file__).resolve().parents[1]
MECHANISM = ROOT / "src/digifly_app/pdcm_bionet/mod/DigiflyPDCMEvent.mod"
DURATION_MS = 20.0
CLOCK_STEP_MS = 0.025
EDGE_DELAY_MS = 0.5
INITIAL_MV = -58.0
DC_CURRENT_PA = 600.0
PULSES = (("exc", 5.013, 250.0), ("inh", 10.017, -150.0))


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _mechanisms(output: Path) -> Path:
    bmtk_spec = importlib.util.find_spec("bmtk")
    if bmtk_spec is None or bmtk_spec.origin is None:
        raise RuntimeError("A compatible BMTK+NEURON interpreter is required")
    events_mod = (
        Path(bmtk_spec.origin).parent / "utils/scripts/bionet/mechanisms/modfiles/vecevent.mod"
    )
    if not events_mod.is_file():
        raise RuntimeError(f"BMTK VecStim mechanism missing: {events_mod}")
    compiler = Path(sys.prefix) / "bin/nrnivmodl"
    if not compiler.is_file():
        raise RuntimeError(f"NEURON compiler missing: {compiler}")
    mod_dir = output / "mechanisms"
    mod_dir.mkdir()
    shutil.copyfile(MECHANISM, mod_dir / MECHANISM.name)
    shutil.copyfile(events_mod, mod_dir / "vecevent.mod")
    built = subprocess.run(
        [str(compiler)], cwd=mod_dir, text=True, capture_output=True, check=False,
    )
    (output / "nrnivmodl.log").write_text(built.stdout + built.stderr, encoding="utf-8")
    if built.returncode:
        raise RuntimeError("Independent BioNet mechanism compilation failed; inspect nrnivmodl.log")
    return mod_dir


def _direct_neuron(mod_dir: Path, event_times: list[float]) -> list[float]:
    import neuron
    from neuron import h

    neuron.load_mechanisms(str(mod_dir))
    h.load_file("stdrun.hoc")
    cell = h.DigiflyPDCMEvent()
    cell.m = INITIAL_MV
    cell.i = 0.0
    cell.Iext = DC_CURRENT_PA
    delivery = h.NetCon(None, cell)
    delivery.weight[0] = 0.0
    pulse_connections = []
    for _name, _time_ms, weight_pa in PULSES:
        connection = h.NetCon(None, cell)
        connection.weight[0] = weight_pa
        pulse_connections.append(connection)
    spikes = h.Vector()
    recorder = h.NetCon(cell, None)
    recorder.record(spikes)
    h.finitialize(-65.0)
    for time_ms in event_times:
        delivery.event(time_ms + EDGE_DELAY_MS)
    for connection, (_name, time_ms, _weight_pa) in zip(pulse_connections, PULSES):
        connection.event(time_ms + EDGE_DELAY_MS)
    h.continuerun(DURATION_MS)
    return [float(time_ms) for time_ms in spikes]


def _bionet(mod_dir: Path, output: Path, event_times: list[float]) -> list[float]:
    import h5py
    from neuron import h
    from bmtk.builder import NetworkBuilder
    from bmtk.simulator import bionet
    from bmtk.utils.reports.spike_trains.spike_trains import SpikeGenerator

    components = output / "components"
    components.mkdir()
    _json(components / "cell.json", {"Iext": DC_CURRENT_PA, "m_init": INITIAL_MV})
    _json(components / "event.json", {"sign": 1})
    network_dir = output / "network"
    network_dir.mkdir()
    cortex = NetworkBuilder("cortex")
    cortex.add_nodes(
        N=1, model_type="point_neuron", model_template="digifly:DigiflyPDCMEvent",
        dynamics_params="cell.json", population_label="L2e",
    )
    cortex.build()
    cortex.save(output_dir=str(network_dir))
    clock = NetworkBuilder("clock")
    clock.add_nodes(N=1, model_type="virtual")
    clock.add_edges(
        source=clock.nodes(), target=cortex.nodes(), connection_rule=1,
        dynamics_params="event.json", syn_weight=0.0, delay=EDGE_DELAY_MS,
    )
    clock.build()
    clock.save(output_dir=str(network_dir))
    pulse_networks = []
    for name, time_ms, weight_pa in PULSES:
        source = NetworkBuilder(name)
        source.add_nodes(N=1, model_type="virtual")
        source.add_edges(
            source=source.nodes(), target=cortex.nodes(), connection_rule=1,
            dynamics_params="event.json", syn_weight=weight_pa, delay=EDGE_DELAY_MS,
        )
        source.build()
        source.save(output_dir=str(network_dir))
        pulse_file = output / f"{name}_spikes.h5"
        pulse_spikes = SpikeGenerator(population=name)
        pulse_spikes.add_spikes(node_ids=[0], timestamps=[time_ms])
        pulse_spikes.to_sonata(str(pulse_file))
        pulse_networks.append((name, pulse_file))
    spikes = SpikeGenerator(population="clock")
    spikes.add_spikes(node_ids=[0] * len(event_times), timestamps=event_times)
    event_file = output / "clock_spikes.h5"
    spikes.to_sonata(str(event_file))

    @bionet.cell_model("digifly:DigiflyPDCMEvent", model_type="point_neuron")
    def _make_cell(_node, _template, dynamics):
        cell = h.DigiflyPDCMEvent()
        cell.Iext = float(dynamics["Iext"])
        cell.m = float(dynamics["m_init"])
        cell.i = 0.0
        return cell

    native = output / "native_output"
    config = {
        "target_simulator": "BioNet",
        "run": {"tstart": 0.0, "tstop": DURATION_MS, "dt": CLOCK_STEP_MS, "nsteps_block": 400},
        "conditions": {"v_init": -65.0, "celsius": 34.0},
        "components": {
            "point_neuron_models_dir": str(components.resolve()),
            "synaptic_models_dir": str(components.resolve()),
            "mechanisms_dir": str(mod_dir.resolve()),
        },
        "networks": {
            "nodes": [
                {"nodes_file": str((network_dir / "cortex_nodes.h5").resolve()),
                 "node_types_file": str((network_dir / "cortex_node_types.csv").resolve())},
                {"nodes_file": str((network_dir / "clock_nodes.h5").resolve()),
                 "node_types_file": str((network_dir / "clock_node_types.csv").resolve())},
            ] + [
                {"nodes_file": str((network_dir / f"{name}_nodes.h5").resolve()),
                 "node_types_file": str((network_dir / f"{name}_node_types.csv").resolve())}
                for name, _file in pulse_networks
            ],
            "edges": [
                {"edges_file": str((network_dir / "clock_cortex_edges.h5").resolve()),
                 "edge_types_file": str((network_dir / "clock_cortex_edge_types.csv").resolve())},
            ] + [
                {"edges_file": str((network_dir / f"{name}_cortex_edges.h5").resolve()),
                 "edge_types_file": str((network_dir / f"{name}_cortex_edge_types.csv").resolve())}
                for name, _file in pulse_networks
            ],
        },
        "node_sets": {"clock_input": {"population": "clock"}} | {
            f"{name}_input": {"population": name} for name, _file in pulse_networks
        },
        "inputs": {"clock_input": {
            "input_type": "spikes", "module": "sonata", "node_set": "clock_input",
            "input_file": str(event_file.resolve()),
        }} | {
            f"{name}_input": {
                "input_type": "spikes", "module": "sonata", "node_set": f"{name}_input",
                "input_file": str(pulse_file.resolve()),
            }
            for name, pulse_file in pulse_networks
        },
        "output": {
            "output_dir": str(native.resolve()),
            "spikes_file": str((native / "spikes.h5").resolve()),
            "spikes_sort_order": "by_time", "overwrite_output_dir": True,
        },
    }
    config_file = output / "config.json"
    _json(config_file, config)
    bionet_config = bionet.Config.from_json(str(config_file))
    bionet_config.build_env()
    network = bionet.BioNetwork.from_config(bionet_config)
    simulator = bionet.BioSimulator.from_config(bionet_config, network=network)
    simulator.run()
    with h5py.File(native / "spikes.h5", "r") as handle:
        return [float(value) for value in handle["spikes/cortex/timestamps"][:]]


def run(output: Path) -> dict:
    output = output.expanduser().resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite {output}")
    output.mkdir(parents=True, exist_ok=False)
    event_times = [index * CLOCK_STEP_MS for index in range(int(DURATION_MS / CLOCK_STEP_MS))]
    oracle = EventCellState(voltage_mv=INITIAL_MV)
    parameters = EventCellParameters(external_current_pa=DC_CURRENT_PA)
    scheduled = [(time_ms + EDGE_DELAY_MS, 0.0) for time_ms in event_times]
    scheduled.extend((time_ms + EDGE_DELAY_MS, weight_pa) for _name, time_ms, weight_pa in PULSES)
    scheduled.sort(key=lambda event: event[0])
    oracle_spikes = [time_ms for time_ms, weight_pa in scheduled if oracle.receive(time_ms, weight_pa, parameters)]
    mod_dir = _mechanisms(output)
    direct = _direct_neuron(mod_dir, event_times)
    bionet_spikes = _bionet(mod_dir, output, event_times)
    error = max((abs(a - b) for a, b in zip(direct, bionet_spikes)), default=0.0)
    oracle_error = max((abs(a - b) for a, b in zip(oracle_spikes, bionet_spikes)), default=0.0)
    passed = (
        bool(direct) and len(direct) == len(bionet_spikes) == len(oracle_spikes)
        and error <= 1e-6 and oracle_error <= 1e-6
    )
    import bmtk
    import neuron

    compiled = list(mod_dir.glob("*/libnrnmech.*"))
    if len(compiled) != 1:
        raise RuntimeError(f"Expected one compiled mechanism library, found {compiled}")

    result = {
        "schema_version": 1,
        "kind": "digifly_independent_point_cell_bionet_tiny",
        "paper_replication": False,
        "network_size": "one_point_cell_one_virtual_clock_two_signed_virtual_inputs",
        "independent_mechanism": True,
        "mod_source_sha256": _hash(MECHANISM),
        "compiled_library_sha256": _hash(compiled[0]),
        "bmtk_version": bmtk.__version__,
        "neuron_version": neuron.__version__,
        "clock_event_count": len(event_times),
        "signed_pulses": [
            {"source": name, "time_ms": time_ms, "weight_pa": weight_pa}
            for name, time_ms, weight_pa in PULSES
        ],
        "direct_spikes_ms": direct,
        "bionet_spikes_ms": bionet_spikes,
        "equation_oracle_spikes_ms": oracle_spikes,
        "max_error_ms": error,
        "max_oracle_error_ms": oracle_error,
        "passed": passed,
    }
    _json(output / "parity.json", result)
    _json(output / "artifact_manifest.json", {
        "schema_version": 1,
        "kind": "digifly_independent_point_cell_bionet_tiny_artifacts",
        "probe_source_sha256": _hash(Path(__file__)),
        "files": {
            str(path.relative_to(output)): _hash(path)
            for path in sorted(output.rglob("*"))
            if path.is_file() and path.name != "artifact_manifest.json"
        },
    })
    if not passed:
        raise RuntimeError(f"Independent BioNet tiny parity failed; inspect {output}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output)))


if __name__ == "__main__":
    main()
