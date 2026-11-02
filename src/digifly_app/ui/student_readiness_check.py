"""Explicit native engineering checks; fake busy workers, no simulation or network."""
from __future__ import annotations
import os
import json
from pathlib import Path
import subprocess
import sys
import traceback
from contextlib import contextmanager
from threading import Event
from types import SimpleNamespace


@contextmanager
def capture_qt_callback_errors():
    """Catch Python errors Qt otherwise prints without failing the caller.

    Scoped engineering guard only; it neither catches native crashes nor
    replaces normal application error reporting or simulator validation.
    """
    original = sys.excepthook
    errors = []
    def record(kind, value, tb):
        errors.append(''.join(traceback.format_exception(kind, value, tb)))
    sys.excepthook = record
    try:
        yield errors
    finally:
        sys.excepthook = original


def check_intake_totals(destination: Path) -> None:
    """Owned tiny files, real intake worker/slot; no download or simulator."""
    from PySide6.QtWidgets import QApplication
    from digifly_app.core.resource_profile import (
        PROFILE_PATH_ENV, ResourceProfile, make_standalone_profile,
    )
    from digifly_app.ui.data_library import DataLibraryPage, _ImportWorker

    destination.mkdir(parents=True, exist_ok=False)
    profile = make_standalone_profile(workstation_root=destination/'workspace')
    profile_path = profile.save(destination/'resources-v2.json')
    previous = os.environ.get(PROFILE_PATH_ENV)
    os.environ[PROFILE_PATH_ENV] = str(profile_path)
    page = None
    try:
        page = DataLibraryPage()
        results, failures = [], []
        raw = '1 1 0 0 0 1 -1\n2 3 1 0 0 1 1\n'
        for index, (body, total) in enumerate((('42', 1), ('42', 1), ('43', 2))):
            source = destination/f'input-{index}'/f'{body}.swc'
            source.parent.mkdir()
            source.write_text(raw)
            worker = _ImportWorker(ResourceProfile.load(profile_path), profile_path,
                source, f'intake-{index}', 'v1',
                ('https://neuprint.janelia.org', 'male-cns:v1.0', None))
            worker.completed.connect(page._import_completed)
            worker.failed.connect(failures.append)
            worker.run()
            assert not failures, failures
            message = page.action_status.text()
            assert f'Dataset library now contains {total} saved neuron' in message
            assert 'Imported' not in message
            assert len(list(profile.managed_data_root.glob('swc-libraries/*/*/export_swc/**/*.swc'))) == total
            assert source.read_text() == raw
            results.append({'body_id': body, 'saved_total': total, 'message': message})
        (destination/'receipt.json').write_text(json.dumps({
            'cases': results, 'no_worker_errors': True,
            'scope': 'Native real intake/slot with tiny engineering SWCs; not manual file-picker or scientific acceptance',
        }, indent=2))
    finally:
        if page is not None:
            page.close()
            QApplication.processEvents()
        if previous is None:
            os.environ.pop(PROFILE_PATH_ENV, None)
        else:
            os.environ[PROFILE_PATH_ENV] = previous


def check_model_modal_selection(window, destination: Path) -> None:
    """Check the modal precondition with inert pickers, not Cocoa crash recovery."""
    from PySide6.QtWidgets import QFileDialog
    editor = window.netpyne_page.netpyne_editor
    original = editor.model_spec()
    original_path = editor.current_path
    original_open, original_save = QFileDialog.getOpenFileName, QFileDialog.getSaveFileName
    grids = [records.table for records in (editor.cell_rules_table,
        editor.populations_table, editor.synapses_table,
        editor.connections_table, editor.stimuli_table)]
    observed = []
    try:
        for invalid in (False, True):
            if invalid:
                editor.cell_rules_table.table.item(0, 1).setText('-1')
            document = editor._collect_model().to_dict()
            checks = [editor.voltage_population_list.item(i).checkState()
                      for i in range(editor.voltage_population_list.count())]
            def canceled_picker(*args, **kwargs):
                assert all(not grid.selectedIndexes() for grid in grids)
                assert not editor.voltage_population_list.selectedIndexes()
                assert editor._collect_model().to_dict() == document
                observed.append({'invalid_model': invalid, 'title': args[1]})
                return '', ''
            QFileDialog.getOpenFileName = QFileDialog.getSaveFileName = canceled_picker
            for action in (editor._choose_load, editor._choose_save, editor._choose_export):
                for grid in grids:
                    grid.selectRow(0)
                    assert grid.selectedIndexes()
                editor.voltage_population_list.setCurrentRow(0)
                action()
                assert checks == [editor.voltage_population_list.item(i).checkState()
                                  for i in range(editor.voltage_population_list.count())]
        assert len(observed) == 6
    finally:
        QFileDialog.getOpenFileName, QFileDialog.getSaveFileName = original_open, original_save
        editor.load_model(original)
        editor.current_path = original_path
    assert editor.model_spec() == original
    (destination/'model-modal-selection-check.json').write_text(json.dumps({
        'cases': observed, 'rows_parameters_and_recording_checks_preserved': True,
        'scope': 'App-side modal selection precondition with canceled inert pickers; not native Cocoa crash acceptance',
    }, indent=2))


def check_view_mode_toggles(window, destination: Path) -> None:
    """Real Qt toggle action with a tiny owned cable; no scientific output."""
    from PySide6.QtGui import QAccessible, QAccessibleActionInterface
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.morphology import load_swc
    from digifly_app.ui.circuit_viewport import DISPLAY_MODE_SOMA_POINTS, DISPLAY_MODE_FULL_SKELETONS
    source = destination/'view-toggle-input.swc'
    source.write_text('1 1 0 0 0 1 -1\n2 3 10 5 0 .5 1\n')
    original = source.read_bytes()
    results = window.results_page
    results.result_viewport.set_morphologies((load_swc(
        NeuronRecord('1', 'UNKNOWN', 'view fixture', str(source), 'fixture')),))
    results.result_soma_points_button.setEnabled(True)
    results.result_full_skeletons_button.setEnabled(True)
    checked = []
    for label, viewport, soma, full in (
        ('builder', window.circuit_builder_page.viewport, window.circuit_builder_page.soma_points_button,
         window.circuit_builder_page.full_skeletons_button),
        ('results', results.result_viewport, results.result_soma_points_button,
         results.result_full_skeletons_button),
    ):
        viewport.set_display_mode(DISPLAY_MODE_SOMA_POINTS)
        soma.setChecked(True)
        full.setChecked(False)
        for button, mode in ((full, DISPLAY_MODE_FULL_SKELETONS), (soma, DISPLAY_MODE_SOMA_POINTS)):
            interface = QAccessible.queryAccessibleInterface(button)
            actions = interface.actionInterface()
            assert QAccessibleActionInterface.toggleAction() in actions.actionNames()
            actions.doAction(QAccessibleActionInterface.toggleAction())
            assert viewport.display_mode == mode
            assert soma.isChecked() == (mode == DISPLAY_MODE_SOMA_POINTS)
            assert full.isChecked() == (mode == DISPLAY_MODE_FULL_SKELETONS)
            checked.append({'surface': label, 'rendered_mode': mode})
    assert source.read_bytes() == original
    results.expand_trace_button.setChecked(True)
    assert results.expand_trace_button.isChecked() and results.circuit_panel.isHidden()
    results._set_trace_view_expanded(False)
    assert not results.expand_trace_button.isChecked() and not results.circuit_panel.isHidden()
    results.expand_trace_button.click()
    assert results.expand_trace_button.isChecked() and results.circuit_panel.isHidden()
    results.expand_trace_button.click()
    assert not results.expand_trace_button.isChecked() and not results.circuit_panel.isHidden()
    (destination/'trace-expansion-check.json').write_text(json.dumps({
        'forced_restore_clears_checked_state': True,
        'next_click_expands_immediately': True,
        'scope': 'Native Qt actual control/setter path; saved-result manual interaction acceptance is separate',
    }, indent=2))
    results.result_viewport.set_morphologies(())
    results.result_viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
    results.result_soma_points_button.setChecked(False)
    results.result_full_skeletons_button.setChecked(True)
    results.result_soma_points_button.setEnabled(False)
    results.result_full_skeletons_button.setEnabled(False)
    editor = window.netpyne_page.netpyne_editor
    model_before = editor.model_spec()
    for index, button in enumerate(editor.section_nav_buttons):
        interface = QAccessible.queryAccessibleInterface(button)
        actions = interface.actionInterface()
        for _ in range(2):
            actions.doAction(QAccessibleActionInterface.toggleAction())
            assert editor.sections.currentIndex() == index
            assert [b.isChecked() for b in editor.section_nav_buttons] == [i == index for i in range(6)]
        checked.append({'surface': 'model section', 'index': index})
    assert editor.model_spec() == model_before
    editor.sections.setCurrentIndex(0)
    (destination/'view-toggle-check.json').write_text(json.dumps({
        'checks': checked, 'source_unchanged': True,
        'scope': 'Native Qt accessibility/control-state gate, not full manual UI or scientific acceptance',
    }, indent=2))


def check_student_safety(destination: Path) -> dict:
    from PySide6.QtCore import QThread, QTimer
    from PySide6.QtGui import QCloseEvent, QDesktopServices
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QLineEdit, QPushButton
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV
    from digifly_app.core import workspace as workspace_module, runtime_discovery
    from digifly_app.core.models import CheckState
    from digifly_app.ui.main_window import MainWindow, ResultsPage, _reveal

    destination.mkdir(parents=True, exist_ok=False)
    saved_env = {key: os.environ.get(key) for key in (SETTINGS_ROOT_ENV, PROFILE_PATH_ENV)}
    os.environ[SETTINGS_ROOT_ENV] = str(destination/'settings')
    os.environ[PROFILE_PATH_ENV] = str(destination/'missing-profile.json')
    warning = QMessageBox.warning
    question = QMessageBox.question
    save_picker = QFileDialog.getSaveFileName
    open_picker = QFileDialog.getOpenFileName
    desktop_open = QDesktopServices.openUrl
    neuron_probe = workspace_module._probe_neuron_runtime
    module_probe = workspace_module._probe_python_module
    resource_path = workspace_module.resource_path
    engine_probe = workspace_module.DigiflyWorkspace.probe_engines
    subprocess_run = runtime_discovery.subprocess.run
    warnings = []
    app = QApplication.instance() or QApplication([])
    window = None
    try:
        QMessageBox.warning = lambda *args: warnings.append(args[2])
        window = MainWindow()
        check_intake_totals(destination/'intake-totals')
        check_view_mode_toggles(window, destination)
        check_model_modal_selection(window, destination)
        with capture_qt_callback_errors() as deliberate_errors:
            button = QPushButton('Owned callback guard self-check')
            def expected_callback_error():
                raise RuntimeError('owned deliberate callback guard test')
            button.clicked.connect(expected_callback_error)
            button.click(); app.processEvents()
        assert len(deliberate_errors)==1 and 'owned deliberate callback guard test' in deliberate_errors[0]
        from digifly_app.ui.main_window import _path_row
        browse_calls = []
        def preserving_picker(*args, **kwargs):
            browse_calls.append(args)
            options = kwargs.get('options', args[5] if len(args)>5 else QFileDialog.Option(0))
            return (args[2] if options & QFileDialog.Option.DontResolveSymlinks else '/wrong/base/python', '')
        QFileDialog.getOpenFileName = preserving_picker
        for name in ('NEURON','Arbor','BioNet','NetPyNE'):
            edit = QLineEdit('/owned/'+name+'/bin/python')
            row = _path_row(edit, window, 'Choose '+name+' Python', file_mode=True)
            row.findChild(QPushButton).click()
            assert edit.text() == '/owned/'+name+'/bin/python'
            row.close(); row.deleteLater()
        assert len(browse_calls)==4 and all(call[5] & QFileDialog.Option.DontResolveSymlinks for call in browse_calls)
        QFileDialog.getOpenFileName = open_picker
        (destination/'runtime-browse-check.json').write_text(json.dumps({
            'all_four_runtime_pickers_preserve_launcher_option': True,
            'scope': 'Native button/slot and injected chooser response; real native symlink picker acceptance is separate',
        }, indent=2))
        window.current_project_path = destination/'sentinel.digifly.json'
        window.experiment_page.name_edit.setText('Running student sentinel')
        before = window.current_project_path
        window.experiment_page._process = object()  # deliberately inert
        window.new_project()
        window.open_project(str(destination/'must-not-open.json'))
        assert window.current_project_path == before
        assert window.experiment_page.name_edit.text() == 'Running student sentinel'
        assert len(warnings) == 2
        window.experiment_page._process = None
        class Busy:
            def isRunning(self): return True
        window.netpyne_page._netpyne_worker = Busy()
        assert window._update_busy()
        event = QCloseEvent(); window.closeEvent(event)
        assert not event.isAccepted() and 'Stop' in warnings[-1]
        window.netpyne_page._netpyne_worker = None

        # Source availability, not an assertion about a real runtime/compiler.
        workspace_module._probe_neuron_runtime = lambda *_: (CheckState.PASS, 'inert runtime')
        workspace_module._probe_python_module = lambda *a, **kw: (CheckState.PASS, 'inert runtime')
        workspace = workspace_module.DigiflyWorkspace(destination/'no-legacy-tree')
        arbor = next(p for p in workspace.probe_engines(sys.executable) if p.key == 'arbor')
        assert arbor.source_state == CheckState.PASS and 'app-owned Arbor worker' in arbor.summary
        workspace_module.resource_path = lambda *parts: destination/'missing-sources'/Path(*parts)
        missing = next(p for p in workspace.probe_engines(sys.executable) if p.key == 'arbor')
        assert missing.source_state == CheckState.FAIL
        workspace_module.resource_path = resource_path

        # Exercise the real thread/queued delivery path with an inert evaluator.
        release = Event()
        observations = []
        def inert_readiness(*args, **kwargs):
            observations.append({'off_gui_thread': QThread.currentThread() != app.thread(),
                                 'budget': kwargs.get('bionet_timeout'),
                                 'gui_timer_fired': release.wait(2)})
            return ()
        workspace_module.DigiflyWorkspace.probe_engines = inert_readiness
        page = window.overview_page
        page.workspace_edit.setText(str(destination/'no-legacy-tree'))
        page.output_edit.setText(str(destination/'runs'))
        QTimer.singleShot(30, release.set)
        page.refresh()
        assert window._update_busy()
        close = QCloseEvent();window.closeEvent(close)
        assert not close.isAccepted()
        page.output_edit.setText(str(destination/'changed-runs'))
        for _ in range(300):
            QTest.qWait(10)
            if page._doctor_worker is None:break
        assert page._doctor_worker is None
        assert observations == [{'off_gui_thread': True, 'budget': 60.0, 'gui_timer_fired': True}]
        assert page.cached_readiness_report() is None and 'Choices changed' in page.doctor_summary.text()
        assert page.doctor_button.isEnabled()
        (destination/'readiness-thread-check.json').write_text(json.dumps({
            'observations': observations, 'stale_report_rejected': True,
            'active_close_refused': True, 'scope': 'Inert evaluator; no simulator or runtime import',
        }, indent=2))
        workspace_module.DigiflyWorkspace.probe_engines = engine_probe

        # Idle document guards: cancellation and failed/cancelled Save retain edits.
        window.current_project_path = None
        window.experiment_page.name_edit.setText('Owned unsaved native fixture')
        QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Cancel
        window.new_project()
        idle_close = QCloseEvent();window.closeEvent(idle_close)
        assert not idle_close.isAccepted()
        assert window.experiment_page.name_edit.text() == 'Owned unsaved native fixture'
        QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Save
        QFileDialog.getSaveFileName = lambda *a, **kw: ('', '')
        window.new_project()
        assert window.experiment_page.name_edit.text() == 'Owned unsaved native fixture'
        QFileDialog.getSaveFileName = save_picker
        QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Discard
        window.new_project()
        assert window.experiment_page.name_edit.text() == 'Untitled experiment'
        circuit = window.circuit_builder_page
        from itertools import product
        from digifly_app.core.connectomes import NeuronRecord
        from digifly_app.core.morphology import load_swc
        from digifly_app.ui.circuit_viewport import CircuitViewport, DISPLAY_MODE_FULL_SKELETONS
        display_fixture = destination/'engineering-display-only.swc'
        points = tuple(product((-20,20), repeat=3))
        display_fixture.write_text('\n'.join(
            f'{i} 3 {x} {y} {z} 1 {-1 if i == 1 else 1}'
            for i,(x,y,z) in enumerate(points,1))+'\n')
        shape = load_swc(NeuronRecord('1','UNKNOWN','engineering display fixture',str(display_fixture),'fixture'))
        viewport = CircuitViewport(camera_only=True)
        try:
            viewport.resize(300,1200)
            viewport.set_morphologies((shape,))
            viewport.set_display_mode(DISPLAY_MODE_FULL_SKELETONS)
            viewport.fit_all()
            pixels = [viewport._project(p,viewport._mvp()) for p in points]
            assert all(p is not None and 0 <= p[0] <= viewport.width() and
                       0 <= p[1] <= viewport.height() for p in pixels)
        finally:
            viewport.close(); viewport.deleteLater(); app.processEvents()
        (destination/'narrow-framing-check.json').write_text(json.dumps({
            'all_points_inside_portrait_pane': True,
            'scope': 'Native projection math on an explicitly synthetic display-only shape; actual saved-image review remains separate',
        }, indent=2))
        circuit.replace_hh_check.setChecked(True)
        QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Cancel
        window.new_project()
        assert circuit.replace_hh_check.isChecked() and circuit.has_unapplied_settings()
        idle_close = QCloseEvent(); window.closeEvent(idle_close)
        assert not idle_close.isAccepted()
        target = destination/'must-not-save-unapplied.digifly.json'
        window.current_project_path = target
        assert not window.save_project() and not target.exists()
        try:
            window._save_update_draft(target)
        except ValueError as exc:
            assert 'Unapplied Circuit Builder' in str(exc)
        else:
            raise AssertionError('Update recovery omitted unapplied settings')
        circuit._selection_controls_changed()
        assert circuit.replace_hh_check.isChecked()
        circuit.apply_cell_set_defaults()
        assert not circuit.has_unapplied_settings() and circuit.spec.membrane.replace_builtin_hh
        assert window.save_project() and target.is_file()
        QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Discard
        window.new_project()
        (destination/'unapplied-settings-check.json').write_text(json.dumps({
            'cancel_new_and_quit_retain_editor': True,
            'save_and_update_refuse_omission': True,
            'cancel_selection_retains_editor': True,
            'explicit_apply_then_save': True,
            'scope': 'Owned native control fixtures with injected dialog responses; not full manual interaction coverage',
        }, indent=2))
        editor = window.netpyne_page.netpyne_editor
        editor.name_edit.setText('Separate native model')
        QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Cancel
        idle_close = QCloseEvent();window.closeEvent(idle_close)
        assert not idle_close.isAccepted() and editor.name_edit.text() == 'Separate native model'
        (destination/'idle-draft-check.json').write_text(json.dumps({
            'cancel_new_retains_edits': True, 'cancel_quit_refused': True,
            'cancel_save_cancels_new': True, 'explicit_discard_resets': True,
            'separate_netpyne_close_guard': True,
            'scope': 'Owned native form fixtures; dialog responses injected, not human interaction coverage',
        }, indent=2))

        # A native app's sys.executable is not a portable test interpreter.
        # Use an owned executable placeholder; it is never actually launched.
        probe_python = (destination/'inert-probe-python').absolute()
        probe_python.write_text('#!/bin/sh\nexit 1\n')
        probe_python.chmod(0o700)
        import_calls = []
        def broken_import(command, **kwargs):
            import_calls.append(command)
            assert 'import_module' in command[2]
            assert kwargs['timeout'] == 8 and kwargs['env']['PYTHONDONTWRITEBYTECODE'] == '1'
            assert kwargs['env']['PYTHONNOUSERSITE'] == '1'
            return SimpleNamespace(returncode=1, stdout='', stderr='RuntimeError: owned broken import')
        runtime_discovery.subprocess.run = broken_import
        state, detail = module_probe(str(probe_python), 'arbor', clean_environment=True)
        assert state == CheckState.FAIL and 'owned broken import' in detail and len(import_calls) == 1
        (destination/'arbor-import-check.json').write_text(json.dumps({
            'actual_import_program_required': True, 'broken_import_refused': True,
            'read_only_environment': True, 'scope': 'Inert child response; no real runtime launched',
        }, indent=2))
        opened = []
        artifact = destination/'reader-artifact.txt'
        artifact.write_text('Owned engineering file; must not launch the artifact itself.\n')
        QDesktopServices.openUrl = lambda url: opened.append(url.toLocalFile()) or True
        if sys.platform == 'win32':
            QDesktopServices.openUrl = lambda url: opened.append(str(Path(url.toLocalFile()))) or True
        assert _reveal(str(artifact), window) and opened == [str(destination)]
        QDesktopServices.openUrl = lambda url: False
        assert not _reveal(str(artifact), window) and 'manually' in warnings[-1]
        QDesktopServices.openUrl = desktop_open
        (destination/'folder-action-check.json').write_text(json.dumps({
            'file_routes_to_containing_folder': True, 'desktop_failure_reported': True,
            'scope': 'Inert desktop URL handler; actual Finder action remains separate',
        }, indent=2))
        timeout_calls = []
        def timeout(command, **kwargs):
            timeout_calls.append(command)
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        runtime_discovery.subprocess.run = timeout
        result = runtime_discovery.probe_simulator_runtime(probe_python, timeout=20)
        (destination/'timeout-check.json').write_text(json.dumps({
            'executable': str(probe_python), 'executable_exists': probe_python.is_file(),
            'mock_calls': len(timeout_calls), 'error': result.error,
            'scope': 'Inert timeout injection; no interpreter or simulator launched',
        }, indent=2))
        assert len(timeout_calls) == 1, repr(result.error)
        assert 'timed out after 20' in result.error and len(result.error) < 250
        assert 'import importlib' not in result.error
        unrelated = destination/'not-a-summary.json'
        unrelated.write_text(json.dumps({'schema': 'student-control-inventory', 'controls': []}))
        try:
            ResultsPage._load_result(unrelated)
        except ValueError as exc:
            assert 'supported run summary' in str(exc)
        else:
            raise AssertionError('Results accepted an unrelated JSON document')
        return dict(student_project_busy_guards=True, student_arbor_doctor_sources=True,
                    student_probe_timeout_message=True,
                    student_unrelated_result_refusal=True,
                    student_readiness_background_and_stale_guard=True,
                    student_unsaved_document_guards=True,
                    student_arbor_import_failure_guard=True,
                    student_result_folder_action=True,
                    student_unapplied_settings_guards=True,
                    student_narrow_viewport_framing=True,
                    student_runtime_browse_link_guard=True,
                    student_qt_callback_error_capture=True,
                    student_intake_cumulative_total_message=True,
                    student_view_toggle_render_binding=True,
                    student_result_expansion_state_binding=True,
                    student_model_modal_selection_guard=True,
                    student_check_scope='Native producer/control checks with inert workers/runtime probes; not a simulator run or every-button acceptance')
    finally:
        runtime_discovery.subprocess.run = subprocess_run
        QFileDialog.getSaveFileName = save_picker
        QFileDialog.getOpenFileName = open_picker
        QDesktopServices.openUrl = desktop_open
        workspace_module.DigiflyWorkspace.probe_engines = engine_probe
        workspace_module.resource_path = resource_path
        workspace_module._probe_neuron_runtime = neuron_probe
        workspace_module._probe_python_module = module_probe
        if window is not None:
            worker = window.overview_page._doctor_worker
            if worker is not None:
                release.set()
                worker.wait(3000)
                app.processEvents()
            window.experiment_page._process = None
            window.netpyne_page._netpyne_worker = None
            # Explicitly discard only this owned engineering fixture document.
            QMessageBox.question = lambda *a, **kw: QMessageBox.StandardButton.Discard
            window.close(); app.processEvents()
        QMessageBox.question = question
        QMessageBox.warning = warning
        for key, value in saved_env.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
