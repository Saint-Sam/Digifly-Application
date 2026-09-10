from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QVector3D
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton

from digifly_app.core.circuit import (
    CircuitSpec,
    ConnectomeRef,
    HodgkinHuxleySpec,
    NeuronQuery,
)
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.data_library import ManagedResource
from digifly_app.core.mechanisms import (
    ChannelAssignment,
    MembraneMechanismSpec,
    membrane_profile,
)
from digifly_app.core.morphology import (
    Morphology,
    SwcNode,
    SwcSegment,
    load_swc,
    save_custom_morphology,
)
from digifly_app.core.resource_profile import (
    AccessMode,
    ResourceBinding,
    ResourceKind,
    ResourceProfile,
    make_default_profile,
)
from digifly_app.ui.circuit_builder import CircuitBuilderPage
from digifly_app.ui.circuit_viewport import (
    CircuitViewport,
    DEFAULT_PITCH_DEGREES,
    DEFAULT_YAW_DEGREES,
    DISPLAY_MODE_FULL_SKELETONS,
    DISPLAY_MODE_SOMA_POINTS,
    DEFAULT_ROLL_DEGREES,
    MALE_CNS_ROLL_DEGREES,
    REFERENCE_CAMERA_FOCAL_POINT,
    REFERENCE_CAMERA_POSITION,
    REFERENCE_CAMERA_VIEW_UP,
    SELECTED_COMPARTMENT_COLOR,
    SELECTED_NEURON_COLOR,
)
from digifly_app.ui import circuit_viewport as circuit_viewport_module


class _OverviewStub:
    def __init__(self, root: Path):
        self.workspace_edit = QLineEdit(str(root))


def _pair_ui_workspace(tmp_path: Path) -> Path:
    public = tmp_path / "Digifly Public"
    swc_root = public / "Phase 1" / "manc_v1.2.1" / "export_swc"
    for neuron_id in ("100", "200", "300", "400"):
        path = swc_root / "IN" / "PairTest" / neuron_id / f"{neuron_id}_healed.swc"
        path.parent.mkdir(parents=True, exist_ok=True)
        x = int(neuron_id) / 100.0
        path.write_text(
            f"1 1 {x} 0 0 1 -1\n2 2 {x + 1} 0 0 0.5 1\n",
            encoding="utf-8",
        )
    edge_root = swc_root / "edges"
    edge_root.mkdir(parents=True)
    with sqlite3.connect(edge_root / "master_edges_cache.sqlite") as connection:
        connection.execute("CREATE TABLE edges (pre_id INTEGER, post_id INTEGER)")
        connection.executemany(
            "INSERT INTO edges VALUES (?, ?)",
            ((100, 200), (100, 200), (200, 100)),
        )

    bundle = (
        public
        / "Phase 2_Arbor_staging"
        / "Projects"
        / "Escape-SIZ"
        / "arbor_inputs"
        / "giant_fiber_ablation"
    )
    bundle.mkdir(parents=True)
    gap_csv = bundle / "gap_contacts_arbor.csv"
    with gap_csv.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("pre_id", "post_id", "g_uS"))
        writer.writeheader()
        writer.writerow({"pre_id": 100, "post_id": 200, "g_uS": 0.001})
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "kind": "gap_contacts",
                        "path": gap_csv.name,
                        "row_count": 1,
                        "selected_neuron_ids": [100, 200, 300],
                        "sha256": hashlib.sha256(gap_csv.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return public


def test_circuit_builder_assembles_local_swc_and_stores_compartment_override(tmp_path):
    swc = (
        tmp_path
        / "Phase 1"
        / "manc_v1.2.1"
        / "export_swc"
        / "DN"
        / "DNp01"
        / "10000"
        / "10000_axodendro_with_synapses.swc"
    )
    swc.parent.mkdir(parents=True)
    swc.write_text("1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n", encoding="utf-8")
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        assert page.selected_engine_key() == "arbor"
        assert page.viewport.display_mode == DISPLAY_MODE_SOMA_POINTS
        assert page.soma_points_button.isChecked() is True
        assert page.full_skeletons_button.isChecked() is False
        page.query_edit.setText("10000")
        page.assemble_circuit()
        assert page.viewport.neuron_count == 1
        assert page.viewport.segment_count == 1
        assert page.viewport.accessibleName() == "Circuit visualization viewport"
        assert page.viewport.yaw_degrees == DEFAULT_YAW_DEGREES
        assert page.viewport.pitch_degrees == DEFAULT_PITCH_DEGREES
        assert page.viewport_summary.wordWrap() is True
        assert "Soma points (default)" in page.viewport_controls_hint.text()
        assert "Full skeletons" in page.viewport_controls_hint.text()
        assert page.viewport_controls_hint.textInteractionFlags() & (
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        assert page.circuit_spec().neuron_ids == ("10000",)
        assert page.circuit_spec().membrane.active_channels == ()

        page.full_skeletons_button.click()
        assert page.viewport.display_mode == DISPLAY_MODE_FULL_SKELETONS
        assert page.full_skeletons_button.isChecked() is True
        assert page.soma_points_button.isChecked() is False
        assert "Ctrl+Shift+left-drag" in page.viewport_controls_hint.text()
        page.viewport.focus_neuron("10000")
        escape_profile = page.channel_profile_combo.findData("escape_siz_para_hh_k")
        page.channel_profile_combo.setCurrentIndex(escape_profile)
        assert page.channel_checks["para"].isChecked()
        assert page.channel_branch_editors["para"].value() == 0.005
        assert page.hh_editors["soma_gnabar_s_cm2"].value() == 0.0
        assert page.hh_editors["soma_gkbar_s_cm2"].value() == 0.036
        assert page.hh_editors["ena_mV"].value() == 50.0
        assert page.hh_editors["ek_mV"].value() == -77.0
        assert page.hh_editors["celsius_C"].value() == 6.3
        page.apply_neuron_override()
        assert page.spec.neuron_mechanism_overrides["10000"]["channels"]["para"][
            "enabled"
        ]
        segment = page.viewport.morphologies["10000"].segments[0]
        midpoint = tuple((a + b) / 2.0 for a, b in zip(segment.parent, segment.child))
        projected = page.viewport._project(midpoint, page.viewport._mvp())
        assert projected is not None
        picked = page.viewport._pick(QPointF(*projected))
        assert picked is not None
        assert (picked[0], picked[1].child_id) == ("10000", 2)

        page.viewport.selected_compartments = {2}
        page._compartments_changed("10000", (2,))
        assert "electric magenta" in page.viewport_controls_hint.text()
        assert "1 compartment(s)" in page.viewport_controls_hint.text()
        page.apply_compartment_overrides()
        assert "2" in page.circuit_spec().compartment_overrides["10000"]
        assert (
            page.circuit_spec()
            .compartment_mechanism_overrides["10000"]["2"]["channels"]["para"][
                "suffix"
            ]
            == "na16a"
        )

        page.mass_apply_to_loaded_neurons()
        assert page.spec.neuron_mechanism_overrides["10000"]["profile_key"] == (
            "escape_siz_para_hh_k"
        )

        page.gap_mode_combo.setCurrentIndex(
            page.gap_mode_combo.findData("heterotypic_rectifying")
        )
        page.apply_gap_policy()
        assert page.spec.gap_junction_policy.mode == "heterotypic_rectifying"
        assert "not Arbor-qualified" in page.mechanism_capability_label.text()

        page.query_edit.setText("99999")
        page.query_edit.textEdited.emit("99999")
        assert page.viewport.neuron_count == 0
        assert page.circuit_spec().neuron_ids == ()

        with pytest.raises(ValueError, match="Unsupported"):
            page.set_selected_engine_key("made-up-engine")
    finally:
        page.close()
        application.processEvents()


def test_hh_settings_use_expandable_sections_without_losing_values(tmp_path):
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        assert tuple(page.hh_sections) == (
            "membrane_profile",
            "sodium_channels",
            "potassium_channels",
            "calcium_channels",
            "passive_properties",
            "soma_hh",
            "branch_hh",
            "chemical_synapses",
            "gap_junctions",
            "apply_save",
        )
        assert page.hh_sections["membrane_profile"].is_expanded
        assert not page.hh_sections["membrane_profile"].body.isHidden()
        assert all(
            not section.is_expanded
            for key, section in page.hh_sections.items()
            if key != "membrane_profile"
        )

        sodium = page.hh_sections["sodium_channels"]
        assert sodium.body.isHidden()
        assert "Collapsed" in sodium.toggle_button.accessibleDescription()
        sodium.toggle_button.click()
        assert sodium.is_expanded
        assert not sodium.body.isHidden()
        assert "Expanded" in sodium.toggle_button.accessibleDescription()

        page.channel_soma_editors["augustin_nat"].setValue(0.314)
        sodium.toggle_button.click()
        sodium.toggle_button.click()
        assert page.channel_soma_editors["augustin_nat"].value() == pytest.approx(0.314)

        passive = page.hh_sections["passive_properties"]
        passive.set_expanded(True)
        page.hh_editors["cm_uF_cm2"].setValue(1.7)
        passive.set_expanded(False)
        passive.set_expanded(True)
        assert page.hh_editors["cm_uF_cm2"].value() == pytest.approx(1.7)
    finally:
        page.close()
        application.processEvents()


def test_missing_manc_type_uses_prefilled_neuprint_import_and_loads_result(
    tmp_path, monkeypatch
):
    public = tmp_path / "Digifly Public"
    (public / "Phase 1" / "manc_v1.2.1" / "export_swc").mkdir(parents=True)
    (public / "README.md").write_text("Digifly Public\n", encoding="utf-8")
    profile = make_default_profile(
        workspace_root=public,
        output_root=tmp_path / "workspace" / "runs",
        managed_data_root=tmp_path / "workspace" / "data",
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(profile_path))
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(public))
    try:
        page.query_edit.setText("AN08B098")
        page.assemble_circuit()
        assert page.viewport.neuron_count == 0
        assert not page.download_missing_button.isHidden()
        assert page._missing_neuprint_request is not None
        dataset, selection, expression = page._missing_neuprint_request
        assert dataset == "manc:v1.2.1"
        assert selection.mode == "type_exact"
        assert selection.value == "AN08B098"
        assert expression == "AN08B098"

        bundle = tmp_path / "workspace" / "data" / "neuprint-bundle"
        swc_root = bundle / "source" / "export_swc"
        swc = swc_root / "AN" / "AN08B098" / "16900" / "16900_neuprint_raw.swc"
        swc.parent.mkdir(parents=True)
        swc.write_text(
            "1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n",
            encoding="utf-8",
        )
        binding = ResourceBinding(
            "an08b098",
            ResourceKind.MORPHOLOGY_SOURCE,
            str(swc_root),
            AccessMode.READ_ONLY,
            "AN08B098",
            False,
            {
                "dataset": "manc:v1.2.1",
                "connectome_key": "neuprint:manc:v1.2.1:an08b098",
            },
        )
        ResourceProfile(
            profile_id=profile.profile_id,
            resources=(*profile.resources, binding),
            label=profile.label,
        ).save(profile_path, replace=True)
        resource = ManagedResource(
            "neuprint",
            "an08b098",
            "fixture",
            bundle,
            bundle / "digifly-resource.json",
            1,
            swc.stat().st_size,
            1,
            "2026-08-31T00:00:00Z",
            "an08b098",
        )
        dialog_args: dict[str, object] = {}

        class _ImportedDialog(QDialog):
            resource_imported = Signal(object)

            def __init__(self, *args, **kwargs):
                super().__init__(kwargs.get("parent"))
                dialog_args.update(kwargs)

            def exec(self):
                self.resource_imported.emit(resource)
                return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            "digifly_app.ui.circuit_builder.NeuPrintImportDialog",
            _ImportedDialog,
        )
        reviews: list[bool] = []
        changes: list[bool] = []
        monkeypatch.setattr(page, "review_recent_imports", lambda: reviews.append(True))
        page.managed_data_changed.connect(lambda: changes.append(True))
        page.open_missing_neuprint_import()

        assert dialog_args["preferred_dataset"] == "manc:v1.2.1"
        assert dialog_args["initial_selection"] == selection
        assert page.viewport.neuron_count == 1
        assert page.circuit_spec().neuron_ids == ("16900",)
        assert Path(page._selected_source().root).resolve() == swc_root.resolve()
        assert page.download_missing_button.isHidden()
        assert reviews == [True]
        assert changes == [True]
    finally:
        page.close()
        application.processEvents()


def test_matching_machine_profile_prefers_legacy_full_manc_source(tmp_path, monkeypatch):
    public = tmp_path / "Digifly Public"
    subset = public / "Phase 1" / "manc_v1.2.1" / "export_swc"
    subset.mkdir(parents=True)
    male_cns = public / "Phase 1" / "male-cns_v0.9" / "export_swc"
    male_cns.mkdir(parents=True)
    full = tmp_path / "Digifly_NEW" / "Phase 2" / "data" / "export_swc"
    swc = full / "DN" / "DNp01" / "10000" / "10000_axodendro_with_synapses.swc"
    swc.parent.mkdir(parents=True)
    swc.write_text("1 1 0 0 0 1 -1\n", encoding="utf-8")
    (full / ".phase2_export_index.json").write_text("{}\n", encoding="utf-8")
    (full / "edges").mkdir()
    (full / "edges" / "master_edges_cache.sqlite").write_bytes(b"fixture")
    profile = make_default_profile(
        workspace_root=public,
        output_root=tmp_path / "runs",
        morphology_sources=(("external-swcs", full, "External morphology"),),
    )
    profile_path = profile.save(tmp_path / "resources-v2.json")
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(profile_path))
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(public))
    try:
        assert page._selected_source().key == "manc:v1.2.1:full-local"
        assert page.connectome_combo.currentText() == "MANC v1.2.1 · full local SWCs"
        assert page.connectome_combo.count() == 2
        assert [
            page.connectome_combo.itemText(index)
            for index in range(page.connectome_combo.count())
        ] == ["MANC v1.2.1 · full local SWCs", "Male CNS v0.9"]
    finally:
        page.close()
        application.processEvents()


def test_male_cns_morphology_defaults_and_resets_to_180_degree_roll():
    application = QApplication.instance() or QApplication([])
    record = NeuronRecord(
        "57245",
        "AN",
        "AN08B098",
        "/57245.swc",
        "male-cns:v0.9",
    )
    morphology = Morphology(
        record,
        (),
        (SwcSegment(2, 1, (2.0, 1.0, 0.0), (0.0, 0.0, 0.0), 0.5, 2),),
        (0.0, 2.0, 0.0, 1.0, 0.0, 0.0),
    )
    viewport = CircuitViewport()
    try:
        viewport.resize(640, 480)
        viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
        viewport.set_morphologies((morphology,))
        assert viewport.yaw_degrees == DEFAULT_YAW_DEGREES
        assert viewport.roll_degrees == MALE_CNS_ROLL_DEGREES
        male_projection = viewport._project((2.0, 1.0, 0.0), viewport._mvp())
        viewport.yaw_degrees = 23.0
        viewport.roll_degrees = 42.0
        viewport.setFocus()
        QTest.keyClick(viewport, Qt.Key.Key_R)
        assert viewport.yaw_degrees == DEFAULT_YAW_DEGREES
        assert viewport.roll_degrees == MALE_CNS_ROLL_DEGREES

        manc = Morphology(
            NeuronRecord("16900", "AN", "AN08B098", "/16900.swc", "manc:v1.2.1"),
            (),
            morphology.segments,
            morphology.bounds,
        )
        viewport.set_morphologies((manc,))
        assert viewport.yaw_degrees == DEFAULT_YAW_DEGREES
        assert viewport.roll_degrees == DEFAULT_ROLL_DEGREES
        manc_projection = viewport._project((2.0, 1.0, 0.0), viewport._mvp())
        assert male_projection is not None
        assert manc_projection is not None
        assert male_projection[0] == pytest.approx(viewport.width() - manc_projection[0])
        assert male_projection[1] == pytest.approx(viewport.height() - manc_projection[1])
    finally:
        viewport.close()
        application.processEvents()


def test_two_neuron_pair_panel_tracks_recency_overrides_and_edge_evidence(tmp_path):
    public = _pair_ui_workspace(tmp_path)
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(public))
    try:
        page.query_edit.setText("100, 200, 300, 400")
        page.assemble_circuit()
        assert page.viewport.neuron_count == 4
        assert page.pair_panel.isHidden()

        page.viewport.set_neuron_selection(("100", "200"))
        assert tuple(page.viewport.selected_neuron_ids) == ("100", "200")
        assert not page.pair_panel.isHidden()
        readout = page.pair_connection_readout.text()
        assert "2 100 → 200" in readout
        assert "1 200 → 100" in readout
        assert "1 contact row" in readout
        assert "bidirectional" in readout
        assert page.chemical_pair_check.isEnabled()
        assert page.gap_pair_check.isEnabled()
        assert page.chemical_pair_check.isChecked()
        assert page.gap_pair_check.isChecked()

        page.chemical_pair_check.click()
        page.gap_pair_check.click()
        override = page.spec.connection_override("200", "100")
        assert override is not None
        assert override.chemical_enabled is False
        assert override.gap_junction_enabled is False
        restored = CircuitSpec.from_dict(page.circuit_spec().to_dict())
        assert restored.connection_override("100", "200") == override

        # A third selection advances the two-item recency window rather than
        # leaving an ambiguous three-neuron panel.
        page.viewport.toggle_neuron_selection("300")
        assert tuple(page.viewport.selected_neuron_ids) == ("200", "300")
        assert not page.pair_panel.isHidden()
        assert "zero in the loaded source" in page.pair_connection_readout.text()
        assert not page.chemical_pair_check.isEnabled()
        assert not page.gap_pair_check.isEnabled()

        # Command/Control-toggle semantics deselect an already selected cell.
        page.viewport.toggle_neuron_selection("300")
        assert tuple(page.viewport.selected_neuron_ids) == ("200",)
        assert page.pair_panel.isHidden()

        # A neuron outside the validated Arbor bundle is unknown, not a gap zero.
        page.viewport.set_neuron_selection(("300", "400"))
        assert "data unavailable" in page.pair_connection_readout.text()
        assert "unknown, not zero" in page.pair_connection_readout.text()
        assert not page.gap_pair_check.isEnabled()

        page.viewport.setFocus()
        QTest.keyClick(page.viewport, Qt.Key.Key_Escape)
        assert tuple(page.viewport.selected_neuron_ids) == ()
        assert page.pair_panel.isHidden()

        page.viewport.set_neuron_selection(("100", "200"))
        page.findChild(QPushButton, "ClearPairSelectionButton").click()
        assert tuple(page.viewport.selected_neuron_ids) == ()
        assert page.pair_panel.isHidden()
    finally:
        page.close()
        application.processEvents()


def test_ui_preserves_advanced_channel_parameters_and_requires_explicit_apply(tmp_path):
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        rich = MembraneMechanismSpec(
            profile_key="custom",
            channels={
                "cacophony": ChannelAssignment(
                    "cacophony",
                    "cav21cac",
                    enabled=True,
                    soma_gbar_s_cm2=0.0002,
                    branch_gbar_s_cm2=0.0001,
                    parameters={"q10": 1.5, "celsius_ref": 21.0},
                )
            },
        )
        page._set_membrane(rich)
        assert page._read_membrane().channels["cacophony"].parameters == {
            "q10": 1.5,
            "celsius_ref": 21.0,
        }

        page.hh_editors["cm_uF_cm2"].setValue(1.7)
        assert page.channel_profile_combo.currentData() == "custom"
        page.gap_mode_combo.setCurrentIndex(
            page.gap_mode_combo.findData("heterotypic_rectifying")
        )
        unapplied = page.circuit_spec()
        assert unapplied.hh.cm_uF_cm2 == 1.0
        assert unapplied.gap_junction_policy.mode == "none"

        page.apply_cell_set_defaults()
        assert page.spec.hh.cm_uF_cm2 == 1.7
        assert page.spec.gap_junction_policy.mode == "none"
        page.apply_gap_policy()
        assert page.spec.gap_junction_policy.mode == "heterotypic_rectifying"
        assert page.spec.gap_junction_policy.effective_closed_floor == 0.2
    finally:
        page.close()
        application.processEvents()


def test_mixed_segment_design_requires_a_deliberate_edit_before_replacement(tmp_path):
    swc = (
        tmp_path
        / "Phase 1"
        / "manc_v1.2.1"
        / "export_swc"
        / "DN"
        / "DNp01"
        / "10000"
        / "10000_healed.swc"
    )
    swc.parent.mkdir(parents=True)
    swc.write_text(
        "1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n3 2 2 0 0 0.5 2\n",
        encoding="utf-8",
    )
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        page.query_edit.setText("10000")
        page.assemble_circuit()
        page.viewport.focus_neuron("10000")
        page.spec.apply_compartment_override(
            "10000", (2,), HodgkinHuxleySpec(cm_uF_cm2=1.1).to_dict()
        )
        page.spec.apply_compartment_override(
            "10000", (3,), HodgkinHuxleySpec(cm_uF_cm2=1.2).to_dict()
        )
        page.viewport.selected_compartments = {2, 3}
        page._compartments_changed("10000", (2, 3))
        assert page._mixed_selection_design is True
        assert page.apply_compartments_button.isEnabled() is False

        page.hh_editors["cm_uF_cm2"].setValue(1.4)
        assert page._mixed_selection_design is False
        assert page.apply_compartments_button.isEnabled() is True
    finally:
        page.close()
        application.processEvents()


def test_saved_cell_set_restores_exact_ids_instead_of_replaying_query(tmp_path):
    swc_root = tmp_path / "Phase 1" / "manc_v1.2.1" / "export_swc" / "DN" / "DNp01"
    for neuron_id in ("10000", "10001"):
        path = swc_root / neuron_id / f"{neuron_id}_healed.swc"
        path.parent.mkdir(parents=True)
        path.write_text("1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n", encoding="utf-8")
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        spec = CircuitSpec(
            connectome=ConnectomeRef(
                "manc:v1.2.1",
                "MANC v1.2.1",
                str(tmp_path / "Phase 1" / "manc_v1.2.1" / "export_swc"),
                "manc_v1.2.1",
            ),
            query=NeuronQuery("family:DN", 64),
            neuron_ids=("10000",),
        )
        page.set_circuit_spec(spec)
        page.load_saved_assets()
        assert tuple(page.loaded_records) == ("10000",)
        assert page.query_edit.text() == "family:DN"
        page.spec.morphology_sha256["10000"] = "0" * 64
        with pytest.raises(ValueError, match="identity changed"):
            page.load_saved_assets()
    finally:
        page.close()
        application.processEvents()


def test_saved_source_identity_does_not_substitute_same_key_at_another_root(tmp_path):
    discovered = tmp_path / "Phase 1" / "manc_v1.2.1" / "export_swc"
    archived = tmp_path / "archived-export-swc"
    for root, x in ((discovered, 1), (archived, 9)):
        path = root / "DN" / "DNp01" / "10000" / "10000_healed.swc"
        path.parent.mkdir(parents=True)
        path.write_text(f"1 1 0 0 0 1 -1\n2 2 {x} 0 0 0.5 1\n", encoding="utf-8")
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        spec = CircuitSpec(
            connectome=ConnectomeRef("manc:v1.2.1", "Archived MANC", str(archived), "manc_v1.2.1"),
            neuron_ids=("10000",),
        )
        page.set_circuit_spec(spec)
        page.load_saved_assets()
        assert page._selected_source().root == str(archived)
        assert page.loaded_morphologies["10000"].nodes[1].x == 9.0
    finally:
        page.close()
        application.processEvents()


def test_overlapping_segment_pick_uses_projected_depth():
    application = QApplication.instance() or QApplication([])

    def morphology(neuron_id: str, z: float) -> Morphology:
        record = NeuronRecord(neuron_id, "IN", "test", f"/{neuron_id}.swc", "test")
        segment = SwcSegment(2, 1, (1.0, 0.0, z), (-1.0, 0.0, z), 0.5, 2)
        return Morphology(record, (), (segment,), (-1.0, 1.0, 0.0, 0.0, z, z))

    viewport = CircuitViewport()
    viewport.resize(632, 286)
    morphologies = (morphology("back", -1.0), morphology("front", 1.0))
    viewport.set_morphologies(morphologies)
    viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
    mvp = viewport._mvp()
    projected = {
        item.record.neuron_id: viewport._project_with_depth((0.0, 0.0, item.center[2]), mvp)
        for item in morphologies
    }
    expected = min(projected, key=lambda key: projected[key][2])
    point = projected[expected]
    picked = viewport._pick(QPointF(point[0], point[1]))
    assert picked is not None
    assert picked[0] == expected
    viewport.close()
    application.processEvents()


def test_default_view_uses_ablation_notebook_camera_right_and_up():
    application = QApplication.instance() or QApplication([])
    viewport = CircuitViewport()
    viewport.resize(632, 286)
    focal = QVector3D(*REFERENCE_CAMERA_FOCAL_POINT)
    position = QVector3D(*REFERENCE_CAMERA_POSITION)
    forward = (focal - position).normalized()
    view_up = QVector3D(*REFERENCE_CAMERA_VIEW_UP)
    up = (view_up - forward * QVector3D.dotProduct(view_up, forward)).normalized()
    right = QVector3D.crossProduct(forward, up).normalized()
    viewport.scene_center = focal
    viewport.scene_radius = 1.0
    viewport.distance = 10.0
    mvp = viewport._mvp()
    center_screen = viewport._project((focal.x(), focal.y(), focal.z()), mvp)
    right_point = focal + right
    up_point = focal + up
    right_screen = viewport._project((right_point.x(), right_point.y(), right_point.z()), mvp)
    up_screen = viewport._project((up_point.x(), up_point.y(), up_point.z()), mvp)
    assert center_screen is not None and right_screen is not None and up_screen is not None
    assert right_screen[0] > center_screen[0]
    assert abs(right_screen[1] - center_screen[1]) < 1e-4
    assert up_screen[1] < center_screen[1]
    assert abs(up_screen[0] - center_screen[0]) < 1e-4
    viewport.close()
    application.processEvents()


def test_soma_point_mode_is_default_counts_points_picks_and_preserves_compartments():
    application = QApplication.instance() or QApplication([])

    def morphology(neuron_id: str, x: float) -> Morphology:
        record = NeuronRecord(neuron_id, "IN", "point-test", f"/{neuron_id}.swc", "test")
        nodes = (
            SwcNode(1, 1, x, 0.0, 0.0, 1.0, -1),
            SwcNode(2, 2, x + 1.0, 0.0, 0.0, 0.5, 1),
        )
        segment = SwcSegment(
            2,
            1,
            (x + 1.0, 0.0, 0.0),
            (x, 0.0, 0.0),
            0.5,
            2,
        )
        return Morphology(record, nodes, (segment,), (x, x + 1.0, 0.0, 0.0, 0.0, 0.0))

    viewport = CircuitViewport()
    viewport.resize(632, 286)
    viewport.set_morphologies((morphology("left", -10.0), morphology("right", 10.0)))
    assert viewport.display_mode == DISPLAY_MODE_SOMA_POINTS
    assert viewport.soma_point_count == 2
    assert "soma-point view" in viewport.accessibleDescription()

    right_location = viewport.soma_location("right")
    assert right_location is not None
    projected = viewport._project(right_location.point, viewport._mvp())
    assert projected is not None
    assert viewport._pick_soma(QPointF(*projected)) == "right"

    viewport.focus_neuron("right", isolate=True)
    viewport.selected_compartments = {2}
    viewport._rebuild_selection_data()
    viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
    assert viewport.selected_neuron_id == "right"
    assert viewport.isolated is True
    assert viewport.selected_compartments == {2}
    assert "full SWC skeleton view" in viewport.accessibleDescription()
    viewport.close()
    application.processEvents()


def test_box_selection_adds_projected_compartments_and_magenta_is_reserved():
    application = QApplication.instance() or QApplication([])
    record = NeuronRecord("box", "IN", "box-test", "/box.swc", "test")
    segments = (
        SwcSegment(2, 1, (-8.0, 0.0, 0.0), (-2.0, 0.0, 0.0), 0.5, 2),
        SwcSegment(3, 1, (8.0, 0.0, 0.0), (2.0, 0.0, 0.0), 0.5, 2),
    )
    morphology = Morphology(record, (), segments, (-8.0, 8.0, 0.0, 0.0, 0.0, 0.0))
    viewport = CircuitViewport()
    viewport.resize(632, 286)
    viewport.set_morphologies((morphology,))
    viewport.focus_neuron("box", isolate=True)
    midpoint = tuple(
        (a + b) / 2.0 for a, b in zip(segments[0].parent, segments[0].child)
    )
    projected = viewport._project(midpoint, viewport._mvp())
    assert projected is not None
    viewport.selected_compartments = {3}
    assert viewport._select_compartments_in_rect(
        QRectF(projected[0] - 3.0, projected[1] - 3.0, 6.0, 6.0)
    ) == 0
    assert viewport.selected_compartments == {3}
    viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
    projected = viewport._project(midpoint, viewport._mvp())
    assert projected is not None
    added = viewport._select_compartments_in_rect(
        QRectF(projected[0] - 3.0, projected[1] - 3.0, 6.0, 6.0)
    )
    assert added == 1
    assert viewport.selected_compartments == {2, 3}
    assert SELECTED_COMPARTMENT_COLOR not in viewport._palette
    assert SELECTED_COMPARTMENT_COLOR != SELECTED_NEURON_COLOR
    assert viewport._box_selection_requested(
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    )
    assert not viewport._box_selection_requested(Qt.KeyboardModifier.ShiftModifier)
    viewport.close()
    application.processEvents()


def test_large_multi_neuron_navigation_uses_smaller_connected_preview(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(circuit_viewport_module, "INTERACTION_PREVIEW_THRESHOLD", 10)
    monkeypatch.setattr(circuit_viewport_module, "INTERACTION_PREVIEW_SEGMENT_BUDGET", 12)
    record = NeuronRecord("lod", "IN", "lod-test", "/lod.swc", "test")
    segments = tuple(
        SwcSegment(
            index + 2,
            index + 1,
            (float(index + 1), 0.0, 0.0),
            (float(index), 0.0, 0.0),
            0.5,
            2,
        )
        for index in range(600)
    )
    morphology = Morphology(record, (), segments, (0.0, 600.0, 0.0, 0.0, 0.0, 0.0))
    viewport = CircuitViewport()
    viewport.set_morphologies((morphology,))
    viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
    assert 0 < viewport.interaction_preview_segment_count < viewport.segment_count
    viewport._begin_interaction_preview()
    assert viewport._interaction_preview is True
    viewport.distance = viewport.scene_radius * 0.1
    viewport._begin_interaction_preview()
    assert viewport._interaction_preview is False
    visible_at_deep_zoom = viewport.exact_visible_segment_count()
    assert 0 < visible_at_deep_zoom < viewport.segment_count
    viewport.focus_neuron("lod", isolate=True)
    assert viewport._interaction_preview is False
    assert viewport.interaction_preview_segment_count <= 256
    viewport.close()
    application.processEvents()


def test_custom_bundle_discovers_and_restores_verified_hh_draft(tmp_path, monkeypatch):
    source = tmp_path / "source" / "10000_healed.swc"
    source.parent.mkdir(parents=True)
    source.write_text("1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n", encoding="utf-8")
    record = NeuronRecord("10000", "DN", "DNp01", str(source), "manc:v1.2.1")
    morphology = load_swc(record)
    draft = CircuitSpec(connectome=ConnectomeRef("manc:v1.2.1", "MANC", str(source.parent)))
    draft.apply_compartment_override("10000", (2,), {"branch_gnabar_s_cm2": 0.05})
    draft.membrane = membrane_profile("phase2_para_shab")
    draft.apply_compartment_mechanism_override("10000", (2,), draft.membrane)
    library = tmp_path / "library"
    save_custom_morphology(
        morphology,
        draft,
        selected_engine="arbor",
        library_root=library,
        label="verified",
    )
    monkeypatch.setattr("digifly_app.ui.circuit_builder.morphology_library_root", lambda: library)
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path / "empty-workspace"))
    try:
        assert page._selected_source().dataset == "custom"
        page.query_edit.setText("10000")
        page.assemble_circuit()
        assert page.viewport.neuron_count == 1
        assert page.spec.compartment_overrides["10000"]["2"]["branch_gnabar_s_cm2"] == 0.05
        restored = page.spec.compartment_mechanism_overrides["10000"]["2"]
        assert restored["channels"]["shab"]["suffix"] == "kv21shab"
        assert restored["channels"]["shab"]["enabled"] is True
    finally:
        page.close()
        application.processEvents()
