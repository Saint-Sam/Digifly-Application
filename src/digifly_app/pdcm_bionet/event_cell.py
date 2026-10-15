"""Small, independent event-rule oracle for the BioNet point cell.

The discrete update is intentionally explicit: every received event, including
zero-weight clock input, advances synaptic current and membrane voltage. This
is a test oracle for the new NMODL implementation, not a population simulator.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class EventCellParameters:
    tau_m_ms: float = 10.0
    tau_syn_ms: float = 0.5
    tau_ref_ms: float = 2.0
    reset_mv: float = -65.0
    threshold_mv: float = -50.0
    capacitance_pf: float = 250.0
    external_current_pa: float = 0.0

    def validate(self) -> None:
        for name in ("tau_m_ms", "tau_syn_ms", "tau_ref_ms", "capacitance_pf"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("reset_mv", "threshold_mv", "external_current_pa"):
            if not math.isfinite(getattr(self, name)):
                raise ValueError(f"{name} must be finite")


@dataclass
class EventCellState:
    voltage_mv: float = -65.0
    synaptic_current_pa: float = 0.0
    last_event_ms: float = 0.0
    refractory_until_ms: float | None = None

    def release(self, time_ms: float, params: EventCellParameters) -> None:
        if self.refractory_until_ms is None or time_ms < self.refractory_until_ms:
            raise ValueError("refractory release is not due")
        self.voltage_mv = params.reset_mv
        self.last_event_ms = self.refractory_until_ms
        self.refractory_until_ms = None

    def receive(self, time_ms: float, weight_pa: float, params: EventCellParameters) -> bool:
        """Apply one incoming contact; return whether this event emits a spike."""
        params.validate()
        if not math.isfinite(time_ms) or not math.isfinite(weight_pa):
            raise ValueError("event time and weight must be finite")
        if time_ms < self.last_event_ms:
            raise ValueError("event times must be monotone")
        if self.refractory_until_ms is not None:
            if time_ms < self.refractory_until_ms:
                return False
            self.release(time_ms, params)
        dt = time_ms - self.last_event_ms
        self.synaptic_current_pa *= 1.0 - dt / params.tau_syn_ms
        self.synaptic_current_pa += weight_pa
        self.voltage_mv += dt * (
            (params.reset_mv - self.voltage_mv) / params.tau_m_ms
            + (self.synaptic_current_pa + params.external_current_pa) / params.capacitance_pf
        )
        self.last_event_ms = time_ms
        if self.voltage_mv > params.threshold_mv:
            self.voltage_mv = params.reset_mv + 30.0
            self.refractory_until_ms = time_ms + params.tau_ref_ms
            return True
        return False
