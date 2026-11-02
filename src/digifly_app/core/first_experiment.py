"""Small, offline teaching recipe shared by the first-use guide and its checks.

No network, runtime discovery, simulation, or file writes belong in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import codecs
import hashlib
import json
import math
from typing import Any

from .circuit import CircuitSpec, HodgkinHuxleySpec
from .experiment import ConditionSpec, ExperimentSpec, RecordingSpec, StimulusSpec
from .mechanisms import ChemicalSynapsePolicy, membrane_profile

DATASET = "male-cns:v1.0"
NEURON_IDS = ("10001", "10010", "800146", "804642", "802401", "903327",
    "800718", "800890", "801295", "801895", "801970", "801998", "802544",
    "803013", "803048", "1050014552")
QUERY = "DNp01, TTMn, PSI, DLMn"
GUIDE_VERSION = 3
SETTINGS_KEY = "first_experiment_v1"


class _ContactJsonStream:
    """Decode one contact at a time and hash the same bytes being reviewed."""

    def __init__(self, source):
        self.source = source
        self.buffer = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.json = json.JSONDecoder()
        self.digest = hashlib.sha256()
        self.eof = False

    def fill(self):
        block = self.source.read(64 * 1024)
        self.digest.update(block)
        self.eof = not block
        self.buffer += self.decoder.decode(block, final=self.eof)

    def peek(self):
        while True:
            self.buffer = self.buffer.lstrip(" \t\r\n")
            if self.buffer or self.eof:
                return self.buffer[:1]
            self.fill()

    def take(self, token):
        if self.peek() != token:
            raise ValueError("Saved chemical-contact JSON is invalid.")
        self.buffer = self.buffer[1:]

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.json.raw_decode(self.buffer)
            except json.JSONDecodeError:
                if self.eof:
                    raise ValueError("Saved chemical-contact JSON is invalid.")
                self.fill()
                continue
            # A number can end exactly at a chunk boundary before more digits.
            if end == len(self.buffer) and not self.eof:
                self.fill()
                continue
            self.buffer = self.buffer[end:]
            return value


def review_teaching_contacts(path, body_ids):
    """Review a contact document without a file-size cutoff or whole-file load.

    Memory follows the largest individual contact or metadata value, rather
    than the number of contacts. Whitespace is discarded between chunks.
    """
    count = 0
    seen = set()
    with path.open("rb") as source:
        reader = _ContactJsonStream(source)
        reader.take("{")
        while reader.peek() != "}":
            key = reader.value()
            if not isinstance(key, str) or key in seen:
                raise ValueError("Saved chemical-contact JSON has invalid or duplicate fields.")
            seen.add(key)
            reader.take(":")
            if key == "chemical_edges":
                reader.take("[")
                while reader.peek() != "]":
                    row = reader.value()
                    if (not isinstance(row, dict) or not row.get("located_contact")
                            or row.get("pre_id") not in body_ids or row.get("post_id") not in body_ids):
                        raise ValueError("The saved run needs located chemical contacts between the teaching cells and both completed conditions.")
                    count += 1
                    if reader.peek() != ",":
                        break
                    reader.take(",")
                    if reader.peek() == "]":
                        raise ValueError("Saved chemical-contact JSON is invalid.")
                reader.take("]")
            elif key == "electrical_edges":
                reader.take("[")
                reader.take("]")
            else:
                reader.value()
            if reader.peek() != ",":
                break
            reader.take(",")
            if reader.peek() == "}":
                raise ValueError("Saved chemical-contact JSON is invalid.")
        reader.take("}")
        if reader.peek():
            raise ValueError("Saved chemical-contact JSON has trailing data.")
        if not count or not {"chemical_edges", "electrical_edges"}.issubset(seen):
            raise ValueError("The saved run needs located chemical contacts between the teaching cells and both completed conditions.")
        return count, reader.digest.hexdigest()


@dataclass(frozen=True)
class GuideStep:
    key: str
    title: str
    page: int
    instructions: str
    action: str = ""
    action_label: str = ""
    details: str = ""


STEPS = (
    GuideStep("welcome", "Your first NEURON experiment", 0,
        "Build a Male CNS v1.0 circuit with DNp01, TTMn, PSI and DLMn. Give its two DNp01 cells a short pulse. "
        "Then compare their voltage with the same circuit at rest.\n\n"
        "Use Next to follow the guide. You can pause and return from First experiment at any time.",
        "new", "Start a new project...",
        "The selection contains 16 neurons: two DNp01, two TTMn, two PSI and ten DLMn. "
        "Downloads and simulations start only when you press their own buttons. "
        "If you already have a project open, save it before starting a new one."),
    GuideStep("workspace", "Choose a save folder", 0,
        "1. Click Browse beside Workstation output root and choose a folder for your runs.\n\n"
        "2. Save the project with the button below.",
        "save", "Save project as...",
        "The .digifly.json project file stores your design and the locations of its data. "
        "Keep the SWCs, connectivity and recordings with it when moving to another device."),
    GuideStep("runtime", "Set up NEURON", 0,
        "1. Open Find or install runtimes below. Search for NEURON, select its Python and click Use selected runtimes.\n\n"
        "2. Click Run workspace doctor in the highlighted area. Check that NEURON passes.",
        "runtime", "Find or install runtimes...",
        "Already know your NEURON Python location? Use Browse beside NEURON Python.\n\n"
        "On Mac, NEURON is installed separately. With Python 3.13 installed, the Terminal commands are:\n"
        "python3.13 -m venv ~/Digifly-NEURON\n"
        '~/Digifly-NEURON/bin/python -m pip install "neuron==9.0.1" matplotlib\n'
        '~/Digifly-NEURON/bin/python -c "from neuron import h; print(h.nrnversion())"\n'
        "Then select ~/Digifly-NEURON/bin/python. These commands have not yet been tested on a clean Mac. "
        "Windows has separate NEURON setup controls in the runtime dialog."),
    GuideStep("download", "Download the neurons", 1,
        "1. Click Download from neuPrint. Connect with your token and select male-cns:v1.0.\n\n"
        "2. Search for DNp01, TTMn, PSI, DLMn with Limit 64, then click Preview neurons. You should see 16 cells.\n\n"
        "3. Keep Update connectivity and chemical contact locations checked. Click Review and download.",
        details="Server: https://neuprint.janelia.org. Token help explains how to get a token. "
        "The app can also read Phase 1/Neuprint Token.txt in your workspace. Keep that file private.\n\n"
        "Cell IDs\nDNp01: 10001/10010\nTTMn: 800146/804642\nPSI: 802401/903327\n"
        "DLMn: 800718, 800890, 801295, 801895, 801970, 801998, 802544, 803013, 803048, 1050014552\n\n"
        "SWCs go into this release's export_swc family folders. Connections are added to "
        "connectivity/connectivity.sqlite. Existing files are kept unless you approve replacement."),
    GuideStep("healing", "Check the neuron shapes", 2,
        "1. Under More options, click Check recent imports.\n\n"
        "2. Read Show Details. For needed radius repairs, choose Save healed copy. For disconnected pieces, "
        "review the bridge preview's cable length and volume before confirming.\n\n"
        "3. Load the cells again after saving repairs.",
        details="Radius repair keeps the original point and contact positions, but changes the cable diameter. "
        "A bridge adds cable across a gap. Review any proposed bridge separately. Original files are kept. "
        "Existing projects keep their saved file choices until you load a fresh selection."),
    GuideStep("cells", "Load the 16 cells", 2,
        "1. Choose Male CNS v1.0 and Simulator NEURON.\n\n"
        "2. Enter DNp01, TTMn, PSI, DLMn with Limit 64, then click Load neurons.\n\n"
        "3. Check that 16 cells appear. Click Full skeletons to see their shapes.",
        "selection", "Fill selection fields...",
        "Expected groups: two DNp01, two TTMn, two PSI and ten DLMn. All body IDs are listed in the download step's Details. "
        "TTMn is in the MN family.\n\n"
        "Drag to rotate, Shift-drag to pan, scroll to zoom and right-click to center a cell. "
        "A soma proxy supplies a model soma when the downloaded shape lacks one.\n\n"
        "Fill selection fields asks before changing the source, simulator and search. Click Load neurons afterward."),
    GuideStep("membrane", "Set the membrane model", 2,
        "1. Click Apply example membrane below and confirm. This sets Classic HH for all 16 cells.\n\n"
        "2. Open the highlighted sections to inspect the values. Use Details here for the full settings list.",
        "membrane", "Apply example membrane...",
        "Profile: Classic HH, all regions, built-in Na/K on, extra channels off.\n\n"
        "Passive properties\nRa: 100 ohm cm\nCm: 1 uF/cm2\nLeak: 0.0001 S/cm2 at -65 mV\n\n"
        "Soma conductances (S/cm2)\nNa: 0.12\nK: 0.036\nLeak: 0.0003\n\n"
        "Branch conductances (S/cm2)\nNa: 0.02\nK: 0.01\nLeak: 0.0001\n\n"
        "Reversal potentials (mV)\nSoma and branch leak: -65\nNa: +65\nK: -74\nCa: +120\n\n"
        "Initial voltage: -65 mV. Temperature: 22 C. The example uses shared defaults, with no cell or segment overrides. "
        "To enter these manually, use Set HH + channels as cell-set defaults after editing."),
    GuideStep("connections", "Set the connections", 2,
        "1. Click Apply example connections below and confirm. Chemical synapses will use the downloaded contact locations.\n\n"
        "2. Check Chemical-synapse policy and Gap-junction policy in the highlighted panel. Gaps should be set to None.",
        "connections", "Apply example connections...",
        "Chemical model: Exp2Syn at imported post-contact locations.\n"
        "Strength: source-or-default per contact\nFallback strength: 3e-6 uS per contact\nMultiplier: 1\n"
        "Delay: 1 ms\nRise: 0.5 ms\nDecay: 3 ms\nReversal: 0 mV\nSoma release threshold: 0 mV\n"
        "Geometric delay: off\nGap-junction policy: None\n\n"
        "Connections come from the selected Male CNS v1.0 download. "
        "This example adds no gap junctions. If entering values manually, apply both connection policies."),
    GuideStep("experiment", "Set up the pulse", 3,
        "1. Click Fill example experiment below and confirm.\n\n"
        "2. Inspect the pulse, conditions and recording settings. The run lasts 8 ms and compares Control with No stimulus.\n\n"
        "The full settings are in Details. Filling them does not start the run.",
        "experiment", "Fill example experiment...",
        "Experiment\nTemplate: Blank\nSimulator: NEURON\nDuration: 8 ms\nIntegration step: 0.005 ms\n"
        "Initial voltage: -65 mV\nTemperature: 22 C\nSeed: 1\nRepetitions: 1\nWorkers: 1\n\n"
        "Stimulus\nTargets: 10001, 10010 (DNp01)\nRegion: Soma\nType: Pulse train\nAmplitude: 0.05 nA\n"
        "Delay: 1 ms\nWidth: 1 ms\nFrequency: 100 Hz\nPulse count: 1\n\n"
        "Conditions\nControl: chemical on, gaps off, stimulus scale 1\n"
        "No stimulus: chemical on, gaps off, stimulus scale 0\nDisabled neurons: blank\nMechanism scales: {}\n\n"
        "Recording\nTargets: blank (all 16 cells)\nRegion: All compartments\nSignals: Membrane voltage and Spike events\n"
        "Spike threshold: -20 mV\nSample interval: 0.05 ms\nCurrent tracking: Off\nGenerate standard plots: unchecked\n\n"
        "The fill button chooses a new experiment name."),
    GuideStep("readiness", "Check before running", 3,
        "1. Save the project.\n\n"
        "2. Click Check run readiness in Experiment Builder. Read the report and fix any failed checks.\n\n"
        "Continue when the report says Preflight passed.",
        "save", "Save project as...",
        "Readiness checks the selected sources, coordinate frames, simulator and recording limits. "
        "Apply any pending Circuit Builder edits first. The guide's Check settings button compares your values "
        "with this example; use Check run readiness to check the simulator inputs."),
    GuideStep("run", "Run the experiment", 3,
        "1. Click Run experiment in the highlighted area.\n\n"
        "2. Wait for Control and No stimulus to finish. Results opens when the experiment completes.\n\n"
        "If the status is unclear, read the log before pressing Run again.",
        details="Stop safely requests termination. A stopped or failed experiment must be resolved before "
        "you can finish this guide. Moving between guide pages never starts a simulation."),
    GuideStep("playback", "Watch the voltage replay", 4,
        "1. Wait for the recording to load, then choose Full skeletons and the recorded voltage view.\n\n"
        "2. Select Control. Press Play, or drag the time slider between 0 and 8 ms.\n\n"
        "Colors show each recorded location's voltage. Gray marks locations without a recording.",
        "result", "Open a saved result...",
        "To reopen a result, first open its saved project, then choose the run's summary.json. "
        "Voltage playback and current tracking are separate recordings; this experiment records voltage."),
    GuideStep("results", "Compare the traces", 4,
        "1. In the highlighted trace panel, compare Control with No stimulus. Zoom in around the pulse at 1 to 2 ms.\n\n"
        "2. Filter by a cell or recording location to inspect its response. Clear the filter to see the other traces.\n\n"
        "Click Finish when you have inspected the replay and traces.",
        details="For the first DNp01 soma, enter this trace filter:\n"
        "SWC 154 Control 10001, SWC 154 No stimulus 10001\n\n"
        "Compare the same recording location in Control and No stimulus to see the pulse's effect.\n\n"
        "For a future current recording, select Sparse or Full current tracking before Run. "
        "Sparse records selected centers and connection sites. Full records every native compartment.\n\n"
        "Finish checks that the loaded result belongs to this project's 16-cell experiment."),
)


def tutorial_experiment(name: str) -> ExperimentSpec:
    return ExperimentSpec(
        name=name, template_key="blank", engine="neuron", duration_ms=8.0,
        integration_dt_ms=0.005, initial_voltage_mV=-65, temperature_C=22,
        random_seed=1, repetitions=1, workers=1,
        stimuli=(StimulusSpec(target_neuron_ids=("10001", "10010"), amplitude_nA=0.05,
            delay_ms=1, pulse_width_ms=1, frequency_hz=100, pulse_count=1),),
        conditions=(ConditionSpec(name="Control", gap_junctions_enabled=False),
            ConditionSpec(name="No stimulus", gap_junctions_enabled=False, stimulus_scale=0)),
        recording=RecordingSpec(target_region="all", make_plots=False),
    )


def values_match(actual: Any, expected: Any) -> bool:
    """Compare configured fields at a tight, unit-appropriate roundoff tolerance."""
    if isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (float, int)):
        return isinstance(actual, (float, int)) and not isinstance(actual, bool) and math.isclose(
            actual, expected, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
            values_match(actual[key], value) for key, value in expected.items())
    if isinstance(expected, (tuple, list)):
        return isinstance(actual, (tuple, list)) and len(actual) == len(expected) and all(
            values_match(a, e) for a, e in zip(actual, expected))
    return actual == expected


def circuit_issues(circuit: CircuitSpec, *, model: bool = True) -> list[str]:
    issues = []
    if circuit.connectome.dataset != DATASET:
        issues.append("Select male-cns:v1.0; other releases use different identities.")
    if set(circuit.neuron_ids) != set(NEURON_IDS) or len(circuit.neuron_ids) != len(NEURON_IDS):
        issues.append("Load exactly the 16 example body IDs.")
    if not model:
        return issues
    if not circuit.connectome.pair_graph_path or not circuit.connectome.pair_graph_sha256:
        issues.append("Download and attach this release's connectivity and chemical contact locations in Data Library.")
    if not values_match(circuit.hh.to_dict(), HodgkinHuxleySpec().to_dict()):
        issues.append("Apply the example shared HH values.")
    if not values_match(circuit.membrane.to_dict(), membrane_profile("classic_hh").to_dict()):
        issues.append("Use Classic HH with extra channels off.")
    if not values_match(circuit.chemical_synapse_policy.to_dict(), ChemicalSynapsePolicy().to_dict()):
        issues.append("Apply the example located chemical model.")
    if circuit.gap_junction_policy.mode != "none":
        issues.append("Set the gap policy to None.")
    if any((circuit.neuron_overrides, circuit.compartment_overrides,
            circuit.neuron_mechanism_overrides, circuit.compartment_mechanism_overrides,
            circuit.connection_overrides)):
        issues.append("The shared teaching example has no cell, segment or pair overrides. Keep custom work in a separate project.")
    return issues


def experiment_issues(config: ExperimentSpec) -> list[str]:
    expected = tutorial_experiment(config.name).to_dict()
    actual = config.to_dict()
    return [f"Check {key.replace('_', ' ')} against the guide." for key, value in expected.items()
        if not values_match(actual.get(key), value)]
