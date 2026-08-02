from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping


CIRCUIT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class HHParameterDefinition:
    key: str
    label: str
    unit: str
    minimum: float
    maximum: float
    step: float
    decimals: int


HH_PARAMETER_DEFINITIONS: tuple[HHParameterDefinition, ...] = (
    HHParameterDefinition("ra_ohm_cm", "Axial resistance", "Ω·cm", 0.01, 10000.0, 1.0, 3),
    HHParameterDefinition("cm_uF_cm2", "Membrane capacitance", "µF/cm²", 0.0001, 100.0, 0.05, 5),
    HHParameterDefinition("g_pas_s_cm2", "Passive conductance", "S/cm²", 0.0, 10.0, 0.00001, 8),
    HHParameterDefinition("e_pas_mV", "Passive reversal", "mV", -200.0, 200.0, 1.0, 3),
    HHParameterDefinition("ena_mV", "Na⁺ reversal", "mV", -200.0, 200.0, 1.0, 3),
    HHParameterDefinition("ek_mV", "K⁺ reversal", "mV", -200.0, 200.0, 1.0, 3),
    HHParameterDefinition("v_init_mV", "Initial voltage", "mV", -200.0, 200.0, 1.0, 3),
    HHParameterDefinition("celsius_C", "Temperature", "°C", -10.0, 60.0, 0.5, 2),
    HHParameterDefinition("soma_gnabar_s_cm2", "Soma Na⁺ ḡ", "S/cm²", 0.0, 10.0, 0.005, 7),
    HHParameterDefinition("soma_gkbar_s_cm2", "Soma K⁺ ḡ", "S/cm²", 0.0, 10.0, 0.002, 7),
    HHParameterDefinition("soma_gl_s_cm2", "Soma leak g", "S/cm²", 0.0, 10.0, 0.00001, 8),
    HHParameterDefinition("soma_el_mV", "Soma leak reversal", "mV", -200.0, 200.0, 1.0, 3),
    HHParameterDefinition("branch_gnabar_s_cm2", "Branch Na⁺ ḡ", "S/cm²", 0.0, 10.0, 0.002, 7),
    HHParameterDefinition("branch_gkbar_s_cm2", "Branch K⁺ ḡ", "S/cm²", 0.0, 10.0, 0.001, 7),
    HHParameterDefinition("branch_gl_s_cm2", "Branch leak g", "S/cm²", 0.0, 10.0, 0.00001, 8),
    HHParameterDefinition("branch_el_mV", "Branch leak reversal", "mV", -200.0, 200.0, 1.0, 3),
)


@dataclass
class HodgkinHuxleySpec:
    """Backend-unbound cable-HH draft; adapters may translate or reject fields."""

    ra_ohm_cm: float = 100.0
    cm_uF_cm2: float = 1.0
    g_pas_s_cm2: float = 1e-4
    e_pas_mV: float = -65.0
    ena_mV: float = 65.0
    ek_mV: float = -74.0
    v_init_mV: float = -65.0
    celsius_C: float = 22.0
    soma_gnabar_s_cm2: float = 0.12
    soma_gkbar_s_cm2: float = 0.036
    soma_gl_s_cm2: float = 3e-4
    soma_el_mV: float = -65.0
    branch_gnabar_s_cm2: float = 0.02
    branch_gkbar_s_cm2: float = 0.01
    branch_gl_s_cm2: float = 1e-4
    branch_el_mV: float = -65.0
    active_scope: str = "all"

    def __post_init__(self) -> None:
        for definition in HH_PARAMETER_DEFINITIONS:
            value = float(getattr(self, definition.key))
            if not definition.minimum <= value <= definition.maximum:
                raise ValueError(
                    f"{definition.label} must be between {definition.minimum:g} and {definition.maximum:g} {definition.unit}."
                )
            setattr(self, definition.key, value)
        if self.ra_ohm_cm <= 0.0:
            raise ValueError("Axial resistance must be positive.")
        if self.cm_uF_cm2 <= 0.0:
            raise ValueError("Membrane capacitance must be positive.")
        if self.active_scope not in {"all", "soma_ais", "soma"}:
            raise ValueError(f"Unsupported active channel scope: {self.active_scope}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "HodgkinHuxleySpec":
        raw = dict(payload or {})
        allowed = cls().__dict__.keys()
        values = {key: raw[key] for key in allowed if key in raw}
        return cls(**values)

@dataclass(frozen=True)
class ConnectomeRef:
    key: str
    label: str
    root: str
    dataset: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "ConnectomeRef":
        raw = dict(payload or {})
        return cls(
            key=str(raw.get("key") or ""),
            label=str(raw.get("label") or raw.get("key") or "Unselected"),
            root=str(raw.get("root") or ""),
            dataset=str(raw.get("dataset") or ""),
        )


@dataclass(frozen=True)
class NeuronQuery:
    expression: str = ""
    max_neurons: int = 64

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "NeuronQuery":
        raw = dict(payload or {})
        return cls(
            expression=str(raw.get("expression") or ""),
            max_neurons=max(1, int(raw.get("max_neurons") or 64)),
        )


def _string_keyed_nested(payload: Mapping[Any, Any] | None) -> dict[str, Any]:
    return {str(key): value for key, value in dict(payload or {}).items()}


@dataclass
class CircuitSpec:
    """A morphology-backed cell-set design that does not select its execution engine."""

    connectome: ConnectomeRef = field(default_factory=lambda: ConnectomeRef("", "Unselected", ""))
    query: NeuronQuery = field(default_factory=NeuronQuery)
    neuron_ids: tuple[str, ...] = field(default_factory=tuple)
    hh: HodgkinHuxleySpec = field(default_factory=HodgkinHuxleySpec)
    morphology_sha256: dict[str, str] = field(default_factory=dict)
    neuron_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    compartment_overrides: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    schema_version: int = CIRCUIT_SCHEMA_VERSION

    def apply_neuron_override(self, neuron_id: str | int, values: Mapping[str, Any]) -> None:
        self.neuron_overrides[str(neuron_id)] = dict(values)

    def apply_compartment_override(
        self,
        neuron_id: str | int,
        node_ids: Iterable[str | int],
        values: Mapping[str, Any],
    ) -> None:
        bucket = self.compartment_overrides.setdefault(str(neuron_id), {})
        for node_id in node_ids:
            bucket[str(node_id)] = dict(values)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "connectome": self.connectome.to_dict(),
            "query": self.query.to_dict(),
            "neuron_ids": list(self.neuron_ids),
            "hh": self.hh.to_dict(),
            "morphology_sha256": self.morphology_sha256,
            "neuron_overrides": self.neuron_overrides,
            "compartment_overrides": self.compartment_overrides,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "CircuitSpec":
        raw = dict(payload or {})
        version = int(raw.get("schema_version", CIRCUIT_SCHEMA_VERSION))
        if version != CIRCUIT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported circuit schema {version}; expected {CIRCUIT_SCHEMA_VERSION}."
            )
        neuron_overrides = {
            key: dict(value or {})
            for key, value in _string_keyed_nested(raw.get("neuron_overrides")).items()
        }
        compartment_overrides: dict[str, dict[str, dict[str, Any]]] = {}
        for neuron_id, node_map in _string_keyed_nested(raw.get("compartment_overrides")).items():
            compartment_overrides[neuron_id] = {
                str(node_id): dict(values or {})
                for node_id, values in dict(node_map or {}).items()
            }
        return cls(
            connectome=ConnectomeRef.from_dict(raw.get("connectome")),
            query=NeuronQuery.from_dict(raw.get("query")),
            neuron_ids=tuple(str(value) for value in raw.get("neuron_ids") or ()),
            hh=HodgkinHuxleySpec.from_dict(raw.get("hh")),
            morphology_sha256={
                str(neuron_id): str(digest)
                for neuron_id, digest in dict(raw.get("morphology_sha256") or {}).items()
            },
            neuron_overrides=neuron_overrides,
            compartment_overrides=compartment_overrides,
            schema_version=version,
        )
