"""Bounded engineering checks of production Experiment Builder controls.

Used by source tests and the native package check. Tiny synthetic circuit only;
no network access, scientific data, simulator launch or numerical acceptance.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import combinations, product
from pathlib import Path


# Explicit inventory: new editable controls must be reviewed, not silently skipped.
CONTROLS = (
    "name_edit", "template_combo", "engine_combo", "duration", "integration_dt",
    "initial_voltage", "temperature", "seed", "repetitions", "workers",
    "stimulus_targets", "stimulus_region", "waveform_combo", "amplitude",
    "delay", "pulse_width", "frequency", "pulse_count", "control_enabled",
    "control_gap_enabled", "control_chemical_enabled", "manipulation_enabled",
    "manipulation_name", "manip_gap_enabled", "manip_chemical_enabled",
    "disabled_neurons", "stimulus_scale", "mechanism_scales", "recording_targets",
    "recording_region", "record_voltage", "detect_spikes", "sample_dt",
    "spike_threshold", "make_plots",
)
OPTIONS = {
    "template_combo": ("blank", "pulse_train_comparison"),
    "engine_combo": ("arbor", "neuron", "bmtk"),
    "stimulus_region": ("soma", "ais", "selected_compartments", "all"),
    "waveform_combo": ("pulse_train", "step", "ramp"),
    "recording_region": ("soma", "all", "ais", "selected_compartments"),
}
FACTORS = {
    "engine": ("arbor", "neuron", "bmtk"),
    "waveform": ("pulse_train", "step", "ramp"),
    "stimulus_region": ("soma", "ais", "selected_compartments", "all"),
    "recording_region": ("soma", "ais", "selected_compartments", "all"),
    "conditions": ("control", "comparison", "both"),
    "control_gap": (False, True), "control_chemical": (False, True),
    "comparison_gap": (False, True), "comparison_chemical": (False, True),
    "signals": ("voltage", "spikes", "both"), "plots": (False, True),
    "targets": ("all", "id", "type"), "recording_targets": ("all", "subset"),
    "ablation": (False, True), "scales": ("none", "hh_na", "hh_k"),
    "stimulus_off": (False, True), "parallel": (False, True),
}


def check_inventory(page) -> None:
    from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox, QLineEdit, QWidget
    editable = {
        widget for widget in page.selector_panel.findChildren(QWidget)
        if isinstance(widget, (QLineEdit, QAbstractSpinBox, QComboBox, QCheckBox))
        and not (isinstance(widget, QLineEdit)
                and isinstance(widget.parent(), QAbstractSpinBox))
    }
    declared = {getattr(page, name) for name in CONTROLS}
    if editable != declared:
        raise ValueError("Experiment setting inventory mismatch: "
                         f"{len(editable - declared)} uncovered, "
                         f"{len(declared - editable)} stale controls")
    for name, expected in OPTIONS.items():
        combo = getattr(page, name)
        if tuple(combo.itemData(i) for i in range(combo.count())) != expected:
            raise ValueError(f"Uncovered or changed Experiment option: {name}")


def _pairs(row):
    return {(i, row[i], j, row[j]) for i, j in combinations(range(len(row)), 2)}


def pairwise_cases():
    """Deterministic greedy covering array, with exact pair coverage checked."""
    levels = tuple(FACTORS.values())
    needed = {(i, a, j, b) for i, j in combinations(range(len(levels)), 2)
              for a, b in product(levels[i], levels[j])}
    # One candidate for every pair plus deterministic mixed rows. No simulator
    # randomness, external test generator dependency or unbounded search.
    candidates = set()
    for i, a, j, b in sorted(needed, key=repr):
        row = [choices[0] for choices in levels]
        row[i], row[j] = a, b
        candidates.add(tuple(row))
    import random
    randomizer = random.Random(20261026)
    candidates.update(tuple(randomizer.choice(v) for v in levels) for _ in range(1200))
    pool = [(row, _pairs(row)) for row in sorted(candidates, key=repr)]
    selected = []
    while needed:
        row, covered = max(pool, key=lambda item: len(needed & item[1]))
        if not needed & covered:
            raise ValueError("Interaction generator failed to cover a declared pair")
        selected.append(row)
        needed.difference_update(covered)
    return tuple(dict(zip(FACTORS, row)) for row in selected)


def check_pair_coverage(cases):
    needed = {(i, a, j, b) for i, j in combinations(range(len(FACTORS)), 2)
              for a, b in product(tuple(FACTORS.values())[i], tuple(FACTORS.values())[j])}
    covered = set().union(*(_pairs(tuple(c[k] for k in FACTORS)) for c in cases)) if cases else set()
    missing = needed - covered
    if missing:
        raise ValueError(f"Uncovered Experiment setting interaction pairs: {len(missing)}")
    return len(needed)


def check_edit_binding(name, config, value):
    """Independent field oracle: an editable widget must affect its run field."""
    root = {"name_edit": "name", "engine_combo": "engine", "duration": "duration_ms",
            "integration_dt": "integration_dt_ms", "initial_voltage": "initial_voltage_mV",
            "temperature": "temperature_C", "seed": "random_seed", "repetitions": "repetitions",
            "workers": "workers"}
    stimulus = {"stimulus_region": "target_region", "waveform_combo": "waveform",
                "amplitude": "amplitude_nA", "delay": "delay_ms", "pulse_width": "pulse_width_ms",
                "frequency": "frequency_hz", "pulse_count": "pulse_count"}
    recording = {"recording_region": "target_region", "record_voltage": "record_voltage",
                 "detect_spikes": "detect_spikes", "sample_dt": "sample_dt_ms",
                 "spike_threshold": "spike_threshold_mV", "make_plots": "make_plots"}
    control = next((c for c in config.conditions if c.name == "Control"), None)
    comparison = next((c for c in config.conditions if c.name != "Control"), None)
    if name in root:
        actual = getattr(config, root[name])
    elif name in stimulus:
        actual = getattr(config.stimuli[0], stimulus[name])
    elif name in recording:
        actual = getattr(config.recording, recording[name])
    elif name == "template_combo":
        actual = config.template_key
        if config.workers != 4 or config.name != "Pulse-train comparison":
            raise ValueError("Template failed to apply its declared protocol")
    elif name in {"control_enabled", "manipulation_enabled"}:
        actual = (control is not None) if name == "control_enabled" else (comparison is not None)
    elif name in {"stimulus_targets", "recording_targets"}:
        actual = (config.stimuli[0].target_neuron_ids if name == "stimulus_targets"
                  else config.recording.target_neuron_ids)
        value = ("1",)
    else:
        owner, field = {
            "control_gap_enabled": (control, "gap_junctions_enabled"),
            "control_chemical_enabled": (control, "chemical_synapses_enabled"),
            "manipulation_name": (comparison, "name"),
            "manip_gap_enabled": (comparison, "gap_junctions_enabled"),
            "manip_chemical_enabled": (comparison, "chemical_synapses_enabled"),
            "disabled_neurons": (comparison, "disabled_neuron_ids"),
            "stimulus_scale": (comparison, "stimulus_scale"),
            "mechanism_scales": (comparison, "mechanism_scales"),
        }[name]
        actual = getattr(owner, field)
        if name == "disabled_neurons":
            value = ("2",)
        elif name == "mechanism_scales":
            value = {"hh_na": .5}
    if actual != value:
        raise ValueError(f"Setting is not bound to its run field: {name}: {actual!r} != {value!r}")


def check_settings_interactions(destination: Path, *, parent=None) -> dict:
    import json
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit
    from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.experiment import ConditionSpec, ExperimentSpec, RecordingSpec, StimulusSpec
    from digifly_app.core.morphology import load_swc
    from digifly_app.ui.experiment_builder import ExperimentBuilderPage

    destination.mkdir(exist_ok=False)
    page = ExperimentBuilderPage(parent, output_root=destination / "not-created-runs")
    records = []
    circuit = CircuitSpec(connectome=ConnectomeRef("engineering:v1", "Synthetic", str(destination)),
                          neuron_ids=("1", "2"), compartment_overrides={"1": {"3": {"cm_uF_cm2": 1.5}}})
    for body, family, cell_type in (("1", "DN", "DNp01"), ("2", "MN", "TTMn")):
        swc = destination / (body + ".swc")
        swc.write_text("1 1 0 0 0 2 -1\n2 1 0 0 4 2 1\n3 2 0 0 8 1 2\n", encoding="utf-8")
        records.append(load_swc(NeuronRecord(body, family, cell_type, str(swc), "engineering:v1")))
    page.set_circuit_snapshot(circuit, tuple(records))
    before_circuit = page.circuit_spec().to_dict()
    check_inventory(page)
    changes = []
    page.experiment_changed.connect(changes.append)
    edited = {}
    values = {
        "name_edit": "Settings engineering", "template_combo": "pulse_train_comparison",
        "engine_combo": "neuron", "duration": 120., "integration_dt": .005,
        "initial_voltage": -64., "temperature": 23., "seed": 17, "repetitions": 2,
        "workers": 2, "stimulus_targets": "DNp01", "stimulus_region": "ais",
        "waveform_combo": "ramp", "amplitude": 1.25, "delay": 2., "pulse_width": .5,
        "frequency": 200., "pulse_count": 3, "control_enabled": False,
        "control_gap_enabled": False, "control_chemical_enabled": False,
        "manipulation_enabled": False, "manipulation_name": "Changed comparison",
        "manip_gap_enabled": True, "manip_chemical_enabled": False,
        "disabled_neurons": "2", "stimulus_scale": .5,
        "mechanism_scales": '{"hh_na": 0.5}', "recording_targets": "1",
        "recording_region": "all", "record_voltage": False, "detect_spikes": False,
        "sample_dt": .1, "spike_threshold": -30., "make_plots": False,
    }
    try:
        page.document_preview.setPlainText("previous validation")
        count = len(changes)
        page.name_edit.setText("Accessibility edit")
        if len(changes) <= count or page.document_preview.toPlainText():
            raise ValueError("External text edits leave a stale experiment document")
        page.stimulus_targets.setText("1")
        if "1 soma/pseudosoma" not in page.target_region_visualization_label.text():
            raise ValueError("External target edits leave a stale region preview")
        from digifly_app.workers.generic_experiment_worker import _regional_report_budget
        budget_case = {"duration_ms": 6, "integration_dt_ms": .001, "repetitions": 1,
                       "recording": {"target_region": "all", "sample_dt_ms": .001}}
        try:
            _regional_report_budget({"a": {"estimated_nseg": 1000, "sections": [{}] * 1000}}, budget_case)
        except ValueError as exc:
            if "playback" not in str(exc):
                raise
        else:
            raise ValueError("All-compartment readiness accepts a recording that playback must refuse")
        # Actual Qt keyboard/state signals, not just dataclass construction.
        for name in CONTROLS:
            page.set_config(ExperimentSpec())
            widget, value = getattr(page, name), values[name]
            page.document_preview.setPlainText("previous validation")
            count = len(changes)
            if isinstance(widget, QLineEdit):
                widget.setFocus()
                widget.selectAll()
                QTest.keyClicks(widget, value)
                observed = widget.text()
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(widget.findData(value))
                observed = widget.currentData()
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value)
                observed = widget.isChecked()
            else:
                widget.setValue(value)
                observed = widget.value()
            if observed != value or len(changes) <= count or page.document_preview.toPlainText():
                raise ValueError(f"Setting edit failed to update/invalidate its draft: {name}")
            config = page.config()
            check_edit_binding(name, config, value)
            if ExperimentSpec.from_dict(config.to_dict()) != config:
                raise ValueError(f"Setting document roundtrip changed: {name}")
            page.set_config(config)
            if page.config() != config:
                raise ValueError(f"Setting widget roundtrip changed: {name}")
            edited[name] = True

        cases = pairwise_cases()
        pairs = check_pair_coverage(cases)
        outcomes = []
        for number, case in enumerate(cases):
            conditions = []
            if case["conditions"] != "comparison":
                conditions.append(ConditionSpec(name="Control", gap_junctions_enabled=case["control_gap"],
                    chemical_synapses_enabled=case["control_chemical"]))
            if case["conditions"] != "control":
                conditions.append(ConditionSpec(name="Comparison", gap_junctions_enabled=case["comparison_gap"],
                    chemical_synapses_enabled=case["comparison_chemical"],
                    disabled_neuron_ids=("2",) if case["ablation"] else (),
                    mechanism_scales={} if case["scales"] == "none" else {case["scales"]: .5},
                    stimulus_scale=0. if case["stimulus_off"] else 1.))
            stimulus = StimulusSpec(target_neuron_ids=("1",) if case["targets"] != "all" else ("1", "2"),
                target_region=case["stimulus_region"], waveform=case["waveform"], delay_ms=.5,
                pulse_width_ms=.2, frequency_hz=1000., pulse_count=3)
            expected = ExperimentSpec(name=f"Settings pair {number}", engine=case["engine"],
                duration_ms=4., integration_dt_ms=.005, initial_voltage_mV=-64., temperature_C=23.,
                random_seed=17, repetitions=2 if case["parallel"] else 1,
                workers=2 if case["parallel"] else 1, stimuli=(stimulus,), conditions=tuple(conditions),
                recording=RecordingSpec(target_neuron_ids=("1",) if case["recording_targets"] == "subset" else (),
                    target_region=case["recording_region"], record_voltage=case["signals"] != "spikes",
                    detect_spikes=case["signals"] != "voltage", spike_threshold_mV=-30., make_plots=case["plots"]))
            page.set_config(expected)
            if case["targets"] == "type":
                page.stimulus_targets.setText("DNp01")
            elif case["targets"] == "all":
                page.stimulus_targets.clear()
            actual = page.config()
            if actual != expected or actual.errors(circuit):
                raise ValueError(f"Setting pair changed or refused: {number}: {case}: {actual.errors(circuit)}")
            restored = ExperimentSpec.from_dict(json.loads(json.dumps(actual.to_dict())))
            page.set_config(restored)
            if page.config() != expected or page.circuit_spec().to_dict() != before_circuit:
                raise ValueError(f"Save/reopen or circuit immutability failed: {number}")
            outcomes.append({"case": case, "roundtrip": True, "circuit_unchanged": True})
        # Targeted higher-order and negative interactions. Validation must stop
        # locally without opening a simulator, including unfinished drafts.
        refusals = {}
        for case in ("conditions_off", "signals_off", "recording_faster_than_integration",
                     "missing_target", "active_invalid_json", "reserved_comparison_name"):
            page.set_config(ExperimentSpec())
            if case == "conditions_off":
                page.control_enabled.setChecked(False)
                page.manipulation_enabled.setChecked(False)
                expected_message = "At least one condition"
                saved = ExperimentSpec.from_dict(page.config().to_dict())
                page.set_config(saved)
            elif case == "signals_off":
                page.record_voltage.setChecked(False)
                page.detect_spikes.setChecked(False)
                expected_message = "at least one recording"
            elif case == "recording_faster_than_integration":
                page.integration_dt.setValue(.1)
                page.sample_dt.setValue(.01)
                expected_message = "smaller than the integration"
            elif case == "missing_target":
                page.stimulus_targets.setText("not-loaded")
                expected_message = "not present in the loaded circuit"
            elif case == "active_invalid_json":
                page.mechanism_scales.setText('{"hh_na":')
                expected_message = "Expecting value"
            else:
                page.control_enabled.setChecked(False)
                page.manipulation_name.setText("CONTROL")
                expected_message = "reserved"
            page.validate_draft()
            if expected_message not in page.validation_state.text():
                raise ValueError(f"Unsafe combination lacks its intended refusal: {case}")
            refusals[case] = page.validation_state.text()
        page.mechanism_scales.setText('{"hh_na":')
        page.manipulation_name.clear()
        page.disabled_neurons.setText("not-loaded")
        page.manipulation_enabled.setChecked(False)
        page.control_enabled.setChecked(True)
        if page.config().errors(circuit) or len(page.config().conditions) != 1:
            raise ValueError("Inactive comparison still affects Control")
        if (destination / "not-created-runs").exists() or page._process is not None:
            raise ValueError("Setting-only inspection unexpectedly launched/created a run")
        result = {"scope": "production widgets and document bindings; synthetic, no simulator execution",
                  "controls": edited, "options": OPTIONS, "pairwise_factors": FACTORS,
                  "covered_pairs": pairs, "cases": outcomes, "intentional_refusals": refusals,
                  "external_text_edits_refresh": True,
                  "inactive_comparison_ignored": True, "no_run_created": True}
        (destination / "settings-interactions.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return {"experiment_settings_inventory": True, "experiment_settings_pairwise": True,
                "experiment_settings_refusals": True,
                "experiment_settings_external_edits": True,
                "all_compartment_playback_readiness": True,
                "experiment_settings_controls": len(edited), "experiment_settings_cases": len(cases),
                "experiment_settings_pairs": pairs}
    finally:
        page.close()
        page.deleteLater()
        QApplication.processEvents()
