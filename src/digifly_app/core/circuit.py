from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping

from .mechanisms import (
    MEMBRANE_PROFILE_BY_KEY,
    GapJunctionPolicy,
    MembraneMechanismSpec,
    membrane_profile,
)


CIRCUIT_SCHEMA_VERSION = 2
LEGACY_CIRCUIT_SCHEMA_VERSIONS = {1}


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
    HHParameterDefinition("eca_mV", "Ca²⁺ reversal", "mV", -200.0, 300.0, 1.0, 3),
    HHParameterDefinition("v_init_mV", "Initial voltage", "mV", -200.0, 200.0, 1.0, 3),
    HHParameterDefinition("celsius_C", "Temperature", "°C", -10.0, 60.0, 0.5, 2),
    HHParameterDefinition("soma_gnabar_s_cm2", "Built-in HH soma Na⁺ ḡ", "S/cm²", 0.0, 10.0, 0.005, 7),
    HHParameterDefinition("soma_gkbar_s_cm2", "Built-in HH soma K⁺ ḡ", "S/cm²", 0.0, 10.0, 0.002, 7),
    HHParameterDefinition("soma_gl_s_cm2", "Built-in HH soma leak g", "S/cm²", 0.0, 10.0, 0.00001, 8),
    HHParameterDefinition("soma_el_mV", "Built-in HH soma leak reversal", "mV", -200.0, 200.0, 1.0, 3),
    HHParameterDefinition("branch_gnabar_s_cm2", "Built-in HH branch Na⁺ ḡ", "S/cm²", 0.0, 10.0, 0.002, 7),
    HHParameterDefinition("branch_gkbar_s_cm2", "Built-in HH branch K⁺ ḡ", "S/cm²", 0.0, 10.0, 0.001, 7),
    HHParameterDefinition("branch_gl_s_cm2", "Built-in HH branch leak g", "S/cm²", 0.0, 10.0, 0.00001, 8),
    HHParameterDefinition("branch_el_mV", "Built-in HH branch leak reversal", "mV", -200.0, 200.0, 1.0, 3),
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
    eca_mV: float = 120.0
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


def cell_design_profile(key: str) -> tuple[HodgkinHuxleySpec, MembraneMechanismSpec]:
    """Build both halves of a named HH/native-mechanism profile atomically."""

    definition = MEMBRANE_PROFILE_BY_KEY.get(str(key))
    if definition is None:
        raise KeyError(f"Unknown cell-design profile: {key}")
    return HodgkinHuxleySpec.from_dict(definition.hh_updates), membrane_profile(key)


def _profile_matches_design(
    hh_payload: Mapping[str, Any],
    membrane: MembraneMechanismSpec,
) -> bool:
    definition = MEMBRANE_PROFILE_BY_KEY.get(membrane.profile_key)
    if definition is None:
        return membrane.profile_key == "custom"
    normalized_hh = HodgkinHuxleySpec.from_dict(hh_payload).to_dict()
    for key, expected in definition.hh_updates.items():
        actual = normalized_hh.get(key)
        if isinstance(expected, (float, int)):
            if abs(float(actual) - float(expected)) > 1e-12:
                return False
        elif actual != expected:
            return False
    canonical_membrane = membrane_profile(definition.key)
    return membrane.to_dict() == canonical_membrane.to_dict()


def _membrane_for_hh(
    hh_payload: Mapping[str, Any],
    membrane_payload: Mapping[str, Any] | MembraneMechanismSpec,
) -> MembraneMechanismSpec:
    membrane = (
        membrane_payload
        if isinstance(membrane_payload, MembraneMechanismSpec)
        else MembraneMechanismSpec.from_dict(membrane_payload)
    )
    if not _profile_matches_design(hh_payload, membrane):
        membrane.profile_key = "custom"
    return membrane

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


@dataclass(frozen=True)
class ConnectionPairOverride:
    """User intent for connection classes between an unordered neuron pair.

    ``None`` means to inherit the imported connectivity state. A boolean is an
    explicit plan override. The source edge table remains read-only; execution
    adapters consume this record while materializing their app-owned plans.
    """

    neuron_a: str
    neuron_b: str
    chemical_enabled: bool | None = None
    gap_junction_enabled: bool | None = None

    def __post_init__(self) -> None:
        first, second = sorted((str(self.neuron_a), str(self.neuron_b)))
        if not first or not second or first == second:
            raise ValueError("A connection override requires two different neuron IDs")
        object.__setattr__(self, "neuron_a", first)
        object.__setattr__(self, "neuron_b", second)
        for key in ("chemical_enabled", "gap_junction_enabled"):
            value = getattr(self, key)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{key} must be true, false, or null")

    @property
    def key(self) -> str:
        # Length-prefixing avoids ambiguous keys even if future non-numeric IDs
        # contain punctuation.
        return f"{len(self.neuron_a)}:{self.neuron_a}{self.neuron_b}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ConnectionPairOverride":
        raw = dict(payload or {})
        return cls(
            neuron_a=str(raw.get("neuron_a") or ""),
            neuron_b=str(raw.get("neuron_b") or ""),
            chemical_enabled=raw.get("chemical_enabled"),
            gap_junction_enabled=raw.get("gap_junction_enabled"),
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
    membrane: MembraneMechanismSpec = field(default_factory=MembraneMechanismSpec)
    gap_junction_policy: GapJunctionPolicy = field(default_factory=GapJunctionPolicy)
    morphology_sha256: dict[str, str] = field(default_factory=dict)
    neuron_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    compartment_overrides: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    neuron_mechanism_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    compartment_mechanism_overrides: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    connection_overrides: dict[str, ConnectionPairOverride] = field(default_factory=dict)
    schema_version: int = CIRCUIT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        normalized_connection_overrides: dict[str, ConnectionPairOverride] = {}
        for value in self.connection_overrides.values():
            override = (
                value
                if isinstance(value, ConnectionPairOverride)
                else ConnectionPairOverride.from_dict(value)
            )
            normalized_connection_overrides[override.key] = override
        self.connection_overrides = normalized_connection_overrides
        self._synchronize_profile_identities()

    def _synchronize_profile_identities(self) -> None:
        base_hh = self.hh.to_dict()
        self.membrane = _membrane_for_hh(base_hh, self.membrane)
        for neuron_id, membrane_payload in tuple(self.neuron_mechanism_overrides.items()):
            hh_payload = self.neuron_overrides.get(neuron_id, base_hh)
            self.neuron_mechanism_overrides[neuron_id] = _membrane_for_hh(
                hh_payload, membrane_payload
            ).to_dict()
        for neuron_id, node_map in tuple(self.compartment_mechanism_overrides.items()):
            neuron_hh = self.neuron_overrides.get(neuron_id, base_hh)
            node_hh_map = self.compartment_overrides.get(neuron_id, {})
            for node_id, membrane_payload in tuple(node_map.items()):
                hh_payload = node_hh_map.get(node_id, neuron_hh)
                node_map[node_id] = _membrane_for_hh(
                    hh_payload, membrane_payload
                ).to_dict()

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

    def apply_neuron_mechanism_override(
        self,
        neuron_id: str | int,
        value: MembraneMechanismSpec | Mapping[str, Any],
    ) -> None:
        normalized = (
            value if isinstance(value, MembraneMechanismSpec) else MembraneMechanismSpec.from_dict(value)
        )
        self.neuron_mechanism_overrides[str(neuron_id)] = normalized.to_dict()

    def apply_compartment_mechanism_override(
        self,
        neuron_id: str | int,
        node_ids: Iterable[str | int],
        value: MembraneMechanismSpec | Mapping[str, Any],
    ) -> None:
        normalized = (
            value if isinstance(value, MembraneMechanismSpec) else MembraneMechanismSpec.from_dict(value)
        ).to_dict()
        bucket = self.compartment_mechanism_overrides.setdefault(str(neuron_id), {})
        for node_id in node_ids:
            bucket[str(node_id)] = deepcopy(normalized)

    def mass_apply_cell_design(
        self,
        neuron_ids: Iterable[str | int],
        hh: HodgkinHuxleySpec | Mapping[str, Any],
        membrane: MembraneMechanismSpec | Mapping[str, Any],
    ) -> int:
        hh_payload = (
            hh if isinstance(hh, HodgkinHuxleySpec) else HodgkinHuxleySpec.from_dict(hh)
        ).to_dict()
        membrane_payload = (
            membrane
            if isinstance(membrane, MembraneMechanismSpec)
            else MembraneMechanismSpec.from_dict(membrane)
        ).to_dict()
        normalized_ids = tuple(dict.fromkeys(str(neuron_id) for neuron_id in neuron_ids))
        for neuron_id in normalized_ids:
            self.neuron_overrides[neuron_id] = dict(hh_payload)
            self.neuron_mechanism_overrides[neuron_id] = deepcopy(membrane_payload)
        return len(normalized_ids)

    def connection_override(
        self, neuron_a: str | int, neuron_b: str | int
    ) -> ConnectionPairOverride | None:
        probe = ConnectionPairOverride(str(neuron_a), str(neuron_b))
        return self.connection_overrides.get(probe.key)

    def set_connection_class_enabled(
        self,
        neuron_a: str | int,
        neuron_b: str | int,
        connection_class: str,
        enabled: bool,
    ) -> ConnectionPairOverride:
        probe = ConnectionPairOverride(str(neuron_a), str(neuron_b))
        current = self.connection_overrides.get(probe.key, probe)
        if connection_class == "chemical":
            updated = ConnectionPairOverride(
                current.neuron_a,
                current.neuron_b,
                chemical_enabled=bool(enabled),
                gap_junction_enabled=current.gap_junction_enabled,
            )
        elif connection_class == "gap_junction":
            updated = ConnectionPairOverride(
                current.neuron_a,
                current.neuron_b,
                chemical_enabled=current.chemical_enabled,
                gap_junction_enabled=bool(enabled),
            )
        else:
            raise ValueError(f"Unsupported connection class: {connection_class}")
        self.connection_overrides[updated.key] = updated
        return updated

    def clear_connection_override(
        self, neuron_a: str | int, neuron_b: str | int
    ) -> bool:
        probe = ConnectionPairOverride(str(neuron_a), str(neuron_b))
        return self.connection_overrides.pop(probe.key, None) is not None

    def to_dict(self) -> dict[str, Any]:
        self._synchronize_profile_identities()
        return {
            "schema_version": CIRCUIT_SCHEMA_VERSION,
            "connectome": self.connectome.to_dict(),
            "query": self.query.to_dict(),
            "neuron_ids": list(self.neuron_ids),
            "hh": self.hh.to_dict(),
            "membrane": self.membrane.to_dict(),
            "gap_junction_policy": self.gap_junction_policy.to_dict(),
            "morphology_sha256": self.morphology_sha256,
            "neuron_overrides": self.neuron_overrides,
            "compartment_overrides": self.compartment_overrides,
            "neuron_mechanism_overrides": self.neuron_mechanism_overrides,
            "compartment_mechanism_overrides": self.compartment_mechanism_overrides,
            "connection_overrides": [
                override.to_dict()
                for override in sorted(
                    self.connection_overrides.values(),
                    key=lambda item: (item.neuron_a, item.neuron_b),
                )
            ],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "CircuitSpec":
        raw = dict(payload or {})
        version = int(raw.get("schema_version", 1))
        if version != CIRCUIT_SCHEMA_VERSION and version not in LEGACY_CIRCUIT_SCHEMA_VERSIONS:
            raise ValueError(
                f"Unsupported circuit schema {version}; expected one of "
                f"{sorted((*LEGACY_CIRCUIT_SCHEMA_VERSIONS, CIRCUIT_SCHEMA_VERSION))}."
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
        neuron_mechanism_overrides = {
            key: MembraneMechanismSpec.from_dict(value).to_dict()
            for key, value in _string_keyed_nested(raw.get("neuron_mechanism_overrides")).items()
        }
        compartment_mechanism_overrides: dict[str, dict[str, dict[str, Any]]] = {}
        for neuron_id, node_map in _string_keyed_nested(
            raw.get("compartment_mechanism_overrides")
        ).items():
            compartment_mechanism_overrides[neuron_id] = {
                str(node_id): MembraneMechanismSpec.from_dict(values).to_dict()
                for node_id, values in dict(node_map or {}).items()
            }
        connection_overrides: dict[str, ConnectionPairOverride] = {}
        raw_connection_overrides = raw.get("connection_overrides") or ()
        values = (
            raw_connection_overrides.values()
            if isinstance(raw_connection_overrides, Mapping)
            else raw_connection_overrides
        )
        for value in values:
            override = ConnectionPairOverride.from_dict(value)
            connection_overrides[override.key] = override
        restored = cls(
            connectome=ConnectomeRef.from_dict(raw.get("connectome")),
            query=NeuronQuery.from_dict(raw.get("query")),
            neuron_ids=tuple(str(value) for value in raw.get("neuron_ids") or ()),
            hh=HodgkinHuxleySpec.from_dict(raw.get("hh")),
            membrane=MembraneMechanismSpec.from_dict(raw.get("membrane")),
            gap_junction_policy=GapJunctionPolicy.from_dict(raw.get("gap_junction_policy")),
            morphology_sha256={
                str(neuron_id): str(digest)
                for neuron_id, digest in dict(raw.get("morphology_sha256") or {}).items()
            },
            neuron_overrides=neuron_overrides,
            compartment_overrides=compartment_overrides,
            neuron_mechanism_overrides=neuron_mechanism_overrides,
            compartment_mechanism_overrides=compartment_mechanism_overrides,
            connection_overrides=connection_overrides,
            schema_version=CIRCUIT_SCHEMA_VERSION,
        )
        restored._synchronize_profile_identities()
        return restored
