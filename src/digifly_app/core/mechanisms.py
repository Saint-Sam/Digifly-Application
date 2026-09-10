from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping

from .augustin_2019 import PARAMETERS as AUGUSTIN_2019_PARAMETERS
from .augustin_2019 import PROFILE_KEY as AUGUSTIN_2019_PROFILE_KEY


@dataclass(frozen=True)
class MembraneMechanismDefinition:
    """A native Phase 2 density mechanism exposed by the design UI."""

    key: str
    catalog_id: str
    label: str
    short_label: str
    family: str
    ion: str
    suffix: str
    source_relpath: str
    source_sha256: str
    source_default_gbar_s_cm2: float
    editor_step: float
    note: str
    supported_engines: tuple[str, ...] = ("neuron",)


MEMBRANE_MECHANISMS: tuple[MembraneMechanismDefinition, ...] = (
    MembraneMechanismDefinition(
        "augustin_nat",
        "digifly.augustin2019.gf.nat.modeldb245415.v1",
        "Augustin GF transient Na⁺",
        "Augustin · transient Na⁺",
        "sodium",
        "Na+",
        "nat",
        "mechanisms/augustin_2019/nat.mod",
        "609f7786edf939ae7ac44f2e06216a99fc3217fd2ee5fbb449982516da1ab925",
        AUGUSTIN_2019_PARAMETERS["gnatbar_s_cm2"],
        0.001,
        "Exact Augustin-2019/ModelDB-245415 transient-Na equations; upstream source hash is locked separately.",
    ),
    MembraneMechanismDefinition(
        "augustin_nap",
        "digifly.augustin2019.gf.nap.modeldb245415.v1",
        "Augustin GF persistent Na⁺",
        "Augustin · persistent Na⁺",
        "sodium",
        "Na+",
        "nap",
        "mechanisms/augustin_2019/nap.mod",
        "582dfd3fad7505554de5742a2f59c01093124ee4fd183440a4a5e7cfa8e31126",
        AUGUSTIN_2019_PARAMETERS["gnapbar_s_cm2"],
        0.00001,
        "Exact Augustin-2019/ModelDB-245415 persistent-Na equations; upstream source hash is locked separately.",
    ),
    MembraneMechanismDefinition(
        "augustin_k",
        "digifly.augustin2019.gf.k.modeldb245415.v1",
        "Augustin GF K⁺",
        "Augustin · K⁺",
        "potassium",
        "K+",
        "k",
        "mechanisms/augustin_2019/k.mod",
        "73f5d0a0f1baaf575430c9744c6a5e060567831e6b94d1e1add2df6defd30da7",
        AUGUSTIN_2019_PARAMETERS["gkbar_s_cm2"],
        0.001,
        "Exact Augustin-2019/ModelDB-245415 K-channel equations; upstream source hash is locked separately.",
    ),
    MembraneMechanismDefinition(
        "para",
        "digifly.phase2.para.na16a.v1",
        "Para surrogate (Nav1.6 model)",
        "Para · na16a",
        "sodium",
        "Na+",
        "na16a",
        "Phase 2/data/drosophila_channel_surrogates/Nav16_a.mod",
        "fa6bfeba0d840a8871097f9275a2bf14883a02833dce84d031e91455c5e922c5",
        0.1,
        0.001,
        "Phase 2's default para-family surrogate; the public helper uses 0.03 S/cm².",
    ),
    MembraneMechanismDefinition(
        "para_alt",
        "digifly.phase2.para_alt.na14a.v1",
        "Alternate Para surrogate (Nav1.4 model)",
        "Para alt · na14a",
        "sodium",
        "Na+",
        "na14a",
        "Phase 2/data/drosophila_channel_surrogates/Nav14_a.mod",
        "530eb3922de00528b8ff99073f9eb5c2785081ac4e0f19733d82c0f883c76d6a",
        0.1,
        0.001,
        "Alternative sodium surrogate; disabled in the public full-family helper by default.",
    ),
    MembraneMechanismDefinition(
        "shaker",
        "digifly.phase2.shaker.kv14sh.v1",
        "Shaker-family surrogate (Kv1.4)",
        "Shaker · kv14sh",
        "potassium",
        "K+",
        "kv14sh",
        "Phase 2/data/drosophila_channel_surrogates/Kv14_Shaker.mod",
        "c33e0476afbc638e32d38f02931ed4909d4787d05199340dbd3fc45d6e9e698a",
        0.001,
        0.0001,
        "First-pass Shaker-family A-type surrogate.",
    ),
    MembraneMechanismDefinition(
        "shal",
        "digifly.phase2.shal.kv42shal.v1",
        "Shal-family surrogate (Kv4.2)",
        "Shal · kv42shal",
        "potassium",
        "K+",
        "kv42shal",
        "Phase 2/data/drosophila_channel_surrogates/Kv42_Shal.mod",
        "9100f7532effefa4c9b4d46842eabfff4d4c78d2839599784211c9fc7d0a14d0",
        0.001,
        0.0001,
        "First-pass Shal-family A-type surrogate.",
    ),
    MembraneMechanismDefinition(
        "shab",
        "digifly.phase2.shab.kv21shab.v1",
        "Shab-family surrogate (Kv2.1)",
        "Shab · kv21shab",
        "potassium",
        "K+",
        "kv21shab",
        "Phase 2/data/drosophila_channel_surrogates/Kv21_Shab.mod",
        "c70493b3611722548f6a29ff864fdb2562b016bffb180befaa82764d92628c2e",
        0.001,
        0.0001,
        "The optional Para+Shab helper's default potassium surrogate.",
    ),
    MembraneMechanismDefinition(
        "shaw",
        "digifly.phase2.shaw.kv31shaw.v1",
        "Shaw-family surrogate (Kv3.1)",
        "Shaw · kv31shaw",
        "potassium",
        "K+",
        "kv31shaw",
        "Phase 2/data/drosophila_channel_surrogates/Kv31_Shaw.mod",
        "7beacdda766d17fb05b69f3db89a2e68d1e2b4ee091b284e43290f7b160cf6e5",
        0.001,
        0.0001,
        "First-pass Shaw-family fast delayed-rectifier surrogate.",
    ),
    MembraneMechanismDefinition(
        "cacophony",
        "digifly.phase2.cacophony.cav21cac.v1",
        "Cacophony-family surrogate (Cav2.1)",
        "Cacophony · cav21cac",
        "calcium",
        "Ca2+",
        "cav21cac",
        "Phase 2/data/drosophila_channel_surrogates/Cav21_cac.mod",
        "3ba1367cb1ea0c83f1f086656352fe74c6e0c4d609919473c81aa5fa535baca5",
        0.0001,
        0.00001,
        "First-pass cacophony/Cav2-family surrogate; disabled in public helpers by default.",
    ),
    MembraneMechanismDefinition(
        "ca_alpha1t",
        "digifly.phase2.ca_alpha1t.cav31t.v1",
        "Ca-alpha1T-family surrogate (Cav3.1)",
        "Ca-alpha1T · cav31t",
        "calcium",
        "Ca2+",
        "cav31t",
        "Phase 2/data/drosophila_channel_surrogates/Cav31_alpha1T.mod",
        "24bae2629d65f3671ae8681acbd9245fcee5c664b75ce4882525b88f13013dd7",
        0.0001,
        0.00001,
        "First-pass Ca-alpha1T/Cav3-family surrogate; disabled in public helpers by default.",
    ),
)

MEMBRANE_MECHANISM_BY_KEY = {item.key: item for item in MEMBRANE_MECHANISMS}


@dataclass
class ChannelAssignment:
    mechanism_key: str
    suffix: str
    catalog_id: str = ""
    source_relpath: str = ""
    source_sha256: str = ""
    enabled: bool = False
    soma_gbar_s_cm2: float = 0.0
    branch_gbar_s_cm2: float = 0.0
    parameters: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.mechanism_key = str(self.mechanism_key).strip()
        self.suffix = str(self.suffix).strip()
        if not self.mechanism_key or not self.suffix:
            raise ValueError("A membrane-channel assignment needs a mechanism key and suffix.")
        definition = MEMBRANE_MECHANISM_BY_KEY.get(self.mechanism_key)
        if definition is not None:
            expected = {
                "suffix": definition.suffix,
                "catalog_id": definition.catalog_id,
                "source_relpath": definition.source_relpath,
                "source_sha256": definition.source_sha256,
            }
            for field_name, expected_value in expected.items():
                value = str(getattr(self, field_name) or expected_value)
                if value != expected_value:
                    raise ValueError(
                        f"Known mechanism {self.mechanism_key!r} has conflicting {field_name}: "
                        f"expected {expected_value!r}, got {value!r}."
                    )
                setattr(self, field_name, value)
        else:
            self.catalog_id = str(self.catalog_id or self.mechanism_key)
            self.source_relpath = str(self.source_relpath or "")
            self.source_sha256 = str(self.source_sha256 or "")
        if self.source_sha256 and (
            len(self.source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.source_sha256)
        ):
            raise ValueError(f"{self.suffix} source SHA-256 must be 64 lowercase hex characters.")
        self.enabled = bool(self.enabled)
        self.soma_gbar_s_cm2 = float(self.soma_gbar_s_cm2)
        self.branch_gbar_s_cm2 = float(self.branch_gbar_s_cm2)
        for value, region in (
            (self.soma_gbar_s_cm2, "soma"),
            (self.branch_gbar_s_cm2, "branch"),
        ):
            if not 0.0 <= value <= 10.0:
                raise ValueError(f"{self.suffix} {region} gbar must be between 0 and 10 S/cm².")
        self.parameters = {str(key): float(value) for key, value in self.parameters.items()}
        if any(not math.isfinite(value) for value in self.parameters.values()):
            raise ValueError(f"{self.suffix} parameters must be finite numbers.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(
        cls,
        payload: Mapping[str, Any] | None,
        *,
        fallback_key: str = "",
    ) -> "ChannelAssignment":
        raw = dict(payload or {})
        key = str(raw.get("mechanism_key") or fallback_key)
        definition = MEMBRANE_MECHANISM_BY_KEY.get(key)
        suffix = str(raw.get("suffix") or (definition.suffix if definition else key))
        default_gbar = definition.source_default_gbar_s_cm2 if definition else 0.0
        return cls(
            mechanism_key=key,
            suffix=suffix,
            catalog_id=str(raw.get("catalog_id") or (definition.catalog_id if definition else key)),
            source_relpath=str(
                raw.get("source_relpath") or (definition.source_relpath if definition else "")
            ),
            source_sha256=str(
                raw.get("source_sha256") or (definition.source_sha256 if definition else "")
            ),
            enabled=bool(raw.get("enabled", False)),
            soma_gbar_s_cm2=float(raw.get("soma_gbar_s_cm2", raw.get("gbar_s_cm2", default_gbar))),
            branch_gbar_s_cm2=float(raw.get("branch_gbar_s_cm2", raw.get("gbar_s_cm2", default_gbar))),
            parameters=dict(raw.get("parameters") or {}),
        )


def _blank_assignments() -> dict[str, ChannelAssignment]:
    return {
        definition.key: ChannelAssignment(
            mechanism_key=definition.key,
            suffix=definition.suffix,
            enabled=False,
            soma_gbar_s_cm2=definition.source_default_gbar_s_cm2,
            branch_gbar_s_cm2=definition.source_default_gbar_s_cm2,
        )
        for definition in MEMBRANE_MECHANISMS
    }


@dataclass
class MembraneMechanismSpec:
    """Mechanism identity and regional densities, separate from generic HH scalars."""

    profile_key: str = "classic_hh"
    replace_builtin_hh: bool = False
    channels: dict[str, ChannelAssignment] = field(default_factory=_blank_assignments)

    def __post_init__(self) -> None:
        self.profile_key = str(self.profile_key or "custom")
        self.replace_builtin_hh = bool(self.replace_builtin_hh)
        normalized: dict[str, ChannelAssignment] = {}
        for key, value in dict(self.channels or {}).items():
            normalized_key = str(key)
            assignment = (
                value
                if isinstance(value, ChannelAssignment)
                else ChannelAssignment.from_dict(value, fallback_key=normalized_key)
            )
            if assignment.mechanism_key != normalized_key:
                raise ValueError(
                    f"Channel map key {normalized_key!r} conflicts with assignment key "
                    f"{assignment.mechanism_key!r}."
                )
            normalized[normalized_key] = assignment
        for key, assignment in _blank_assignments().items():
            normalized.setdefault(key, assignment)
        self.channels = normalized

    @property
    def active_channels(self) -> tuple[ChannelAssignment, ...]:
        return tuple(
            assignment
            for assignment in self.channels.values()
            if assignment.enabled
            and (assignment.soma_gbar_s_cm2 > 0.0 or assignment.branch_gbar_s_cm2 > 0.0)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_key": self.profile_key,
            "replace_builtin_hh": self.replace_builtin_hh,
            "channels": {key: value.to_dict() for key, value in self.channels.items()},
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "MembraneMechanismSpec":
        raw = dict(payload or {})
        channels = {
            str(key): ChannelAssignment.from_dict(value, fallback_key=str(key))
            for key, value in dict(raw.get("channels") or {}).items()
        }
        return cls(
            profile_key=str(raw.get("profile_key") or "classic_hh"),
            replace_builtin_hh=bool(raw.get("replace_builtin_hh", False)),
            channels=channels or _blank_assignments(),
        )


@dataclass(frozen=True)
class MembraneProfileDefinition:
    key: str
    label: str
    summary: str
    replace_builtin_hh: bool
    enabled_gbars: Mapping[str, tuple[float, float]]
    hh_updates: Mapping[str, Any]


_PORTABLE_CLASSIC_HH: Mapping[str, Any] = {
    "ra_ohm_cm": 100.0,
    "cm_uF_cm2": 1.0,
    "g_pas_s_cm2": 1e-4,
    "e_pas_mV": -65.0,
    "ena_mV": 65.0,
    "ek_mV": -74.0,
    "eca_mV": 120.0,
    "v_init_mV": -65.0,
    "celsius_C": 22.0,
    "soma_gnabar_s_cm2": 0.12,
    "soma_gkbar_s_cm2": 0.036,
    "soma_gl_s_cm2": 3e-4,
    "soma_el_mV": -65.0,
    "branch_gnabar_s_cm2": 0.02,
    "branch_gkbar_s_cm2": 0.01,
    "branch_gl_s_cm2": 1e-4,
    "branch_el_mV": -65.0,
    "active_scope": "all",
}

_AUGUSTIN_2019_GF_HH: Mapping[str, Any] = {
    **_PORTABLE_CLASSIC_HH,
    "ra_ohm_cm": AUGUSTIN_2019_PARAMETERS["ra_ohm_cm"],
    "cm_uF_cm2": AUGUSTIN_2019_PARAMETERS["cm_uF_cm2"],
    "g_pas_s_cm2": AUGUSTIN_2019_PARAMETERS["g_pas_s_cm2"],
    "e_pas_mV": AUGUSTIN_2019_PARAMETERS["e_pas_mV"],
    "ena_mV": AUGUSTIN_2019_PARAMETERS["ena_mV"],
    "ek_mV": AUGUSTIN_2019_PARAMETERS["ek_mV"],
    "v_init_mV": AUGUSTIN_2019_PARAMETERS["v_init_mV"],
    "celsius_C": AUGUSTIN_2019_PARAMETERS["celsius_C"],
    # Built-in squid HH is removed. The three exact ModelDB mechanisms below
    # carry transient Na, persistent Na, and K conductances atomically.
    "soma_gnabar_s_cm2": 0.0,
    "soma_gkbar_s_cm2": 0.0,
    "soma_gl_s_cm2": AUGUSTIN_2019_PARAMETERS["g_pas_s_cm2"],
    "soma_el_mV": AUGUSTIN_2019_PARAMETERS["e_pas_mV"],
    "branch_gnabar_s_cm2": 0.0,
    "branch_gkbar_s_cm2": 0.0,
    "branch_gl_s_cm2": AUGUSTIN_2019_PARAMETERS["g_pas_s_cm2"],
    "branch_el_mV": AUGUSTIN_2019_PARAMETERS["e_pas_mV"],
    "active_scope": "all",
}

_PHASE2_NATIVE_CHANNEL_HH: Mapping[str, Any] = {
    **_PORTABLE_CLASSIC_HH,
    # The native helpers leave these unset, so NEURON's runtime defaults apply.
    "ena_mV": 50.0,
    "ek_mV": -77.0,
    "celsius_C": 6.3,
    "soma_gnabar_s_cm2": 0.0,
    "soma_gkbar_s_cm2": 0.0,
    "soma_gl_s_cm2": 1e-4,
    "branch_gnabar_s_cm2": 0.0,
    "branch_gkbar_s_cm2": 0.0,
    "branch_gl_s_cm2": 1e-4,
}

_ESCAPE_SIZ_PARA_HH_K: Mapping[str, Any] = {
    **_PORTABLE_CLASSIC_HH,
    # Voltage Sink does not override NEURON's ion/temperature defaults.
    "ena_mV": 50.0,
    "ek_mV": -77.0,
    "celsius_C": 6.3,
    "soma_gnabar_s_cm2": 0.0,
    "soma_gkbar_s_cm2": 0.036,
    "soma_gl_s_cm2": 3e-4,
    "branch_gnabar_s_cm2": 0.0,
    "branch_gkbar_s_cm2": 0.01,
    "branch_gl_s_cm2": 1e-4,
}


MEMBRANE_PROFILES: tuple[MembraneProfileDefinition, ...] = (
    MembraneProfileDefinition(
        AUGUSTIN_2019_PROFILE_KEY,
        "Augustin 2019 GF (exact)",
        "GF-specific published membrane: ELeak −85 mV, ENa +65 mV, EK −74 mV, transient Na 0.3, persistent Na 0.00011, and K 0.01 S/cm² at 25 °C. It initializes at −65 mV but predicts a zero-current equilibrium of −74.670 mV; initialization is not rest.",
        True,
        {
            "augustin_nat": (
                AUGUSTIN_2019_PARAMETERS["gnatbar_s_cm2"],
                AUGUSTIN_2019_PARAMETERS["gnatbar_s_cm2"],
            ),
            "augustin_nap": (
                AUGUSTIN_2019_PARAMETERS["gnapbar_s_cm2"],
                AUGUSTIN_2019_PARAMETERS["gnapbar_s_cm2"],
            ),
            "augustin_k": (
                AUGUSTIN_2019_PARAMETERS["gkbar_s_cm2"],
                AUGUSTIN_2019_PARAMETERS["gkbar_s_cm2"],
            ),
        },
        _AUGUSTIN_2019_GF_HH,
    ),
    MembraneProfileDefinition(
        "classic_hh",
        "Classic HH (portable draft)",
        "No native Phase 2 density mechanisms; keeps built-in HH Na/K.",
        False,
        {},
        _PORTABLE_CLASSIC_HH,
    ),
    MembraneProfileDefinition(
        "phase2_para_shab",
        "Phase 2 Para + Shab",
        "Public Phase 2 helper defaults: na16a 0.03, kv21shab 0.01, and passive leak 0.0001 S/cm²; replaces built-in HH and uses native NEURON ion/temperature defaults.",
        True,
        {"para": (0.03, 0.03), "shab": (0.01, 0.01)},
        _PHASE2_NATIVE_CHANNEL_HH,
    ),
    MembraneProfileDefinition(
        "phase2_full_family",
        "Phase 2 channel-family set",
        "Para plus Shaker, Shal, Shab, and Shaw with passive leak 0.0001 S/cm²; Ca mechanisms remain available but off, matching the public helper defaults.",
        True,
        {
            "para": (0.03, 0.03),
            "shaker": (0.001, 0.001),
            "shal": (0.001, 0.001),
            "shab": (0.001, 0.001),
            "shaw": (0.001, 0.001),
        },
        _PHASE2_NATIVE_CHANNEL_HH,
    ),
    MembraneProfileDefinition(
        "escape_siz_para_hh_k",
        "Escape-SIZ Para + HH K",
        "Voltage Sink defaults: na16a at 0.03 soma / 0.005 branch, built-in HH K retained, HH Na zero, and the native NEURON defaults 50/-77 mV and 6.3 °C.",
        False,
        {"para": (0.03, 0.005)},
        _ESCAPE_SIZ_PARA_HH_K,
    ),
)

MEMBRANE_PROFILE_BY_KEY = {item.key: item for item in MEMBRANE_PROFILES}


def membrane_profile(key: str) -> MembraneMechanismSpec:
    """Return the mechanism half of a named profile.

    Call ``cell_design_profile`` from ``core.circuit`` when both the HH values and
    native mechanisms are needed as one atomic design.
    """

    definition = MEMBRANE_PROFILE_BY_KEY.get(str(key))
    if definition is None:
        raise KeyError(f"Unknown membrane-mechanism profile: {key}")
    assignments = _blank_assignments()
    for mechanism_key, (soma_gbar, branch_gbar) in definition.enabled_gbars.items():
        assignment = assignments[mechanism_key]
        assignment.enabled = True
        assignment.soma_gbar_s_cm2 = float(soma_gbar)
        assignment.branch_gbar_s_cm2 = float(branch_gbar)
    return MembraneMechanismSpec(
        profile_key=definition.key,
        replace_builtin_hh=definition.replace_builtin_hh,
        channels=assignments,
    )


GAP_MECHANISM_PROVENANCE: Mapping[str, tuple[str, str, str, str]] = {
    "none": ("none", "", "", ""),
    "ohmic": (
        "digifly.phase2.gap.ohmic.v1",
        "Gap",
        "Phase 2/data/Gap.mod",
        "e3ab9d0a37811314d3baa8461050e6d65fbb9b8e163ff6618a6d9f2f2549c1f2",
    ),
    "rectifying": (
        "digifly.phase2.gap.rectifying.v1",
        "RectGap",
        "Phase 2/data/RectGap.mod",
        "d9fa308ff0433ad0eb424017fff4384d5c06915ca9a2cbe25485ad176db3a8d6",
    ),
    "heterotypic_rectifying": (
        "digifly.phase2.gap.heterotypic_kinetic_residual.v1",
        "HeteroRectGap",
        "Phase 2/data/HeteroRectGap.mod",
        "22b505af799076bb8079f204d222adfa72432f8609bd89fd050f7ffb325c9c2f",
    ),
}


@dataclass
class ChemicalSynapsePolicy:
    """Simulation parameters applied to imported anatomical chemical contacts.

    Connectome exports define the participating cells and contact location, but
    they do not consistently carry electrophysiological kinetics.  Keeping
    these values in the circuit document makes that modeling choice explicit,
    editable, and reproducible instead of presenting it as connectome data.
    """

    mechanism: str = "exp2syn"
    source_mode: str = "soma_threshold"
    placement_policy: str = "imported_post_contact"
    weight_policy: str = "source_or_default_per_contact"
    default_weight_uS: float = 0.000003
    weight_scale: float = 1.0
    default_delay_ms: float = 1.0
    use_geometric_delay: bool = False
    base_release_delay_ms: float = 0.4
    conduction_velocity_um_per_ms: float = 1500.0
    tau1_ms: float = 0.5
    tau2_ms: float = 3.0
    reversal_mV: float = 0.0
    spike_threshold_mV: float = 0.0
    aggregation_policy: str = "per_site_unchanged"
    edge_scope: str = "all_chemical_edges"

    def __post_init__(self) -> None:
        if self.mechanism != "exp2syn":
            raise ValueError("The generic chemical lane currently supports exp2syn only.")
        if self.source_mode != "soma_threshold":
            raise ValueError("The generic chemical lane currently uses soma-threshold sources.")
        if self.placement_policy != "imported_post_contact":
            raise ValueError("Chemical synapses require imported postsynaptic contact placement.")
        if self.weight_policy != "source_or_default_per_contact":
            raise ValueError("Unsupported chemical-synapse weight policy.")
        if self.aggregation_policy != "per_site_unchanged":
            raise ValueError("Chemical contacts must remain unaggregated in the generic lane.")
        if self.edge_scope != "all_chemical_edges":
            raise ValueError(f"Unsupported chemical edge scope: {self.edge_scope}")
        self.use_geometric_delay = bool(self.use_geometric_delay)
        for key in (
            "default_weight_uS",
            "weight_scale",
            "default_delay_ms",
            "base_release_delay_ms",
            "conduction_velocity_um_per_ms",
            "tau1_ms",
            "tau2_ms",
            "reversal_mV",
            "spike_threshold_mV",
        ):
            value = float(getattr(self, key))
            if not math.isfinite(value):
                raise ValueError(f"Chemical-synapse {key} must be finite.")
            setattr(self, key, value)
        if self.default_weight_uS < 0.0 or self.weight_scale < 0.0:
            raise ValueError("Chemical-synapse weights and scaling cannot be negative.")
        if self.default_delay_ms <= 0.0 or self.base_release_delay_ms < 0.0:
            raise ValueError("Chemical-synapse delays must be positive.")
        if self.conduction_velocity_um_per_ms <= 0.0:
            raise ValueError("Chemical conduction velocity must be positive.")
        if self.tau1_ms <= 0.0 or self.tau2_ms <= self.tau1_ms:
            raise ValueError("exp2syn requires 0 < tau1 < tau2.")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "ChemicalSynapsePolicy":
        raw = dict(payload or {})
        allowed = cls().__dict__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})


@dataclass
class GapJunctionPolicy:
    """A default for electrical *edges*; it is never attached to a cell alone."""

    mode: str = "none"
    catalog_id: str = ""
    mechanism_name: str = ""
    source_relpath: str = ""
    source_sha256: str = ""
    g_uS: float = 0.001
    preferred_direction: str = "a_to_b"
    g_closed_frac: float = 0.0
    vhalf_mV: float = 0.0
    vslope_mV: float = 5.0
    empirical_residual_frac: float = 0.20
    tau_open_ms: float = 6.0
    tau_close_ms: float = 2.0
    placement_policy: str = "imported_contact_sites"
    conductance_basis: str = "per_site"
    aggregation_policy: str = "per_site_unchanged"
    endpoint_a_role: str = "unspecified"
    endpoint_b_role: str = "unspecified"
    edge_scope: str = "all_electrical_edges"

    def __post_init__(self) -> None:
        if self.mode not in {"none", "ohmic", "rectifying", "heterotypic_rectifying"}:
            raise ValueError(f"Unsupported gap-junction mode: {self.mode}")
        expected_catalog, expected_name, expected_path, expected_sha256 = (
            GAP_MECHANISM_PROVENANCE[self.mode]
        )
        for field_name, expected_value in (
            ("catalog_id", expected_catalog),
            ("mechanism_name", expected_name),
            ("source_relpath", expected_path),
            ("source_sha256", expected_sha256),
        ):
            value = str(getattr(self, field_name) or expected_value)
            if value != expected_value:
                raise ValueError(
                    f"Gap mode {self.mode!r} has conflicting {field_name}: "
                    f"expected {expected_value!r}, got {value!r}."
                )
            setattr(self, field_name, value)
        if self.source_sha256 and (
            len(self.source_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.source_sha256)
        ):
            raise ValueError("Gap-junction source SHA-256 must be 64 lowercase hex characters.")
        if self.preferred_direction not in {"a_to_b", "b_to_a"}:
            raise ValueError(f"Unsupported preferred gap direction: {self.preferred_direction}")
        if self.placement_policy not in {"imported_contact_sites", "ais_pair", "soma_pair"}:
            raise ValueError(f"Unsupported gap-junction placement policy: {self.placement_policy}")
        if self.conductance_basis not in {"per_site", "pair_total"}:
            raise ValueError(f"Unsupported gap-junction conductance basis: {self.conductance_basis}")
        expected_aggregation = (
            "per_site_unchanged"
            if self.conductance_basis == "per_site"
            else "equal_split_across_selected_sites"
        )
        if self.aggregation_policy != expected_aggregation:
            raise ValueError(
                f"Conductance basis {self.conductance_basis!r} requires aggregation policy "
                f"{expected_aggregation!r}."
            )
        self.endpoint_a_role = str(self.endpoint_a_role or "unspecified").strip()
        self.endpoint_b_role = str(self.endpoint_b_role or "unspecified").strip()
        self.g_uS = float(self.g_uS)
        self.g_closed_frac = float(self.g_closed_frac)
        self.vhalf_mV = float(self.vhalf_mV)
        self.vslope_mV = float(self.vslope_mV)
        self.empirical_residual_frac = float(self.empirical_residual_frac)
        self.tau_open_ms = float(self.tau_open_ms)
        self.tau_close_ms = float(self.tau_close_ms)
        numeric_values = (
            self.g_uS,
            self.g_closed_frac,
            self.vhalf_mV,
            self.vslope_mV,
            self.empirical_residual_frac,
            self.tau_open_ms,
            self.tau_close_ms,
        )
        if any(not math.isfinite(value) for value in numeric_values):
            raise ValueError("Gap-junction parameters must be finite numbers.")
        if not 0.0 <= self.g_uS <= 1000.0:
            raise ValueError("Gap conductance must be between 0 and 1000 µS.")
        if not 0.0 <= self.g_closed_frac <= 1.0:
            raise ValueError("Closed gap fraction must be between 0 and 1.")
        if not 0.0 <= self.empirical_residual_frac <= 1.0:
            raise ValueError("Empirical residual fraction must be between 0 and 1.")
        if self.vslope_mV <= 0.0:
            raise ValueError("Gap-junction voltage slope must be positive.")
        if self.tau_open_ms <= 0.0 or self.tau_close_ms <= 0.0:
            raise ValueError("Gap-junction time constants must be positive.")
        if self.edge_scope != "all_electrical_edges":
            raise ValueError(f"Unsupported gap-junction edge scope: {self.edge_scope}")

    @property
    def mechanism_label(self) -> str:
        return {
            "none": "None",
            "ohmic": "Gap / digifly_gap",
            "rectifying": "RectGap",
            "heterotypic_rectifying": "HeteroRectGap",
        }[self.mode]

    @property
    def effective_closed_floor(self) -> float:
        if self.mode != "heterotypic_rectifying":
            return self.g_closed_frac
        return max(self.g_closed_frac, self.empirical_residual_frac)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "GapJunctionPolicy":
        raw = dict(payload or {})
        if "aggregation_policy" not in raw:
            raw["aggregation_policy"] = (
                "equal_split_across_selected_sites"
                if raw.get("conductance_basis") == "pair_total"
                else "per_site_unchanged"
            )
        allowed = cls().__dict__.keys()
        return cls(**{key: raw[key] for key in allowed if key in raw})


def mechanism_capability_message(
    engine: str,
    membrane: MembraneMechanismSpec,
    gap_policy: GapJunctionPolicy,
) -> str:
    """Describe preservation and execution readiness for the selected adapter."""

    engine = str(engine or "arbor")
    active = membrane.active_channels
    statements: list[str] = []
    if active:
        suffixes = ", ".join(item.suffix for item in active)
        if engine == "neuron":
            statements.append(
                f"Native Phase 2 NMODL identity is preserved ({suffixes}), but the "
                "generic NEURON lane currently accepts classic HH only; execution "
                "fails closed instead of substituting another channel."
            )
        elif engine == "arbor":
            statements.append(f"Preserved but not Arbor-qualified: {suffixes}. The staged custom catalogue is not linked/validated, so execution must be blocked rather than approximated.")
        else:
            statements.append(
                f"Preserved as design intent ({suffixes}); the bounded BMTK BioNet "
                "lane currently accepts classic HH only."
            )
    elif engine == "arbor" and not membrane.replace_builtin_hh:
        statements.append("Built-in classic HH is within the qualified mechanism subset of the curated Arbor staging scenarios.")
    elif engine == "neuron" and not membrane.replace_builtin_hh:
        statements.append(
            "Built-in classic HH is executable in the generic morphology-backed "
            "NEURON lane."
        )
    elif engine == "bmtk" and not membrane.replace_builtin_hh:
        statements.append(
            "Built-in classic HH is executable through the bounded morphology-backed "
            "BMTK BioNet/SONATA lane."
        )
    else:
        statements.append("Built-in HH is explicitly removed; with no enabled native channel this design is passive-only.")

    if gap_policy.mode == "ohmic":
        if engine == "arbor":
            statements.append(
                "The app-owned digifly_gap equation port is built and validated in a real "
                "two-cell Arbor test. The generic two-cell lane now materializes validated "
                "imported contact rows into a versioned run-owned edge manifest; conductance is "
                f"applied as {gap_policy.conductance_basis.replace('_', ' ')}."
            )
        elif engine == "neuron":
            statements.append(
                "The generic NEURON lane executes validated ohmic contacts with the "
                "exact app-owned Gap mechanism."
            )
        else:
            statements.append(
                "The ohmic edge policy is preserved, but BMTK BioNet electrical-edge "
                "execution is intentionally blocked."
            )
    elif gap_policy.mode in {"rectifying", "heterotypic_rectifying"}:
        if engine == "neuron":
            statements.append(
                f"The generic NEURON lane executes validated contacts with the exact "
                f"app-owned {gap_policy.mechanism_label} mechanism."
            )
        elif engine == "arbor":
            arbor_port = (
                "digifly_rect_gap"
                if gap_policy.mode == "rectifying"
                else "digifly_hetero_rect_gap"
            )
            statements.append(
                f"The app-owned {arbor_port} equation port for {gap_policy.mechanism_label} "
                "is built and validated in a real two-cell Arbor test; the dedicated "
                "Escape-SIZ adapter uses digifly_hetero_rect_gap for its locked recipe. "
                "BLOCKED for generic Arbor execution until the Circuit Builder can translate "
                "electrical endpoints and contact placement; no static ohmic approximation "
                "will be substituted."
            )
        else:
            statements.append(
                f"{gap_policy.mechanism_label} is preserved, but BMTK BioNet "
                "electrical-edge execution is intentionally blocked."
            )
    else:
        statements.append("No mass-applied electrical-edge mechanism is selected.")
    return "  ".join(statements)
