"""Own only fixture widgets; never close or inspect a user's app."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication,QWidget
from shiboken6 import isValid
from digifly_app.ui.package_library_smoke import dispose_fixture_window


def fixture_window(active=False):
    window=QWidget();calls=[]
    page=SimpleNamespace(loading=active,current_plot=SimpleNamespace(loading=False),
        cancel_loading=lambda **kw:calls.append('cancel'))
    window.results_page=page
    window.data_library_page=SimpleNamespace(neuron_browser=SimpleNamespace(shutdown=lambda:calls.append('browser')))
    window.circuit_builder_page=SimpleNamespace(shutdown=lambda:calls.append('builder'))
    return window,calls


def test_fixture_deletes_while_qt_is_live_not_at_interpreter_shutdown():
    app=QApplication.instance() or QApplication([])
    window,calls=fixture_window()
    dispose_fixture_window(window,app)
    assert not isValid(window)
    assert calls==['cancel','browser','builder']


def test_fixture_refuses_to_delete_an_active_reader():
    app=QApplication.instance() or QApplication([])
    window,calls=fixture_window(active=True)
    try:
        with pytest.raises(RuntimeError,match='refusing to destroy'):
            dispose_fixture_window(window,app,timeout_seconds=0)
        assert isValid(window)
        assert calls==['cancel','browser','builder']
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window,QEvent.Type.DeferredDelete)


def test_neuron_fixture_uses_the_live_qt_disposal_path():
    from digifly_app.ui.package_neuron_smoke import dispose_neuron_fixture
    app=QApplication.instance() or QApplication([])
    window,calls=fixture_window()
    dispose_neuron_fixture(window,app)
    assert not isValid(window)
    assert calls==['cancel','browser','builder']


def test_neuron_fixture_never_deletes_an_active_experiment():
    from digifly_app.ui.package_neuron_smoke import dispose_neuron_fixture
    app=QApplication.instance() or QApplication([])
    window,calls=fixture_window()
    window.experiment_page=SimpleNamespace(_process=object())
    try:
        with pytest.raises(RuntimeError,match='active fixture experiment'):
            dispose_neuron_fixture(window,app)
        assert isValid(window) and not calls
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(window,QEvent.Type.DeferredDelete)
