"""A resumable wizard dialog with a click-through spotlight on real controls."""
from __future__ import annotations

import json
from html import escape
import math
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QScrollArea, QTextBrowser, QVBoxLayout, QWidget,
)

from digifly_app.core.circuit import HodgkinHuxleySpec
from digifly_app.core.first_experiment import (
    DATASET, GUIDE_VERSION, NEURON_IDS, QUERY, SETTINGS_KEY, STEPS,
    circuit_issues, experiment_issues, tutorial_experiment, values_match, review_teaching_contacts,
)
from digifly_app.core.mechanisms import ChemicalSynapsePolicy, GapJunctionPolicy, membrane_profile


class _WizardShade(QWidget):
    """Block only the dimmed area. Holes remain real, interactive app widgets."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("FirstExperimentShade")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.holes = []

    def set_holes(self, holes):
        self.holes = holes
        mask = QRegion(self.rect())
        for rect in holes:
            path = QPainterPath()
            path.addRoundedRect(rect, 8, 8)
            mask -= QRegion(path.toFillPolygon().toPolygon())
        self.setMask(mask)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 185))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QColor(107, 181, 255))
        for rect in self.holes:
            painter.drawRoundedRect(rect.adjusted(-2, -2, 2, 2), 9, 9)


class FirstExperimentGuide(QFrame):
    def __init__(self, window):
        # A floating dialog card, not a second native Cocoa window. QDialog
        # creates an invalid native child handle when embedded in this native-
        # scene app. Use ordinary Qt accessibility and named real controls.
        super().__init__(window)
        self.window = window
        self.setObjectName("FirstExperimentGuide")
        self.setAccessibleName("First experiment wizard dialog")
        self.setWindowTitle("Your first NEURON experiment")
        self.shade = _WizardShade(window)
        self.shade.hide()
        self.destroyed.connect(self.shade.deleteLater)
        self._selecting = False
        self._laying_out = False
        self._layout_pending = False
        self._watched = set()
        self._targets = []
        self._active = False
        self._navigate_on_show = False
        # Timers belong to this widget. Bare singleShot Python callbacks can
        # survive its C++ parent and access deleted widgets during shutdown.
        self._layout_timer = self._timer(self._layout_spotlight)
        self._focus_timer = self._timer(self._restore_focus)
        self._page_timer = self._timer(self.open_page)
        try:
            state = json.loads(str(window.settings.value(SETTINGS_KEY, "{}")))
            if not isinstance(state, dict) or state.get("version") != GUIDE_VERSION:
                state = {}
        except (ValueError, TypeError):
            state = {}
        raw_step = state.get("step", 0)
        self.index = raw_step if type(raw_step) is int and 0 <= raw_step < len(STEPS) else 0
        self.finished = state.get("finished") is True
        # A first-use invitation never edits a project or starts external work.
        active = state.get("active", True) is True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)
        self.progress = QLabel()
        self.progress.setObjectName("FirstExperimentProgress")
        layout.addWidget(self.progress)
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setObjectName("FirstExperimentTitle")
        layout.addWidget(self.title)
        self.instructions = QTextBrowser()
        self.instructions.setObjectName("FirstExperimentInstructions")
        self.instructions.setAccessibleName("First experiment instructions")
        self.instructions.setOpenExternalLinks(False)
        self.instructions.setMinimumHeight(100)
        layout.addWidget(self.instructions)
        self.status = QLabel()
        self.status.setObjectName("FirstExperimentStatus")
        self.status.setWordWrap(True)
        self.status.setMaximumHeight(76)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.hide()
        layout.addWidget(self.status)
        # Standard wizard navigation stays on the bottom row, at every step.
        buttons = QHBoxLayout()
        self.pause_button = self._button("Pause guide", "FirstExperimentPause", self.pause)
        self.back_button = self._button("Previous", "FirstExperimentBack", lambda: self.select(self.index - 1))
        self.next_button = self._button("Next", "FirstExperimentNext", self.next_step)
        self.next_button.setProperty("primary", True)
        buttons.addWidget(self.pause_button)
        buttons.addStretch(1)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)
        window.installEventFilter(self)
        QApplication.instance().focusChanged.connect(self._focus_changed)
        self.select(self.index)
        self._active = active
        self._navigate_on_show = active
        self.setVisible(active)
        self._persist(active=active)
        self._schedule_layout()

    def _button(self, label, name, callback):
        button = QPushButton(label)
        button.setObjectName(name)
        button.clicked.connect(callback)
        return button

    def _timer(self, callback):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        return timer

    def _status(self, message):
        self.status.setText(message)
        self.status.setToolTip(message)
        self.status.setVisible(bool(message))
        self._schedule_layout()

    @staticmethod
    def _readable_text(browser, text):
        # Escape all copy, including example shell commands. Only paragraph
        # spacing and line breaks are markup; user copy never supplies HTML.
        paragraphs = ["<p>" + escape(part).replace("\n", "<br>") + "</p>"
                      for part in text.split("\n\n")]
        browser.document().setDefaultStyleSheet(
            "p { margin-top: 0; margin-bottom: 12px; line-height: 145%; }")
        browser.setHtml("".join(paragraphs))
        browser.moveCursor(browser.textCursor().MoveOperation.Start)

    def _persist(self, *, active=True):
        self.window.settings.setValue(SETTINGS_KEY, json.dumps({
            "version": GUIDE_VERSION, "step": self.index, "active": active,
            "finished": self.finished,
        }))
        self.window.settings.sync()

    def resume(self):
        self._active = True
        self.show()
        self.open_page()
        self._persist()
        self._focus_timer.start(0)

    def _restore_focus(self):
        current = QApplication.focusWidget()
        if (not self._active or current is None or QApplication.activeModalWidget() is not None
                or current.window() is not self.window
                or current is self or self.isAncestorOf(current)
                or any(target is current or target.isAncestorOf(current) for target in self._targets)):
            return
        self.next_button.setFocus()

    def pause(self):
        self._active = False
        self._navigate_on_show = False
        for timer in (self._layout_timer, self._focus_timer, self._page_timer):
            timer.stop()
        self._layout_pending = False
        self.hide()
        self.shade.hide()
        self._persist(active=False)

    def reject(self):
        self.pause()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.pause()
            event.accept()
        else:
            super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        if (watched is self.window and event.type() == QEvent.Type.Show
                and self._active and self._navigate_on_show):
            self._navigate_on_show = False
            self._page_timer.start(0)
        if event.type() in {QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
                            QEvent.Type.Hide, QEvent.Type.LayoutRequest}:
            self._schedule_layout()
        return False

    def _schedule_layout(self, *args):
        if not self._active or self._layout_pending or self._laying_out:
            return
        self._layout_pending = True
        self._layout_timer.start(0)

    def _watch(self, widget):
        while widget is not None and widget is not self.window:
            if widget not in self._watched:
                widget.installEventFilter(self)
                if isinstance(widget, QScrollArea):
                    widget.verticalScrollBar().valueChanged.connect(self._schedule_layout)
                    widget.horizontalScrollBar().valueChanged.connect(self._schedule_layout)
                self._watched.add(widget)
            widget = widget.parentWidget()

    def _focus_changed(self, old, current):
        if (not self._active or current is None or self._laying_out
                or QApplication.activeModalWidget() is not None
                or current.window() is not self.window):
            return
        if current is self or self.isAncestorOf(current):
            return
        if any(target is current or target.isAncestorOf(current) for target in self._targets):
            return
        # Tab cannot activate a dimmed control. Native dialogs/popups remain free.
        self._focus_timer.start(0)

    def _focus_targets(self):
        w, key = self.window, STEPS[self.index].key
        overview, builder, experiment = w.overview_page, w.circuit_builder_page, w.experiment_page
        if key == "workspace":
            return [overview.output_edit.parentWidget(), w.menuBar()]
        if key == "runtime":
            return [overview.python_edit.parentWidget(), overview.runtime_setup_button,
                    overview.doctor_button.parentWidget()]
        if key == "download":
            return [w.data_library_page.neuprint_button, w.data_library_page.neuron_browser]
        if key == "healing":
            return [builder.more_options]
        if key == "cells":
            return [builder.query_edit.parentWidget(), builder.neuron_table.parentWidget()]
        if key in {"membrane", "connections"}:
            # The complete settings column is independently scrollable. Let the
            # user scroll it, while the unrelated scene and other pages stay dim.
            section = builder.hh_sections["membrane_profile" if key == "membrane" else "chemical_synapses"]
            parent = section.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            return [parent] if parent is not None else [section]
        if key == "experiment":
            return [experiment.selector_panel]
        if key == "readiness":
            return [experiment.validate_button.parentWidget(), w.menuBar()]
        if key == "run":
            return [experiment.validate_button.parentWidget()]
        if key == "playback":
            return [w.results_page.circuit_panel]
        if key == "results":
            return [w.results_page.figure_panel]
        return []

    def _visible_rect(self, widget):
        rect = QRect(widget.mapTo(self.window, QPoint()), widget.size())
        parent = widget.parentWidget()
        while parent is not None and parent is not self.window:
            if not parent.isVisible():
                return QRect()
            rect = rect.intersected(QRect(parent.mapTo(self.window, QPoint()), parent.size()))
            parent = parent.parentWidget()
        return rect.adjusted(-6, -6, 6, 6).intersected(self.window.rect()) if not rect.isEmpty() else rect

    def _layout_spotlight(self):
        self._layout_pending = False
        if not self._active or not self.window.isVisible():
            return
        self._laying_out = True
        try:
            self._targets = [target for target in self._focus_targets() if target is not None]
            holes = []
            for target in self._targets:
                self._watch(target)
                if target.isVisible():
                    rect = self._visible_rect(target)
                    if not rect.isEmpty():
                        holes.append(rect)
            bounds = self.window.rect().adjusted(16, 34, -16, -28)
            width = min(640, bounds.width())
            # Reserve the frame/document insets before Qt lays out wrapped text.
            # Measuring a slightly wider document can hide the final line.
            # Measure a detached document. Changing the live document's wrap
            # width fights QTextBrowser's viewport sizing and can continuously
            # post new layouts on offscreen/Windows platforms.
            measured = self.instructions.document().clone()
            measured.setTextWidth(max(100, width - 80))
            self.instructions.setFixedHeight(min(480, max(100,
                math.ceil(measured.size().height()) + 16)))
            height = min(bounds.height(), self.layout().sizeHint().height())
            # Use the largest free side of the spotlight, avoiding the controls.
            choices = [QRect(bounds.left(), bounds.top(), width, height),
                       QRect(bounds.right() - width, bounds.top(), width, height),
                       QRect(bounds.left(), bounds.bottom() - height, width, height),
                       QRect(bounds.right() - width, bounds.bottom() - height, width, height)]
            if not holes:
                card = QRect(bounds.center().x() - width // 2, bounds.center().y() - height // 2, width, height)
            else:
                card = min(choices, key=lambda candidate: sum(
                    candidate.intersected(hole).width() * candidate.intersected(hole).height()
                    for hole in holes if candidate.intersects(hole)))
            self.setGeometry(card)
            self.shade.setGeometry(self.window.rect())
            self.shade.set_holes(holes)
            self.shade.show()
            self.shade.raise_()
            self.raise_()
            # AX press can change steps without focusing its navigation button.
            # A field from the former spotlight must not remain editable once
            # the new step dims it. Still-highlighted fields and native dialogs
            # are explicitly preserved by this same existing focus guard.
            self._restore_focus()
        finally:
            self._laying_out = False

    def select(self, index):
        if self._selecting or not 0 <= index < len(STEPS):
            return
        self._selecting = True
        self.index = index
        self._selecting = False
        step = STEPS[index]
        self.progress.setText(f"First experiment  |  {index + 1} of {len(STEPS)}")
        self.title.setText(step.title)
        self._readable_text(self.instructions, step.instructions)
        self.back_button.setEnabled(index > 0)
        self.next_button.setText("Finish" if index == len(STEPS) - 1 else "Next")
        self._status("")
        self._persist()
        if self._active:
            self.open_page()

    def next_step(self):
        if self.index < len(STEPS) - 1:
            self.select(self.index + 1)
        elif self.check_step():
            self.finished = True
            self._persist()
            self._status("Experiment loaded. The guide is complete.")
            self.pause()

    def open_page(self):
        step = STEPS[self.index]
        w = self.window
        w.show_page(step.page)
        # Only explicit page navigation selects this library view, never its contents.
        if step.page == 1:
            w.data_library_page.open_connectome(DATASET)
        if step.key == "healing":
            w.circuit_builder_page.more_options.set_expanded(True)
        if step.key in {"membrane", "connections"}:
            keys = ("membrane_profile", "passive", "soma_hh", "branch_hh", "apply") if step.key == "membrane" else ("chemical", "gap")
            for key, section in w.circuit_builder_page.hh_sections.items():
                if any(part in key for part in keys):
                    section.set_expanded(True)
            section = w.circuit_builder_page.hh_sections[
                "membrane_profile" if step.key == "membrane" else "chemical_synapses"]
            parent = section.parentWidget()
            while parent is not None and not isinstance(parent, QScrollArea):
                parent = parent.parentWidget()
            if parent is not None:
                parent.ensureWidgetVisible(section.toggle_button, 8, 8)
        if step.page == 3:
            for section in w.experiment_page.selector_sections.values():
                section.set_expanded(True)
        targets = self._focus_targets()
        if targets and targets[0] is not None:
            target = targets[0]
            parent = target.parentWidget()
            while parent is not None and parent is not w:
                if isinstance(parent, QScrollArea):
                    if step.key in {"playback", "results"}:
                        parent.verticalScrollBar().setValue(max(0, target.mapTo(parent.widget(), QPoint()).y() - 12))
                    else:
                        parent.ensureWidgetVisible(target, 12, 12)
                    target = parent
                parent = parent.parentWidget()
        self._schedule_layout()

    def _can_edit(self):
        w = self.window
        if w._update_busy() or not w.update_button.isEnabled():
            self._status("Wait for active work to finish before changing these settings.")
            return False
        return True

    def _confirm(self, detail):
        return QMessageBox.question(self.window, "Apply example settings?", detail +
            "\n\nSave your current project first if you want to keep its settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def perform_action(self):
        w = self.window
        action = STEPS[self.index].action
        if not self._can_edit():
            return
        if action == "new":
            w.new_project()  # Existing Save/Discard/Cancel guard remains authoritative.
            return
        if action == "save":
            w.save_project(save_as=True)
            return
        if action == "runtime":
            w.overview_page.open_runtime_setup()
            return
        if action == "result":
            w.results_page.open_summary()
            return
        page = w.circuit_builder_page
        if page.has_unapplied_settings():
            self._status("Apply or restore pending Circuit Builder edits first. They were kept unchanged.")
            return
        if action == "selection":
            if not self._confirm("Select Male CNS v1.0 and NEURON, and replace the search field with " + QUERY + "? Existing loaded cells may be cleared by a source change."):
                return
            page.refresh_connectomes()
            page.select_library_release(DATASET)
            page.set_selected_engine_key("neuron")
            page.query_edit.setText(QUERY)
            page.limit_spin.setValue(64)
            self.open_page()
            self._status("Fields filled. Click Load neurons to load the 16 cells.")
            return
        issues = circuit_issues(page.circuit_spec(), model=False)
        if issues or set(page.loaded_morphologies) != set(NEURON_IDS):
            self._status("Load the 16 Male CNS v1.0 cells first. " + " ".join(issues))
            return
        if any((page.spec.neuron_overrides, page.spec.compartment_overrides,
                page.spec.neuron_mechanism_overrides, page.spec.compartment_mechanism_overrides,
                page.spec.connection_overrides)):
            self._status("This project has custom overrides. Start a separate project to use the example settings.")
            return
        if not self._confirm("Replace the displayed " + action + " values with the PowerPoint example and apply them to this teaching project?"):
            return
        if action == "membrane":
            page._set_hh(HodgkinHuxleySpec())
            page._set_membrane(membrane_profile("classic_hh"))
            page.apply_cell_set_defaults()
        elif action == "connections":
            page._set_chemical_policy(ChemicalSynapsePolicy())
            page._set_gap_policy(GapJunctionPolicy())
            page.apply_chemical_policy()
            page.apply_gap_policy()
        elif action == "experiment":
            w.experiment_page.set_config(tutorial_experiment("First NEURON experiment " + uuid4().hex[:10]))
        self.open_page()
        self._status("Example settings applied. Review the fields and save your project.")

    def _result_issues(self):
        w = self.window
        page = w.results_page
        record = page._displayed_result
        if record is None or page.loading or page._prepared_result is None:
            return ["Open the completed matching result and wait for its recordings to load."]
        if record.status != "complete":
            return ["The displayed result is not a completed experiment."]
        try:
            root = Path(record.summary_path).parent
            def document(name):
                path = root / name
                with path.open(encoding="utf-8") as stream:
                    value = json.load(stream)
                if not isinstance(value, dict):
                    raise ValueError("Saved teaching document is invalid.")
                return value
            saved_config = document("experiment.json")
            saved_circuit = document("circuit.json")
            summary = document("summary.json")
            if summary.get("status") != "complete" or summary.get("engine") != "neuron":
                return ["The saved summary must be a completed NEURON experiment."]
            if not values_match(saved_config, w.experiment_page.config().to_dict()):
                return ["The displayed run belongs to different experiment settings or a different name."]
            if not values_match(saved_circuit, w.circuit_builder_page.circuit_spec().to_dict()):
                return ["The displayed run belongs to different circuit inputs. Open its saved project."]
            edge_path = root / "edge_manifest.json"
            contact_count, contact_digest = review_teaching_contacts(edge_path, NEURON_IDS)
            metadata = summary.get("metadata") or {}
            if (metadata.get("Chemical contacts") != contact_count
                    or metadata.get("Conditions") != 2):
                return ["The saved run needs located chemical contacts between the teaching cells and both completed conditions."]
            if contact_digest != metadata.get("Edge manifest SHA-256"):
                return ["The run's saved chemical-contact file differs from its completed summary."]
            if page._voltage_table is None:
                return ["The saved voltage recording has not loaded."]
        except (OSError, ValueError, TypeError) as exc:
            return ["Could not check the saved teaching inputs: " + str(exc)]
        return []

    def check_step(self):
        step = STEPS[self.index]
        w = self.window
        issues = []
        if step.key in {"cells", "membrane", "connections", "experiment", "readiness", "run", "playback", "results"}:
            issues += circuit_issues(w.circuit_builder_page.circuit_spec(),
                model=step.key not in {"cells", "membrane", "connections"})
            if set(w.circuit_builder_page.loaded_morphologies) != set(NEURON_IDS):
                issues.append("Load all 16 neuron shapes in Circuit Builder.")
            if w.circuit_builder_page.has_unapplied_settings():
                issues.append("Apply or restore pending Circuit Builder settings.")
        if step.key == "membrane":
            if not values_match(w.circuit_builder_page.spec.hh.to_dict(), HodgkinHuxleySpec().to_dict()):
                issues.append("Apply the example HH values.")
            if not values_match(w.circuit_builder_page.spec.membrane.to_dict(), membrane_profile("classic_hh").to_dict()):
                issues.append("Apply Classic HH with extra channels off.")
        if step.key == "connections":
            if not values_match(w.circuit_builder_page.spec.chemical_synapse_policy.to_dict(), ChemicalSynapsePolicy().to_dict()):
                issues.append("Apply the example chemical model.")
            if w.circuit_builder_page.spec.gap_junction_policy.mode != "none":
                issues.append("Set the gap policy to None.")
        if step.key in {"experiment", "readiness", "run", "playback", "results"}:
            try:
                issues += experiment_issues(w.experiment_page.config())
            except (ValueError, TypeError) as exc:
                issues.append(str(exc))
        if step.key == "runtime":
            self._status("Click Run workspace doctor to test your selected NEURON Python.")
            return False
        if step.key in {"welcome", "workspace", "download", "healing"}:
            self._status({"welcome": "Click Next to begin.",
                "workspace": "Choose an output folder and save the project.",
                "download": "Confirm the 16 cells and their connectivity in the download dialog.",
                "healing": "Check recent imports lists shape problems and saved repairs."}[step.key])
            return False
        if step.key in {"run", "playback", "results"} and not issues:
            issues += self._result_issues()
        if issues:
            self._status("Needs attention: " + " ".join(issues))
            return False
        self._status("Settings match the example."
            if step.key not in {"run", "playback", "results"} else "The completed experiment and its voltage recording are loaded.")
        return True
