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


def _await_saved_inventory(page):
    # New connectome-wide indexing is deliberately asynchronous; a disabled
    # Load button cannot be bypassed by calling its slot in a unit fixture.
    if page._selected_source().server:
        for _ in range(300):
            QApplication.processEvents()
            if page.saved_browser.inventory is not None:
                return
            QTest.qWait(10)
        raise AssertionError('Tiny saved-neuron fixture did not finish indexing')


def test_chemical_kinetics_choice_roundtrips_explicit_source_precedence(tmp_path):
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        assert page._read_chemical_policy().kinetics_policy == "source_or_default"
        page.chemical_kinetics_policy.setCurrentIndex(page.chemical_kinetics_policy.findData("global_override"))
        page.chemical_tau1.setValue(.3)
        policy = page._read_chemical_policy()
        assert policy.kinetics_policy == "global_override"
        assert policy.tau1_ms == .3
        page._set_chemical_policy(type(policy).from_dict(policy.to_dict()))
        assert page.chemical_kinetics_policy.currentData() == "global_override"
        assert "Override every contact" in page.chemical_kinetics_policy.currentText()
    finally:
        page.close()
        application.processEvents()


def test_explicit_downloaded_count_model_roundtrips_all_editable_choices(tmp_path):
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        assert not page.chemical_aggregate_basis.isEnabled()
        for placement in ("aggregate_post_soma", "aggregate_post_ais"):
            page.chemical_placement.setCurrentIndex(page.chemical_placement.findData(placement))
            for basis in ("count_times_default", "per_pair_default"):
                page.chemical_aggregate_basis.setCurrentIndex(page.chemical_aggregate_basis.findData(basis))
                page.chemical_default_weight.setValue(0.002)
                page.apply_chemical_policy()
                policy = page.spec.chemical_synapse_policy
                assert policy.placement_policy == placement and policy.weight_policy == basis
                assert policy.default_weight_uS == 0.002
                assert policy.aggregation_policy == "one_exp2syn_per_pair"
                assert page.chemical_aggregate_basis.isEnabled()
                page._set_chemical_policy(type(policy).from_dict(policy.to_dict()))
                assert page._read_chemical_policy() == policy
        page.chemical_placement.setCurrentIndex(0)
        assert page._read_chemical_policy().weight_policy == "source_or_default_per_contact"
        assert not page.chemical_aggregate_basis.isEnabled()
    finally:
        page.close()
        application.processEvents()


def test_fragment_preview_requires_separate_confirmation_and_keeps_original(tmp_path, monkeypatch):
    from digifly_app.core.swc_quality import analyze_swc
    from digifly_app.ui import circuit_builder as module
    application = QApplication.instance() or QApplication([])
    path = tmp_path / "1.swc"
    path.write_text("1 1 0 0 0 1 -1\n2 2 2 0 0 1 1\n20 2 8 0 0 0.8 -1\n21 2 10 0 0 0.8 20\n")
    original = path.read_bytes()
    report = analyze_swc(path)
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    previews = []
    def decline(message):
        previews.append(message)
        return module.QMessageBox.StandardButton.No
    try:
        monkeypatch.setattr(page, "_confirm_fragment_join", decline)
        assert page._offer_fragment_join(report) == (True, "")
        assert len(list(tmp_path.glob("*.swc"))) == 1
        assert "total volume" in previews[0] and "change electrical conduction" in previews[0]
        monkeypatch.setattr(page, "_confirm_fragment_join", lambda *args: module.QMessageBox.StandardButton.Yes)
        reviewed, joined = page._offer_fragment_join(report)
        assert reviewed and Path(joined).is_file()
        assert path.read_bytes() == original
        morphology = load_swc(NeuronRecord("1", "MN", "test", joined, "custom:test"))
        assert sum(node.parent_id < 0 for node in morphology.nodes) == 1
    finally:
        page.close()
        application.processEvents()


def test_same_size_table_refresh_retains_accessible_items_and_blocks_partial_selection(tmp_path):
    application = QApplication.instance() or QApplication([])
    path = tmp_path / "1.swc"
    path.write_text("1 1 0 0 0 5 -1\n2 1 0 0 10 5 1\n3 3 0 0 50 2 2\n")
    morphology = load_swc(NeuronRecord("1", "control", "synthetic", str(path), "custom:control"))
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        page._populate_table([morphology])
        original = [page.neuron_table.item(0, column) for column in range(4)]
        emissions = []
        page.neuron_table.itemSelectionChanged.connect(lambda: emissions.append(True))
        page.neuron_table.selectRow(0)
        emissions.clear()
        page._populate_table([morphology])
        assert not emissions
        assert all(page.neuron_table.item(0, column) is item for column, item in enumerate(original))
        assert page.neuron_table.item(0, 0).text() == "1"
        assert not page.neuron_table.signalsBlocked()
    finally:
        page.close()
        application.processEvents()


def test_official_disconnected_skeleton_preview_is_explicit_and_unchanged(tmp_path):
    path = tmp_path / "Phase 1/male-cns_v0.9/export_swc/MN/MNad21/164190/164190_release_v09_um.swc"
    path.parent.mkdir(parents=True)
    original = ("# Source: Janelia male-cns v0.9 public skeleton release\n"
                "# x/y/z/r converted from8nm units to micrometers, scale0.008\n"
                "1 1 0 0 0 1 -1\n2 3 1 1 1 1 1\n3 3 2 2 2 1 -1\n4 3 3 3 3 1 3\n")
    path.write_text(original)
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        page.query_edit.setText("164190")
        _await_saved_inventory(page)
        page.assemble_circuit()
        assert page.viewport.neuron_count == 1 and page.viewport.segment_count == 2
        assert "disconnected skeleton(s): preview only; not simulation-ready" in page.viewport_summary.text()
        assert path.read_text() == original
        with pytest.raises(ValueError, match="exactly one root"):
            load_swc(page.loaded_records["164190"])
        page.load_saved_assets()
        assert page.viewport.segment_count == 2
        assert "preview only; not simulation-ready" in page.viewport_summary.text()
    finally:
        page.close()
        application.processEvents()


def test_point_only_release_preview_is_explicit_on_load_and_restore(tmp_path):
    path = tmp_path / "Phase 1/male-cns_v0.9/export_swc/UNKNOWN/Unknown/179143/179143.swc"
    path.parent.mkdir(parents=True)
    path.write_text("# Source: Janelia male-cns v0.9 public skeleton release\n"
                    "# x/y/z/r converted from8nm units to micrometers, scale0.008\n"
                    "1 1 8 16 24 1 -1\n")
    application = QApplication.instance() or QApplication([])
    page = CircuitBuilderPage(_OverviewStub(tmp_path))
    try:
        page.query_edit.setText("179143")
        _await_saved_inventory(page)
        page.assemble_circuit()
        assert page.viewport.neuron_count == 1 and page.viewport.segment_count == 0
        assert "point-only skeleton(s): no released branches; not simulation-ready" in page.viewport_summary.text()
        with pytest.raises(ValueError, match="No drawable SWC segments"):
            load_swc(page.loaded_records["179143"])
        page.load_saved_assets()
        assert "point-only skeleton(s)" in page.viewport_summary.text()
    finally:
        page.close()
        application.processEvents()


@pytest.fixture(autouse=True)
def _isolated_machine_profile(tmp_path, monkeypatch):
    # A developer's real data bindings must not override synthetic SWC fixtures.
    monkeypatch.setenv("DIGIFLY_WORKSTATION_PROFILE", str(tmp_path / "resources-v2.json"))


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
        _await_saved_inventory(page)
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
        assert "Arbor 0.12.2 equation translations" in page.mechanism_capability_label.text()

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
        _await_saved_inventory(page)
        page.assemble_circuit()
        assert page.viewport.neuron_count == 0
        assert not page.download_missing_button.isHidden()
        assert page._missing_neuprint_request is not None
        dataset, selection, expression = page._missing_neuprint_request
        assert dataset == "manc:v1.2.1"
        assert selection.mode == "type_exact"
        assert selection.value == "AN08B098"
        assert expression == "AN08B098"

        incoming = tmp_path / 'completed-download'
        swc = incoming / 'export_swc/AN/AN08B098/16900/16900_neuprint_raw.swc'
        swc.parent.mkdir(parents=True)
        swc.write_text(
            "1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n",
            encoding="utf-8",
        )
        from digifly_app.core.data_library import import_local_source
        resource = import_local_source(profile, incoming, provider='neuprint',
            resource_id='an08b098', source_version='fixture', dataset='manc:v1.2.1',
            profile_path=profile_path)
        swc_root = resource.root / 'source/export_swc'
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
        _await_saved_inventory(page)

        assert dialog_args["preferred_dataset"] == "manc:v1.2.1"
        assert dialog_args["initial_selection"] == selection
        assert page.viewport.neuron_count == 1
        assert page.circuit_spec().neuron_ids == ("16900",)
        assert Path(page.loaded_records['16900'].swc_path).is_relative_to(swc_root.resolve())
        assert page._selected_source().key == 'library:manc:v1.2.1'
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
    male_cns = public / "Phase 1" / "male-cns_v1.0" / "export_swc"
    male_cns.mkdir(parents=True)
    legacy = public / "Phase 1" / "male-cns_v0.9" / "export_swc"
    legacy_swc = legacy / "DN" / "DNp01" / "10001" / "10001_healed.swc"
    legacy_swc.parent.mkdir(parents=True)
    legacy_swc.write_text("# bodyId 10001\n1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n", encoding="utf-8")
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
        assert page._selected_source().key == "library:manc:v1.2.1"
        assert Path(page._selected_source().root) == full.resolve()
        assert len([s for s in page._sources if s.server]) == 9
        # Legacy project references remain available internally, not as peer
        # choices in the new connectome/release controls.
        page.connectome_combo.setCurrentIndex(page.connectome_combo.findData('male-cns:v0.9'))
        assert Path(page._selected_source().root) == legacy.resolve()
        page.query_edit.setText("DNp01")
        _await_saved_inventory(page)
        page.assemble_circuit()
        assert page.circuit_spec().neuron_ids == ("10001",)
        assert page.viewport.neuron_count == 1
        page.refresh_connectomes()
        assert len([s for s in page._sources if s.server]) == 9
        assert page._selected_source().key == "male-cns:v0.9"
        assert Path(page._selected_source().root) == legacy.resolve()
    finally:
        page.close()
        application.processEvents()


def test_primary_sources_prefer_older_app_local_male_cns_over_managed_v1(tmp_path):
    from digifly_app.core.connectomes import ConnectomeCatalog
    from digifly_app.ui.circuit_builder import _primary_circuit_sources, _neuprint_dataset

    root = tmp_path / "source" / "export_swc"
    swc = root / "PSI" / "802401" / "802401_radius_healed.swc"
    swc.parent.mkdir(parents=True)
    swc.write_text("1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n", encoding="utf-8")
    manc = ConnectomeRef("manc:v1.2.1:full-local", "MANC", str(tmp_path / "manc"), "manc_v1.2.1")
    legacy_root = tmp_path / "old" / "export_swc"
    legacy_swc = legacy_root / "DN" / "DNp01" / "10001" / "10001_healed.swc"
    legacy_swc.parent.mkdir(parents=True)
    legacy_swc.write_text("1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n", encoding="utf-8")
    legacy = ConnectomeRef("male-cns:v0.9", "Male CNS v0.9", str(legacy_root), "male-cns_v0.9")
    managed = ConnectomeRef("neuprint:male-cns:v1.0:selection", "selection", str(root), "male-cns:v1.0")
    selected = _primary_circuit_sources((manc, legacy, managed))
    assert len(selected) == 2
    assert selected[1].label == "Male CNS v0.9"
    assert selected[1].key == legacy.key
    assert selected[1].root == legacy.root
    assert _neuprint_dataset(selected[1]) == "male-cns:v0.9"
    assert legacy.label == "Male CNS v0.9"
    catalog = ConnectomeCatalog.scan(selected[1])
    assert catalog.query("DNp01").records[0].neuron_id == "10001"
    assert load_swc(catalog.by_id["10001"]).nodes
    # Missing local v0.9 still permits v1.0 without falsifying its version.
    fallback = _primary_circuit_sources((manc, managed))
    assert fallback[1].key == managed.key
    assert fallback[1].root == managed.root
    assert fallback[1].label == "Male CNS v1.0"


@pytest.mark.parametrize("source_key", (
    "male-cns:v0.9", "male-cns:v1.0",
    "neuprint:male-cns:v0.9:selection", "neuprint:male-cns:v1.0:selection",
    "library:male-cns:v0.9", "library:male-cns:v1.0",
    "library:male-cns:v0.9@https://example.test",
    "library:male-cns:v1.0@https://example.test",
))
def test_male_cns_morphology_defaults_and_resets_to_180_degree_roll(source_key):
    application = QApplication.instance() or QApplication([])
    record = NeuronRecord(
        "57245",
        "AN",
        "AN08B098",
        "/57245.swc",
        source_key,
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
        original_vertices = viewport._vertex_data
        assert viewport.yaw_degrees == DEFAULT_YAW_DEGREES
        assert viewport.roll_degrees == MALE_CNS_ROLL_DEGREES
        assert viewport._vertex_data == original_vertices
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
        assert viewport._vertex_data == original_vertices
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
        _await_saved_inventory(page)
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
        _await_saved_inventory(page)
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
