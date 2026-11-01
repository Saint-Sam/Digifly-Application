"""A checkable view selector must update the rendered mode, not just its lamp."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QAccessible, QAccessibleActionInterface
from PySide6.QtWidgets import QApplication, QLineEdit

from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import load_swc
from digifly_app.ui.circuit_builder import CircuitBuilderPage
from digifly_app.ui.circuit_viewport import DISPLAY_MODE_FULL_SKELETONS, DISPLAY_MODE_SOMA_POINTS
from digifly_app.ui.main_window import MainWindow


@pytest.mark.parametrize('surface', ['builder', 'results'])
@pytest.mark.parametrize('action', ['state', 'accessible', 'click'])
def test_view_mode_state_and_accessible_actions_match_rendering(tmp_path, monkeypatch, surface, action):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE', str(tmp_path / 'absent-profile.json'))
    monkeypatch.setenv('DIGIFLY_WORKSTATION_SETTINGS_ROOT', str(tmp_path / 'settings'))
    if surface == 'builder':
        owner = CircuitBuilderPage(SimpleNamespace(workspace_edit=QLineEdit(str(tmp_path))))
        viewport = owner.viewport
        soma, full = owner.soma_points_button, owner.full_skeletons_button
    else:
        owner = MainWindow()
        page = owner.results_page
        viewport = page.result_viewport
        soma, full = page.result_soma_points_button, page.result_full_skeletons_button
        soma.setEnabled(True)
        full.setEnabled(True)
    swc = tmp_path / '1.swc'
    swc.write_text('1 1 0 0 0 1 -1\n2 3 10 5 0 .5 1\n')
    before = swc.read_bytes()
    morphology = load_swc(NeuronRecord('1', 'UNKNOWN', 'view fixture', str(swc), 'fixture'))
    viewport.set_morphologies((morphology,))
    # Establish a synchronized starting state without invoking the behavior under test.
    viewport.set_display_mode(DISPLAY_MODE_SOMA_POINTS)
    soma.setChecked(True)
    full.setChecked(False)
    modes = []
    viewport.display_mode_changed.connect(modes.append)
    def activate(button):
        if action == 'state':
            button.setChecked(True)
        elif action == 'accessible':
            interface = QAccessible.queryAccessibleInterface(button)
            assert interface is not None
            actions = interface.actionInterface()
            assert QAccessibleActionInterface.toggleAction() in actions.actionNames()
            actions.doAction(QAccessibleActionInterface.toggleAction())
        else:
            button.click()
        app.processEvents()
    try:
        activate(full)
        assert viewport.display_mode == DISPLAY_MODE_FULL_SKELETONS
        assert full.isChecked() and not soma.isChecked()
        activate(soma)
        assert viewport.display_mode == DISPLAY_MODE_SOMA_POINTS
        assert soma.isChecked() and not full.isChecked()
        assert modes == [DISPLAY_MODE_FULL_SKELETONS, DISPLAY_MODE_SOMA_POINTS]
        assert viewport.morphologies['1'] == morphology and swc.read_bytes() == before
    finally:
        owner.close()
        owner.deleteLater()
        app.processEvents()
