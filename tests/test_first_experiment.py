"""The beginner guide teaches the existing app without taking over execution."""
import json
import hashlib
import os
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest

from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
from digifly_app.core.circuit import CircuitSpec, ConnectomeRef
from digifly_app.core.first_experiment import (
    DATASET, NEURON_IDS, SETTINGS_KEY, STEPS, circuit_issues, experiment_issues,
    tutorial_experiment, values_match,
)
from digifly_app.core.models import ResultRecord
from digifly_app.core.resource_profile import PROFILE_PATH_ENV
from digifly_app.ui.main_window import MainWindow


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv(SETTINGS_ROOT_ENV, str(tmp_path / "settings"))
    monkeypatch.setenv(PROFILE_PATH_ENV, str(tmp_path / "no-profile.json"))
    app = QApplication.instance() or QApplication([])
    value = MainWindow()
    yield value
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Discard)
    value.close()
    value.deleteLater()
    QCoreApplication.sendPostedEvents(value, QEvent.Type.DeferredDelete)
    app.processEvents()


def teaching_circuit():
    return CircuitSpec(connectome=ConnectomeRef("test", "Male CNS v1.0", "", DATASET,
        pair_graph_path="checked/connectivity.sqlite", pair_graph_sha256="a" * 64),
        neuron_ids=NEURON_IDS)


def select(guide, key):
    guide.select(next(i for i, step in enumerate(STEPS) if step.key == key))


def test_recipe_is_valid_and_covers_every_ppt_setting():
    circuit = teaching_circuit()
    config = tutorial_experiment("Student example")
    assert not config.errors(circuit)
    assert not circuit_issues(circuit)
    assert not experiment_issues(config)
    assert config.engine == "neuron" and config.duration_ms == 8
    assert config.temperature_C == 22 and config.recording.target_region == "all"
    assert not config.recording.make_plots and config.recording.current_tracking == "off"
    assert config.conditions[1].name == "No stimulus" and config.conditions[1].stimulus_scale == 0
    assert config.stimuli[0].final_offset_ms == 2
    assert "10001/10010" in STEPS[3].details and "1050014552" in STEPS[3].details
    assert len(NEURON_IDS) == 16 and len(set(NEURON_IDS)) == 16


def test_each_step_has_usable_instructions_without_references_to_removed_controls():
    for step in STEPS:
        assert len(step.instructions.split()) <= 120, step.key
        assert step.details, step.key
        assert "\n\n" in step.instructions, step.key
        assert "does not certify" not in step.instructions
        assert "Physiological calibration" not in step.instructions
        for phrase in ("below", "Use Details", "in Details", "Fill example", "Apply example"):
            assert phrase not in step.instructions, (step.key, phrase)
    assert "0.005 ms" in STEPS[8].details
    assert "3e-6 uS" in STEPS[7].details


def test_earlier_six_cell_walkthrough_cannot_finish_the_expanded_guide():
    circuit = teaching_circuit()
    circuit.neuron_ids = NEURON_IDS[:6]
    assert "16 example" in " ".join(circuit_issues(circuit))


def test_wrong_release_extra_cells_and_custom_overrides_are_not_teaching():
    circuit = teaching_circuit()
    circuit.connectome = replace(circuit.connectome, dataset="manc:v1.2.1")
    assert "other releases" in " ".join(circuit_issues(circuit))
    circuit = teaching_circuit()
    circuit.neuron_ids += ("999",)
    assert "exactly" in " ".join(circuit_issues(circuit))
    circuit = teaching_circuit()
    circuit.neuron_overrides["10001"] = {"cm_uF_cm2": 2}
    assert "overrides" in " ".join(circuit_issues(circuit))
    assert experiment_issues(replace(tutorial_experiment("x"), duration_ms=80))
    circuit = teaching_circuit()
    circuit.connectome = replace(circuit.connectome, pair_graph_path="", pair_graph_sha256="")
    assert "contact locations" in " ".join(circuit_issues(circuit))


def test_comparisons_allow_roundoff_but_not_clock_or_boolean_changes():
    assert values_match(0.05 + 1e-15, 0.05)
    assert not values_match(0.051, 0.05)
    assert not values_match(1, True)
    assert not values_match(float("nan"), 1.0)


def test_first_open_and_all_navigation_never_execute_or_edit(window, monkeypatch):
    guide = window.first_experiment_guide
    before = window._project_state_identity()
    forbidden = lambda *a, **k: pytest.fail("Navigation invoked a consequential action")
    monkeypatch.setattr(window.experiment_page, "request_run", forbidden)
    monkeypatch.setattr(window.experiment_page, "validate_draft", forbidden)
    monkeypatch.setattr(window.overview_page, "refresh", forbidden)
    monkeypatch.setattr(window.data_library_page, "open_neuprint_import", forbidden)
    monkeypatch.setattr(window.circuit_builder_page, "assemble_circuit", forbidden)
    monkeypatch.setattr(window.circuit_builder_page, "review_recent_imports", forbidden)
    for index in range(len(STEPS)):
        guide.select(index)
        guide.open_page()
        guide.check_step()
    assert window._project_state_identity() == before
    assert not guide.finished
    assert window.experiment_page._process is None


def test_pause_resume_and_restart_application_keep_step_not_completion(window):
    guide = window.first_experiment_guide
    select(guide, "experiment")
    guide.pause()
    state = json.loads(window.settings.value(SETTINGS_KEY))
    assert state["step"] == 8 and not state["active"] and not state["finished"]
    from digifly_app.ui.first_experiment import FirstExperimentGuide
    reopened = FirstExperimentGuide(window)
    assert reopened.index == 8 and reopened.isHidden()
    reopened.resume()
    assert not reopened.isHidden()
    assert json.loads(window.settings.value(SETTINGS_KEY))["active"]
    reopened.deleteLater()


def test_pause_before_first_show_keeps_the_requested_page(window):
    app = QApplication.instance()
    guide = window.first_experiment_guide
    guide.pause()
    window.show_page(4)
    window.show()
    QTest.qWait(30)
    app.processEvents()
    assert window.pages.currentWidget() is window.results_page
    assert guide.isHidden() and not guide._page_timer.isActive()


def test_standard_dialog_navigation_spotlight_and_minimum_size(window):
    app = QApplication.instance()
    window.resize(1120, 760)
    window.show()
    guide = window.first_experiment_guide
    app.processEvents()
    assert not guide.isWindow()
    assert guide.parentWidget() is window and "wizard dialog" in guide.accessibleName()
    assert guide.back_button.text() == "Previous"
    assert guide.next_button.text() == "Next"
    assert guide.shade.isVisible() and guide.shade.holes == []
    assert guide.shade.mask().contains(window.rect().center())
    assert window.rect().contains(guide.geometry())
    assert guide.back_button.geometry().top() > guide.instructions.geometry().bottom()
    assert guide.next_button.geometry().top() == guide.back_button.geometry().top()
    # Step navigation reveals a real row, without executing or changing values.
    QTest.mouseClick(guide.next_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    field = window.overview_page.output_edit
    point = field.mapTo(window, field.rect().center())
    assert not guide.shade.mask().contains(point)
    assert not guide.geometry().contains(point)
    assert guide.shade.mask().contains(QPoint(8, 100))
    visible = QApplication.widgetAt(window.mapToGlobal(point))
    assert visible is field
    QTest.mouseClick(field, Qt.MouseButton.LeftButton)
    assert field.hasFocus()
    # A dimmed control cannot take keyboard focus away from the wizard.
    window.update_button.setFocus()
    app.processEvents()
    assert guide.next_button.hasFocus()
    assert "2 of" in guide.progress.text()
    QTest.mouseClick(guide.back_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert guide.index == 0 and "1 of" in guide.progress.text()
    for index in range(len(STEPS)):
        guide.select(index)
        app.processEvents()
        assert window.rect().contains(guide.geometry()), (index, guide.geometry())
        for button in (guide.pause_button, guide.back_button, guide.next_button):
            assert guide.rect().contains(button.geometry()), (index, button.geometry())
    guide.pause()
    app.processEvents()
    assert guide.isHidden() and guide.shade.isHidden()


def test_spotlight_resize_scroll_and_escape_restore_app(window):
    app = QApplication.instance()
    window.show()
    guide = window.first_experiment_guide
    select(guide, "runtime")
    app.processEvents()
    before = window._project_state_identity()
    window.resize(1220, 810)
    app.processEvents()
    assert guide.shade.geometry() == window.rect()
    assert len(guide.shade.holes) == 3
    for target in guide._targets:
        point = target.mapTo(window, target.rect().center())
        assert not guide.shade.mask().contains(point)
    guide.next_button.setFocus()
    QTest.keyClick(guide.next_button, Qt.Key.Key_Escape)
    app.processEvents()
    assert guide.isHidden() and guide.shade.isHidden()
    assert window._project_state_identity() == before


def test_every_step_has_only_instructions_and_standard_navigation(window):
    app = QApplication.instance()
    window.resize(1120, 760)
    window.show()
    guide = window.first_experiment_guide
    before = window._project_state_identity()
    for index, step in enumerate(STEPS):
        guide.select(index)
        app.processEvents()
        assert guide.status.isHidden()
        assert guide.instructions.toPlainText().split() == step.instructions.split()
        assert guide.instructions.verticalScrollBar().maximum() == 0, step.key
        assert {button.text() for button in guide.findChildren(__import__('PySide6.QtWidgets', fromlist=['QPushButton']).QPushButton)} == {
            "Pause guide", "Previous", "Finish" if index == len(STEPS)-1 else "Next"}
        for name in ("FirstExperimentDetailsToggle", "FirstExperimentDetailsPanel", "FirstExperimentStep",
                     "FirstExperimentAction", "FirstExperimentPage", "FirstExperimentCheck"):
            assert guide.findChild(__import__('PySide6.QtWidgets', fromlist=['QWidget']).QWidget, name) is None
        assert guide.instructions.isVisible()
        for control in (guide.instructions, guide.pause_button,
                        guide.back_button, guide.next_button):
            point = control.mapTo(guide, control.rect().topLeft())
            assert guide.rect().contains(point), (step.key, control.objectName())
            assert guide.rect().contains(control.mapTo(guide, control.rect().bottomRight()))
    assert window._project_state_identity() == before


def test_accessible_previous_moves_focus_out_of_newly_dimmed_control(window):
    from PySide6.QtGui import QAccessible, QAccessibleActionInterface
    app = QApplication.instance()
    window.show()
    guide = window.first_experiment_guide
    select(guide, "workspace")
    app.processEvents()
    field = window.overview_page.output_edit
    original = field.text()
    field.setFocus()
    app.processEvents()
    assert field.hasFocus()
    # AX press activates the button without necessarily assigning it focus.
    # That is the actual installed-window route that exposed the retained field.
    actions = QAccessible.queryAccessibleInterface(guide.back_button).actionInterface()
    assert QAccessibleActionInterface.pressAction() in actions.actionNames()
    actions.doAction(QAccessibleActionInterface.pressAction())
    QTest.qWait(150)  # Qt's accessible press uses the real timed button animation.
    app.processEvents()
    assert guide.index == 0 and not guide.shade.holes
    assert not field.hasFocus()
    assert guide.isAncestorOf(app.focusWidget())
    assert field.text() == original


def test_spotlight_layout_preserves_focus_in_a_still_highlighted_control(window):
    app = QApplication.instance()
    window.show()
    guide = window.first_experiment_guide
    select(guide, "workspace")
    app.processEvents()
    field = window.overview_page.output_edit
    field.setFocus()
    app.processEvents()
    guide._layout_spotlight()
    assert field.hasFocus()


@pytest.mark.parametrize('key', ['workspace', 'readiness'])
def test_save_steps_expose_the_real_file_menu_after_shortcut_panel_removal(window, key):
    app = QApplication.instance()
    window.menuBar().setNativeMenuBar(False)
    window.show()
    guide = window.first_experiment_guide
    select(guide, key)
    app.processEvents()
    menu = window.menuBar()
    point = menu.mapTo(window, menu.actionGeometry(menu.actions()[0]).center())
    assert menu.isVisible() and not guide.shade.mask().contains(point)
    assert not guide.geometry().contains(point)
    assert window.childAt(point) is menu


def test_cell_step_exposes_the_view_controls_it_teaches(window):
    app = QApplication.instance()
    window.show()
    guide = window.first_experiment_guide
    select(guide, "cells")
    app.processEvents()
    page = window.circuit_builder_page
    assert len(guide._targets) == 2
    for width in (1120, 1440):
        window.resize(width, 940)
        app.processEvents()
        for control in (page.query_edit, page.assemble_button, page.full_skeletons_button):
            point = control.mapTo(window, control.rect().center())
            assert window.rect().contains(point), control
            assert not guide.shade.mask().contains(point), control
            assert not guide.geometry().contains(point), control
            # The offscreen platform has an 800px virtual display even when
            # testing a wider app. Child hit testing still checks its real mask.
            assert window.childAt(point) is control, control


def test_results_steps_do_not_hide_the_panel_they_teach(window):
    guide = window.first_experiment_guide
    select(guide, "playback")
    assert guide._focus_targets() == [window.results_page.circuit_panel]
    select(guide, "results")
    assert guide._focus_targets() == [window.results_page.figure_panel]


@pytest.mark.parametrize("value", ['not json', '[]', '{"version":1,"step":999}', '{"version":1,"step":true}'])
def test_bad_saved_step_recovers_without_actions(window, value):
    from digifly_app.ui.first_experiment import FirstExperimentGuide
    window.settings.setValue(SETTINGS_KEY, value)
    reopened = FirstExperimentGuide(window)
    assert reopened.index == 0 and not reopened.finished
    reopened.deleteLater()


def test_apply_cancel_and_busy_preserve_every_setting(window, monkeypatch):
    guide = window.first_experiment_guide
    before = window._project_state_identity()
    select(guide, "cells")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.No)
    guide.perform_action()
    assert window._project_state_identity() == before
    monkeypatch.setattr(window, "_update_busy", lambda: True)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: pytest.fail("Busy action opened confirmation"))
    guide.perform_action()
    assert "Wait for active" in guide.status.text()
    assert window._project_state_identity() == before


def bind_teaching_state(window):
    page = window.circuit_builder_page
    page.spec = teaching_circuit()
    page.loaded_morphologies = dict.fromkeys(NEURON_IDS)
    # Isolate source selection only; real actions and actual editor serialization remain in use.
    page.circuit_spec = lambda: page.spec
    window.experiment_page.set_config(tutorial_experiment("Student example"))


def test_explicit_fill_matches_recipe_and_refuses_overrides(window, monkeypatch):
    guide = window.first_experiment_guide
    bind_teaching_state(window)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(guide, "open_page", lambda: None)
    # Real circuit_changed would expect real morphologies; the guide uses the ordinary apply scopes.
    window.circuit_builder_page.circuit_changed.disconnect()
    for key in ("membrane", "connections", "experiment"):
        select(guide, key)
        guide.perform_action()
    assert not circuit_issues(window.circuit_builder_page.spec)
    assert not experiment_issues(window.experiment_page.config())
    assert window.experiment_page.config().name.startswith("First NEURON experiment ")
    assert window.experiment_page._process is None
    previous = window.experiment_page.config().to_dict()
    window.circuit_builder_page.spec.neuron_overrides["10001"] = {"cm_uF_cm2": 2}
    guide.perform_action()
    assert "custom overrides" in guide.status.text()
    assert window.experiment_page.config().to_dict() == previous


@pytest.mark.parametrize("edge_padding", [0, 3_100_000, 9_000_000])
def test_finish_requires_loaded_matching_completed_result(window, tmp_path, edge_padding):
    guide = window.first_experiment_guide
    bind_teaching_state(window)
    select(guide, "results")
    guide.next_step()
    assert not guide.finished
    config = window.experiment_page.config().to_dict()
    circuit = window.circuit_builder_page.spec.to_dict()
    edge_bytes = json.dumps({"chemical_edges": [{"pre_id": "10001", "post_id": "800146",
        "located_contact": {"source": "engineering fixture"}}], "electrical_edges": []}).encode() + b" " * edge_padding
    (tmp_path / "edge_manifest.json").write_bytes(edge_bytes)
    summary = {"status": "complete", "engine": "neuron", "metadata": {
        "Chemical contacts": 1, "Conditions": 2, "Edge manifest SHA-256": hashlib.sha256(edge_bytes).hexdigest()}}
    for name, data in (("experiment.json", config), ("circuit.json", circuit),
                       ("summary.json", summary)):
        (tmp_path / name).write_text(json.dumps(data))
    record = ResultRecord(str(tmp_path / "summary.json"), "cancelled", "", "Example", {}, ())
    page = window.results_page
    page._displayed_result = record
    page._prepared_result = object()
    page._voltage_table = object()
    assert "not a completed" in " ".join(guide._result_issues())
    page._displayed_result = replace(record, status="complete")
    assert not guide._result_issues()
    summary["metadata"]["Chemical contacts"] = 0
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    assert "located chemical contacts" in " ".join(guide._result_issues())
    summary["metadata"]["Chemical contacts"] = 1
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    (tmp_path / "edge_manifest.json").write_bytes(edge_bytes + b" ")
    assert "differs" in " ".join(guide._result_issues())
    (tmp_path / "edge_manifest.json").write_bytes(edge_bytes)
    config["name"] = "Unrelated run"
    (tmp_path / "experiment.json").write_text(json.dumps(config))
    assert "different experiment" in " ".join(guide._result_issues())
    config["name"] = window.experiment_page.config().name
    (tmp_path / "experiment.json").write_text(json.dumps(config))
    (tmp_path / "edge_manifest.json").write_bytes(b" " * 8_000_001)
    assert "invalid" in " ".join(guide._result_issues())
    (tmp_path / "edge_manifest.json").write_bytes(edge_bytes)
    (tmp_path / "experiment.json").write_text(json.dumps(config) + " " * 9_000_000)
    assert not guide._result_issues()
    (tmp_path / "experiment.json").write_text(" " * 2_000_001)
    assert "Could not check" in " ".join(guide._result_issues())
    (tmp_path / "experiment.json").write_text(json.dumps(config))
    guide.next_step()
    assert guide.finished
    assert json.loads(window.settings.value(SETTINGS_KEY))["finished"]
    # Do not let synthetic objects reach real shutdown rendering.
    page._prepared_result = page._voltage_table = None


def test_contact_review_reads_chunks_and_handles_split_utf8():
    import io
    from types import SimpleNamespace
    from digifly_app.core.first_experiment import review_teaching_contacts
    payload = json.dumps({"metadata": {"units": "µm", "count": 123456789},
        "chemical_edges": [{"pre_id": "10001", "post_id": "800146",
            "located_contact": {"label": "µm"}}] * 1200,
        "electrical_edges": []}, ensure_ascii=False).encode()
    sizes = []
    class Chunked(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 64 * 1024
            sizes.append(size)
            return super().read(min(size, 7))
    path = SimpleNamespace(open=lambda mode: Chunked(payload))
    count, digest = review_teaching_contacts(path, NEURON_IDS)
    assert count == 1200 and digest == hashlib.sha256(payload).hexdigest()
    assert len(sizes) > 100


@pytest.mark.parametrize("payload", [
    b'{"chemical_edges":[],"electrical_edges":[]}',
    b'{"chemical_edges":[],"chemical_edges":[],"electrical_edges":[]}',
    b'{"chemical_edges":[,],"electrical_edges":[]}',
    b'{"chemical_edges":[],"electrical_edges":[]} trailing',
    b'{"chemical_edges":[],"electrical_edges":[1]}',
])
def test_contact_review_refuses_malformed_or_incomplete_documents(tmp_path, payload):
    from digifly_app.core.first_experiment import review_teaching_contacts
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        review_teaching_contacts(path, NEURON_IDS)
