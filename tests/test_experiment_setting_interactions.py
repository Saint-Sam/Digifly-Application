"""Every exposed protocol control, option and bounded interaction contract.

These are real production-widget/source tests, not simulator or packaged-app
acceptance. The native package fixture invokes the same inventory separately.
"""
from dataclasses import replace
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from digifly_app.core.experiment import ExperimentSpec
from digifly_app.ui.experiment_builder import ExperimentBuilderPage


@pytest.fixture
def page(tmp_path):
    app = QApplication.instance() or QApplication([])
    widget = ExperimentBuilderPage(output_root=tmp_path)
    yield widget
    widget.close()
    app.processEvents()


def test_disabled_comparison_cannot_block_control_with_unfinished_json(page):
    page.mechanism_scales.setText('{"hh_na":')
    page.disabled_neurons.setText("not-a-loaded-neuron")
    page.manipulation_name.clear()
    page.manipulation_enabled.setChecked(False)
    assert len(page.config().conditions) == 1
    assert page.config().conditions[0].name == "Control"
    page.manipulation_enabled.setChecked(True)
    with pytest.raises(ValueError):
        page.config()


def test_all_conditions_off_remain_off_after_saved_document_roundtrip(page):
    page.control_enabled.setChecked(False)
    page.manipulation_enabled.setChecked(False)
    document = page.config().to_dict()
    restored = ExperimentSpec.from_dict(document)
    assert restored.conditions == ()
    assert "At least one condition must be enabled." in restored.errors()
    page.set_config(restored)
    assert not page.control_enabled.isChecked()
    assert not page.manipulation_enabled.isChecked()


def test_comparison_cannot_silently_become_control_after_save(page):
    page.control_enabled.setChecked(False)
    page.manipulation_name.setText("  CONTROL  ")
    page.disabled_neurons.setText("10068")
    page.stimulus_scale.setValue(.5)
    with pytest.raises(ValueError, match="reserved"):
        page.config()


def test_inactive_controls_are_disabled_without_discarding_values(page):
    page.mechanism_scales.setText('{"hh_na": 0.5}')
    page.stimulus_scale.setValue(.5)
    page.control_gap_enabled.setChecked(False)
    page.manipulation_enabled.setChecked(False)
    page.control_enabled.setChecked(False)
    assert not page.mechanism_scales.isEnabled()
    assert not page.manip_chemical_enabled.isEnabled()
    assert not page.control_gap_enabled.isEnabled()
    page.manipulation_enabled.setChecked(True)
    page.control_enabled.setChecked(True)
    assert page.mechanism_scales.isEnabled()
    assert page.config().conditions[1].mechanism_scales == {"hh_na": .5}
    assert page.config().conditions[1].stimulus_scale == .5
    assert not page.config().conditions[0].gap_junctions_enabled
    page.waveform_combo.setCurrentIndex(page.waveform_combo.findData("step"))
    assert not page.frequency.isEnabled() and not page.pulse_count.isEnabled()
    page.waveform_combo.setCurrentIndex(page.waveform_combo.findData("pulse_train"))
    assert page.frequency.isEnabled() and page.pulse_count.isEnabled()
    page.detect_spikes.setChecked(False)
    assert not page.spike_threshold.isEnabled()
    page.detect_spikes.setChecked(True)
    assert page.spike_threshold.isEnabled()


def test_explicit_empty_protocol_is_not_replaced_by_template_defaults():
    original = replace(ExperimentSpec(), stimuli=(), conditions=())
    assert ExperimentSpec.from_dict(original.to_dict()) == original
    # Missing fields in legacy documents still receive their old defaults.
    assert ExperimentSpec.from_dict({}) == ExperimentSpec()


def test_all_controls_and_pairwise_bindings(tmp_path):
    from digifly_app.ui.experiment_settings_check import check_settings_interactions
    app = QApplication.instance() or QApplication([])
    result = check_settings_interactions(tmp_path / "settings")
    assert result["experiment_settings_controls"] == 35
    assert result["experiment_settings_cases"] > 1
    assert result["experiment_settings_pairs"] > 500
    app.processEvents()


def test_inventory_rejects_a_new_uncovered_control(page):
    from PySide6.QtWidgets import QCheckBox
    from digifly_app.ui.experiment_settings_check import check_inventory
    check_inventory(page)
    added = QCheckBox("New untested setting", page.selector_panel)
    with pytest.raises(ValueError, match="inventory mismatch"):
        check_inventory(page)
    added.deleteLater()


def test_pair_coverage_rejects_missing_interactions():
    from digifly_app.ui.experiment_settings_check import pairwise_cases, check_pair_coverage
    cases = pairwise_cases()
    assert check_pair_coverage(cases) > 500
    with pytest.raises(ValueError, match="Uncovered"):
        check_pair_coverage(cases[:1])


def test_binding_guard_rejects_a_setting_ignored_by_serialization():
    from digifly_app.ui.experiment_settings_check import check_edit_binding
    check_edit_binding("workers", replace(ExperimentSpec(), workers=2), 2)
    with pytest.raises(ValueError, match="not bound"):
        check_edit_binding("workers", ExperimentSpec(), 2)


def test_frequency_control_does_not_round_its_positive_minimum_to_zero(page):
    assert page.frequency.minimum() > 0


def test_invalid_train_frequency_reports_error_instead_of_dividing_by_zero():
    spec = ExperimentSpec()
    invalid = replace(spec, stimuli=(replace(spec.stimuli[0], frequency_hz=0),))
    assert "Stimulus frequency must be positive." in invalid.errors()


def test_accessibility_text_edits_invalidate_the_ready_document(page):
    changes = []
    page.experiment_changed.connect(changes.append)
    page.document_preview.setPlainText("old ready document")
    # Qt accessibility setters use setText rather than emitting textEdited.
    page.name_edit.setText("Accessible name edit")
    assert changes and changes[-1].name == "Accessible name edit"
    assert not page.document_preview.toPlainText()


def test_accessibility_target_edit_refreshes_the_live_region_preview(page, tmp_path):
    from digifly_app.core.circuit import CircuitSpec
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.morphology import load_swc
    cells = []
    for body in ("1", "2"):
        path = tmp_path / (body + ".swc")
        path.write_text("1 1 0 0 0 2 -1\n2 3 0 0 4 1 1\n")
        cells.append(load_swc(NeuronRecord(body, "DN", "DNp01", str(path), "test:v1")))
    page.set_circuit_snapshot(CircuitSpec(neuron_ids=("1", "2")), tuple(cells))
    assert "2 soma/pseudosoma" in page.target_region_visualization_label.text()
    page.stimulus_targets.setText("1")
    assert page.config().stimuli[0].target_neuron_ids == ("1",)
    assert "1 soma/pseudosoma" in page.target_region_visualization_label.text()
