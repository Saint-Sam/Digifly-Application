from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .circuit import CircuitSpec


EXPERIMENT_SCHEMA_VERSION = 1
EXPERIMENT_BUILDER_WORKFLOW = "experiment_builder_v1"


def _ids(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = values.replace(",", " ").split()
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


@dataclass(frozen=True)
class StimulusSpec:
    """One app-owned stimulus protocol, independent of a simulator notebook."""

    name: str = "Primary pulse train"
    enabled: bool = True
    target_neuron_ids: tuple[str, ...] = ()
    target_region: str = "soma"
    waveform: str = "pulse_train"
    amplitude_nA: float = 0.9
    delay_ms: float = 5.0
    pulse_width_ms: float = 0.4
    frequency_hz: float = 100.0
    pulse_count: int = 10

    @property
    def final_offset_ms(self) -> float:
        if self.pulse_count <= 1:
            return self.delay_ms + self.pulse_width_ms
        return (
            self.delay_ms
            + (self.pulse_count - 1) * (1000.0 / self.frequency_hz)
            + self.pulse_width_ms
        )

    def errors(self) -> list[str]:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("Every stimulus needs a name.")
        if self.target_region not in {"soma", "ais", "selected_compartments", "all"}:
            errors.append(f"Unsupported stimulus target region {self.target_region!r}.")
        if self.waveform not in {"pulse_train", "step", "ramp"}:
            errors.append(f"Unsupported stimulus waveform {self.waveform!r}.")
        if self.amplitude_nA < 0:
            errors.append("Stimulus amplitude cannot be negative.")
        if self.delay_ms < 0:
            errors.append("Stimulus delay cannot be negative.")
        if self.pulse_width_ms <= 0:
            errors.append("Stimulus duration must be positive.")
        if self.frequency_hz <= 0:
            errors.append("Stimulus frequency must be positive.")
        if self.pulse_count < 1:
            errors.append("Stimulus pulse count must be at least one.")
        return errors

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StimulusSpec":
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in payload.items() if key in fields}
        values["target_neuron_ids"] = _ids(values.get("target_neuron_ids"))
        return cls(**values)


@dataclass(frozen=True)
class ConditionSpec:
    """Runtime manipulation applied to the same immutable circuit design."""

    name: str = "Control"
    enabled: bool = True
    gap_junctions_enabled: bool = True
    chemical_synapses_enabled: bool = True
    disabled_neuron_ids: tuple[str, ...] = ()
    stimulus_scale: float = 1.0
    mechanism_scales: dict[str, float] = field(default_factory=dict)

    def errors(self) -> list[str]:
        errors: list[str] = []
        if not self.name.strip():
            errors.append("Every condition needs a name.")
        if self.stimulus_scale < 0:
            errors.append(f"Condition {self.name!r} has a negative stimulus scale.")
        for mechanism, scale in self.mechanism_scales.items():
            if not str(mechanism).strip() or float(scale) < 0:
                errors.append(f"Condition {self.name!r} has an invalid mechanism scale.")
        return errors

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ConditionSpec":
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in payload.items() if key in fields}
        values["disabled_neuron_ids"] = _ids(values.get("disabled_neuron_ids"))
        raw_scales = values.get("mechanism_scales") or {}
        values["mechanism_scales"] = {
            str(key): float(value) for key, value in dict(raw_scales).items()
        }
        return cls(**values)


@dataclass(frozen=True)
class RecordingSpec:
    target_neuron_ids: tuple[str, ...] = ()
    target_region: str = "all"
    record_voltage: bool = True
    detect_spikes: bool = True
    sample_dt_ms: float = 0.05
    spike_threshold_mV: float = -20.0
    make_plots: bool = True

    def errors(self) -> list[str]:
        errors: list[str] = []
        if self.target_region not in {"all", "soma", "ais", "selected_compartments"}:
            errors.append(f"Unsupported recording region {self.target_region!r}.")
        if not self.record_voltage and not self.detect_spikes:
            errors.append("Enable at least one recording output.")
        if self.sample_dt_ms <= 0:
            errors.append("Recording sample interval must be positive.")
        return errors

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "RecordingSpec":
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in payload.items() if key in fields}
        values["target_neuron_ids"] = _ids(values.get("target_neuron_ids"))
        return cls(**values)


def _default_stimuli() -> tuple[StimulusSpec, ...]:
    return (StimulusSpec(),)


def _default_conditions() -> tuple[ConditionSpec, ...]:
    return (
        ConditionSpec(name="Control"),
        ConditionSpec(name="Gap junctions disabled", gap_junctions_enabled=False),
    )


@dataclass(frozen=True)
class ExperimentSpec:
    """Simulator-neutral controls for running an already-built CircuitSpec."""

    schema_version: int = EXPERIMENT_SCHEMA_VERSION
    name: str = "Untitled experiment"
    template_key: str = "blank"
    engine: str = "arbor"
    duration_ms: float = 110.0
    integration_dt_ms: float = 0.01
    initial_voltage_mV: float = -65.0
    temperature_C: float = 6.3
    random_seed: int = 1
    repetitions: int = 1
    workers: int = 1
    stimuli: tuple[StimulusSpec, ...] = field(default_factory=_default_stimuli)
    conditions: tuple[ConditionSpec, ...] = field(default_factory=_default_conditions)
    recording: RecordingSpec = field(default_factory=RecordingSpec)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ExperimentSpec":
        version = int(payload.get("schema_version", EXPERIMENT_SCHEMA_VERSION))
        if version != EXPERIMENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported experiment schema {version}; expected {EXPERIMENT_SCHEMA_VERSION}."
            )
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in payload.items() if key in fields}
        values["schema_version"] = version
        values["stimuli"] = tuple(
            StimulusSpec.from_dict(item) for item in payload.get("stimuli", ())
        ) or _default_stimuli()
        values["conditions"] = tuple(
            ConditionSpec.from_dict(item) for item in payload.get("conditions", ())
        ) or _default_conditions()
        raw_recording = payload.get("recording") or {}
        values["recording"] = RecordingSpec.from_dict(raw_recording)
        return cls(**values)

    @classmethod
    def pulse_train_comparison(cls) -> "ExperimentSpec":
        """App-owned translation of the useful run knobs from Escape-SIZ notebooks."""

        return cls(
            name="Pulse-train comparison",
            template_key="pulse_train_comparison",
            engine="arbor",
            duration_ms=110.0,
            integration_dt_ms=0.01,
            repetitions=1,
            workers=4,
            stimuli=(StimulusSpec(),),
            conditions=_default_conditions(),
            recording=RecordingSpec(sample_dt_ms=0.05),
        )

    def errors(self, circuit: CircuitSpec | None = None) -> list[str]:
        errors: list[str] = []
        if self.schema_version != EXPERIMENT_SCHEMA_VERSION:
            errors.append(f"Unsupported experiment schema {self.schema_version}.")
        if not self.name.strip():
            errors.append("Experiment name is required.")
        if self.engine not in {"arbor", "neuron", "bmtk"}:
            errors.append(f"Unsupported experiment engine {self.engine!r}.")
        if self.duration_ms <= 0:
            errors.append("Simulation duration must be positive.")
        if self.integration_dt_ms <= 0:
            errors.append("Integration time step must be positive.")
        if self.recording.sample_dt_ms < self.integration_dt_ms:
            errors.append("Recording interval cannot be smaller than the integration time step.")
        if self.repetitions < 1:
            errors.append("Repetitions must be at least one.")
        if not 1 <= self.workers <= 256:
            errors.append("Worker count must be between 1 and 256.")
        if not self.stimuli:
            errors.append("At least one stimulus is required.")
        if not any(condition.enabled for condition in self.conditions):
            errors.append("At least one condition must be enabled.")
        names = [condition.name.casefold().strip() for condition in self.conditions]
        if len(names) != len(set(names)):
            errors.append("Condition names must be unique.")
        for stimulus in self.stimuli:
            errors.extend(stimulus.errors())
            if stimulus.enabled and stimulus.final_offset_ms > self.duration_ms:
                errors.append(
                    f"Stimulus {stimulus.name!r} ends after the simulation duration."
                )
        for condition in self.conditions:
            errors.extend(condition.errors())
        errors.extend(self.recording.errors())

        if circuit is not None:
            circuit_ids = set(circuit.neuron_ids)
            if not circuit_ids:
                errors.append("Load a circuit with at least one neuron before running an experiment.")
            referenced = {
                neuron_id
                for stimulus in self.stimuli
                for neuron_id in stimulus.target_neuron_ids
            } | {
                neuron_id
                for condition in self.conditions
                for neuron_id in condition.disabled_neuron_ids
            } | set(self.recording.target_neuron_ids)
            missing = sorted(referenced - circuit_ids)
            if missing:
                errors.append(
                    "Experiment targets are not present in the loaded circuit: "
                    + ", ".join(missing)
                )
        return errors

