"""Small, declarative NetPyNE model format for the first Workbench increment.

This is deliberately separate from CircuitSpec and the pinned PDCM replication
workflow. Version 1 supports synthetic, single-soma HH cells only. External
datasets and morphologies can be recorded as references, but are not imported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, field
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping


NETPYNE_WORKBENCH_SCHEMA_VERSION = 1
MODEL_FAMILY = "synthetic_single_soma_hh"
_LABEL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


def _number(value: Any, *, minimum: float | None = None, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(value):
        return False
    return (not positive or value > 0) and (minimum is None or value >= minimum)


def _integer(value: Any, *, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _label_errors(label: Any, location: str) -> list[str]:
    if not isinstance(label, str) or not _LABEL.fullmatch(label):
        return [f"{location} must start with a letter and use only letters, digits, _, -, or ."]
    return []


def _known_label(label: Any, known: set[str]) -> bool:
    return isinstance(label, str) and label in known


@dataclass(frozen=True)
class CellRuleSpec:
    label: str
    diameter_um: float = 18.8
    length_um: float = 18.8
    axial_resistance_ohm_cm: float = 123.0
    capacitance_uF_cm2: float = 1.0
    gnabar_S_cm2: float = 0.12
    gkbar_S_cm2: float = 0.036
    gl_S_cm2: float = 0.003
    el_mV: float = -70.0


@dataclass(frozen=True)
class PopulationSpec:
    label: str
    cell_rule: str
    count: int


@dataclass(frozen=True)
class SynapseSpec:
    label: str
    role: str  # excitatory or inhibitory; the reversal potential determines actual effect
    tau1_ms: float
    tau2_ms: float
    reversal_mV: float


@dataclass(frozen=True)
class ConnectionSpec:
    label: str
    pre_population: str
    post_population: str
    synapse: str
    weight_uS: float
    delay_ms: float
    probability: float | None = None
    convergence: int | None = None
    divergence: int | None = None


@dataclass(frozen=True)
class NetStimSpec:
    label: str
    target_population: str
    synapse: str
    rate_hz: float
    noise: float
    start_ms: float
    weight_uS: float
    delay_ms: float
    number: int = 1_000_000_000


@dataclass(frozen=True)
class ExternalResourceRef:
    """A provenance reference, never a bundled or silently imported dataset."""

    label: str
    kind: str  # swc, dataset, mod, or other
    uri: str
    usage: str = "provenance"  # model_input is stored but not exportable in v1
    sha256: str | None = None


@dataclass(frozen=True)
class NetPyNEExperimentSpec:
    duration_ms: float = 50.0
    dt_ms: float = 0.025
    record_step_ms: float = 0.1
    initial_voltage_mV: float = -65.0
    spike_threshold_mV: float = -20.0
    temperature_C: float = 34.0
    connection_seed: int = 1
    stimulus_seed: int = 1
    location_seed: int = 1
    cell_seed: int = 1
    record_spikes: bool = True
    record_voltage: bool = True
    voltage_populations: tuple[str, ...] = ()  # empty means all populations


def _read_record(record_type: type, payload: Any, location: str) -> Any:
    if not isinstance(payload, Mapping):
        raise ValueError(f"{location} must be an object")
    allowed = {part.name for part in fields(record_type)}
    extra = set(payload) - allowed
    if extra:
        raise ValueError(f"{location} has unknown fields: {', '.join(sorted(map(str, extra)))}")
    values = dict(payload)
    if record_type is NetPyNEExperimentSpec and "voltage_populations" in values:
        labels = values["voltage_populations"]
        if not isinstance(labels, list):
            raise ValueError(f"{location}.voltage_populations must be an array")
        values["voltage_populations"] = tuple(labels)
    try:
        return record_type(**values)
    except TypeError as exc:
        raise ValueError(f"Invalid {location}: {exc}") from exc


def _read_records(record_type: type, payload: Mapping[str, Any], key: str) -> tuple:
    items = payload.get(key)
    if not isinstance(items, list):
        raise ValueError(f"{key} must be an array")
    return tuple(_read_record(record_type, item, f"{key}[{index}]") for index, item in enumerate(items))


@dataclass(frozen=True)
class NetPyNEWorkbenchSpec:
    name: str
    cell_rules: tuple[CellRuleSpec, ...]
    populations: tuple[PopulationSpec, ...]
    synapses: tuple[SynapseSpec, ...]
    connections: tuple[ConnectionSpec, ...]
    stimuli: tuple[NetStimSpec, ...]
    experiment: NetPyNEExperimentSpec = field(default_factory=NetPyNEExperimentSpec)
    external_resources: tuple[ExternalResourceRef, ...] = ()
    schema_version: int = NETPYNE_WORKBENCH_SCHEMA_VERSION
    model_family: str = MODEL_FAMILY

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ("cell_rules", "populations", "synapses", "connections", "stimuli", "external_resources"):
            payload[key] = list(payload[key])
        payload["experiment"]["voltage_populations"] = list(payload["experiment"]["voltage_populations"])
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "NetPyNEWorkbenchSpec":
        if not isinstance(payload, Mapping):
            raise ValueError("NetPyNE workbench document must be an object")
        allowed = {part.name for part in fields(cls)}
        extra = set(payload) - allowed
        if extra:
            raise ValueError(f"NetPyNE workbench document has unknown fields: {', '.join(sorted(map(str, extra)))}")
        version = payload.get("schema_version")
        if type(version) is not int or version != NETPYNE_WORKBENCH_SCHEMA_VERSION:
            raise ValueError(f"Unsupported NetPyNE workbench schema {version!r}; expected version 1")
        if payload.get("model_family") != MODEL_FAMILY:
            raise ValueError(f"Unsupported NetPyNE model family {payload.get('model_family')!r}")
        if "experiment" not in payload:
            raise ValueError("experiment is required")
        resources = dict(payload)
        resources.setdefault("external_resources", [])
        try:
            model = cls(
                name=payload["name"],
                cell_rules=_read_records(CellRuleSpec, payload, "cell_rules"),
                populations=_read_records(PopulationSpec, payload, "populations"),
                synapses=_read_records(SynapseSpec, payload, "synapses"),
                connections=_read_records(ConnectionSpec, payload, "connections"),
                stimuli=_read_records(NetStimSpec, payload, "stimuli"),
                experiment=_read_record(NetPyNEExperimentSpec, payload["experiment"], "experiment"),
                external_resources=_read_records(ExternalResourceRef, resources, "external_resources"),
                schema_version=version,
                model_family=MODEL_FAMILY,
            )
        except KeyError as exc:
            raise ValueError(f"Missing NetPyNE workbench field: {exc.args[0]}") from exc
        model.require_valid()
        return model

    def errors(self) -> list[str]:
        errors: list[str] = []
        if (
            type(self.schema_version) is not int
            or self.schema_version != NETPYNE_WORKBENCH_SCHEMA_VERSION
            or self.model_family != MODEL_FAMILY
        ):
            errors.append("Only the version-1 synthetic single-soma HH model family is supported")
        if not isinstance(self.name, str) or not self.name.strip():
            errors.append("Model name is required")
        for key, values, required in (
            ("cell rule", self.cell_rules, True),
            ("population", self.populations, True),
            ("synapse", self.synapses, bool(self.connections or self.stimuli)),
            ("connection", self.connections, False),
            ("stimulus", self.stimuli, False),
            ("external resource", self.external_resources, False),
        ):
            if required and not values:
                errors.append(f"At least one {key} is required")
            labels = [getattr(value, "label", None) for value in values]
            for label in labels:
                errors.extend(_label_errors(label, key))
            if len(labels) != len({label.casefold() for label in labels if isinstance(label, str)}):
                errors.append(f"{key.title()} labels must be unique (ignoring case)")

        cell_labels = {item.label for item in self.cell_rules if isinstance(item.label, str)}
        population_labels = {item.label for item in self.populations if isinstance(item.label, str)}
        synapse_labels = {item.label for item in self.synapses if isinstance(item.label, str)}
        for cell in self.cell_rules:
            for key in (
                "diameter_um", "length_um", "axial_resistance_ohm_cm", "capacitance_uF_cm2", "gl_S_cm2"
            ):
                if not _number(getattr(cell, key), positive=True):
                    errors.append(f"Cell rule {cell.label!r}: {key} must be finite and positive")
            for key in ("gnabar_S_cm2", "gkbar_S_cm2"):
                if not _number(getattr(cell, key), minimum=0):
                    errors.append(f"Cell rule {cell.label!r}: {key} must be finite and nonnegative")
            if not _number(cell.el_mV):
                errors.append(f"Cell rule {cell.label!r}: el_mV must be finite")
        for population in self.populations:
            errors.extend(_label_errors(population.cell_rule, f"Population {population.label!r} cell rule"))
            if not _known_label(population.cell_rule, cell_labels):
                errors.append(f"Population {population.label!r} references unknown cell rule {population.cell_rule!r}")
            if not _integer(population.count, minimum=1):
                errors.append(f"Population {population.label!r} count must be a positive integer")
        experiment = self.experiment
        for key in ("duration_ms", "dt_ms", "record_step_ms"):
            if not _number(getattr(experiment, key), positive=True):
                errors.append(f"Experiment {key} must be finite and positive")
        if _number(experiment.dt_ms, positive=True) and _number(experiment.record_step_ms, positive=True):
            if experiment.record_step_ms < experiment.dt_ms:
                errors.append("Recording step cannot be smaller than the integration step")
        for key in ("initial_voltage_mV", "spike_threshold_mV", "temperature_C"):
            if not _number(getattr(experiment, key)):
                errors.append(f"Experiment {key} must be finite")
        for key in ("connection_seed", "stimulus_seed", "location_seed", "cell_seed"):
            if not _integer(getattr(experiment, key)):
                errors.append(f"Experiment {key} must be a nonnegative integer")
        if type(experiment.record_spikes) is not bool or type(experiment.record_voltage) is not bool:
            errors.append("Recording switches must be booleans")
        elif not (experiment.record_spikes or experiment.record_voltage):
            errors.append("Enable spike or voltage recording")
        if not isinstance(experiment.voltage_populations, tuple):
            errors.append("Voltage populations must be an array of population labels")
        else:
            for label in experiment.voltage_populations:
                if not _known_label(label, population_labels):
                    errors.append(f"Voltage recording references unknown population {label!r}")
        for synapse in self.synapses:
            if not isinstance(synapse.role, str) or synapse.role not in {"excitatory", "inhibitory"}:
                errors.append(f"Synapse {synapse.label!r} role must be excitatory or inhibitory")
            if not _number(synapse.tau1_ms, positive=True) or not _number(synapse.tau2_ms, positive=True):
                errors.append(f"Synapse {synapse.label!r} time constants must be finite and positive")
            elif synapse.tau1_ms >= synapse.tau2_ms:
                errors.append(f"Synapse {synapse.label!r} requires tau1_ms < tau2_ms")
            if not _number(synapse.reversal_mV):
                errors.append(f"Synapse {synapse.label!r} reversal potential must be finite")
            elif _number(experiment.initial_voltage_mV):
                if synapse.role == "excitatory" and synapse.reversal_mV <= experiment.initial_voltage_mV:
                    errors.append(f"Synapse {synapse.label!r} excitatory reversal must exceed initial voltage")
                if synapse.role == "inhibitory" and synapse.reversal_mV >= experiment.initial_voltage_mV:
                    errors.append(f"Synapse {synapse.label!r} inhibitory reversal must be below initial voltage")
        for connection in self.connections:
            for key in ("pre_population", "post_population"):
                if not _known_label(getattr(connection, key), population_labels):
                    errors.append(
                        f"Connection {connection.label!r} references unknown {key} "
                        f"{getattr(connection, key)!r}"
                    )
            if not _known_label(connection.synapse, synapse_labels):
                errors.append(f"Connection {connection.label!r} references unknown synapse {connection.synapse!r}")
            choices = [connection.probability, connection.convergence, connection.divergence]
            if sum(choice is not None for choice in choices) != 1:
                errors.append(
                    f"Connection {connection.label!r} needs exactly one of "
                    "probability, convergence, or divergence"
                )
            if connection.probability is not None and (
                not _number(connection.probability, minimum=0) or connection.probability > 1
            ):
                errors.append(f"Connection {connection.label!r} probability must be between 0 and 1")
            for key in ("convergence", "divergence"):
                value = getattr(connection, key)
                if value is not None and not _integer(value, minimum=1):
                    errors.append(f"Connection {connection.label!r} {key} must be a positive integer")
            if not _number(connection.weight_uS, positive=True) or not _number(connection.delay_ms, positive=True):
                errors.append(f"Connection {connection.label!r} weight and delay must be finite and positive")
        for stimulus in self.stimuli:
            if not _known_label(stimulus.target_population, population_labels):
                errors.append(
                    f"Stimulus {stimulus.label!r} references unknown population "
                    f"{stimulus.target_population!r}"
                )
            if not _known_label(stimulus.synapse, synapse_labels):
                errors.append(f"Stimulus {stimulus.label!r} references unknown synapse {stimulus.synapse!r}")
            for key in ("rate_hz", "weight_uS", "delay_ms"):
                if not _number(getattr(stimulus, key), positive=True):
                    errors.append(f"Stimulus {stimulus.label!r} {key} must be finite and positive")
            if not _number(stimulus.noise, minimum=0) or stimulus.noise > 1:
                errors.append(f"Stimulus {stimulus.label!r} noise must be between 0 and 1")
            if not _number(stimulus.start_ms, minimum=0):
                errors.append(f"Stimulus {stimulus.label!r} start must be finite and nonnegative")
            if not _integer(stimulus.number, minimum=1):
                errors.append(f"Stimulus {stimulus.label!r} number must be a positive integer")
        for resource in self.external_resources:
            if not isinstance(resource.kind, str) or resource.kind not in {"swc", "dataset", "mod", "other"}:
                errors.append(f"External resource {resource.label!r} has unsupported kind {resource.kind!r}")
            if not isinstance(resource.usage, str) or resource.usage not in {"provenance", "model_input"}:
                errors.append(f"External resource {resource.label!r} has unsupported usage {resource.usage!r}")
            if not isinstance(resource.uri, str) or not resource.uri.strip():
                errors.append(f"External resource {resource.label!r} needs a URI or path")
            if resource.sha256 is not None and (
                not isinstance(resource.sha256, str) or not _SHA256.fullmatch(resource.sha256)
            ):
                errors.append(f"External resource {resource.label!r} sha256 must contain 64 hex digits")
        return errors

    def require_valid(self) -> None:
        errors = self.errors()
        if errors:
            raise ValueError("Invalid NetPyNE workbench model:\n- " + "\n- ".join(errors))

    def require_exportable(self) -> None:
        self.require_valid()
        unsupported = [item.label for item in self.external_resources if item.usage == "model_input"]
        if unsupported:
            raise ValueError(
                "NetPyNE Workbench v1 cannot import external morphology, MOD, or dataset inputs: "
                + ", ".join(unsupported)
            )


def save_workbench_spec(path: str | Path, model: NetPyNEWorkbenchSpec) -> Path:
    """Atomically save a validated, stable JSON document."""
    model.require_valid()
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(model.to_dict(), indent=2, sort_keys=True, allow_nan=False) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent,
            prefix=f".{destination.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination


def load_workbench_spec(path: str | Path) -> NetPyNEWorkbenchSpec:
    return NetPyNEWorkbenchSpec.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def to_netpyne_payload(model: NetPyNEWorkbenchSpec) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return deterministic NetPyNE-compatible network and simulation dictionaries."""
    model.require_exportable()
    network: dict[str, Any] = {
        "cellParams": {}, "popParams": {}, "synMechParams": {},
        "connParams": {}, "stimSourceParams": {}, "stimTargetParams": {},
        "defaultThreshold": model.experiment.spike_threshold_mV,
    }
    for cell in sorted(model.cell_rules, key=lambda item: item.label):
        network["cellParams"][cell.label] = {
            "conds": {"cellType": cell.label},
            "secs": {"soma": {
                "geom": {
                    "diam": cell.diameter_um, "L": cell.length_um,
                    "Ra": cell.axial_resistance_ohm_cm, "cm": cell.capacitance_uF_cm2,
                },
                "mechs": {"hh": {
                    "gnabar": cell.gnabar_S_cm2, "gkbar": cell.gkbar_S_cm2,
                    "gl": cell.gl_S_cm2, "el": cell.el_mV,
                }},
            }},
        }
    for population in sorted(model.populations, key=lambda item: item.label):
        network["popParams"][population.label] = {
            "cellType": population.cell_rule, "numCells": population.count,
        }
    for synapse in sorted(model.synapses, key=lambda item: item.label):
        network["synMechParams"][synapse.label] = {
            "mod": "Exp2Syn", "tau1": synapse.tau1_ms,
            "tau2": synapse.tau2_ms, "e": synapse.reversal_mV,
        }
    for connection in sorted(model.connections, key=lambda item: item.label):
        rule: dict[str, Any] = {
            "preConds": {"pop": connection.pre_population},
            "postConds": {"pop": connection.post_population},
            "synMech": connection.synapse, "weight": connection.weight_uS,
            "delay": connection.delay_ms, "sec": "soma", "loc": 0.5,
        }
        for choice in ("probability", "convergence", "divergence"):
            value = getattr(connection, choice)
            if value is not None:
                rule[choice] = value
        network["connParams"][connection.label] = rule
    for stimulus in sorted(model.stimuli, key=lambda item: item.label):
        network["stimSourceParams"][stimulus.label] = {
            "type": "NetStim", "rate": stimulus.rate_hz,
            "noise": stimulus.noise, "start": stimulus.start_ms,
            "number": stimulus.number,
        }
        network["stimTargetParams"][f"{stimulus.label}__target"] = {
            "source": stimulus.label,
            "conds": {"pop": stimulus.target_population},
            "synMech": stimulus.synapse,
            "weight": stimulus.weight_uS,
            "delay": stimulus.delay_ms,
            "sec": "soma", "loc": 0.5,
        }
    cfg = model.experiment
    simulation: dict[str, Any] = {
        "duration": cfg.duration_ms, "dt": cfg.dt_ms,
        "hParams": {"celsius": cfg.temperature_C, "v_init": cfg.initial_voltage_mV},
        "seeds": {"conn": cfg.connection_seed, "stim": cfg.stimulus_seed,
                  "loc": cfg.location_seed, "cell": cfg.cell_seed},
        "recordStep": cfg.record_step_ms,
        "recordCellsSpikes": -1 if cfg.record_spikes else [],
        "recordCells": (
            list(cfg.voltage_populations) if cfg.voltage_populations else ["all"]
        ) if cfg.record_voltage else [],
        "recordTraces": (
            {"V_soma": {"sec": "soma", "loc": 0.5, "var": "v"}}
            if cfg.record_voltage else {}
        ),
        "saveJson": False,
        "savePickle": False,
        "analysis": {},
        "verbose": False,
    }
    return network, simulation


def to_netpyne_specs(
    model: NetPyNEWorkbenchSpec, *, output_dir: str | Path | None = None
) -> tuple[Any, Any]:
    """Build actual NetPyNE NetParams/SimConfig objects, importing NetPyNE lazily."""
    network, simulation = to_netpyne_payload(model)
    from netpyne import specs

    params = specs.NetParams()
    for key, rules in network.items():
        if isinstance(rules, dict):
            getattr(params, key).update(rules)
        else:
            setattr(params, key, rules)
    cfg = specs.SimConfig()
    for key, value in simulation.items():
        setattr(cfg, key, value)
    if output_dir is not None:
        cfg.saveFolder = str(Path(output_dir))
        cfg.filename = "digifly_netpyne"
        cfg.saveJson = True
    return params, cfg


def synthetic_two_population_example() -> NetPyNEWorkbenchSpec:
    """Small E/I smoke-test model; not equivalent to the PDCM paper."""
    return NetPyNEWorkbenchSpec(
        name="Synthetic E/I example (not PDCM)",
        cell_rules=(CellRuleSpec("E"), CellRuleSpec("I")),
        populations=(PopulationSpec("E", "E", 4), PopulationSpec("I", "I", 2)),
        synapses=(
            SynapseSpec("exc", "excitatory", 0.1, 5.0, 0.0),
            SynapseSpec("inh", "inhibitory", 0.1, 8.0, -80.0),
        ),
        connections=(
            ConnectionSpec("E_to_I", "E", "I", "exc", 0.02, 2.0, probability=0.5),
            ConnectionSpec("I_to_E", "I", "E", "inh", 0.02, 2.0, probability=0.5),
        ),
        stimuli=(NetStimSpec("drive_E", "E", "exc", 100.0, 0.0, 5.0, 0.5, 1.0),),
        experiment=NetPyNEExperimentSpec(duration_ms=100.0),
    )
