from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from digifly_app.ui.main_window import MainWindow, OverviewPage, _workspace_home


def test_main_window_constructs_without_importing_simulators():
    application = QApplication.instance() or QApplication([])
    overview = OverviewPage()
    assert overview.output_edit.text() == str(_workspace_home() / "runs")
    overview.close()
    window = MainWindow()
    try:
        assert window.pages.count() == 4
        assert window.windowTitle() == "Digifly App"
        assert window.experiment_page.run_button.isEnabled() is False
        assert "Digifly App.app" not in str(_workspace_home() / "runs")
    finally:
        window.close()
        application.processEvents()
