"""Native engineering check of guide controls, without acquisition or a simulator."""
import json
import time
import hashlib


def check_first_experiment_guide(window, destination):
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from digifly_app.core.first_experiment import SETTINGS_KEY, STEPS, NEURON_IDS, review_teaching_contacts
    from .first_experiment import FirstExperimentGuide

    def click_ready(control):
        # Page changes resize and reposition the card on the next layout turn.
        # Wait for a stationary, hittable real control before pressing it.
        previous, stable = None, 0
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            QApplication.processEvents()
            point = control.mapToGlobal(control.rect().center())
            state = (point.x(), point.y(), control.width(), control.height())
            hittable = control.isVisible() and QApplication.widgetAt(point) is control
            stable = stable + 1 if hittable and state == previous else 0
            if stable >= 2:
                QTest.mouseClick(control, Qt.MouseButton.LeftButton)
                return
            previous = state
            QTest.qWait(10)
        raise ValueError("Guide control did not settle for pointer input: " + control.objectName())

    guide = window.first_experiment_guide
    before = window._project_state_identity()
    sections = [window.circuit_builder_page.more_options,
                *window.circuit_builder_page.hh_sections.values(),
                *window.experiment_page.selector_sections.values()]
    expanded_before = [(section, section.is_expanded) for section in sections]
    contact_bytes = json.dumps({"chemical_edges": [{"pre_id": "10001", "post_id": "800146",
        "located_contact": {"source": "native engineering fixture"}}], "electrical_edges": []}).encode() + b" " * 9_000_000
    contact_path = destination / "large-teaching-contact-fixture.json"
    contact_path.write_bytes(contact_bytes)
    if review_teaching_contacts(contact_path, NEURON_IDS) != (1, hashlib.sha256(contact_bytes).hexdigest()):
        raise ValueError("Large teaching contacts failed the chunked review")
    if guide.isHidden() or guide.index != 0:
        raise ValueError("A fresh profile did not offer the first-experiment guide")
    QTest.qWait(30)
    if guide.parentWidget() is not window or guide.isWindow() or "wizard dialog" not in guide.accessibleName():
        raise ValueError("The wizard must be a dialog card inside the app, not another app window")
    if guide.back_button.text() != "Previous" or guide.next_button.text() != "Next":
        raise ValueError("Standard bottom Previous/Next navigation is missing")
    if not guide.shade.isVisible() or guide.shade.holes:
        raise ValueError("The introductory wizard must dim the whole app")
    window.grab().save(str(destination / "wizard-introduction.png"))
    for index, step in enumerate(STEPS):
        guide.select(index)
        QTest.qWait(20)
        if not guide.details_panel.isHidden() or not guide.status.isHidden():
            raise ValueError("New guide pages must begin with the task, without an open reference panel or status message")
        if guide.instructions.verticalScrollBar().maximum() != 0:
            raise ValueError("The main guide task requires scrolling: " + step.key)
        click_ready(guide.details_button)
        QTest.qWait(20)
        if not guide.details_panel.isVisible() or guide.details.toPlainText().split() != step.details.split():
            raise ValueError("The step's reference settings are missing: " + step.key)
        guide.grab().save(str(destination / ("wizard-details-" + step.key + ".png")))
        click_ready(guide.details_button)
        QTest.qWait(20)
        guide.grab().save(str(destination / ("wizard-task-" + step.key + ".png")))
    guide.select(0)
    click_ready(guide.next_button)
    QTest.qWait(30)
    if guide.index != 1 or window.pages.currentIndex() != 0:
        raise ValueError("Guide Next did not open the workspace step")
    field = window.overview_page.output_edit
    point = field.mapTo(window, field.rect().center())
    if guide.shade.mask().contains(point) or guide.geometry().contains(point):
        raise ValueError("The wizard covers the output folder it is teaching")
    if QApplication.widgetAt(window.mapToGlobal(point)) is not field:
        raise ValueError("The spotlight does not pass pointer input to the actual folder field")
    QTest.mouseClick(field, Qt.MouseButton.LeftButton)
    if not field.hasFocus():
        raise ValueError("The highlighted real control cannot receive keyboard input")
    if not guide.shade.mask().contains(QPoint(8, 100)):
        raise ValueError("Unrelated app controls are not dimmed and blocked")
    window.grab().save(str(destination / "wizard-workspace-spotlight.png"))
    click_ready(guide.next_button)
    QTest.qWait(20)
    closed_runtime = []
    def close_runtime():
        modal = QApplication.activeModalWidget()
        if modal is None:
            raise ValueError("Runtime setup did not open from the wizard")
        closed_runtime.append(modal.windowTitle())
        modal.reject()
    QTimer.singleShot(100, close_runtime)
    click_ready(guide.action_button)
    if not closed_runtime or window._project_state_identity() != before:
        raise ValueError("The wizard blocked the real runtime dialog or changed its selection on cancel")
    guide.select(1)
    QTest.qWait(20)
    field.setFocus()
    from PySide6.QtGui import QAccessible, QAccessibleActionInterface
    actions = QAccessible.queryAccessibleInterface(guide.back_button).actionInterface()
    actions.doAction(QAccessibleActionInterface.pressAction())
    QTest.qWait(150)
    if guide.index != 0 or field.hasFocus():
        raise ValueError("Accessible Previous left keyboard focus in a newly dimmed field")
    guide.select(1)
    click_ready(guide.back_button)
    if guide.index != 0:
        raise ValueError("Guide Previous failed")
    choice = guide.step_choice._digifly_accessible_choice.button
    if guide.step_choice.currentText() not in choice.accessibleName():
        raise ValueError("The wizard's accessible step picker kept a stale step label")
    # The actual step combo selects instructions; the separate page button navigates.
    guide.step_choice.setCurrentIndex(3)
    click_ready(guide.page_button)
    if window.pages.currentIndex() != 1 or window.data_library_page._display_dataset != "male-cns:v1.0":
        raise ValueError("Guide download step did not open the exact release")
    click_ready(guide.check_button)
    if guide.check_step() or "download dialog" not in guide.status.text():
        raise ValueError("Review-only guide step falsely claimed completion")
    # A real confirmation must allow refusal without touching the active draft.
    guide.step_choice.setCurrentIndex(5)
    QTest.qWait(30)
    for control in (window.circuit_builder_page.query_edit,
                    window.circuit_builder_page.assemble_button,
                    window.circuit_builder_page.full_skeletons_button):
        point = control.mapTo(window, control.rect().center())
        if guide.shade.mask().contains(point) or guide.geometry().contains(point):
            raise ValueError("The cell-loading step hides a control it teaches")
        if QApplication.widgetAt(window.mapToGlobal(point)) is not control:
            raise ValueError("The highlighted cell-view control does not receive pointer input")
    saved = QMessageBox.question
    try:
        observed = []
        def decline(*args, **kwargs):
            observed.append(args[2])
            return QMessageBox.StandardButton.No
        QMessageBox.question = decline
        click_ready(guide.action_button)
        if not observed or window._project_state_identity() != before:
            raise ValueError("Guide confirmation refusal failed to preserve the draft")
    finally:
        QMessageBox.question = saved
    guide.step_choice.setCurrentIndex(len(STEPS) - 1)
    click_ready(guide.next_button)
    if guide.finished or "Needs attention" not in guide.status.text():
        raise ValueError("Guide accepted an empty or unrelated result")
    guide.step_choice.setCurrentIndex(8)
    click_ready(guide.pause_button)
    if not guide.isHidden():
        raise ValueError("Guide Pause did not return page space")
    reopened = FirstExperimentGuide(window)
    try:
        if not reopened.isHidden() or reopened.index != 8:
            raise ValueError("Guide saved progress was not restored")
    finally:
        reopened.deleteLater()
    click_ready(window.first_experiment_button)
    if guide.isHidden() or guide.index != 8:
        raise ValueError("First experiment did not resume the saved step")
    guide.grab().save(str(destination / "first-experiment-guide.png"))
    previous_size = window.size()
    window.resize(1120, 760)
    QTest.qWait(40)
    if not window.rect().contains(guide.geometry()):
        raise ValueError("The wizard dialog is outside the minimum-size app")
    for control in (guide.step_choice, guide.pause_button, guide.action_button,
                    guide.back_button, guide.page_button, guide.check_button, guide.next_button):
        point = control.mapTo(window, control.rect().topLeft())
        if (point.x() < 0 or point.x() + control.width() > window.width()
                or point.y() < 0 or point.y() + control.height() > window.height()):
            raise ValueError("A guide control is outside the minimum-width window")
    guide.grab().save(str(destination / "first-experiment-guide-1120.png"))
    window.resize(previous_size)
    state = json.loads(window.settings.value(SETTINGS_KEY))
    if set(state) != {"version", "step", "active", "finished"} or state["finished"]:
        raise ValueError("Guide state stored unexpected or false completion data")
    if window._project_state_identity() != before or window.experiment_page._process is not None:
        raise ValueError("Guide navigation changed scientific input or started execution")
    guide.pause()
    # Visiting the repair and settings pages intentionally opens their sections.
    # Restore the fixture's starting layout for the complete library suite's
    # independent compact-default checks. Never touch project/model settings.
    for section, expanded in expanded_before:
        section.set_expanded(expanded)
    window.show_page(0)
    return dict(first_experiment_offered=True, first_experiment_navigation_safe=True,
        first_experiment_decline_preserves_project=True, first_experiment_resume=True,
        first_experiment_unrelated_result_refused=True, first_experiment_no_worker_started=True,
        first_experiment_minimum_width=True, first_experiment_dialog=True,
        first_experiment_background_dimmed=True, first_experiment_spotlight_interactive=True,
        first_experiment_previous_next=True, first_experiment_modal_cancel_preserved=True,
        first_experiment_accessible_step_label=True, first_experiment_cell_view_controls_interactive=True,
        first_experiment_accessible_dimmed_focus_cleared=True,
        first_experiment_task_text_visible=True, first_experiment_details_disclosure=True,
        first_experiment_large_contacts_streamed=True)
