"""A verified saved run must not bless a subsequently damaged display cache."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import struct

from PySide6.QtWidgets import QApplication
from digifly_app.ui.result_loading import ResultLoadWorker
from digifly_app.core.models import ResultRecord
from test_result_loading_async import wait_for
from test_result_voltage_store import recording


def test_changed_mapped_cache_rebuilds_instead_of_reusing_corrupted_samples(tmp_path):
    app = QApplication.instance() or QApplication([])
    path = recording(tmp_path)
    saved = path.read_bytes()
    summary = path.parent/'summary.json';summary.write_text('{}')
    record = ResultRecord(str(summary),'complete','','fixture',{},())
    invalidated = []
    def read(previous=None):
        worker = ResultLoadWorker(path.parent,(),tmp_path/'cache',previous=previous,result=record)
        result = []; errors = []
        worker.succeeded.connect(result.append);worker.failed.connect(errors.append)
        if hasattr(worker,'invalidated_cache'):
            worker.invalidated_cache.connect(lambda actual:invalidated.append(actual))
        worker.start();wait_for(app,lambda:not worker.isRunning())
        assert not errors and len(result) == 1
        return result[0]
    first = read()
    folder = next((tmp_path/'cache').glob('*/ready.json')).parent
    store = first['table'].series
    offset = store.offsets[0]*8
    with (folder/'values.bin').open('r+b') as stream:
        stream.seek(offset);stream.write(struct.pack('d',-12.))
    assert next(iter(first['table'].series.values())).values[0] == -12.
    second = read(first)
    assert not second['prepared_reused']
    assert invalidated == [record]
    assert next(iter(second['table'].series.values())).values[0] == -65.
    assert path.read_bytes() == saved
    assert first['traces'] != second['traces']
    # The repaired path now names a new file. The old mapping still owns its
    # damaged unlinked file, so matching path bytes cannot bless that mapping.
    third = read(first)
    assert not third['prepared_reused']
    assert next(iter(third['table'].series.values())).values[0] == -65.
    assert invalidated == [record,record]


def test_late_or_stale_cache_notice_cannot_clear_or_relabel_completed_view():
    from types import SimpleNamespace
    from digifly_app.ui.main_window import ResultsPage
    calls = []
    note = SimpleNamespace(setText=lambda value:calls.append(('note',value)),show=lambda:calls.append('show'))
    page = SimpleNamespace(_result_load_generation=3,_keeping_result_display=False,
        result_load_note=note,_reset_result_display=lambda record:calls.append(('reset',record)))
    ResultsPage._result_cache_invalidated(page,3,'late record')
    assert calls == []
    page._keeping_result_display = True
    ResultsPage._result_cache_invalidated(page,2,'stale record')
    assert calls == []
    ResultsPage._result_cache_invalidated(page,3,'actual discovered record')
    assert calls[0] == ('reset','actual discovered record')
    assert not page._keeping_result_display
