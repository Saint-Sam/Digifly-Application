"""A failed Qt slot must not produce a successful native-check receipt."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import json
import sys
from PySide6.QtWidgets import QApplication, QPushButton


def test_native_entry_refuses_uncaught_button_error(tmp_path, monkeypatch):
    from digifly_app import app as entry
    from digifly_app.ui import package_library_smoke, fragment_review_check, student_readiness_check
    app=QApplication.instance() or QApplication([])
    destination=tmp_path/'owned-native-fixture'
    def smoke(path):
        path.mkdir()
        button=QPushButton('Owned failing callback')
        def fail(): raise ValueError('owned callback failure')
        button.clicked.connect(fail)
        button.click(); app.processEvents()
        (path/'receipt.json').write_text(json.dumps({'ok':True}))
        return 0
    # Keep this deliberate failure off the process's default stderr hook.
    # The real entrypoint must install its own guard and reject the receipt.
    observed=[]
    monkeypatch.setattr(sys,'excepthook',lambda *a: observed.append(a))
    monkeypatch.setattr(package_library_smoke,'run_library_smoke',smoke)
    monkeypatch.setattr(fragment_review_check,'check_fragment_record_roundoff',lambda *a: None)
    monkeypatch.setattr(student_readiness_check,'check_student_safety',lambda *a: {})
    monkeypatch.setattr(sys,'argv',['digifly','--package-library-test',str(destination)])
    assert entry.main()==1
    saved=json.loads((destination/'receipt.json').read_text())
    assert saved['ok'] is False and 'owned callback failure' in saved['qt_callback_errors'][0]


def test_capture_scope_records_a_failed_signal_and_restores_hook():
    from digifly_app.ui.student_readiness_check import capture_qt_callback_errors
    app=QApplication.instance() or QApplication([])
    original=sys.excepthook
    button=QPushButton('Deliberate owned signal failure')
    def fail(): raise RuntimeError('owned expected failure')
    button.clicked.connect(fail)
    with capture_qt_callback_errors() as errors:
        button.click(); app.processEvents()
    assert len(errors)==1 and 'owned expected failure' in errors[0]
    assert sys.excepthook is original


def test_clean_signal_has_no_errors():
    from digifly_app.ui.student_readiness_check import capture_qt_callback_errors
    app=QApplication.instance() or QApplication([])
    button=QPushButton('Ordinary owned signal'); received=[]
    button.clicked.connect(lambda:received.append(True))
    with capture_qt_callback_errors() as errors:
        button.click(); app.processEvents()
    assert received==[True] and not errors
