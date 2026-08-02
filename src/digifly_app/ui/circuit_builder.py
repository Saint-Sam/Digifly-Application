from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from digifly_app.core.circuit import (
    CircuitSpec,
    ConnectomeRef,
    HH_PARAMETER_DEFINITIONS,
    HodgkinHuxleySpec,
    NeuronQuery,
)
from digifly_app.core.connectomes import ConnectomeCatalog, NeuronRecord, discover_connectomes
from digifly_app.core.morphology import (
    Morphology,
    load_custom_biophysics,
    load_swc,
    morphology_library_root,
    save_custom_morphology,
    sha256_file,
)
from .circuit_viewport import CircuitViewport
from .widgets import Card


CIRCUIT_BUILDER_WORKFLOW = "circuit_builder_v1"


ENGINE_PROFILES = (
    (
        "arbor",
        "Arbor",
        "Default design target; validated for specific staged Phase 2 Arbor workflows.",
    ),
    ("neuron", "NEURON", "Reference multicompartment target for native Digifly mechanisms."),
    ("bmtk", "BMTK / SONATA", "Future SONATA/BioNet adapter target; no runnable plan in this slice."),
)


def _source_identity(source: ConnectomeRef) -> tuple[str, str, str]:
    return (
        source.key,
        source.dataset,
        str(Path(source.root).expanduser().resolve()),
    )


def _header() -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(5)
    eyebrow = QLabel("MORPHOLOGY CELL-SET DESIGN · BACKEND-UNBOUND")
    eyebrow.setObjectName("Eyebrow")
    title = QLabel("Build a morphology-backed cell-set preview")
    title.setObjectName("PageTitle")
    detail = QLabel(
        "Choose an SWC source, resolve neurons by ID/type/family, draft membrane physics, and inspect SWC segments before an execution adapter builds a model."
    )
    detail.setObjectName("Muted")
    detail.setWordWrap(True)
    layout.addWidget(eyebrow)
    layout.addWidget(title)
    layout.addWidget(detail)
    return layout


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


class CircuitBuilderPage(QWidget):
    status_message = Signal(str)
    circuit_changed = Signal(object)

    def __init__(self, overview: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.overview = overview
        self.spec = CircuitSpec()
        self.catalog: ConnectomeCatalog | None = None
        self.loaded_records: dict[str, NeuronRecord] = {}
        self.loaded_morphologies: dict[str, Morphology] = {}
        self._sources: tuple[ConnectomeRef, ...] = ()
        self._catalog_cache: dict[tuple[str, str], ConnectomeCatalog] = {}
        self._restoring_controls = False

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 24)
        root.setSpacing(14)
        root.addLayout(_header())

        controls = Card()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(16, 14, 16, 14)
        controls_layout.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.engine_combo = QComboBox()
        for key, name, _summary in ENGINE_PROFILES:
            self.engine_combo.addItem(name, key)
        self.engine_combo.setCurrentIndex(0)
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        top.addWidget(QLabel("Engine"))
        top.addWidget(self.engine_combo)

        self.connectome_combo = QComboBox()
        self.connectome_combo.setMinimumWidth(230)
        top.addWidget(QLabel("SWC source"))
        top.addWidget(self.connectome_combo, 1)
        refresh = QPushButton("Refresh sources")
        refresh.clicked.connect(self.refresh_connectomes)
        top.addWidget(refresh)
        controls_layout.addLayout(top)

        query_row = QHBoxLayout()
        query_row.setSpacing(10)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Examples: 10000, 10002 · type:GFC2 · family:DN · all:IN")
        self.query_edit.returnPressed.connect(self.assemble_circuit)
        query_row.addWidget(QLabel("Neurons"))
        query_row.addWidget(self.query_edit, 1)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 500)
        self.limit_spin.setValue(64)
        self.limit_spin.setToolTip("Safety cap for a single visualized morphology cell set")
        query_row.addWidget(QLabel("Limit"))
        query_row.addWidget(self.limit_spin)
        self.assemble_button = QPushButton("Load cell set")
        self.assemble_button.setProperty("primary", True)
        self.assemble_button.clicked.connect(self.assemble_circuit)
        query_row.addWidget(self.assemble_button)
        controls_layout.addLayout(query_row)

        self.engine_note = QLabel()
        self.engine_note.setObjectName("Muted")
        self.engine_note.setWordWrap(True)
        controls_layout.addWidget(self.engine_note)
        root.addWidget(controls)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(9)

        viewport_card = Card()
        viewport_layout = QVBoxLayout(viewport_card)
        viewport_layout.setContentsMargins(8, 8, 8, 8)
        viewport_layout.setSpacing(6)
        viewport_top = QHBoxLayout()
        self.viewport_summary = QLabel("No morphology loaded")
        self.viewport_summary.setStyleSheet("font-weight:650; color:#dce8ff;")
        viewport_top.addWidget(self.viewport_summary)
        viewport_top.addStretch()
        controls_hint = QLabel("Drag rotate · ⇧ drag/middle pan · scroll zoom · right-click center · Esc restore")
        controls_hint.setObjectName("Muted")
        viewport_top.addWidget(controls_hint)
        viewport_layout.addLayout(viewport_top)
        self.viewport = CircuitViewport()
        self.viewport.neuron_selected.connect(self._neuron_selected)
        self.viewport.compartments_changed.connect(self._compartments_changed)
        self.viewport.isolation_changed.connect(self._isolation_changed)
        self.viewport.status_message.connect(self.status_message)
        viewport_layout.addWidget(self.viewport, 1)
        left_layout.addWidget(viewport_card, 1)

        self.neuron_table = QTableWidget(0, 4)
        self.neuron_table.setHorizontalHeaderLabels(("Neuron ID", "Family", "Type", "Segments"))
        self.neuron_table.horizontalHeader().setStretchLastSection(True)
        self.neuron_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.neuron_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.neuron_table.setMaximumHeight(170)
        self.neuron_table.cellClicked.connect(self._table_clicked)
        left_layout.addWidget(self.neuron_table)
        splitter.addWidget(left)

        side_scroll = QScrollArea()
        side_scroll.setWidgetResizable(True)
        side_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        side = QWidget()
        side.setObjectName("HHSidePanel")
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(14, 10, 14, 18)
        side_layout.setSpacing(10)
        side_layout.addWidget(_section_label("Hodgkin–Huxley settings"))
        help_text = QLabel(
            "Classic-HH design draft. Execution adapters must validate supported fields and map SWC node selections to simulator discretization."
        )
        help_text.setObjectName("Muted")
        help_text.setWordWrap(True)
        side_layout.addWidget(help_text)
        self.selection_label = QLabel("No neuron selected")
        self.selection_label.setStyleSheet("font-weight:650; color:#f1f6ff;")
        self.selection_label.setWordWrap(True)
        side_layout.addWidget(self.selection_label)

        selection_actions = QHBoxLayout()
        restore = QPushButton("Restore all")
        restore.clicked.connect(self.viewport.restore_all)
        clear = QPushButton("Clear SWC segments")
        clear.clicked.connect(self.viewport.clear_compartments)
        selection_actions.addWidget(restore)
        selection_actions.addWidget(clear)
        side_layout.addLayout(selection_actions)

        scope_form = QFormLayout()
        self.active_scope_combo = QComboBox()
        self.active_scope_combo.addItem("All morphology regions", "all")
        self.active_scope_combo.addItem("Soma + AIS draft", "soma_ais")
        self.active_scope_combo.addItem("Soma-only draft", "soma")
        scope_form.addRow("Draft active-region scope", self.active_scope_combo)
        side_layout.addLayout(scope_form)

        self.hh_editors: dict[str, QDoubleSpinBox] = {}
        groups = (
            ("Passive & adapter-dependent", HH_PARAMETER_DEFINITIONS[:8]),
            ("Soma HH", HH_PARAMETER_DEFINITIONS[8:12]),
            ("Branch HH", HH_PARAMETER_DEFINITIONS[12:]),
        )
        for title, definitions in groups:
            side_layout.addWidget(_section_label(title))
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for definition in definitions:
                editor = QDoubleSpinBox()
                editor.setRange(definition.minimum, definition.maximum)
                editor.setDecimals(definition.decimals)
                editor.setSingleStep(definition.step)
                editor.setKeyboardTracking(False)
                editor.setSuffix(f" {definition.unit}")
                self.hh_editors[definition.key] = editor
                form.addRow(definition.label, editor)
            side_layout.addLayout(form)

        apply_neuron = QPushButton("Set selected-neuron defaults")
        apply_neuron.clicked.connect(self.apply_neuron_override)
        side_layout.addWidget(apply_neuron)
        apply_compartments = QPushButton("Apply to selected SWC segments")
        apply_compartments.setProperty("primary", True)
        apply_compartments.clicked.connect(self.apply_compartment_overrides)
        side_layout.addWidget(apply_compartments)
        save = QPushButton("Save unchanged SWC + HH-draft bundle")
        save.clicked.connect(self.save_custom_neuron)
        side_layout.addWidget(save)
        self.override_summary = QLabel("No local overrides")
        self.override_summary.setObjectName("Muted")
        self.override_summary.setWordWrap(True)
        side_layout.addWidget(self.override_summary)
        side_layout.addStretch(1)
        side_scroll.setWidget(side)
        side_scroll.setMinimumWidth(350)
        side_scroll.setMaximumWidth(430)
        splitter.addWidget(side_scroll)
        splitter.setSizes((920, 380))
        root.addWidget(splitter, 1)

        self._set_hh(HodgkinHuxleySpec())
        self.refresh_connectomes()
        self.query_edit.textEdited.connect(self._selection_controls_changed)
        self.limit_spin.valueChanged.connect(self._selection_controls_changed)
        self.connectome_combo.currentIndexChanged.connect(self._selection_controls_changed)
        self._engine_changed()

    def selected_engine_key(self) -> str:
        return str(self.engine_combo.currentData() or "arbor")

    def set_selected_engine_key(self, key: str) -> None:
        index = self.engine_combo.findData(str(key))
        if index < 0:
            raise ValueError(f"Unsupported circuit engine: {key}")
        self.engine_combo.setCurrentIndex(index)

    def _engine_changed(self, *_args: Any) -> None:
        key = self.selected_engine_key()
        _profile_key, profile_name, profile_summary = next(
            profile for profile in ENGINE_PROFILES if profile[0] == key
        )
        parity = {
            "arbor": "The staged runtime passes four curated archived-baseline scenarios using built-in HH/passive, exp2syn, and ohmic gj. This does not validate arbitrary circuit designs, Drosophila MOD channels, or true HeteroRectGap.",
            "neuron": "Reference path for native HH, NMODL channels, and rectifying/heterotypic gap mechanisms.",
            "bmtk": "PointNet/DPointNet are LIF/GLIF lanes and do not consume this cable-HH draft. A general BioNet cable adapter is future work.",
        }[key]
        self.engine_note.setText(
            f"{profile_summary}  {parity}  This page currently loads morphology/cell sets only; connectivity and execution plans are not built yet."
        )
        self.status_message.emit(f"Selected {profile_name} as the design target")

    def refresh_connectomes(self) -> None:
        self._catalog_cache.clear()
        previous_source = self._selected_source()
        discovered = discover_connectomes(
            self.overview.workspace_edit.text(),
            morphology_library_root=morphology_library_root(),
        )
        if (
            previous_source is not None
            and _source_identity(previous_source) not in {_source_identity(source) for source in discovered}
            and Path(previous_source.root).expanduser().is_dir()
        ):
            discovered = (*discovered, previous_source)
        self._sources = discovered
        prior_guard = self._restoring_controls
        self._restoring_controls = True
        try:
            self.connectome_combo.clear()
            for source in self._sources:
                self.connectome_combo.addItem(source.label, source.key)
            if previous_source is not None:
                previous_identity = _source_identity(previous_source)
                index = next(
                    (
                        candidate
                        for candidate, source in enumerate(self._sources)
                        if _source_identity(source) == previous_identity
                    ),
                    -1,
                )
                if index >= 0:
                    self.connectome_combo.setCurrentIndex(index)
        finally:
            self._restoring_controls = prior_guard
        self.assemble_button.setEnabled(bool(self._sources))
        if self._sources:
            self.status_message.emit(f"Found {len(self._sources)} local SWC/morphology source(s)")
        else:
            self.status_message.emit("No local SWC morphology source was found under the configured workspace")
        current_source = self._selected_source()
        if (
            previous_source is not None
            and (current_source is None or _source_identity(current_source) != _source_identity(previous_source))
            and not prior_guard
        ):
            self._selection_controls_changed()

    def _selection_controls_changed(self, *_args: Any) -> None:
        if self._restoring_controls:
            return
        if self.spec.neuron_ids or self.loaded_morphologies:
            self._clear_loaded_assets("Cell-set controls changed · click Load cell set")
            self.status_message.emit("Cell-set controls changed; reload morphologies before saving")

    def _clear_loaded_assets(self, summary: str = "No morphology loaded") -> None:
        self.catalog = None
        self.loaded_records.clear()
        self.loaded_morphologies.clear()
        self.viewport.clear()
        self.neuron_table.setRowCount(0)
        self.viewport_summary.setText(summary)
        self.selection_label.setText("No neuron selected")
        self.override_summary.setText("No local overrides")
        self.spec.neuron_ids = ()
        self.spec.morphology_sha256.clear()
        self.spec.neuron_overrides.clear()
        self.spec.compartment_overrides.clear()

    def _selected_source(self) -> ConnectomeRef | None:
        index = self.connectome_combo.currentIndex()
        return self._sources[index] if 0 <= index < len(self._sources) else None

    def _catalog_for(self, source: ConnectomeRef) -> ConnectomeCatalog:
        cache_key = (source.key, source.root)
        catalog = self._catalog_cache.get(cache_key)
        if catalog is None:
            catalog = ConnectomeCatalog.scan(source)
            self._catalog_cache[cache_key] = catalog
        return catalog

    def assemble_circuit(self) -> None:
        source = self._selected_source()
        if source is None:
            self.status_message.emit("Choose an available SWC morphology source first")
            return
        expression = self.query_edit.text().strip()
        self.assemble_button.setEnabled(False)
        self.viewport_summary.setText(f"Indexing {source.label}…")
        QApplication.processEvents()
        try:
            catalog = self._catalog_for(source)
            result = catalog.query(expression, limit=self.limit_spin.value())
            if not result.records:
                detail = ", ".join(result.unmatched) if result.unmatched else expression
                self._clear_loaded_assets(f"No local SWCs matched: {detail or 'empty query'}")
                self.status_message.emit(self.viewport_summary.text())
                return
            morphologies: list[Morphology] = []
            failures: list[str] = []
            for record in result.records:
                try:
                    morphologies.append(load_swc(record))
                except Exception as exc:
                    failures.append(f"{record.neuron_id}: {exc}")
            if not morphologies:
                self._clear_loaded_assets("Matched records could not be loaded as SWC morphology")
                self.status_message.emit(self.viewport_summary.text())
                return

            previous_neuron_overrides = dict(self.spec.neuron_overrides)
            previous_compartment_overrides = dict(self.spec.compartment_overrides)
            if source.dataset == "custom":
                for morphology in morphologies:
                    neuron_id = morphology.record.neuron_id
                    sidecar = load_custom_biophysics(
                        source.root,
                        neuron_id,
                        swc_path=morphology.record.swc_path,
                    )
                    if sidecar is None:
                        continue
                    saved_default = sidecar.get("neuron_override") or sidecar.get("base_hh") or {}
                    previous_neuron_overrides[neuron_id] = HodgkinHuxleySpec.from_dict(
                        saved_default
                    ).to_dict()
                    valid_node_ids = {segment.child_id for segment in morphology.segments}
                    restored_compartments: dict[str, dict[str, Any]] = {}
                    for node_id, values in dict(sidecar.get("compartment_overrides") or {}).items():
                        try:
                            numeric_node_id = int(str(node_id))
                        except ValueError as exc:
                            raise ValueError(f"Invalid saved SWC child-node ID: {node_id}") from exc
                        if numeric_node_id not in valid_node_ids:
                            raise ValueError(
                                f"Saved SWC child-node ID {numeric_node_id} is absent from neuron {neuron_id}"
                            )
                        restored_compartments[str(numeric_node_id)] = HodgkinHuxleySpec.from_dict(
                            values
                        ).to_dict()
                    previous_compartment_overrides[neuron_id] = restored_compartments
            self.catalog = catalog
            self.loaded_records = {item.record.neuron_id: item.record for item in morphologies}
            self.loaded_morphologies = {item.record.neuron_id: item for item in morphologies}
            self.spec = CircuitSpec(
                connectome=source,
                query=NeuronQuery(expression, self.limit_spin.value()),
                neuron_ids=tuple(item.record.neuron_id for item in morphologies),
                hh=self._read_hh(),
                morphology_sha256={
                    item.record.neuron_id: sha256_file(item.record.swc_path) for item in morphologies
                },
                neuron_overrides={
                    key: value for key, value in previous_neuron_overrides.items() if key in self.loaded_records
                },
                compartment_overrides={
                    key: value for key, value in previous_compartment_overrides.items() if key in self.loaded_records
                },
            )
            self.viewport.set_morphologies(morphologies)
            self._populate_table(morphologies)
            suffixes = []
            if result.truncated:
                suffixes.append(f"capped at {self.limit_spin.value()}")
            if result.unmatched:
                suffixes.append(f"unmatched: {', '.join(result.unmatched)}")
            if failures:
                suffixes.append(f"{len(failures)} failed SWC(s)")
            suffix = f" · {' · '.join(suffixes)}" if suffixes else ""
            self.viewport_summary.setText(
                f"{len(morphologies)} neuron(s) · {self.viewport.segment_count:,} SWC segments{suffix}"
            )
            self.status_message.emit(f"Loaded morphology cell set from {source.label}{suffix}")
            self.circuit_changed.emit(self.spec)
        except Exception as exc:
            self._clear_loaded_assets(f"Cell-set load failed: {exc}")
            self.status_message.emit(self.viewport_summary.text())
        finally:
            self.assemble_button.setEnabled(bool(self._sources))

    def _populate_table(self, morphologies: list[Morphology]) -> None:
        self.neuron_table.setRowCount(len(morphologies))
        for row, morphology in enumerate(morphologies):
            record = morphology.record
            values = (record.neuron_id, record.family, record.neuron_type, f"{len(morphology.segments):,}")
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, record.neuron_id)
                self.neuron_table.setItem(row, column, item)
        self.neuron_table.resizeColumnsToContents()

    def _table_clicked(self, row: int, _column: int) -> None:
        item = self.neuron_table.item(row, 0)
        if item is not None:
            self.viewport.focus_neuron(str(item.data(Qt.ItemDataRole.UserRole)), isolate=True)

    def _neuron_selected(self, neuron_id: str) -> None:
        record = self.loaded_records.get(neuron_id)
        if record is None:
            return
        self.selection_label.setText(f"{record.neuron_type} · {neuron_id}\nWhole neuron selected; other neurons hidden")
        for row in range(self.neuron_table.rowCount()):
            item = self.neuron_table.item(row, 0)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole)) == neuron_id:
                self.neuron_table.selectRow(row)
                break
        self._update_override_summary()

    def _compartments_changed(self, neuron_id: str, node_ids: object) -> None:
        ids = tuple(node_ids) if isinstance(node_ids, (tuple, list, set)) else ()
        record = self.loaded_records.get(neuron_id)
        label = record.neuron_type if record else "Neuron"
        view_state = "Whole neuron isolated" if self.viewport.isolated else "All loaded neurons visible"
        self.selection_label.setText(
            f"{label} · {neuron_id}\n{view_state} · {len(ids)} SWC segment(s) selected by child-node ID"
        )
        self._update_override_summary()

    def _isolation_changed(self, _isolated: bool) -> None:
        neuron_id = self.viewport.selected_neuron_id
        if neuron_id is not None:
            self._compartments_changed(neuron_id, tuple(sorted(self.viewport.selected_compartments)))

    def _read_hh(self) -> HodgkinHuxleySpec:
        values = {key: editor.value() for key, editor in self.hh_editors.items()}
        values["active_scope"] = str(self.active_scope_combo.currentData() or "all")
        return HodgkinHuxleySpec.from_dict(values)

    def _set_hh(self, spec: HodgkinHuxleySpec) -> None:
        values = spec.to_dict()
        for key, editor in self.hh_editors.items():
            editor.setValue(float(values[key]))
        index = self.active_scope_combo.findData(spec.active_scope)
        self.active_scope_combo.setCurrentIndex(index if index >= 0 else 0)

    def circuit_spec(self) -> CircuitSpec:
        self.spec.hh = self._read_hh()
        self.spec.query = NeuronQuery(self.query_edit.text().strip(), self.limit_spin.value())
        source = self._selected_source()
        if source is not None:
            self.spec.connectome = source
        return self.spec

    def set_circuit_spec(self, spec: CircuitSpec) -> None:
        wanted_identity = _source_identity(spec.connectome)
        index = next(
            (
                candidate
                for candidate, source in enumerate(self._sources)
                if _source_identity(source) == wanted_identity
            ),
            -1,
        )
        if index < 0:
            saved_root = Path(spec.connectome.root).expanduser()
            if not saved_root.is_dir():
                raise ValueError(
                    f"Saved SWC morphology source is unavailable: {spec.connectome.label} ({saved_root})"
                )
            self._sources = (*self._sources, spec.connectome)
            self.connectome_combo.addItem(spec.connectome.label, spec.connectome.key)
            index = self.connectome_combo.count() - 1
        self._restoring_controls = True
        try:
            self.spec = spec
            self.query_edit.setText(spec.query.expression)
            self.limit_spin.setValue(spec.query.max_neurons)
            self._set_hh(spec.hh)
            self.connectome_combo.setCurrentIndex(index)
        finally:
            self._restoring_controls = False
        self.catalog = None
        self.loaded_records.clear()
        self.loaded_morphologies.clear()
        self.viewport.clear()
        self.neuron_table.setRowCount(0)
        self.viewport_summary.setText("Saved cell set not loaded yet")
        self.selection_label.setText("No neuron selected")
        self._update_override_summary()

    def load_saved_assets(self) -> None:
        """Reload the exact saved neuron IDs without re-evaluating the original query."""
        if not self.spec.neuron_ids:
            return
        source = self._selected_source()
        if source is None or _source_identity(source) != _source_identity(self.spec.connectome):
            raise ValueError("The exact saved SWC morphology source is not selected")
        catalog = self._catalog_for(source)
        missing = [neuron_id for neuron_id in self.spec.neuron_ids if neuron_id not in catalog.by_id]
        if missing:
            raise ValueError(f"Saved neuron IDs are unavailable in {source.label}: {', '.join(missing)}")
        morphologies = [load_swc(catalog.by_id[neuron_id]) for neuron_id in self.spec.neuron_ids]
        for morphology in morphologies:
            neuron_id = morphology.record.neuron_id
            actual_hash = sha256_file(morphology.record.swc_path)
            expected_hash = self.spec.morphology_sha256.get(neuron_id)
            if expected_hash and expected_hash != actual_hash:
                raise ValueError(f"Saved morphology identity changed for neuron {neuron_id}")
            self.spec.morphology_sha256[neuron_id] = actual_hash
        self.catalog = catalog
        self.loaded_records = {item.record.neuron_id: item.record for item in morphologies}
        self.loaded_morphologies = {item.record.neuron_id: item for item in morphologies}
        self.viewport.set_morphologies(morphologies)
        self._populate_table(morphologies)
        self.viewport_summary.setText(
            f"{len(morphologies)} saved neuron(s) · {self.viewport.segment_count:,} SWC segments"
        )
        self.status_message.emit(f"Restored exact saved cell set from {source.label}")

    def reset(self) -> None:
        self._restoring_controls = True
        try:
            self.spec = CircuitSpec()
            self.query_edit.clear()
            self.limit_spin.setValue(64)
            self._set_hh(self.spec.hh)
            self._clear_loaded_assets()
            self.set_selected_engine_key("arbor")
        finally:
            self._restoring_controls = False

    def apply_neuron_override(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        if neuron_id is None:
            self.status_message.emit("Select a neuron before creating a neuron-level HH override")
            return
        self.spec.apply_neuron_override(neuron_id, self._read_hh().to_dict())
        self._update_override_summary()
        self.circuit_changed.emit(self.spec)
        self.status_message.emit(f"Stored HH draft for neuron {neuron_id}")

    def apply_compartment_overrides(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        node_ids = sorted(self.viewport.selected_compartments)
        if neuron_id is None or not node_ids:
            self.status_message.emit("Select one or more SWC segments on an isolated neuron first")
            return
        self.spec.apply_compartment_override(neuron_id, node_ids, self._read_hh().to_dict())
        self._update_override_summary()
        self.circuit_changed.emit(self.spec)
        self.status_message.emit(f"Applied HH draft values to {len(node_ids)} SWC segment(s) on {neuron_id}")

    def _update_override_summary(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        if neuron_id is None:
            total = sum(len(nodes) for nodes in self.spec.compartment_overrides.values())
            self.override_summary.setText(
                f"{len(self.spec.neuron_overrides)} neuron override(s) · {total} SWC-segment override(s)"
            )
            return
        count = len(self.spec.compartment_overrides.get(neuron_id, {}))
        has_neuron = neuron_id in self.spec.neuron_overrides
        self.override_summary.setText(
            f"Selected neuron: {'custom defaults' if has_neuron else 'circuit defaults'} · {count} saved SWC-segment override(s)"
        )

    def save_custom_neuron(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        morphology = self.loaded_morphologies.get(neuron_id or "")
        if neuron_id is None or morphology is None:
            self.status_message.emit("Select a loaded neuron before saving a custom copy")
            return
        self.spec.hh = self._read_hh()
        try:
            bundle = save_custom_morphology(
                morphology,
                self.spec,
                selected_engine=self.selected_engine_key(),
            )
        except Exception as exc:
            self.status_message.emit(f"Could not save SWC + HH-draft bundle: {exc}")
            return
        self.status_message.emit(f"Saved unchanged SWC + HH-draft bundle: {bundle}")
        self.refresh_connectomes()
