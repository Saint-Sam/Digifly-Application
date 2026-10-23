from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import re
from typing import Any

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QMessageBox,
    QInputDialog,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSizePolicy,
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
    cell_design_profile,
)
from digifly_app.core.data_library import ManagedResource
from digifly_app.core.neuprint import NeuPrintSelection
from digifly_app.core.neuprint import DEFAULT_NEUPRINT_SERVER
from digifly_app.core.connectomes import (
    ConnectionClassSummary,
    ConnectomeCatalog,
    ConnectomeEdgeCatalog,
    NeuronRecord,
    discover_connectomes,
)
from digifly_app.core.morphology import (
    Morphology,
    load_custom_biophysics,
    load_swc,
    morphology_library_root,
    save_custom_morphology,
    sha256_file,
)
from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.core.providers import profile_connectome_sources
from digifly_app.core.circuit_library import library_sources, source_folders
from digifly_app.core.linked_libraries import canonical_dataset
from digifly_app.core.pair_graph import attach_registered_pair_graph
from digifly_app.core.resource_profile import ResourceKind, default_profile_path, load_default_profile
from digifly_app.core.swc_quality import (
    heal_swc,
    record_review_decision,
    reviewed_hashes,
    scan_recent_imports,
)
from digifly_app.core.mechanisms import (
    MEMBRANE_MECHANISMS,
    MEMBRANE_PROFILES,
    ChannelAssignment,
    ChemicalSynapsePolicy,
    GapJunctionPolicy,
    MembraneMechanismSpec,
    mechanism_capability_message,
)
from .circuit_viewport import (
    DISPLAY_MODE_FULL_SKELETONS,
    DISPLAY_MODE_SOMA_POINTS,
    CircuitViewport,
)
from .neuprint_import import NeuPrintImportDialog
from .pair_graph_import import PairGraphImportDialog
from .widgets import Card, CollapsibleSection, make_label_copyable
from .connectome_picker import CONNECTOMES
from .saved_neurons import SavedNeuronBrowser


CIRCUIT_BUILDER_WORKFLOW = "circuit_builder_v1"

_NEUPRINT_DATASETS = tuple(dataset for choice in CONNECTOMES for dataset in choice.releases)
_NEUPRINT_DATASET_ALIASES = {
    "manc_v1.2.1": "manc:v1.2.1",
    "male-cns_v0.9": "male-cns:v0.9",
    "male-cns_v1.0": "male-cns:v1.0",
}


ENGINE_PROFILES = (
    (
        "arbor",
        "Arbor",
        "Morphology-backed HH and equation-translated native-channel execution.",
    ),
    (
        "neuron",
        "NEURON",
        "Morphology-backed HH/native-channel target with chemical and electrical mechanisms.",
    ),
    (
        "bmtk",
        "BMTK / SONATA",
        "Bounded serial BioNet target for HH, native channels, chemical SONATA contacts and exact electrical contacts through a Digifly extension.",
    ),
)


def _source_identity(source: ConnectomeRef) -> tuple[str, str, str]:
    return (
        source.key,
        source.dataset,
        str(Path(source.root).expanduser().resolve()),
    )


def _neuprint_dataset(source: ConnectomeRef) -> str | None:
    if source.server and source.server != DEFAULT_NEUPRINT_SERVER:
        return None  # Never hand another server's body IDs to the official server.
    for candidate in (source.dataset, source.key):
        if candidate in _NEUPRINT_DATASETS:
            return candidate
        if candidate in _NEUPRINT_DATASET_ALIASES:
            return _NEUPRINT_DATASET_ALIASES[candidate]
    return next(
        (
            dataset
            for dataset in _NEUPRINT_DATASETS
            if source.key.startswith(f"neuprint:{dataset}:")
        ),
        None,
    )


def _neuprint_selection(expression: str, limit: int) -> NeuPrintSelection | None:
    body_tokens = [
        token for token in re.split(r"[\s,;\n]+", expression.strip()) if token
    ]
    if body_tokens and all(token.isdigit() for token in body_tokens):
        return NeuPrintSelection("body_ids", ", ".join(body_tokens), limit)

    parts = [part.strip() for part in re.split(r"[,;\n]+", expression) if part.strip()]
    if not parts:
        return None
    if len(parts) > 1:
        selections = [_neuprint_selection(part, limit) for part in parts]
        if all(item is not None and item.mode == "type_exact" for item in selections):
            return NeuPrintSelection("type_exact", ", ".join(item.value for item in selections), limit)
        return None
    token = parts[0]
    if ":" in token:
        key, value = (part.strip() for part in token.split(":", 1))
        if key.casefold() in {"id", "body", "bodyid", "rootid"} and value.isdigit():
            return NeuPrintSelection("body_ids", value, limit)
        if key.casefold() not in {"type", "celltype"}:
            return None
        token = value
    if not token or token.upper() in {"AN", "DN", "IN", "MN", "SN"}:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", token):
        return None
    return NeuPrintSelection("type_exact", token, limit)


def _primary_circuit_sources(
    sources: tuple[ConnectomeRef, ...],
) -> tuple[ConnectomeRef, ...]:
    """Expose the primary datasets, preserving the older app's local Male CNS.

    Curated subsets, managed download bundles, custom morphologies, and scratch
    exports remain registered in the Data Library.  They are useful source history
    and fallback resources, but should not masquerade as peer connectomes next
    to MANC and Male CNS. Prefer the exact locally discovered v0.9 source used
    by the older app over managed selections or v1.0. Never relabel one version
    as another; v1.0 remains a fallback only when that local v0.9 is absent.
    """

    full_manc = next(
        (source for source in sources if source.key == "manc:v1.2.1:full-local"),
        None,
    )
    male_cns = next(
        (source for source in sources if source.key == "male-cns:v0.9"),
        None,
    )
    if male_cns is None:
        male_cns = next(
            (source for source in sources if source.key == "male-cns:v1.0"),
            None,
        )
    if male_cns is None:
        male_cns = next(
            (source for source in sources if _neuprint_dataset(source) == "male-cns:v1.0"),
            None,
        )
    if full_manc is not None and male_cns is not None:
        primary = (full_manc, ConnectomeRef(
            key=male_cns.key,
            label=(
                "Male CNS v0.9"
                if male_cns.key == "male-cns:v0.9"
                else "Male CNS v1.0"
            ),
            root=male_cns.root,
            dataset=male_cns.dataset,
        ))
        # Cumulative libraries are intentional reusable sources, not per-search
        # subsets. Keep the user's full local reference source as well.
        libraries = tuple(source for source in sources if ":swc-library-" in source.key
                          and source.key not in {item.key for item in primary})
        return (*primary, *libraries)
    return sources


def _header() -> QVBoxLayout:
    layout = QVBoxLayout()
    layout.setSpacing(5)
    title = QLabel("Circuit Builder")
    title.setObjectName("PageTitle")
    detail = QLabel(
        "Choose your connectome, select neurons, then build your circuit."
    )
    detail.setObjectName("Muted")
    detail.setWordWrap(True)
    layout.addWidget(title)
    layout.addWidget(detail)
    return layout


def _section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


class _ScrollSafeDoubleSpinBox(QDoubleSpinBox):
    """Let a surrounding settings panel scroll without mutating a value."""

    def wheelEvent(self, event: Any) -> None:  # noqa: N802 - Qt virtual name
        event.ignore()


class CircuitBuilderPage(QWidget):
    status_message = Signal(str)
    circuit_changed = Signal(object)
    managed_data_changed = Signal()
    experiment_globals_applied = Signal(float, float)
    data_library_requested = Signal(str)

    def __init__(self, overview: Any, parent: QWidget | None = None):
        super().__init__(parent)
        self.overview = overview
        self.spec = CircuitSpec()
        self.catalog: ConnectomeCatalog | None = None
        self.loaded_records: dict[str, NeuronRecord] = {}
        self.loaded_morphologies: dict[str, Morphology] = {}
        self._sources: tuple[ConnectomeRef, ...] = ()
        self._catalog_cache: dict[tuple[str, str], ConnectomeCatalog] = {}
        self._edge_catalog: ConnectomeEdgeCatalog | None = None
        self._restoring_controls = False
        self._setting_mechanism_controls = False
        self._unlisted_channel_assignments: dict[str, ChannelAssignment] = {}
        self._known_channel_parameters: dict[str, dict[str, float]] = {}
        self._mixed_selection_design = False
        self._setting_connection_controls = False
        self._syncing_neuron_table_selection = False
        self._missing_neuprint_request: tuple[
            str, NeuPrintSelection, str
        ] | None = None
        self._assemble_after_inventory = None

        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_scroll = QScrollArea()
        self.page_scroll.setWidgetResizable(True)
        content = QWidget()
        content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.page_scroll.setWidget(content)
        page_layout.addWidget(self.page_scroll)
        # Expanding Browse or More options must scroll the page rather than
        # compress its family rows into overlapping, partly hidden buttons.
        root = QVBoxLayout(content)
        root.setSizeConstraints(QLayout.SizeConstraint.SetNoConstraint,
                                QLayout.SizeConstraint.SetMinimumSize)
        root.setContentsMargins(28, 22, 28, 24)
        root.setSpacing(14)
        root.addLayout(_header())

        controls = Card()
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(16, 14, 16, 14)
        controls_layout.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.family_combo = QComboBox()
        self.family_combo.setAccessibleName('Circuit connectome')
        self.family_combo.setProperty('digiflyAccessibleChoiceInstalled', True)
        for choice in CONNECTOMES:
            self.family_combo.addItem(choice.name, choice.key)
        self.family_combo.addItem('Other local sources', 'other')
        self.release_combo = QComboBox()
        self.release_combo.setAccessibleName('Circuit release')
        self.release_combo.setProperty('digiflyAccessibleChoiceInstalled', True)
        self.family_combo.setParent(self)
        self.family_combo.hide()
        self.release_combo.setParent(self)
        self.release_combo.hide()
        self.family_button = QPushButton()
        self.release_button = QPushButton()
        self.family_button.clicked.connect(lambda: self._open_inline_choices(self.family_combo, 'connectome'))
        self.release_button.clicked.connect(lambda: self._open_inline_choices(self.release_combo, 'release'))
        top.addWidget(QLabel('Connectome'))
        top.addWidget(self.family_button, 1)
        top.addWidget(QLabel('Release'))
        top.addWidget(self.release_button, 1)
        self.library_button = QPushButton('Data Library')
        self.library_button.clicked.connect(lambda: self.data_library_requested.emit(
            canonical_dataset(self._selected_source().dataset) if self._selected_source() else ''))
        top.addWidget(self.library_button)
        controls_layout.addLayout(top)
        self._source_choice_slot = QVBoxLayout()
        self._source_choice_slot.setContentsMargins(0, 0, 0, 0)
        controls_layout.addLayout(self._source_choice_slot)
        self.choice_panel = QWidget()
        self.choice_grid = QGridLayout(self.choice_panel)
        self.choice_grid.setContentsMargins(0, 4, 0, 4)
        self.choice_buttons = []
        self._choice_generation = 0
        self.choice_panel.hide()
        self._source_choice_slot.addWidget(self.choice_panel)
        self._active_choice_slot = self._source_choice_slot
        self.source_summary = QLabel()
        self.source_summary.setObjectName('Muted')
        self.source_summary.setWordWrap(True)
        controls_layout.addWidget(self.source_summary)
        self.connectome_combo = QComboBox(self)
        self.connectome_combo.hide()  # Exact legacy project references, not another user-facing picker.
        self.connectome_combo.setProperty('digiflyAccessibleChoiceInstalled', True)
        self.source_focus_shortcut = QShortcut(QKeySequence('Alt+S'), self)
        self.source_focus_shortcut.activated.connect(self.family_button.setFocus)

        self.more_options = CollapsibleSection('More options', 'circuit_source_tools')
        options = self.more_options.content_layout
        tools = QHBoxLayout()
        self.engine_combo = QComboBox()
        self.engine_combo.setProperty('digiflyAccessibleChoiceInstalled', True)
        for key, name, _summary in ENGINE_PROFILES:
            self.engine_combo.addItem(name, key)
        self.engine_combo.setCurrentIndex(0)
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        self.engine_combo.setParent(self)
        self.engine_combo.hide()
        self.engine_button = QPushButton(self.engine_combo.currentText() + ' ▾')
        self.engine_button.setAccessibleName('Simulator: ' + self.engine_combo.currentText())
        self.engine_combo.currentTextChanged.connect(lambda text: (
            self.engine_button.setText(text + ' ▾'), self.engine_button.setAccessibleName('Simulator: ' + text)))
        self.engine_button.clicked.connect(lambda: self._open_inline_choices(self.engine_combo, 'simulator'))
        simulator_row = QHBoxLayout()
        simulator_row.addWidget(QLabel('Simulator'))
        simulator_row.addWidget(self.engine_button)
        simulator_row.addStretch()
        refresh = QPushButton("Refresh sources")
        refresh.clicked.connect(self.refresh_connectomes)
        tools.addWidget(refresh)
        quality_check = QPushButton("Check recent imports")
        quality_check.setToolTip(
            "Audit manifest-declared recent SWCs using each neuron's own radius distribution"
        )
        quality_check.clicked.connect(self.review_recent_imports)
        tools.addWidget(quality_check)
        tools.addStretch()
        options.addLayout(tools)

        graph_row = QHBoxLayout()
        self.pair_graph_button = QPushButton("Use connectivity Parquet…")
        self.pair_graph_button.setObjectName("UseConnectivityParquetButton")
        self.pair_graph_button.clicked.connect(self.open_pair_graph_import)
        graph_row.addWidget(self.pair_graph_button)
        self.pair_graph_note = QLabel("Connectivity: native contact source; no aggregate pair file selected")
        self.pair_graph_note.setObjectName("PairGraphSourceStatus")
        self.pair_graph_note.setWordWrap(True)
        graph_row.addWidget(self.pair_graph_note, 1)
        options.addLayout(graph_row)

        query_row = QHBoxLayout()
        query_row.setSpacing(10)
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("Examples: 10000, 10002 · type:GFC2 · family:DN · all:IN")
        self.query_edit.returnPressed.connect(self.assemble_circuit)
        query_row.addWidget(QLabel("Neurons"))
        query_row.addWidget(self.query_edit, 1)
        self.browse_button = QPushButton('Browse')
        self.browse_button.setCheckable(True)
        query_row.addWidget(self.browse_button)
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 500)
        self.limit_spin.setValue(64)
        self.limit_spin.setToolTip("Safety cap for a single visualized morphology cell set")
        query_row.addWidget(QLabel("Limit"))
        query_row.addWidget(self.limit_spin)
        self.assemble_button = QPushButton("Load neurons")
        self.assemble_button.setProperty("primary", True)
        self.assemble_button.clicked.connect(self.assemble_circuit)
        query_row.addWidget(self.assemble_button)
        self.download_missing_button = QPushButton("Get missing SWCs…")
        self.download_missing_button.setToolTip(
            "Open the credential-safe neuPrint importer with this dataset and query prefilled"
        )
        self.download_missing_button.setVisible(False)
        self.download_missing_button.clicked.connect(self.open_missing_neuprint_import)
        query_row.addWidget(self.download_missing_button)
        controls_layout.addLayout(query_row)
        controls_layout.addLayout(simulator_row)
        self._simulator_choice_slot = QVBoxLayout()
        self._simulator_choice_slot.setContentsMargins(0, 0, 0, 0)
        controls_layout.addLayout(self._simulator_choice_slot)
        self.saved_browser = SavedNeuronBrowser(self)
        self.saved_browser.choose_neuron.show()
        self.saved_browser.hide()
        self.browse_button.toggled.connect(self.saved_browser.setVisible)
        self.saved_browser.neuron_chosen.connect(self._add_browsed_neuron)
        self.saved_browser.inventory_changed.connect(self._inventory_ready)
        controls_layout.addWidget(self.saved_browser)

        self.engine_note = QLabel()
        self.engine_note.setObjectName("Muted")
        self.engine_note.setWordWrap(True)
        options.addWidget(self.engine_note)
        controls_layout.addWidget(self.more_options)
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
        viewport_top = QVBoxLayout()
        viewport_top.setSpacing(2)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        mode_label = QLabel("Visualization")
        mode_label.setObjectName("Strong")
        mode_row.addWidget(mode_label)
        self.soma_points_button = QPushButton("●  Soma points")
        self.soma_points_button.setObjectName("ViewModeButton")
        self.soma_points_button.setCheckable(True)
        self.soma_points_button.setChecked(True)
        self.soma_points_button.setAccessibleName("Show soma points")
        self.soma_points_button.setToolTip(
            "Fast overview with one soma or rostral MANC DN pseudosoma point per neuron"
        )
        self.full_skeletons_button = QPushButton("Full skeletons")
        self.full_skeletons_button.setObjectName("ViewModeButton")
        self.full_skeletons_button.setCheckable(True)
        self.full_skeletons_button.setAccessibleName("Show full neuron skeletons")
        self.full_skeletons_button.setToolTip(
            "Show complete SWC morphologies for compartment selection and editing"
        )
        self.view_mode_group = QButtonGroup(self)
        self.view_mode_group.setExclusive(True)
        self.view_mode_group.addButton(self.soma_points_button)
        self.view_mode_group.addButton(self.full_skeletons_button)
        mode_row.addStretch(1)
        self.save_visualization_button = QPushButton("Save snapshot…")
        self.save_visualization_button.setObjectName("SaveCircuitVisualizationButton")
        self.save_visualization_button.setToolTip(
            "Save the current camera and overlays as a high-resolution PNG"
        )
        self.save_visualization_button.clicked.connect(
            lambda: self.viewport.save_high_resolution_snapshot(
                "digifly-circuit-visualization.png"
            )
        )
        mode_row.addWidget(self.save_visualization_button)
        viewport_top.addLayout(mode_row)
        view_modes = QHBoxLayout()
        view_modes.addWidget(self.soma_points_button)
        view_modes.addWidget(self.full_skeletons_button)
        view_modes.addStretch()
        viewport_top.addLayout(view_modes)
        self.viewport_summary = QLabel("No morphology loaded")
        self.viewport_summary.setObjectName("Strong")
        self.viewport_summary.setWordWrap(True)
        self.viewport_summary.setMinimumWidth(0)
        viewport_top.addWidget(self.viewport_summary)
        self.viewport_controls_hint = QLabel()
        self.viewport_controls_hint.setObjectName("Muted")
        self.viewport_controls_hint.setWordWrap(True)
        self.viewport_controls_hint.setMinimumWidth(0)
        viewport_top.addWidget(self.viewport_controls_hint)
        viewport_layout.addLayout(viewport_top)
        self.viewport = CircuitViewport()
        self.soma_points_button.clicked.connect(
            lambda: self.viewport.set_display_mode(DISPLAY_MODE_SOMA_POINTS)
        )
        self.full_skeletons_button.clicked.connect(
            lambda: self.viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
        )
        self.viewport.neuron_selected.connect(self._neuron_selected)
        self.viewport.neurons_selected.connect(self._neurons_selected)
        self.viewport.compartments_changed.connect(self._compartments_changed)
        self.viewport.isolation_changed.connect(self._isolation_changed)
        self.viewport.display_mode_changed.connect(self._display_mode_changed)
        self.viewport.status_message.connect(self.status_message)
        viewport_layout.addWidget(self.viewport, 1)
        left_layout.addWidget(viewport_card, 1)

        self.neuron_table = QTableWidget(0, 4)
        self.neuron_table.setHorizontalHeaderLabels(("Neuron ID", "Family", "Type", "Segments"))
        self.neuron_table.horizontalHeader().setStretchLastSection(True)
        self.neuron_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.neuron_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.neuron_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.neuron_table.setMaximumHeight(170)
        self.neuron_table.cellClicked.connect(self._table_clicked)
        self.neuron_table.itemSelectionChanged.connect(self._table_selection_changed)
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
        self.selection_label.setObjectName("Strong")
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

        self.pair_panel = QWidget()
        self.pair_panel.setObjectName("PairConnectionPanel")
        pair_layout = QVBoxLayout(self.pair_panel)
        pair_layout.setContentsMargins(0, 4, 0, 6)
        pair_layout.setSpacing(6)
        pair_layout.addWidget(_section_label("Connections between selected neurons"))
        pair_help = QLabel(
            "Cmd/Ctrl-click two neuron points, skeletons, or table rows. A third replaces "
            "the oldest selection. These switches create plan overrides; native connectome "
            "files remain unchanged."
        )
        pair_help.setObjectName("Muted")
        pair_help.setWordWrap(True)
        pair_layout.addWidget(pair_help)
        self.pair_connection_readout = QLabel()
        self.pair_connection_readout.setObjectName("PairConnectionReadout")
        self.pair_connection_readout.setWordWrap(True)
        pair_layout.addWidget(self.pair_connection_readout)
        self.chemical_pair_check = QCheckBox("Chemical connections enabled")
        self.chemical_pair_check.setObjectName("ChemicalPairEnabled")
        self.chemical_pair_check.toggled.connect(
            lambda enabled: self._pair_connection_toggled("chemical", enabled)
        )
        pair_layout.addWidget(self.chemical_pair_check)
        self.gap_pair_check = QCheckBox("Gap junctions enabled")
        self.gap_pair_check.setObjectName("GapPairEnabled")
        self.gap_pair_check.toggled.connect(
            lambda enabled: self._pair_connection_toggled("gap_junction", enabled)
        )
        pair_layout.addWidget(self.gap_pair_check)
        clear_pair = QPushButton("Clear pair selection")
        clear_pair.setObjectName("ClearPairSelectionButton")
        clear_pair.clicked.connect(self._clear_pair_selection)
        pair_layout.addWidget(clear_pair)
        self.pair_panel.setVisible(False)
        side_layout.addWidget(self.pair_panel)

        self.hh_sections: dict[str, CollapsibleSection] = {}

        def add_hh_section(
            key: str, title: str, *, expanded: bool = False
        ) -> QVBoxLayout:
            section = CollapsibleSection(
                title,
                key,
                expanded=expanded,
                object_name_prefix="HHSection",
            )
            self.hh_sections[key] = section
            side_layout.addWidget(section)
            return section.content_layout

        profile_layout = add_hh_section(
            "membrane_profile", "Membrane profile", expanded=True
        )
        channel_help = QLabel(
            "Mechanism identity is saved separately from built-in HH values. The list includes "
            "the source-locked Augustin GF membrane and exploratory Phase 2 surrogates; "
            "they are not silent cross-backend equivalents. gbar columns are soma / branch in S/cm²."
        )
        channel_help.setObjectName("Muted")
        channel_help.setWordWrap(True)
        profile_layout.addWidget(channel_help)

        channel_profile_form = QFormLayout()
        self.channel_profile_combo = QComboBox()
        self.channel_profile_combo.setObjectName("ChannelProfileCombo")
        for profile in MEMBRANE_PROFILES:
            self.channel_profile_combo.addItem(profile.label, profile.key)
        self.channel_profile_combo.addItem("Custom", "custom")
        self.channel_profile_combo.currentIndexChanged.connect(self._channel_profile_changed)
        channel_profile_form.addRow("Profile", self.channel_profile_combo)
        self.replace_hh_check = QCheckBox("Replace built-in HH Na/K")
        self.replace_hh_check.setObjectName("ReplaceBuiltinHHCheck")
        self.replace_hh_check.toggled.connect(self._mechanism_controls_edited)
        channel_profile_form.addRow("Base mechanism", self.replace_hh_check)
        profile_layout.addLayout(channel_profile_form)
        self.channel_profile_summary = QLabel()
        self.channel_profile_summary.setObjectName("Muted")
        self.channel_profile_summary.setWordWrap(True)
        profile_layout.addWidget(self.channel_profile_summary)

        scope_form = QFormLayout()
        self.active_scope_combo = QComboBox()
        self.active_scope_combo.addItem("All morphology regions", "all")
        self.active_scope_combo.addItem("Soma + geometric AIS proxy", "soma_ais")
        self.active_scope_combo.addItem("Soma only", "soma")
        self.active_scope_combo.currentIndexChanged.connect(self._hh_controls_edited)
        scope_form.addRow("Active-channel distribution", self.active_scope_combo)
        profile_layout.addLayout(scope_form)

        self.channel_checks: dict[str, QCheckBox] = {}
        self.channel_soma_editors: dict[str, QDoubleSpinBox] = {}
        self.channel_branch_editors: dict[str, QDoubleSpinBox] = {}
        for family, family_label, family_key in (
            ("sodium", "Sodium channels (Na⁺)", "sodium_channels"),
            ("potassium", "Potassium channels (K⁺)", "potassium_channels"),
            ("calcium", "Calcium channels (Ca²⁺)", "calcium_channels"),
        ):
            family_layout = add_hh_section(family_key, family_label)
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            mechanism_header = QLabel("Enable / mechanism")
            mechanism_header.setObjectName("Muted")
            soma_header = QLabel("Soma")
            soma_header.setObjectName("Muted")
            branch_header = QLabel("Branch")
            branch_header.setObjectName("Muted")
            grid.addWidget(mechanism_header, 0, 0)
            grid.addWidget(soma_header, 0, 1)
            grid.addWidget(branch_header, 0, 2)
            definitions = [item for item in MEMBRANE_MECHANISMS if item.family == family]
            for row, definition in enumerate(definitions, start=1):
                check = QCheckBox(definition.short_label)
                check.setObjectName(f"ChannelCheck_{definition.key}")
                check.setToolTip(
                    f"{definition.label}\nSUFFIX {definition.suffix}\n"
                    f"{definition.source_relpath}\n{definition.note}"
                )
                check.toggled.connect(self._mechanism_controls_edited)
                soma_editor = self._new_channel_editor(definition.editor_step)
                soma_editor.setObjectName(f"ChannelSoma_{definition.key}")
                branch_editor = self._new_channel_editor(definition.editor_step)
                branch_editor.setObjectName(f"ChannelBranch_{definition.key}")
                soma_editor.valueChanged.connect(self._mechanism_controls_edited)
                branch_editor.valueChanged.connect(self._mechanism_controls_edited)
                self.channel_checks[definition.key] = check
                self.channel_soma_editors[definition.key] = soma_editor
                self.channel_branch_editors[definition.key] = branch_editor
                grid.addWidget(check, row, 0)
                grid.addWidget(soma_editor, row, 1)
                grid.addWidget(branch_editor, row, 2)
            grid.setColumnStretch(0, 1)
            family_layout.addLayout(grid)

        self.hh_editors: dict[str, QDoubleSpinBox] = {}
        groups = (
            (
                "passive_properties",
                "Passive & global properties",
                HH_PARAMETER_DEFINITIONS[:9],
            ),
            (
                "soma_hh",
                "Built-in soma HH",
                HH_PARAMETER_DEFINITIONS[9:13],
            ),
            (
                "branch_hh",
                "Built-in branch HH",
                HH_PARAMETER_DEFINITIONS[13:],
            ),
        )
        for group_key, title, definitions in groups:
            group_layout = add_hh_section(group_key, title)
            if group_key == "passive_properties":
                note = QLabel(
                    "Initial voltage and temperature are experiment-global. "
                    "Apply cell-set defaults or Mass apply to send them to Experiment Builder; "
                    "applying a neuron or SWC-segment design does not change them. "
                    "Saved legacy regional values are not private simulator settings."
                )
                note.setObjectName("ExperimentGlobalSettingsHelp")
                note.setWordWrap(True)
                group_layout.addWidget(note)
            if group_key in {"soma_hh", "branch_hh"}:
                note = QLabel("HH Na/K densities are ignored when Replace built-in HH is checked. Leak values remain active.")
                note.setObjectName("Muted")
                note.setWordWrap(True)
                group_layout.addWidget(note)
            form = QFormLayout()
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            for definition in definitions:
                editor = _ScrollSafeDoubleSpinBox()
                editor.setRange(definition.minimum, definition.maximum)
                editor.setDecimals(definition.decimals)
                editor.setSingleStep(definition.step)
                editor.setKeyboardTracking(False)
                editor.setSuffix(f" {definition.unit}")
                editor.valueChanged.connect(self._hh_controls_edited)
                self.hh_editors[definition.key] = editor
                global_labels = {
                    "v_init_mV": "Experiment-global initial voltage",
                    "celsius_C": "Experiment-global temperature",
                }
                form.addRow(global_labels.get(definition.key, definition.label), editor)
            group_layout.addLayout(form)

        chemical_layout = add_hh_section(
            "chemical_synapses", "Chemical-synapse policy"
        )
        chemical_help = QLabel(
            "Imported contacts define pre/post neurons and postsynaptic placement. "
            "This saved policy supplies electrophysiology that is absent from many "
            "connectome exports; each value and its source history are written into the run manifest."
        )
        chemical_help.setObjectName("Muted")
        chemical_help.setWordWrap(True)
        chemical_layout.addWidget(chemical_help)
        chemical_form = QFormLayout()
        self.chemical_mechanism_label = QLabel("Exp2Syn · per imported contact")
        chemical_form.addRow("Mechanism", self.chemical_mechanism_label)
        self.chemical_default_weight = self._new_gap_editor(
            0.0, 1000.0, 0.000001, 7, " µS"
        )
        self.chemical_default_weight.setObjectName("ChemicalDefaultWeight")
        chemical_form.addRow("Fallback contact weight", self.chemical_default_weight)
        self.chemical_weight_scale = self._new_gap_editor(0.0, 1000000.0, 0.1, 5)
        self.chemical_weight_scale.setObjectName("ChemicalWeightScale")
        chemical_form.addRow("Imported weight scale", self.chemical_weight_scale)
        self.chemical_delay = self._new_gap_editor(0.000001, 10000.0, 0.1, 4, " ms")
        self.chemical_delay.setObjectName("ChemicalDefaultDelay")
        chemical_form.addRow("Fallback delay", self.chemical_delay)
        self.chemical_kinetics_policy = QComboBox()
        self.chemical_kinetics_policy.setObjectName("ChemicalKineticsPolicy")
        self.chemical_kinetics_policy.addItem("Source values; use these only when absent", "source_or_default")
        self.chemical_kinetics_policy.addItem("Override every contact with these values", "global_override")
        self.chemical_kinetics_policy.setToolTip("Choose explicit precedence for rise/decay tau and reversal; imported source values are preserved unless Override is selected.")
        chemical_form.addRow("Kinetics and reversal", self.chemical_kinetics_policy)
        self.chemical_tau1 = self._new_gap_editor(0.000001, 10000.0, 0.1, 4, " ms")
        self.chemical_tau1.setObjectName("ChemicalTau1")
        chemical_form.addRow("Rise tau", self.chemical_tau1)
        self.chemical_tau2 = self._new_gap_editor(0.000001, 10000.0, 0.1, 4, " ms")
        self.chemical_tau2.setObjectName("ChemicalTau2")
        chemical_form.addRow("Decay tau", self.chemical_tau2)
        self.chemical_reversal = self._new_gap_editor(-200.0, 200.0, 1.0, 3, " mV")
        self.chemical_reversal.setObjectName("ChemicalReversal")
        chemical_form.addRow("Reversal", self.chemical_reversal)
        self.chemical_threshold = self._new_gap_editor(-200.0, 200.0, 1.0, 3, " mV")
        self.chemical_threshold.setObjectName("ChemicalSpikeThreshold")
        chemical_form.addRow("Presynaptic threshold", self.chemical_threshold)
        self.chemical_geom_delay = QCheckBox("Use soma-to-contact distance")
        self.chemical_geom_delay.setObjectName("ChemicalGeometricDelay")
        chemical_form.addRow("Geometric delay", self.chemical_geom_delay)
        self.chemical_base_delay = self._new_gap_editor(0.0, 10000.0, 0.1, 4, " ms")
        chemical_form.addRow("Release delay", self.chemical_base_delay)
        self.chemical_velocity = self._new_gap_editor(
            0.000001, 10000000.0, 100.0, 3, " µm/ms"
        )
        chemical_form.addRow("Conduction velocity", self.chemical_velocity)
        for editor in (
            self.chemical_default_weight,
            self.chemical_weight_scale,
            self.chemical_delay,
            self.chemical_tau1,
            self.chemical_tau2,
            self.chemical_reversal,
            self.chemical_threshold,
            self.chemical_base_delay,
            self.chemical_velocity,
        ):
            editor.valueChanged.connect(self._chemical_controls_changed)
        self.chemical_geom_delay.toggled.connect(self._chemical_controls_changed)
        self.chemical_kinetics_policy.currentIndexChanged.connect(self._chemical_controls_changed)
        chemical_layout.addLayout(chemical_form)
        save_chemical_policy = QPushButton(
            "Set policy for all imported chemical contacts"
        )
        save_chemical_policy.setObjectName("ApplyChemicalPolicyButton")
        save_chemical_policy.clicked.connect(self.apply_chemical_policy)
        chemical_layout.addWidget(save_chemical_policy)

        gap_layout = add_hh_section("gap_junctions", "Gap-junction policy")
        gap_help = QLabel(
            "GJs join two electrical-edge endpoints, so they cannot be attached to a neuron alone. "
            "This saves a mass-apply policy for all electrical edges once connectivity is loaded."
        )
        gap_help.setObjectName("Muted")
        gap_help.setWordWrap(True)
        gap_layout.addWidget(gap_help)
        gap_form = QFormLayout()
        self.gap_mode_combo = QComboBox()
        self.gap_mode_combo.setObjectName("GapModeCombo")
        self.gap_mode_combo.addItem("None", "none")
        self.gap_mode_combo.addItem("Ohmic · Gap / digifly_gap", "ohmic")
        self.gap_mode_combo.addItem("Ideal rectifier · RectGap", "rectifying")
        self.gap_mode_combo.addItem(
            "Kinetic heterotypic · HeteroRectGap", "heterotypic_rectifying"
        )
        self.gap_mode_combo.currentIndexChanged.connect(self._gap_controls_changed)
        gap_form.addRow("Edge mechanism", self.gap_mode_combo)
        self.gap_g_editor = self._new_gap_editor(0.0, 1000.0, 0.001, 6, " µS")
        self.gap_g_editor.setObjectName("GapConductance")
        gap_form.addRow("Open conductance", self.gap_g_editor)
        self.gap_direction_combo = QComboBox()
        self.gap_direction_combo.addItem("Endpoint A → B", "a_to_b")
        self.gap_direction_combo.addItem("Endpoint B → A", "b_to_a")
        self.gap_direction_combo.currentIndexChanged.connect(self._gap_controls_changed)
        gap_form.addRow("Preferred direction", self.gap_direction_combo)
        self.gap_placement_combo = QComboBox()
        self.gap_placement_combo.addItem("Imported contact sites", "imported_contact_sites")
        self.gap_placement_combo.addItem("One AIS site per pair", "ais_pair")
        self.gap_placement_combo.addItem("One soma site per pair", "soma_pair")
        self.gap_placement_combo.currentIndexChanged.connect(self._gap_controls_changed)
        gap_form.addRow("Endpoint placement", self.gap_placement_combo)
        self.gap_basis_combo = QComboBox()
        self.gap_basis_combo.addItem("Per contact/site", "per_site")
        self.gap_basis_combo.addItem(
            "Total per neuron pair (equal split across sites)", "pair_total"
        )
        self.gap_basis_combo.currentIndexChanged.connect(self._gap_controls_changed)
        gap_form.addRow("Conductance basis", self.gap_basis_combo)
        self.gap_closed_editor = self._new_gap_editor(0.0, 1.0, 0.05, 4)
        gap_form.addRow("Closed fraction", self.gap_closed_editor)
        self.gap_vhalf_editor = self._new_gap_editor(-200.0, 200.0, 1.0, 3, " mV")
        gap_form.addRow("Half activation", self.gap_vhalf_editor)
        self.gap_vslope_editor = self._new_gap_editor(0.000001, 200.0, 0.5, 4, " mV")
        gap_form.addRow("Voltage slope", self.gap_vslope_editor)
        self.gap_residual_editor = self._new_gap_editor(0.0, 1.0, 0.05, 4)
        gap_form.addRow("Empirical residual", self.gap_residual_editor)
        self.gap_endpoint_a_role = QLineEdit("unspecified")
        self.gap_endpoint_a_role.setPlaceholderText("e.g. GF ShakB(N+16)-like")
        self.gap_endpoint_a_role.textEdited.connect(self._gap_controls_changed)
        gap_form.addRow("Endpoint A role", self.gap_endpoint_a_role)
        self.gap_endpoint_b_role = QLineEdit("unspecified")
        self.gap_endpoint_b_role.setPlaceholderText("e.g. target ShakB(lethal)-like")
        self.gap_endpoint_b_role.textEdited.connect(self._gap_controls_changed)
        gap_form.addRow("Endpoint B role", self.gap_endpoint_b_role)
        self.gap_tau_open_editor = self._new_gap_editor(0.000001, 10000.0, 0.5, 3, " ms")
        gap_form.addRow("Opening tau", self.gap_tau_open_editor)
        self.gap_tau_close_editor = self._new_gap_editor(0.000001, 10000.0, 0.5, 3, " ms")
        gap_form.addRow("Closing tau", self.gap_tau_close_editor)
        for editor in (
            self.gap_g_editor,
            self.gap_closed_editor,
            self.gap_vhalf_editor,
            self.gap_vslope_editor,
            self.gap_residual_editor,
            self.gap_tau_open_editor,
            self.gap_tau_close_editor,
        ):
            editor.valueChanged.connect(self._gap_controls_changed)
        gap_layout.addLayout(gap_form)
        self.gap_effective_floor_label = QLabel()
        self.gap_effective_floor_label.setObjectName("Muted")
        self.gap_effective_floor_label.setWordWrap(True)
        gap_layout.addWidget(self.gap_effective_floor_label)
        save_gap_policy = QPushButton("Set mass-apply policy for all electrical edges")
        save_gap_policy.setObjectName("ApplyGapPolicyButton")
        save_gap_policy.clicked.connect(self.apply_gap_policy)
        gap_layout.addWidget(save_gap_policy)

        apply_layout = add_hh_section("apply_save", "Apply & save")
        self.mechanism_capability_label = QLabel()
        self.mechanism_capability_label.setObjectName("Muted")
        self.mechanism_capability_label.setWordWrap(True)
        apply_layout.addWidget(self.mechanism_capability_label)

        apply_defaults = QPushButton("Set HH + channels as cell-set defaults")
        apply_defaults.clicked.connect(self.apply_cell_set_defaults)
        apply_layout.addWidget(apply_defaults)
        self.apply_neuron_button = QPushButton("Apply HH + channels to selected neuron")
        self.apply_neuron_button.clicked.connect(self.apply_neuron_override)
        apply_layout.addWidget(self.apply_neuron_button)
        self.apply_compartments_button = QPushButton(
            "Apply HH + channels to selected SWC segments"
        )
        self.apply_compartments_button.setProperty("primary", True)
        self.apply_compartments_button.clicked.connect(self.apply_compartment_overrides)
        apply_layout.addWidget(self.apply_compartments_button)
        self.mass_apply_button = QPushButton("Mass apply HH + channels to all loaded neurons")
        self.mass_apply_button.setObjectName("MassApplyChannelsButton")
        self.mass_apply_button.clicked.connect(self.mass_apply_to_loaded_neurons)
        apply_layout.addWidget(self.mass_apply_button)
        save = QPushButton("Save unchanged SWC + biophysics bundle")
        save.clicked.connect(self.save_custom_neuron)
        apply_layout.addWidget(save)
        self.override_summary = QLabel("No local overrides")
        self.override_summary.setObjectName("Muted")
        self.override_summary.setWordWrap(True)
        apply_layout.addWidget(self.override_summary)
        side_layout.addStretch(1)
        side.setMinimumWidth(440)
        side_scroll.setWidget(side)
        side_scroll.setMinimumWidth(470)
        side_scroll.setMaximumWidth(560)
        splitter.addWidget(side_scroll)
        splitter.setSizes((760, 500))
        root.addWidget(splitter, 1)

        self._set_hh(HodgkinHuxleySpec())
        self._set_membrane(self.spec.membrane)
        self._set_chemical_policy(self.spec.chemical_synapse_policy)
        self._set_gap_policy(self.spec.gap_junction_policy)
        self.refresh_connectomes()
        self.query_edit.textEdited.connect(self._selection_controls_changed)
        self.limit_spin.valueChanged.connect(self._selection_controls_changed)
        self.connectome_combo.currentIndexChanged.connect(self._source_changed)
        self.family_combo.currentIndexChanged.connect(self._family_changed)
        self.release_combo.currentIndexChanged.connect(self._release_changed)
        for combo in self.findChildren(QComboBox):
            combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        for label in self.findChildren(QLabel):
            make_label_copyable(label)
        self._update_viewport_guidance()
        self._set_mixed_selection_design(False)
        self._engine_changed()

    @staticmethod
    def _new_channel_editor(step: float) -> QDoubleSpinBox:
        editor = _ScrollSafeDoubleSpinBox()
        editor.setRange(0.0, 10.0)
        editor.setDecimals(7)
        editor.setSingleStep(float(step))
        editor.setKeyboardTracking(False)
        editor.setMinimumWidth(82)
        return editor

    @staticmethod
    def _new_gap_editor(
        minimum: float,
        maximum: float,
        step: float,
        decimals: int,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        editor = _ScrollSafeDoubleSpinBox()
        editor.setRange(minimum, maximum)
        editor.setDecimals(decimals)
        editor.setSingleStep(step)
        editor.setKeyboardTracking(False)
        if suffix:
            editor.setSuffix(suffix)
        return editor

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
            "arbor": "HH and the 11 locked native-channel equation ports support default, per-neuron and per-SWC-edge settings. Original rate-TABLE interpolation is preserved. Mapped soma/all/selected-edge and geometric AIS stimulation/recording are supported; chemical/electrical adapters retain separate gates.",
            "neuron": "Selected-cell execution supports locked native channels, HH replacement and default/per-neuron/per-SWC-edge settings, including mapped region stimulation/recording and a geometric AIS proxy. Chemical contacts and app-owned Gap/RectGap/HeteroRectGap mechanisms retain their separate gates.",
            "bmtk": "The bounded BioNet lane materializes HH/native channels and explicit SWC-edge settings through SONATA, with mapped soma/all/selected-edge and geometric AIS stimulation/recording. Chemical contacts use SONATA; exact imported Gap/RectGap/HeteroRectGap sites use a pinned Digifly serial extension on BioNetwork cells (not standard SONATA electrical portability). MPI, PointNet and DPointNet remain fail-closed.",
        }[key]
        self.engine_note.setText(
            f"{profile_summary}  {parity}  Local pair connectivity is inspected read-only and "
            "pair enable/disable intent is serialized for execution adapters; a generic circuit "
            "execution plan is not built by this page yet."
        )
        self._update_mechanism_capability()
        self.status_message.emit(f"Selected {profile_name} as the design target")

    def refresh_connectomes(self) -> None:
        self.choice_panel.hide()
        self._choice_generation += 1
        self._catalog_cache.clear()
        previous_source = self._selected_source()
        try:
            profile = load_default_profile()
        except (OSError, ValueError):
            profile = None
        all_sources = discover_connectomes(
                self.overview.workspace_edit.text(),
                morphology_library_root=morphology_library_root(),
                external_sources=profile_connectome_sources(profile),
            )
        self._library_discovery_error = ''
        try:
            virtual = library_sources(profile, _NEUPRINT_DATASETS, all_sources)
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._library_discovery_error = str(error)
            virtual = library_sources(None, _NEUPRINT_DATASETS)
        discovered = (*virtual, *all_sources)
        if (
            previous_source is not None
            and _source_identity(previous_source) not in {_source_identity(source) for source in all_sources}
            and _source_identity(previous_source) not in {_source_identity(source) for source in discovered}
            and Path(previous_source.root).expanduser().is_dir()
        ):
            discovered = (*discovered, previous_source)
        self._sources = tuple(attach_registered_pair_graph(source, profile) for source in discovered)
        if previous_source is not None and self.spec.neuron_ids:
            # Refreshing the library does not rebind an already loaded project.
            self._sources = tuple(previous_source if _source_identity(s) == _source_identity(previous_source)
                                  else s for s in self._sources)
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
            elif self._sources:
                workspace_root = Path(
                    self.overview.workspace_edit.text()
                ).expanduser().resolve()
                profile_matches_workspace = bool(
                    profile is not None
                    and profile.workspace_root is not None
                    and profile.workspace_root == workspace_root
                )
                preferred_index = next(
                    (
                        index
                        for index, source in enumerate(self._sources)
                        if profile_matches_workspace
                        and source.key == "library:manc:v1.2.1"
                    ),
                    -1,
                )
                if preferred_index < 0:
                    preferred_index = next(
                        (
                            index
                            for index, source in enumerate(self._sources)
                            if source.root and Path(source.root).expanduser().resolve().is_relative_to(
                                workspace_root
                            )
                        ),
                        -1,
                    )
                if preferred_index < 0:
                    preferred_index = next(
                        (
                            index
                            for index, source in enumerate(self._sources)
                            if source.dataset == "custom"
                        ),
                        0,
                    )
                self.connectome_combo.setCurrentIndex(preferred_index)
        finally:
            self._restoring_controls = prior_guard
        self.assemble_button.setEnabled(bool(self._sources))
        if self._sources:
            self.status_message.emit(f"Found {len(self._sources)} local SWC/morphology source(s)")
        else:
            self.status_message.emit("No local SWC morphology source was found under the configured workspace")
        current_source = self._selected_source()
        self._update_pair_graph_note()
        self._sync_source_controls()
        self._start_source_inventory()
        if (
            previous_source is not None
            and (current_source is None or _source_identity(current_source) != _source_identity(previous_source))
            and not prior_guard
        ):
            self._selection_controls_changed()

    def _sync_source_controls(self):
        source = self._selected_source()
        dataset = canonical_dataset(source.dataset) if source else ''
        choice = next((c for c in CONNECTOMES if dataset in c.releases), None)
        for widget in (self.family_combo, self.release_combo):
            widget.blockSignals(True)
        self.family_combo.setCurrentIndex(self.family_combo.findData(choice.key if choice else 'other'))
        self.release_combo.clear()
        if choice:
            for release in choice.releases:
                for item in self._sources:
                    if item.server and item.dataset == release:
                        suffix = f' · {item.server}' if '@' in item.key else ''
                        self.release_combo.addItem(release.split(':')[-1] + suffix, item.key)
        else:
            for item in self._sources:
                if not item.server and not canonical_dataset(item.dataset):
                    self.release_combo.addItem(item.label, item.key)
        index = self.release_combo.findData(source.key if source else '')
        if index < 0 and dataset:
            index = self.release_combo.findData(f'library:{dataset}')
        self.release_combo.setCurrentIndex(index)
        for widget in (self.family_combo, self.release_combo):
            widget.blockSignals(False)
        self.family_button.setText(self.family_combo.currentText() + ' ▾')
        self.family_button.setAccessibleName('Connectome: ' + self.family_combo.currentText())
        self.release_button.setText(self.release_combo.currentText() + ' ▾')
        self.release_button.setAccessibleName('Release: ' + self.release_combo.currentText())

    def _open_inline_choices(self, combo, kind):
        # Keep one set of ordinary accessible choice buttons, but anchor it
        # beneath the control that opened it rather than always at the top.
        slot = self._simulator_choice_slot if combo is self.engine_combo else self._source_choice_slot
        if slot is not self._active_choice_slot:
            self._active_choice_slot.removeWidget(self.choice_panel)
            slot.addWidget(self.choice_panel)
            self._active_choice_slot = slot
        self._choice_generation += 1
        generation = self._choice_generation
        for button in self.choice_buttons:
            self.choice_grid.removeWidget(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self.choice_buttons = []
        for index in range(combo.count()):
            button = QPushButton(combo.itemText(index))
            button.setAccessibleName(f'Select {kind}: {combo.itemText(index)}')
            button.clicked.connect(lambda checked=False, i=index: self._choose_inline(combo, i, generation))
            self.choice_grid.addWidget(button, index // 3, index % 3)
            self.choice_buttons.append(button)
        cancel = QPushButton('Close choices')
        cancel.clicked.connect(self.choice_panel.hide)
        self.choice_grid.addWidget(cancel, (combo.count() + 2) // 3, 2)
        self.choice_buttons.append(cancel)
        self.choice_panel.show()

    def _choose_inline(self, combo, index, generation):
        if generation != self._choice_generation:
            return
        self.choice_panel.hide()
        self._choice_generation += 1
        unchanged = combo.currentIndex() == index
        combo.setCurrentIndex(index)
        # A restored legacy project can display this release while retaining
        # its exact old root. Explicitly choosing it opts into the shared view.
        if unchanged and combo is self.release_combo:
            self._release_changed()
        elif unchanged and combo is self.family_combo:
            self._family_changed()

    def _family_changed(self):
        key = self.family_combo.currentData()
        choice = next((c for c in CONNECTOMES if c.key == key), None)
        source = next((s for s in self._sources if s.key == f'library:{choice.releases[0]}'), None) if choice else next(
            (s for s in self._sources if not s.server and not canonical_dataset(s.dataset)), None)
        if source:
            self.connectome_combo.setCurrentIndex(self.connectome_combo.findData(source.key))
        else:
            self._sync_source_controls()

    def _release_changed(self):
        key = self.release_combo.currentData()
        if key:
            self.connectome_combo.setCurrentIndex(self.connectome_combo.findData(key))

    def select_library_release(self, dataset):
        """Explicit Data Library handoff; never rebuild a circuit automatically."""
        index = self.connectome_combo.findData(f'library:{canonical_dataset(dataset)}')
        if index >= 0:
            self.connectome_combo.setCurrentIndex(index)

    def _source_changed(self):
        self._assemble_after_inventory = None
        self.choice_panel.hide()
        self._choice_generation += 1
        self._selection_controls_changed()
        self._sync_source_controls()
        self._start_source_inventory()

    def _start_source_inventory(self):
        source = self._selected_source()
        self.browse_button.setEnabled(bool(source and source.server))
        self.pair_graph_button.setVisible(bool(source and canonical_dataset(source.dataset).startswith('manc:')))
        if source and source.server:
            if getattr(self, '_library_discovery_error', ''):
                self.saved_browser.cancel(quiet=True)
                self.saved_browser._reset()
                self.source_summary.setText('Saved locations could not be read. Open Data Library: ' + self._library_discovery_error)
                self.assemble_button.setEnabled(False)
                return
            folders = source_folders(source)
            self.source_summary.setText(f'{source.dataset} · {len(folders)} saved location(s) · Counting saved neurons…')
            self.assemble_button.setEnabled(False)
            self.saved_browser.load(folders, source.dataset)
        else:
            self.saved_browser.cancel(quiet=True)
            self.saved_browser._reset()
            self.source_summary.setText(f'Saved source: {source.label} · {source.root}' if source else 'Choose a connectome')
            self.assemble_button.setEnabled(source is not None)

    def _inventory_ready(self):
        source, inventory = self._selected_source(), self.saved_browser.inventory
        if not source or not source.server or inventory is None or inventory.dataset != source.dataset:
            return
        catalog = ConnectomeCatalog.from_saved_inventory(source, inventory)
        self._catalog_cache[self._catalog_key(source)] = catalog
        count = len(catalog.records)
        issues = f' · {len(inventory.issues)} folder issue(s); count may be incomplete' if inventory.issues else ''
        self.source_summary.setText(f'{count:,} saved neurons · {source.dataset} · {len(source.swc_sources)} location(s){issues}')
        self.source_summary.setToolTip('Search combines matching folders. Preferred SWC variants use the existing '
            'synapse-bearing/healed/plain order. Different equally preferred copies require your choice. '
            'Counts describe saved files, not complete biological coverage.')
        self.assemble_button.setEnabled(bool(count))
        if not count:
            self._missing_neuprint_request = None
            self.source_summary.setText(self.source_summary.text() + ' · Use Data Library to add or download SWCs.')
        pending = self._assemble_after_inventory
        if pending == (source.key, self.query_edit.text(), self.saved_browser.generation):
            self._assemble_after_inventory = None
            self.assemble_circuit()

    def _add_browsed_neuron(self, body):
        tokens = [s.strip() for s in re.split(r'[,;\n]+', self.query_edit.text()) if s.strip()]
        if body not in tokens:
            self.query_edit.setText(', '.join((*tokens, body)))

    @staticmethod
    def _catalog_key(source):
        return source.key, json.dumps(source.to_dict(), sort_keys=True)

    def shutdown(self):
        self.saved_browser.shutdown()

    def closeEvent(self, event):
        self.shutdown()
        super().closeEvent(event)

    def review_recent_imports(self) -> None:
        """Offer explicit copy/overwrite choices for recent imported SWC findings."""

        try:
            profile = load_default_profile()
            if profile is None:
                raise ValueError("no Workstation resource profile is configured")
            batch = scan_recent_imports(profile)
        except (OSError, ValueError) as exc:
            self.status_message.emit(f"Could not audit recent SWC imports: {exc}")
            return
        pending = [
            report
            for report in batch.review_reports
            if report.sha256 not in reviewed_hashes(report)
        ]
        if not pending:
            self.status_message.emit(
                f"Audited {len(batch.reports)} recent SWC import(s); no unreviewed radius flags"
            )
            return

        completed = 0
        for report in pending:
            identity = report.body_id or report.instance or Path(report.path).stem
            box = QMessageBox(self)
            box.setIcon(
                QMessageBox.Icon.Warning if report.errors else QMessageBox.Icon.Question
            )
            box.setWindowTitle("Recent SWC import needs review")
            box.setText(
                f"{report.dataset or 'Imported SWC'} · {identity}\n"
                f"{len(report.findings)} unusually small compartment(s) were flagged "
                "relative to this neuron's own topology and radius distribution."
            )
            box.setInformativeText(
                "The healer changes radii only. Saving a separately named healed SWC is "
                "recommended; overwrite keeps the original filename and first creates a backup."
            )
            details = [
                f"Source: {report.path}",
                f"SHA-256: {report.sha256}",
                f"Detected units: {report.source_unit}",
                "",
            ]
            details.extend(
                f"node {finding.node_id}: {finding.radius_um:.6g} -> "
                f"{finding.suggested_radius_um:.6g} um · {finding.reason}"
                for finding in report.findings
            )
            if report.errors:
                details.extend(("", "Structural errors (automatic healing disabled):"))
                details.extend(report.errors)
            if report.warnings:
                details.extend(("", "Warnings:"))
                details.extend(report.warnings)
            box.setDetailedText("\n".join(details))

            keep_button = box.addButton("Keep original", QMessageBox.ButtonRole.RejectRole)
            if report.can_heal:
                copy_button = box.addButton(
                    "Save healed copy", QMessageBox.ButtonRole.AcceptRole
                )
                overwrite_button = box.addButton(
                    "Overwrite original", QMessageBox.ButtonRole.DestructiveRole
                )
                box.setDefaultButton(copy_button)
            else:
                copy_button = overwrite_button = None
                box.setDefaultButton(keep_button)
            box.exec()
            clicked = box.clickedButton()
            try:
                if clicked is copy_button and copy_button is not None:
                    result = heal_swc(report, mode="copy")
                    record_review_decision(report, action="copy", healing_result=result)
                    self.status_message.emit(f"Saved radius-healed SWC copy: {result.output_path}")
                    completed += 1
                elif clicked is overwrite_button and overwrite_button is not None:
                    result = heal_swc(report, mode="overwrite")
                    record_review_decision(
                        report, action="overwrite", healing_result=result
                    )
                    self.status_message.emit(
                        f"Healed imported SWC in place; backup: {result.backup_path}"
                    )
                    completed += 1
                elif clicked is keep_button:
                    record_review_decision(report, action="keep")
            except (OSError, ValueError) as exc:
                self.status_message.emit(f"Could not heal {identity}: {exc}")

        if completed:
            self.refresh_connectomes()

    def _selection_controls_changed(self, *_args: Any) -> None:
        if self._restoring_controls:
            return
        self._clear_missing_neuprint_request()
        self._update_pair_graph_note()
        if self.spec.neuron_ids or self.loaded_morphologies:
            self._clear_loaded_assets("Selection changed · click Load neurons")
            self.status_message.emit("Cell-set controls changed; reload morphologies before saving")

    def _clear_loaded_assets(self, summary: str = "No morphology loaded") -> None:
        self.catalog = None
        self._edge_catalog = None
        self.loaded_records.clear()
        self.loaded_morphologies.clear()
        self.viewport.clear()
        self.neuron_table.setRowCount(0)
        self.viewport_summary.setText(summary)
        self.selection_label.setText("No neuron selected")
        self.pair_panel.setVisible(False)
        self.override_summary.setText("No local overrides")
        self.spec.neuron_ids = ()
        self.spec.morphology_sha256.clear()
        self.spec.neuron_overrides.clear()
        self.spec.compartment_overrides.clear()
        self.spec.neuron_mechanism_overrides.clear()
        self.spec.compartment_mechanism_overrides.clear()
        self.spec.connection_overrides.clear()
        self._set_hh(self.spec.hh)
        self._set_membrane(self.spec.membrane)
        self._set_mixed_selection_design(False)
        self._update_viewport_guidance()

    def _selected_source(self) -> ConnectomeRef | None:
        index = self.connectome_combo.currentIndex()
        return self._sources[index] if 0 <= index < len(self._sources) else None

    def _update_pair_graph_note(self) -> None:
        source = self._selected_source()
        if source is None or not source.pair_graph_path:
            self.pair_graph_note.setText("Connectivity: native contact source; no aggregate pair file selected")
            return
        try:
            manifest = json.loads((Path(source.pair_graph_index).parent / "manifest.json").read_text(encoding="utf-8"))
            release_note = manifest.get('source_scope', 'release unconfirmed')
            self.pair_graph_note.setText(f"Connectivity: {Path(source.pair_graph_path).name} · {manifest['rows']:,} directed pairs · {manifest['represented_neurons']:,} IDs · total_weight {manifest['min_weight']}–{manifest['max_weight']} · read-only; {release_note}")
        except (OSError, ValueError, KeyError):
            self.pair_graph_note.setText("Selected connectivity file is unavailable. Open Data Library to review it; no fallback source is used.")

    def open_pair_graph_import(self) -> None:
        source = self._selected_source()
        if source is None or not (source.key.startswith("manc:") or source.dataset.startswith("manc")):
            QMessageBox.information(self, "Select MANC first", "Choose the existing MANC SWC source before attaching this MANC pair file. The morphologies will not be replaced.")
            return
        reader = self.overview.python_edit.text() if hasattr(self.overview, "python_edit") else ""
        try:
            profile = load_default_profile()
            if profile:
                for binding in profile.bindings(ResourceKind.DATA_SOURCE):
                    if binding.metadata.get("morphology_key") == source.key and binding.metadata.get("reader_python"):
                        reader = str(binding.metadata["reader_python"])
        except (OSError, ValueError):
            pass
        dialog = PairGraphImportDialog(source, reader, self)
        dialog.exec()
        if dialog.registered_source is None:
            return
        selected = dialog.registered_source
        self._sources = tuple(selected if _source_identity(item) == _source_identity(source) else item for item in self._sources)
        self.spec.connectome = selected
        self._edge_catalog = ConnectomeEdgeCatalog(selected, DigiflyWorkspace(self.overview.workspace_edit.text()))
        self._update_pair_graph_note()
        self._refresh_pair_panel()
        self.circuit_changed.emit(self.spec)
        self.status_message.emit("Using the selected MANC Parquet for aggregate pair lookup; no experiment was started")

    def _catalog_for(self, source: ConnectomeRef) -> ConnectomeCatalog:
        cache_key = self._catalog_key(source)
        catalog = self._catalog_cache.get(cache_key)
        if catalog is None:
            if source.server:
                inventory = self.saved_browser.inventory
                if inventory is not None and inventory.dataset == source.dataset:
                    catalog = ConnectomeCatalog.from_saved_inventory(source, inventory)
                elif source.swc_paths:
                    # Reopen only the bounded, exact saved files while the
                    # full read-only inventory continues in its worker.
                    from digifly_app.core.saved_neurons import _labels
                    folders = source_folders(source)
                    records = []
                    for body, raw_path in source.swc_paths.items():
                        path = Path(raw_path).expanduser()
                        root = next((f.root.resolve() for f in folders if path.is_relative_to(f.root.resolve())), None)
                        if root is None or not path.is_file() or any(p.is_symlink() for p in (path, *path.parents)):
                            raise ValueError(f'Saved SWC file is unavailable or outside its saved location: {path}')
                        family, label = _labels(path, root, body)
                        records.append(NeuronRecord(body, family, label, str(path), source.key))
                    catalog = ConnectomeCatalog(source, records)
                else:
                    raise ValueError('Saved neurons are still being indexed. Wait for the saved-neuron count, or refresh after a cancelled scan.')
            else:
                catalog = ConnectomeCatalog.scan(source)
            self._catalog_cache[cache_key] = catalog
        return catalog

    def _clear_missing_neuprint_request(self) -> None:
        self._missing_neuprint_request = None
        self.download_missing_button.setVisible(False)

    def open_missing_neuprint_import(self) -> None:
        pending = self._missing_neuprint_request
        if pending is None:
            return
        dataset, selection, expression = pending
        try:
            profile_path = default_profile_path()
            profile = load_default_profile()
            if profile is None:
                raise ValueError(
                    "No Workstation resource profile is configured. Set up the Workspace first."
                )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Data Library is not configured", str(exc))
            return

        imported: list[ManagedResource] = []
        dialog = NeuPrintImportDialog(
            profile,
            profile_path,
            self,
            preferred_dataset=dataset,
            initial_selection=selection,
            initial_resource_id=selection.value,
        )
        dialog.resource_imported.connect(imported.append)
        dialog.exec()
        if not imported:
            return

        resource = imported[-1]
        library_manifest = json.loads(resource.manifest.read_text(encoding="utf-8"))
        target_root = (resource.root / ("export_swc" if library_manifest.get("library_schema")
                                      else "source/export_swc")).resolve()
        self.refresh_connectomes()
        source_index = next(
            (
                index
                for index, source in enumerate(self._sources)
                if source.key == f'library:{dataset}'
            ),
            -1,
        )
        if source_index < 0:
            self.status_message.emit(
                "The neuPrint download completed, but its registered SWC source was not found. "
                "Refresh sources or inspect the Data Library."
            )
            self.managed_data_changed.emit()
            return
        self._restoring_controls = True
        try:
            self.connectome_combo.setCurrentIndex(source_index)
            self.query_edit.setText(expression)
        finally:
            self._restoring_controls = False
        self._clear_missing_neuprint_request()
        self.managed_data_changed.emit()
        self.review_recent_imports()
        # This is the existing explicit Get missing SWCs workflow, not an
        # automatic load on release changes. Wait for its combined inventory.
        self._assemble_after_inventory = (self._selected_source().key, expression, self.saved_browser.generation)
        if self.saved_browser.inventory is not None:
            self._inventory_ready()

    def assemble_circuit(self) -> None:
        source = self._selected_source()
        if source is None:
            self.status_message.emit("Choose an available SWC morphology source first")
            return
        if source.server and self.saved_browser.inventory is None:
            self.status_message.emit('Saved neurons are still being indexed. Wait for the count or refresh a cancelled scan.')
            return
        if source.key.startswith('library:'):
            # Explicit Load chooses current connectivity. Merely reopening or
            # refreshing a saved circuit retains its original immutable graph.
            try:
                profile = load_default_profile()
                latest = next((s for s in library_sources(profile, (source.dataset,))
                               if s.key == source.key and s.server == source.server), None)
                if latest is not None:
                    # Saved/external roots may not be in this machine's current
                    # profile. Add current locations without discarding those
                    # exact saved inputs merely because discovery is narrower.
                    locations = {(row['root'], row['dataset'], row['server']): row
                                 for row in (*source.swc_sources, *latest.swc_sources)}
                    locations = tuple(locations.values())
                    locations_changed = source.swc_sources != locations
                    source = replace(source, pair_graph_path=latest.pair_graph_path,
                        pair_graph_index=latest.pair_graph_index, pair_graph_sha256=latest.pair_graph_sha256,
                        swc_sources=locations)
                    self._sources = tuple(source if i == self.connectome_combo.currentIndex() else s
                                          for i, s in enumerate(self._sources))
                    self._catalog_cache.clear()
                    self._edge_catalog = None
                    self._update_pair_graph_note()
                    if locations_changed:
                        self._start_source_inventory()
                        self._assemble_after_inventory = (source.key, self.query_edit.text(), self.saved_browser.generation)
                        return
            except (OSError, ValueError, KeyError) as exc:
                self.status_message.emit('Current connectivity could not be read: ' + str(exc))
                return
        expression = self.query_edit.text().strip()
        self._clear_missing_neuprint_request()
        self.assemble_button.setEnabled(False)
        self.viewport_summary.setText(f"Indexing {source.label}…")
        QApplication.processEvents()
        try:
            catalog = self._catalog_for(source)
            result = catalog.query(expression, limit=self.limit_spin.value())
            try:
                catalog.verify_selection(result.records)
            except ValueError:
                pins = dict(source.swc_paths)
                for record in result.records:
                    paths = catalog.alternatives.get(record.neuron_id, ())
                    if len(paths) < 2:
                        continue
                    labels = [str(path) for path in paths]
                    chosen, ok = QInputDialog.getItem(self, 'Choose saved SWC copy',
                        f'Choose the SWC file for neuron {record.neuron_id}. No file will be replaced.', labels, 0, False)
                    if not ok:
                        self.status_message.emit('Loading cancelled; saved SWCs unchanged')
                        return
                    pins[record.neuron_id] = chosen
                source = replace(source, swc_paths=pins)
                catalog = ConnectomeCatalog.from_saved_inventory(source, self.saved_browser.inventory)
                result = catalog.query(expression, limit=self.limit_spin.value())
            if source.server:
                # Freeze selected physical files into the project. Later library
                # additions cannot silently select a different body copy.
                source = replace(source, swc_paths={r.neuron_id: r.swc_path for r in result.records})
                self._sources = tuple(source if i == self.connectome_combo.currentIndex() else s
                                      for i, s in enumerate(self._sources))
            if result.records and result.unmatched:
                dataset = _neuprint_dataset(source)
                selection = _neuprint_selection(expression, self.limit_spin.value())
                if dataset is not None and selection is not None:
                    # Keep the entire reviewed union so the cumulative source
                    # can display the original and newly missing neurons together.
                    self._missing_neuprint_request = (dataset, selection, expression)
                    self.download_missing_button.setVisible(True)
            if not result.records:
                detail = ", ".join(result.unmatched) if result.unmatched else expression
                dataset = _neuprint_dataset(source)
                selection = _neuprint_selection(expression, self.limit_spin.value())
                if dataset is not None and selection is not None:
                    self._missing_neuprint_request = (dataset, selection, expression)
                    self.download_missing_button.setVisible(True)
                    summary = (
                        f"No local SWCs matched: {detail or 'empty query'}. "
                        f"Use Get missing SWCs to download this bounded {dataset} selection."
                    )
                else:
                    summary = f"No local SWCs matched: {detail or 'empty query'}"
                self._clear_loaded_assets(summary)
                self.status_message.emit(self.viewport_summary.text())
                return
            morphologies: list[Morphology] = []
            failures: list[str] = []
            for record in result.records:
                try:
                    morphologies.append(load_swc(record, allow_forest_preview=True, allow_point_preview=True))
                except Exception as exc:
                    failures.append(f"{record.neuron_id}: {exc}")
            if not morphologies:
                self._clear_loaded_assets("Matched records could not be loaded as SWC morphology")
                self.status_message.emit(self.viewport_summary.text())
                return

            previous_neuron_overrides = dict(self.spec.neuron_overrides)
            previous_compartment_overrides = dict(self.spec.compartment_overrides)
            previous_neuron_mechanism_overrides = dict(
                self.spec.neuron_mechanism_overrides
            )
            previous_compartment_mechanism_overrides = dict(
                self.spec.compartment_mechanism_overrides
            )
            previous_connection_overrides = dict(self.spec.connection_overrides)
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
                    saved_membrane = sidecar.get("neuron_mechanism_override") or sidecar.get(
                        "base_membrane"
                    )
                    if saved_membrane:
                        previous_neuron_mechanism_overrides[neuron_id] = (
                            MembraneMechanismSpec.from_dict(saved_membrane).to_dict()
                        )
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
                    restored_mechanisms: dict[str, dict[str, Any]] = {}
                    for node_id, values in dict(
                        sidecar.get("compartment_mechanism_overrides") or {}
                    ).items():
                        try:
                            numeric_node_id = int(str(node_id))
                        except ValueError as exc:
                            raise ValueError(
                                f"Invalid saved mechanism SWC child-node ID: {node_id}"
                            ) from exc
                        if numeric_node_id not in valid_node_ids:
                            raise ValueError(
                                f"Saved mechanism SWC child-node ID {numeric_node_id} is absent "
                                f"from neuron {neuron_id}"
                            )
                        restored_mechanisms[str(numeric_node_id)] = (
                            MembraneMechanismSpec.from_dict(values).to_dict()
                        )
                    if restored_mechanisms:
                        previous_compartment_mechanism_overrides[neuron_id] = (
                            restored_mechanisms
                        )
            self.catalog = catalog
            self._edge_catalog = ConnectomeEdgeCatalog(
                source, DigiflyWorkspace(self.overview.workspace_edit.text())
            )
            self.loaded_records = {item.record.neuron_id: item.record for item in morphologies}
            self.loaded_morphologies = {item.record.neuron_id: item for item in morphologies}
            self.spec = CircuitSpec(
                connectome=source,
                query=NeuronQuery(expression, self.limit_spin.value()),
                neuron_ids=tuple(item.record.neuron_id for item in morphologies),
                hh=HodgkinHuxleySpec.from_dict(self.spec.hh.to_dict()),
                membrane=MembraneMechanismSpec.from_dict(self.spec.membrane.to_dict()),
                chemical_synapse_policy=ChemicalSynapsePolicy.from_dict(
                    self.spec.chemical_synapse_policy.to_dict()
                ),
                gap_junction_policy=GapJunctionPolicy.from_dict(
                    self.spec.gap_junction_policy.to_dict()
                ),
                morphology_sha256={
                    item.record.neuron_id: sha256_file(item.record.swc_path) for item in morphologies
                },
                neuron_overrides={
                    key: value for key, value in previous_neuron_overrides.items() if key in self.loaded_records
                },
                compartment_overrides={
                    key: value for key, value in previous_compartment_overrides.items() if key in self.loaded_records
                },
                neuron_mechanism_overrides={
                    key: value
                    for key, value in previous_neuron_mechanism_overrides.items()
                    if key in self.loaded_records
                },
                compartment_mechanism_overrides={
                    key: value
                    for key, value in previous_compartment_mechanism_overrides.items()
                    if key in self.loaded_records
                },
                connection_overrides={
                    key: value
                    for key, value in previous_connection_overrides.items()
                    if value.neuron_a in self.loaded_records
                    and value.neuron_b in self.loaded_records
                },
            )
            self.viewport.set_morphologies(morphologies)
            self._set_mixed_selection_design(False)
            self._populate_table(morphologies)
            suffixes = []
            if result.truncated:
                suffixes.append(f"capped at {self.limit_spin.value()}")
            if result.unmatched:
                suffixes.append(f"unmatched: {', '.join(result.unmatched)}")
            if failures:
                suffixes.append(f"{len(failures)} failed SWC(s)")
            forests = sum(sum(node.parent_id == -1 for node in item.nodes) > 1 for item in morphologies)
            if forests:
                suffixes.append(f"{forests} disconnected skeleton(s): preview only; not simulation-ready")
            points = sum(not item.segments for item in morphologies)
            if points:
                suffixes.append(f"{points} point-only skeleton(s): no released branches; not simulation-ready")
            conversions = sum(
                (item.coordinate_transform or {}).get("scale_to_um", 1.0) != 1.0
                for item in morphologies
            )
            if conversions:
                suffixes.append(f"shared micrometer frame: {conversions} source-unit conversion(s)")
            suffix = f" · {' · '.join(suffixes)}" if suffixes else ""
            self.viewport_summary.setText(
                f"{len(morphologies)} neuron(s) · {self.viewport.segment_count:,} "
                f"editable compartment(s) (SWC segments){suffix}"
            )
            self._update_viewport_guidance()
            self.status_message.emit(f"Loaded morphology cell set from {source.label}{suffix}")
            self.circuit_changed.emit(self.spec)
        except Exception as exc:
            self._clear_loaded_assets(f"Cell-set load failed: {exc}")
            self.status_message.emit(self.viewport_summary.text())
        finally:
            selected = self._selected_source()
            inventory = self.saved_browser.inventory
            self.assemble_button.setEnabled(bool(selected and (not selected.server or
                (inventory is not None and inventory.dataset == selected.dataset and
                 any(n.server == selected.server for n in inventory.neurons)))))

    def _populate_table(self, morphologies: list[Morphology]) -> None:
        # Keep existing cell objects alive on a same-size refresh. Replacing
        # selected QTableWidgetItems both re-enters selection handlers midway
        # through population and invalidates macOS accessibility references.
        previous = self.neuron_table.blockSignals(True)
        try:
            self.neuron_table.clearSelection()
            self.neuron_table.setRowCount(len(morphologies))
            for row, morphology in enumerate(morphologies):
                record = morphology.record
                values = (record.neuron_id, record.family, record.neuron_type, f"{len(morphology.segments):,}")
                for column, value in enumerate(values):
                    item = self.neuron_table.item(row, column)
                    if item is None:
                        item = QTableWidgetItem(value)
                        self.neuron_table.setItem(row, column, item)
                    else:
                        item.setText(value)
                    item.setData(Qt.ItemDataRole.UserRole, record.neuron_id)
        finally:
            self.neuron_table.blockSignals(previous)
        self.neuron_table.resizeColumnsToContents()

    def _table_clicked(self, row: int, _column: int) -> None:
        if len(self._selected_table_neuron_ids()) != 1:
            return
        item = self.neuron_table.item(row, 0)
        if item is not None:
            self.viewport.focus_neuron(str(item.data(Qt.ItemDataRole.UserRole)), isolate=True)

    def _selected_table_neuron_ids(self) -> tuple[str, ...]:
        rows = sorted(index.row() for index in self.neuron_table.selectionModel().selectedRows(0))
        return tuple(
            str(self.neuron_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
            for row in rows
            if self.neuron_table.item(row, 0) is not None
        )

    def _sync_table_selection(self, neuron_ids: tuple[str, ...]) -> None:
        if self._syncing_neuron_table_selection:
            return
        wanted = set(neuron_ids)
        self._syncing_neuron_table_selection = True
        try:
            self.neuron_table.clearSelection()
            selection_model = self.neuron_table.selectionModel()
            flags = (
                QItemSelectionModel.SelectionFlag.Select
                | QItemSelectionModel.SelectionFlag.Rows
            )
            for row in range(self.neuron_table.rowCount()):
                item = self.neuron_table.item(row, 0)
                if item is None:
                    continue
                neuron_id = str(item.data(Qt.ItemDataRole.UserRole))
                if neuron_id in wanted:
                    selection_model.select(self.neuron_table.model().index(row, 0), flags)
        finally:
            self._syncing_neuron_table_selection = False

    def _table_selection_changed(self) -> None:
        if self._syncing_neuron_table_selection:
            return
        selected = list(self._selected_table_neuron_ids())
        current = self.neuron_table.currentItem()
        current_id = (
            str(current.data(Qt.ItemDataRole.UserRole)) if current is not None else ""
        )
        previous = list(self.viewport.selected_neuron_ids)
        ordered = [neuron_id for neuron_id in previous if neuron_id in selected]
        for neuron_id in selected:
            if neuron_id not in ordered:
                ordered.append(neuron_id)
        if current_id in ordered:
            ordered.remove(current_id)
            ordered.append(current_id)
        ordered = ordered[-2:]

        self._syncing_neuron_table_selection = True
        try:
            if not ordered:
                self.viewport.clear_neuron_selection(restore_overview=True)
            elif len(ordered) == 1:
                self.viewport.focus_neuron(ordered[0], isolate=True)
            else:
                self.viewport.set_neuron_selection(ordered)
        finally:
            self._syncing_neuron_table_selection = False
        self._sync_table_selection(tuple(ordered))

    def _neuron_selected(self, neuron_id: str) -> None:
        record = self.loaded_records.get(neuron_id)
        if record is None:
            return
        if self.viewport.display_mode == DISPLAY_MODE_SOMA_POINTS:
            location = self.viewport.soma_location(neuron_id)
            marker = "pseudosoma" if location and location.is_pseudosoma else "soma"
            point_state = "isolated" if self.viewport.isolated else "focused"
            self.selection_label.setText(
                f"{record.neuron_type} · {neuron_id}\n"
                f"{marker.capitalize()} point {point_state}; switch to Full skeletons "
                "to inspect or select compartments"
            )
        else:
            neuron_state = (
                "Whole neuron isolated" if self.viewport.isolated else "Neuron focused"
            )
            self.selection_label.setText(
                f"{record.neuron_type} · {neuron_id}\n"
                f"{neuron_state}; left-click a skeleton segment or "
                "Cmd/Ctrl+Shift+left-drag a box to select compartments"
            )
        if len(self.viewport.selected_neuron_ids) <= 1:
            self._sync_table_selection((neuron_id,))
        self._load_effective_selection_design(neuron_id, ())
        self._update_override_summary()
        self._update_viewport_guidance()

    def _neurons_selected(self, neuron_ids: object) -> None:
        ids = tuple(str(value) for value in neuron_ids) if isinstance(
            neuron_ids, (tuple, list, set)
        ) else ()
        self._sync_table_selection(ids)
        self._refresh_pair_panel(ids)
        if len(ids) == 2:
            self.selection_label.setText(
                f"Pair selected · {ids[0]} ↔ {ids[1]}\n"
                "Use the connection panel below to inspect and override imported edge classes."
            )
        elif not ids:
            self.selection_label.setText("No neuron selected")
        self._set_mixed_selection_design(False)
        self._update_override_summary()
        self._update_viewport_guidance()

    @staticmethod
    def _connection_class_readout(
        label: str,
        summary: ConnectionClassSummary,
        neuron_a: str,
        neuron_b: str,
    ) -> str:
        if not summary.available:
            return f"{label}: data unavailable — {summary.unavailable_reason}"
        source = summary.source_text or (
            Path(summary.source_path).name if summary.source_path else "loaded edge source"
        )
        scope = f" {summary.scope_note}" if summary.scope_note else ""
        if summary.connection_kind == "weighted pair":
            return (f"Aggregate connectivity: {neuron_a} → {neuron_b}: total_weight {summary.a_to_b_weight}; "
                    f"{neuron_b} → {neuron_a}: total_weight {summary.b_to_a_weight}. "
                    f"{summary.total_count} supplied directed-pair row(s), not located synapses. {source}.{scope}")
        if summary.total_count == 0:
            return (
                f"{label}: no connections found between these neurons in {source}. "
                f"This is a zero in the loaded source, so there is nothing to toggle.{scope}"
            )
        if label == "Gap junction":
            noun = "contact row" if summary.total_count == 1 else "contact rows"
            return (
                f"{label}: {summary.total_count:,} {noun} between these neurons in {source}. "
                "Electrical contacts are treated as bidirectional; CSV endpoint order is not "
                f"direction.{scope}"
            )
        parts = []
        if summary.a_to_b_count:
            parts.append(f"{summary.a_to_b_count:,} {neuron_a} → {neuron_b}")
        if summary.b_to_a_count:
            parts.append(f"{summary.b_to_a_count:,} {neuron_b} → {neuron_a}")
        return (
            f"{label}: {'; '.join(parts)} ({summary.total_count:,} total rows in {source})."
            f"{scope}"
        )

    def _refresh_pair_panel(self, neuron_ids: tuple[str, ...] | None = None) -> None:
        ids = tuple(neuron_ids if neuron_ids is not None else self.viewport.selected_neuron_ids)
        if len(ids) != 2:
            self.pair_panel.setVisible(False)
            self._setting_connection_controls = True
            try:
                self.chemical_pair_check.setChecked(False)
                self.gap_pair_check.setChecked(False)
                self.chemical_pair_check.setEnabled(False)
                self.gap_pair_check.setEnabled(False)
            finally:
                self._setting_connection_controls = False
            return

        self.pair_panel.setVisible(True)
        neuron_a, neuron_b = ids
        if self._edge_catalog is None:
            chemical = ConnectionClassSummary(
                False,
                unavailable_reason="No connectome edge inventory is loaded.",
            )
            gap = chemical
        else:
            pair = self._edge_catalog.pair_summary(neuron_a, neuron_b)
            chemical, gap = pair.chemical, pair.gap_junction
        deferred_male_chemical = bool(
            self._edge_catalog is not None
            and self._edge_catalog.is_male_cns
            and self._edge_catalog.chemical_parquet.is_file()
        )
        chemical_toggle_available = chemical.has_connections or deferred_male_chemical
        override = self.spec.connection_override(neuron_a, neuron_b)
        chemical_enabled = (
            True
            if override is None or override.chemical_enabled is None
            else override.chemical_enabled
        )
        gap_enabled = (
            True
            if override is None or override.gap_junction_enabled is None
            else override.gap_junction_enabled
        )
        self._setting_connection_controls = True
        try:
            self.chemical_pair_check.setEnabled(chemical_toggle_available)
            self.chemical_pair_check.setChecked(
                bool(chemical_enabled) if chemical_toggle_available else False
            )
            self.gap_pair_check.setEnabled(gap.has_connections)
            self.gap_pair_check.setChecked(
                bool(gap_enabled) if gap.has_connections else False
            )
            self.chemical_pair_check.setToolTip(
                str(self._edge_catalog.chemical_parquet)
                if deferred_male_chemical and self._edge_catalog is not None
                else chemical.source_path or chemical.unavailable_reason
            )
            self.gap_pair_check.setToolTip(gap.source_path or gap.unavailable_reason)
        finally:
            self._setting_connection_controls = False

        chemical_state = (
            f" Plan state: {'enabled' if chemical_enabled else 'disabled'}."
            if chemical_toggle_available
            else ""
        )
        gap_state = (
            f" Plan state: {'enabled' if gap_enabled else 'disabled'}."
            if gap.has_connections
            else ""
        )
        chemical_readout = (
            "Chemical: Male-CNS contacts for this pair are streamed from the external "
            "Parquet source during Experiment preflight; no 7.8 GB scan is copied into the app."
            if deferred_male_chemical
            else self._connection_class_readout(
                "Chemical", chemical, neuron_a, neuron_b
            )
        )
        self.pair_connection_readout.setText(
            f"{neuron_a} ↔ {neuron_b}\n"
            f"{chemical_readout}"
            f"{chemical_state}\n"
            f"{self._connection_class_readout('Gap junction', gap, neuron_a, neuron_b)}"
            f"{gap_state}"
        )

    def _pair_connection_toggled(self, connection_class: str, enabled: bool) -> None:
        if self._setting_connection_controls:
            return
        ids = tuple(self.viewport.selected_neuron_ids)
        if len(ids) != 2:
            return
        control = (
            self.chemical_pair_check
            if connection_class == "chemical"
            else self.gap_pair_check
        )
        if not control.isEnabled():
            return
        self.spec.set_connection_class_enabled(ids[0], ids[1], connection_class, enabled)
        self.circuit_changed.emit(self.spec)
        label = "chemical connections" if connection_class == "chemical" else "gap junctions"
        self.status_message.emit(
            f"{label.capitalize()} between {ids[0]} and {ids[1]} will be "
            f"{'enabled' if enabled else 'disabled'} in experiment plans"
        )
        self._refresh_pair_panel(ids)
        self._update_override_summary()

    def _clear_pair_selection(self) -> None:
        self.viewport.clear_neuron_selection(restore_overview=True)
        self.status_message.emit("Cleared pair selection and restored the circuit overview")

    def _compartments_changed(self, neuron_id: str, node_ids: object) -> None:
        ids = tuple(node_ids) if isinstance(node_ids, (tuple, list, set)) else ()
        record = self.loaded_records.get(neuron_id)
        label = record.neuron_type if record else "Neuron"
        if self.viewport.display_mode == DISPLAY_MODE_SOMA_POINTS:
            location = self.viewport.soma_location(neuron_id)
            marker = "pseudosoma" if location and location.is_pseudosoma else "soma"
            view_state = (
                f"{marker.capitalize()} point isolated"
                if self.viewport.isolated
                else "All loaded soma points visible"
            )
            self.selection_label.setText(
                f"{label} · {neuron_id}\n{view_state} · {len(ids)} compartment(s) retained; "
                "switch to Full skeletons to view or edit them"
            )
        else:
            view_state = (
                "Whole neuron isolated"
                if self.viewport.isolated
                else "All loaded neurons visible"
            )
            self.selection_label.setText(
                f"{label} · {neuron_id}\n{view_state} · {len(ids)} compartment(s) selected "
                "in electric magenta (stored by SWC child-node ID)"
            )
        self._load_effective_selection_design(neuron_id, ids)
        self._update_override_summary()
        self._update_viewport_guidance()

    def _isolation_changed(self, _isolated: bool) -> None:
        neuron_id = self.viewport.selected_neuron_id
        if neuron_id is not None:
            self._compartments_changed(neuron_id, tuple(sorted(self.viewport.selected_compartments)))

    def _display_mode_changed(self, mode: str) -> None:
        self.soma_points_button.setChecked(mode == DISPLAY_MODE_SOMA_POINTS)
        self.full_skeletons_button.setChecked(mode == DISPLAY_MODE_FULL_SKELETONS)
        neuron_id = self.viewport.selected_neuron_id
        if neuron_id is not None:
            self._compartments_changed(
                neuron_id, tuple(sorted(self.viewport.selected_compartments))
            )
        else:
            self._update_viewport_guidance()

    def _update_viewport_guidance(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        pair_ids = tuple(self.viewport.selected_neuron_ids)
        selected_count = len(self.viewport.selected_compartments)
        if len(pair_ids) == 2:
            selection_state = (
                f"Pair selection: {pair_ids[0]} ↔ {pair_ids[1]}. Cmd/Ctrl-click either "
                "selected neuron to remove it; selecting a third replaces the oldest. "
                "Use Clear pair or Esc to leave pair mode."
            )
        elif self.viewport.display_mode == DISPLAY_MODE_SOMA_POINTS:
            if self.viewport.neuron_count == 0:
                selection_state = "Soma points: load neurons to begin."
            elif self.viewport.isolated and neuron_id is not None:
                location = self.viewport.soma_location(neuron_id)
                marker = "pseudosoma" if location and location.is_pseudosoma else "soma"
                retained = (
                    f" · {selected_count} hidden compartment selection(s) retained"
                    if selected_count
                    else ""
                )
                selection_state = (
                    f"Soma points (default): neuron {neuron_id} {marker} isolated{retained}. "
                    "Switch to Full skeletons for compartment selection and editing."
                )
            elif neuron_id is not None:
                selection_state = (
                    f"Soma points (default): neuron {neuron_id} focused. Left-click its "
                    "point to isolate it; switch to Full skeletons for compartment editing."
                )
            else:
                selection_state = (
                    "Soma points (default): one point per neuron. MANC DNs use the rostral "
                    "pseudosoma at the northern end. Left-click a point to isolate its neuron; "
                    "switch to Full skeletons for compartment editing."
                )
        elif self.viewport.neuron_count == 0:
            selection_state = "Selection: load neurons to begin."
        elif self.viewport.isolated and neuron_id is not None:
            selection_state = (
                f"Selection: neuron {neuron_id} isolated · {selected_count} compartment(s) "
                "selected in electric magenta. Left-click a skeleton segment to toggle it; "
                "Cmd/Ctrl+Shift+left-drag a box to add intersecting compartments."
            )
        elif neuron_id is not None:
            selection_state = (
                f"Selection: neuron {neuron_id} focused · {selected_count} compartment(s) "
                "selected. Left-click it to isolate it for compartment editing."
            )
        else:
            selection_state = (
                "Selection: no neuron isolated. Left-click a neuron to isolate it; then "
                "left-click segments or Cmd/Ctrl+Shift+left-drag a box to select compartments."
            )
        self.viewport_controls_hint.setText(
            f"{selection_state}\n"
            "Navigate: left-drag rotate · Shift-left-drag or middle-drag pan · scroll zoom · "
            "right-click center · Cmd/Ctrl-click pair-select · Esc clear pair/restore all · "
            "C clear selected compartments."
        )

    def _load_effective_selection_design(
        self,
        neuron_id: str,
        node_ids: tuple[object, ...],
    ) -> None:
        base_hh = self.spec.neuron_overrides.get(neuron_id, self.spec.hh.to_dict())
        base_membrane = self.spec.neuron_mechanism_overrides.get(
            neuron_id, self.spec.membrane.to_dict()
        )
        if node_ids:
            node_hh = self.spec.compartment_overrides.get(neuron_id, {})
            node_membrane = self.spec.compartment_mechanism_overrides.get(neuron_id, {})
            hh_values = [node_hh.get(str(node_id), base_hh) for node_id in node_ids]
            membrane_values = [
                node_membrane.get(str(node_id), base_membrane) for node_id in node_ids
            ]
            if not all(value == hh_values[0] for value in hh_values[1:]) or not all(
                value == membrane_values[0] for value in membrane_values[1:]
            ):
                self._set_mixed_selection_design(True)
                self.channel_profile_summary.setText(
                    "Mixed saved designs are selected. Editors are intentionally unchanged and "
                    "segment apply is locked until you deliberately edit a value or choose a profile."
                )
                return
            base_hh = hh_values[0]
            base_membrane = membrane_values[0]
        self._set_mixed_selection_design(False)
        self._set_hh(HodgkinHuxleySpec.from_dict(base_hh))
        self._set_membrane(MembraneMechanismSpec.from_dict(base_membrane))
        self._update_mechanism_capability()

    def _set_mixed_selection_design(self, mixed: bool) -> None:
        self._mixed_selection_design = bool(mixed)
        if not hasattr(self, "apply_compartments_button"):
            return
        has_neuron = (
            self.viewport.selected_neuron_id is not None
            and len(self.viewport.selected_neuron_ids) == 1
        )
        has_target = bool(
            has_neuron and self.viewport.selected_compartments
        )
        self.apply_neuron_button.setEnabled(has_neuron)
        self.mass_apply_button.setEnabled(bool(self.loaded_records))
        self.apply_compartments_button.setEnabled(has_target and not mixed)
        self.apply_compartments_button.setToolTip(
            "Edit a value or choose a profile to explicitly replace the mixed saved designs."
            if mixed
            else ""
        )

    def _mark_draft_as_deliberately_edited(self) -> None:
        if self._mixed_selection_design:
            self._set_mixed_selection_design(False)

    def _channel_profile_changed(self, *_args: Any) -> None:
        if self._setting_mechanism_controls:
            return
        key = str(self.channel_profile_combo.currentData() or "custom")
        if key == "custom":
            self._mark_draft_as_deliberately_edited()
            self.channel_profile_summary.setText(
                "Custom mechanism selection. Exact suffixes and regional densities will be preserved."
            )
            self._update_mechanism_capability()
            return
        definition = next(profile for profile in MEMBRANE_PROFILES if profile.key == key)
        profile_hh, profile_membrane = cell_design_profile(key)
        self._set_hh(profile_hh)
        self._set_membrane(profile_membrane)
        self._mark_draft_as_deliberately_edited()
        self._update_mechanism_capability()
        self.status_message.emit(f"Loaded membrane profile: {definition.label}")

    def _mechanism_controls_edited(self, *_args: Any) -> None:
        if self._setting_mechanism_controls:
            return
        self._mark_draft_as_deliberately_edited()
        custom_index = self.channel_profile_combo.findData("custom")
        self._setting_mechanism_controls = True
        try:
            self.channel_profile_combo.setCurrentIndex(custom_index)
            self.channel_profile_summary.setText(
                "Custom mechanism selection. Exact suffixes and regional densities will be preserved."
            )
        finally:
            self._setting_mechanism_controls = False
        self._update_mechanism_capability()

    def _hh_controls_edited(self, *_args: Any) -> None:
        if self._setting_mechanism_controls:
            return
        self._mechanism_controls_edited()

    def _read_membrane(self) -> MembraneMechanismSpec:
        assignments = dict(self._unlisted_channel_assignments)
        for definition in MEMBRANE_MECHANISMS:
            assignments[definition.key] = ChannelAssignment(
                mechanism_key=definition.key,
                suffix=definition.suffix,
                enabled=self.channel_checks[definition.key].isChecked(),
                soma_gbar_s_cm2=self.channel_soma_editors[definition.key].value(),
                branch_gbar_s_cm2=self.channel_branch_editors[definition.key].value(),
                parameters=dict(self._known_channel_parameters.get(definition.key, {})),
            )
        return MembraneMechanismSpec(
            profile_key=str(self.channel_profile_combo.currentData() or "custom"),
            replace_builtin_hh=self.replace_hh_check.isChecked(),
            channels=assignments,
        )

    def _set_membrane(self, spec: MembraneMechanismSpec) -> None:
        prior_guard = self._setting_mechanism_controls
        self._setting_mechanism_controls = True
        try:
            known_keys = {definition.key for definition in MEMBRANE_MECHANISMS}
            self._unlisted_channel_assignments = {
                key: assignment
                for key, assignment in spec.channels.items()
                if key not in known_keys
            }
            self._known_channel_parameters = {
                key: dict(assignment.parameters)
                for key, assignment in spec.channels.items()
                if key in known_keys
            }
            profile_index = self.channel_profile_combo.findData(spec.profile_key)
            if profile_index < 0:
                profile_index = self.channel_profile_combo.findData("custom")
            self.channel_profile_combo.setCurrentIndex(profile_index)
            self.replace_hh_check.setChecked(spec.replace_builtin_hh)
            for definition in MEMBRANE_MECHANISMS:
                assignment = spec.channels.get(definition.key)
                if assignment is None:
                    assignment = ChannelAssignment(
                        mechanism_key=definition.key,
                        suffix=definition.suffix,
                        soma_gbar_s_cm2=definition.source_default_gbar_s_cm2,
                        branch_gbar_s_cm2=definition.source_default_gbar_s_cm2,
                    )
                self.channel_checks[definition.key].setChecked(assignment.enabled)
                self.channel_soma_editors[definition.key].setValue(
                    assignment.soma_gbar_s_cm2
                )
                self.channel_branch_editors[definition.key].setValue(
                    assignment.branch_gbar_s_cm2
                )
            definition = next(
                (item for item in MEMBRANE_PROFILES if item.key == spec.profile_key), None
            )
            summary = (
                definition.summary
                if definition is not None
                else "Custom mechanism selection. Exact suffixes and regional densities will be preserved."
            )
            if self._unlisted_channel_assignments:
                summary += (
                    f"  {len(self._unlisted_channel_assignments)} unlisted mechanism assignment(s) "
                    "are preserved but not editable in this app version."
                )
            self.channel_profile_summary.setText(summary)
        finally:
            self._setting_mechanism_controls = prior_guard

    def _read_chemical_policy(self) -> ChemicalSynapsePolicy:
        return ChemicalSynapsePolicy(
            default_weight_uS=self.chemical_default_weight.value(),
            weight_scale=self.chemical_weight_scale.value(),
            default_delay_ms=self.chemical_delay.value(),
            use_geometric_delay=self.chemical_geom_delay.isChecked(),
            base_release_delay_ms=self.chemical_base_delay.value(),
            conduction_velocity_um_per_ms=self.chemical_velocity.value(),
            tau1_ms=self.chemical_tau1.value(),
            tau2_ms=self.chemical_tau2.value(),
            reversal_mV=self.chemical_reversal.value(),
            spike_threshold_mV=self.chemical_threshold.value(),
            kinetics_policy=str(self.chemical_kinetics_policy.currentData()),
        )

    def _set_chemical_policy(self, policy: ChemicalSynapsePolicy) -> None:
        prior_guard = self._setting_mechanism_controls
        self._setting_mechanism_controls = True
        try:
            self.chemical_default_weight.setValue(policy.default_weight_uS)
            self.chemical_weight_scale.setValue(policy.weight_scale)
            self.chemical_delay.setValue(policy.default_delay_ms)
            self.chemical_geom_delay.setChecked(policy.use_geometric_delay)
            self.chemical_base_delay.setValue(policy.base_release_delay_ms)
            self.chemical_velocity.setValue(policy.conduction_velocity_um_per_ms)
            self.chemical_tau1.setValue(policy.tau1_ms)
            self.chemical_tau2.setValue(policy.tau2_ms)
            self.chemical_reversal.setValue(policy.reversal_mV)
            self.chemical_threshold.setValue(policy.spike_threshold_mV)
            self.chemical_kinetics_policy.setCurrentIndex(self.chemical_kinetics_policy.findData(policy.kinetics_policy))
        finally:
            self._setting_mechanism_controls = prior_guard
        self._update_chemical_control_state()

    def _chemical_controls_changed(self, *_args: Any) -> None:
        if self._setting_mechanism_controls:
            return
        self._update_chemical_control_state()

    def _update_chemical_control_state(self) -> None:
        geometric = self.chemical_geom_delay.isChecked()
        self.chemical_base_delay.setEnabled(geometric)
        self.chemical_velocity.setEnabled(geometric)
        self.chemical_delay.setEnabled(not geometric)

    def _read_gap_policy(self) -> GapJunctionPolicy:
        conductance_basis = str(self.gap_basis_combo.currentData() or "per_site")
        return GapJunctionPolicy(
            mode=str(self.gap_mode_combo.currentData() or "none"),
            g_uS=self.gap_g_editor.value(),
            preferred_direction=str(self.gap_direction_combo.currentData() or "a_to_b"),
            g_closed_frac=self.gap_closed_editor.value(),
            vhalf_mV=self.gap_vhalf_editor.value(),
            vslope_mV=self.gap_vslope_editor.value(),
            empirical_residual_frac=self.gap_residual_editor.value(),
            tau_open_ms=self.gap_tau_open_editor.value(),
            tau_close_ms=self.gap_tau_close_editor.value(),
            placement_policy=str(
                self.gap_placement_combo.currentData() or "imported_contact_sites"
            ),
            conductance_basis=conductance_basis,
            aggregation_policy=(
                "equal_split_across_selected_sites"
                if conductance_basis == "pair_total"
                else "per_site_unchanged"
            ),
            endpoint_a_role=self.gap_endpoint_a_role.text().strip() or "unspecified",
            endpoint_b_role=self.gap_endpoint_b_role.text().strip() or "unspecified",
        )

    def _set_gap_policy(self, policy: GapJunctionPolicy) -> None:
        prior_guard = self._setting_mechanism_controls
        self._setting_mechanism_controls = True
        try:
            mode_index = self.gap_mode_combo.findData(policy.mode)
            self.gap_mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
            direction_index = self.gap_direction_combo.findData(policy.preferred_direction)
            self.gap_direction_combo.setCurrentIndex(
                direction_index if direction_index >= 0 else 0
            )
            placement_index = self.gap_placement_combo.findData(policy.placement_policy)
            self.gap_placement_combo.setCurrentIndex(
                placement_index if placement_index >= 0 else 0
            )
            basis_index = self.gap_basis_combo.findData(policy.conductance_basis)
            self.gap_basis_combo.setCurrentIndex(basis_index if basis_index >= 0 else 0)
            self.gap_g_editor.setValue(policy.g_uS)
            self.gap_closed_editor.setValue(policy.g_closed_frac)
            self.gap_vhalf_editor.setValue(policy.vhalf_mV)
            self.gap_vslope_editor.setValue(policy.vslope_mV)
            self.gap_residual_editor.setValue(policy.empirical_residual_frac)
            self.gap_tau_open_editor.setValue(policy.tau_open_ms)
            self.gap_tau_close_editor.setValue(policy.tau_close_ms)
            self.gap_endpoint_a_role.setText(policy.endpoint_a_role)
            self.gap_endpoint_b_role.setText(policy.endpoint_b_role)
        finally:
            self._setting_mechanism_controls = prior_guard
        self._update_gap_control_state()

    def _gap_controls_changed(self, *_args: Any) -> None:
        if self._setting_mechanism_controls:
            return
        self._update_gap_control_state()
        self._update_mechanism_capability()

    def _update_gap_control_state(self) -> None:
        mode = str(self.gap_mode_combo.currentData() or "none")
        enabled = mode != "none"
        directed = mode in {"rectifying", "heterotypic_rectifying"}
        heterotypic = mode == "heterotypic_rectifying"
        self.gap_g_editor.setEnabled(enabled)
        self.gap_direction_combo.setEnabled(directed)
        self.gap_placement_combo.setEnabled(enabled)
        self.gap_basis_combo.setEnabled(enabled)
        self.gap_endpoint_a_role.setEnabled(enabled)
        self.gap_endpoint_b_role.setEnabled(enabled)
        for editor in (
            self.gap_closed_editor,
            self.gap_vhalf_editor,
            self.gap_vslope_editor,
            self.gap_residual_editor,
            self.gap_tau_open_editor,
            self.gap_tau_close_editor,
        ):
            editor.setEnabled(heterotypic)
        if heterotypic:
            effective = max(
                self.gap_closed_editor.value(), self.gap_residual_editor.value()
            )
            self.gap_effective_floor_label.setText(
                f"Effective closed floor: {effective:.1%} = max(closed fraction, "
                "empirical residual). ShakB labels are project-fit biological roles, not "
                "separate mechanisms."
            )
        elif enabled:
            self.gap_effective_floor_label.setText(
                "Conductance identity and source hash are preserved. Pair-total values are "
                "deterministically split equally across the selected sites."
            )
        else:
            self.gap_effective_floor_label.setText(
                "No electrical-edge mechanism will be mass applied."
            )

    def _update_mechanism_capability(self) -> None:
        if not hasattr(self, "mechanism_capability_label"):
            return
        try:
            message = mechanism_capability_message(
                self.selected_engine_key(),
                self._read_membrane(),
                self._read_gap_policy(),
            )
        except ValueError as exc:
            message = f"Invalid mechanism settings: {exc}"
        self.mechanism_capability_label.setText(message)

    def _read_hh(self) -> HodgkinHuxleySpec:
        values = {key: editor.value() for key, editor in self.hh_editors.items()}
        values["active_scope"] = str(self.active_scope_combo.currentData() or "all")
        return HodgkinHuxleySpec.from_dict(values)

    def _set_hh(self, spec: HodgkinHuxleySpec) -> None:
        prior_guard = self._setting_mechanism_controls
        self._setting_mechanism_controls = True
        try:
            values = spec.to_dict()
            for key, editor in self.hh_editors.items():
                editor.setValue(float(values[key]))
            index = self.active_scope_combo.findData(spec.active_scope)
            self.active_scope_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._setting_mechanism_controls = prior_guard

    def circuit_spec(self) -> CircuitSpec:
        self.spec.query = NeuronQuery(self.query_edit.text().strip(), self.limit_spin.value())
        source = self._selected_source()
        if source is not None:
            self.spec.connectome = source
        return self.spec

    def morphology_snapshot(self) -> tuple[Morphology, ...]:
        """Return immutable loaded geometry in the circuit document's neuron order."""

        return tuple(
            self.loaded_morphologies[neuron_id]
            for neuron_id in self.spec.neuron_ids
            if neuron_id in self.loaded_morphologies
        )

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
        else:
            # A saved project selects its exact connectivity binding, not the latest profile graph.
            self._sources = tuple(spec.connectome if candidate == index else source for candidate, source in enumerate(self._sources))
        self._restoring_controls = True
        try:
            self.spec = spec
            self.query_edit.setText(spec.query.expression)
            self.limit_spin.setValue(spec.query.max_neurons)
            self._set_hh(spec.hh)
            self._set_membrane(spec.membrane)
            self._set_chemical_policy(spec.chemical_synapse_policy)
            self._set_gap_policy(spec.gap_junction_policy)
            self.connectome_combo.setCurrentIndex(index)
        finally:
            self._restoring_controls = False
        self.catalog = None
        self._edge_catalog = None
        self.loaded_records.clear()
        self.loaded_morphologies.clear()
        self.viewport.clear()
        # The saved assets loader immediately fills these rows. Retain cells
        # for same-size project reloads rather than destroy/recreate AX objects.
        if not spec.neuron_ids or self.neuron_table.rowCount() != len(spec.neuron_ids):
            self.neuron_table.setRowCount(0)
        else:
            previous = self.neuron_table.blockSignals(True)
            try:
                self.neuron_table.clearSelection()
            finally:
                self.neuron_table.blockSignals(previous)
        self.viewport_summary.setText("Saved cell set not loaded yet")
        self.selection_label.setText("No neuron selected")
        self.pair_panel.setVisible(False)
        self._update_viewport_guidance()
        self._update_override_summary()
        self._update_mechanism_capability()
        self._update_pair_graph_note()
        self._sync_source_controls()
        self._start_source_inventory()

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
        morphologies = [load_swc(catalog.by_id[neuron_id], allow_forest_preview=True, allow_point_preview=True) for neuron_id in self.spec.neuron_ids]
        for morphology in morphologies:
            neuron_id = morphology.record.neuron_id
            actual_hash = sha256_file(morphology.record.swc_path)
            expected_hash = self.spec.morphology_sha256.get(neuron_id)
            if expected_hash and expected_hash != actual_hash:
                raise ValueError(f"Saved morphology identity changed for neuron {neuron_id}")
            self.spec.morphology_sha256[neuron_id] = actual_hash
        self.catalog = catalog
        self._edge_catalog = ConnectomeEdgeCatalog(
            source, DigiflyWorkspace(self.overview.workspace_edit.text())
        )
        self.loaded_records = {item.record.neuron_id: item.record for item in morphologies}
        self.loaded_morphologies = {item.record.neuron_id: item for item in morphologies}
        self.viewport.set_morphologies(morphologies)
        self._set_mixed_selection_design(False)
        self._populate_table(morphologies)
        warnings = []
        forests = sum(sum(node.parent_id == -1 for node in item.nodes) > 1 for item in morphologies)
        points = sum(not item.segments for item in morphologies)
        if forests:
            warnings.append(f"{forests} disconnected skeleton(s): preview only; not simulation-ready")
        if points:
            warnings.append(f"{points} point-only skeleton(s): no released branches; not simulation-ready")
        suffix = " · " + " · ".join(warnings) if warnings else ""
        self.viewport_summary.setText(
            f"{len(morphologies)} saved neuron(s) · {self.viewport.segment_count:,} "
            f"editable compartment(s) (SWC segments){suffix}"
        )
        self._update_viewport_guidance()
        self.status_message.emit(f"Restored exact saved cell set from {source.label}")
        self.circuit_changed.emit(self.spec)

    def reset(self) -> None:
        self._restoring_controls = True
        try:
            self.spec = CircuitSpec()
            self.query_edit.clear()
            self.limit_spin.setValue(64)
            self._set_hh(self.spec.hh)
            self._set_membrane(self.spec.membrane)
            self._set_chemical_policy(self.spec.chemical_synapse_policy)
            self._set_gap_policy(self.spec.gap_junction_policy)
            self._clear_loaded_assets()
            self.set_selected_engine_key("arbor")
        finally:
            self._restoring_controls = False

    def apply_neuron_override(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        if neuron_id is None:
            self.status_message.emit(
                "Select a neuron before creating a neuron-level HH/channel override"
            )
            return
        self.spec.apply_neuron_override(neuron_id, self._read_regional_hh())
        self.spec.apply_neuron_mechanism_override(neuron_id, self._read_membrane())
        self._update_override_summary()
        self.circuit_changed.emit(self.spec)
        self.status_message.emit(
            f"Stored HH + membrane-channel draft for neuron {neuron_id}; "
            "experiment-global voltage and temperature are unchanged"
        )

    def apply_compartment_overrides(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        node_ids = sorted(self.viewport.selected_compartments)
        if neuron_id is None or not node_ids:
            self.status_message.emit("Select one or more SWC segments on an isolated neuron first")
            return
        self.spec.apply_compartment_override(neuron_id, node_ids, self._read_regional_hh())
        self.spec.apply_compartment_mechanism_override(
            neuron_id, node_ids, self._read_membrane()
        )
        self._update_override_summary()
        self.circuit_changed.emit(self.spec)
        self.status_message.emit(
            f"Applied HH + membrane channels to {len(node_ids)} SWC segment(s) on {neuron_id}; "
            "experiment-global voltage and temperature are unchanged"
        )

    def _read_regional_hh(self) -> dict[str, Any]:
        """Global integration settings are never advertised as regional overrides."""
        values = self._read_hh().to_dict()
        values.pop("v_init_mV", None)
        values.pop("celsius_C", None)
        return values

    def _apply_experiment_globals(self) -> None:
        hh = self._read_hh()
        self.experiment_globals_applied.emit(hh.v_init_mV, hh.celsius_C)

    def apply_cell_set_defaults(self) -> None:
        self.spec.hh = self._read_hh()
        self.spec.membrane = self._read_membrane()
        self.circuit_changed.emit(self.spec)
        self._apply_experiment_globals()
        self._update_override_summary()
        self.status_message.emit(
            "Stored HH + membrane channels as cell-set defaults; "
            "sent global voltage and temperature to Experiment Builder"
        )

    def mass_apply_to_loaded_neurons(self) -> None:
        if not self.loaded_records:
            self.status_message.emit("Load one or more neurons before using mass apply")
            return
        count = self.spec.mass_apply_cell_design(
            self.loaded_records,
            self._read_hh(),
            self._read_membrane(),
        )
        for neuron_id in self.loaded_records:
            self.spec.neuron_overrides[neuron_id].pop("v_init_mV", None)
            self.spec.neuron_overrides[neuron_id].pop("celsius_C", None)
        self._update_override_summary()
        self.circuit_changed.emit(self.spec)
        self._apply_experiment_globals()
        self.status_message.emit(
            f"Mass applied HH + membrane channels to {count} loaded neuron(s); "
            "sent global voltage and temperature to Experiment Builder"
        )

    def apply_chemical_policy(self) -> None:
        try:
            self.spec.chemical_synapse_policy = self._read_chemical_policy()
        except ValueError as exc:
            self.status_message.emit(f"Chemical-synapse policy is invalid: {exc}")
            return
        self.circuit_changed.emit(self.spec)
        self.status_message.emit(
            "Stored Exp2Syn kinetics, delay, threshold, and weight handling for all imported chemical contacts"
        )
        self._update_override_summary()

    def apply_gap_policy(self) -> None:
        self.spec.gap_junction_policy = self._read_gap_policy()
        self.circuit_changed.emit(self.spec)
        policy = self.spec.gap_junction_policy
        if policy.mode == "none":
            self.status_message.emit("Cleared the all-electrical-edge gap-junction policy")
        else:
            self.status_message.emit(
                f"Stored {policy.mechanism_label} as the future all-electrical-edge policy; "
                "no GJ was attached because connectivity is not loaded"
            )
        self._update_override_summary()
        self._update_mechanism_capability()

    def _update_override_summary(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        pair_ids = tuple(self.viewport.selected_neuron_ids)
        pair_total = len(self.spec.connection_overrides)
        if len(pair_ids) == 2:
            override = self.spec.connection_override(pair_ids[0], pair_ids[1])
            if override is None:
                state = "using imported connection defaults"
            else:
                chemical = (
                    "inherit" if override.chemical_enabled is None else
                    ("on" if override.chemical_enabled else "off")
                )
                gap = (
                    "inherit" if override.gap_junction_enabled is None else
                    ("on" if override.gap_junction_enabled else "off")
                )
                state = f"chemical {chemical} · gap {gap}"
            self.override_summary.setText(
                f"Selected pair: {state} · {pair_total} saved pair override(s) total · "
                f"GJ mechanism: {self.spec.gap_junction_policy.mechanism_label}"
            )
            return
        if neuron_id is None:
            total = sum(len(nodes) for nodes in self.spec.compartment_overrides.values())
            mechanism_total = sum(
                len(nodes) for nodes in self.spec.compartment_mechanism_overrides.values()
            )
            self.override_summary.setText(
                f"{len(self.spec.neuron_overrides)} neuron override(s) · "
                f"{max(total, mechanism_total)} SWC-segment override(s) · "
                f"{pair_total} connection-pair override(s) · "
                f"GJ edges: {self.spec.gap_junction_policy.mechanism_label}"
            )
            return
        count = max(
            len(self.spec.compartment_overrides.get(neuron_id, {})),
            len(self.spec.compartment_mechanism_overrides.get(neuron_id, {})),
        )
        has_neuron = (
            neuron_id in self.spec.neuron_overrides
            or neuron_id in self.spec.neuron_mechanism_overrides
        )
        self.override_summary.setText(
            f"Selected neuron: {'custom HH/channels' if has_neuron else 'cell-set defaults'} · "
            f"{count} saved SWC-segment override(s) · "
            f"{pair_total} connection-pair override(s) · "
            f"GJ edges: {self.spec.gap_junction_policy.mechanism_label}"
        )

    def save_custom_neuron(self) -> None:
        neuron_id = self.viewport.selected_neuron_id
        morphology = self.loaded_morphologies.get(neuron_id or "")
        if neuron_id is None or morphology is None:
            self.status_message.emit("Select a loaded neuron before saving a custom copy")
            return
        self.spec.apply_neuron_override(neuron_id, self._read_hh().to_dict())
        self.spec.apply_neuron_mechanism_override(neuron_id, self._read_membrane())
        try:
            bundle = save_custom_morphology(
                morphology,
                self.spec,
                selected_engine=self.selected_engine_key(),
            )
        except Exception as exc:
            self.status_message.emit(f"Could not save SWC + biophysics bundle: {exc}")
            return
        self.status_message.emit(f"Saved unchanged SWC + biophysics bundle: {bundle}")
        self.refresh_connectomes()
