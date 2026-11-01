"""Current traces share the Results clock, not the voltage axis or color scale."""
from __future__ import annotations
import json
import math
from pathlib import Path
from PySide6.QtCore import Signal, QThread
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QComboBox, QPushButton, QProgressBar
from digifly_app.core.result_voltage import LoadCancelled
from digifly_app.workers.current_tracking import SIGNALS, read_index, read_values, MAX_CURRENT_VALUES
from .voltage_plot import VoltagePlotWidget, VoltageTrace, TRACE_COLORS


def prepare_current_indices(root):
    root = Path(root).resolve()
    path = root/'current_recording.json'
    if not path.is_file():
        return ()
    receipt = json.loads(path.read_text(encoding='utf-8'))
    if receipt.get('schema') != 'digifly-current-recording-v1':
        raise ValueError('Unsupported current recording manifest')
    indices = []
    for relative in receipt['indices']:
        path = (root/relative).resolve()
        if not path.is_relative_to(root) or path.name != 'index.json':
            raise ValueError('Current recording points outside its saved run')
        record = read_index(path)
        if not {c['signal'] for c in record['channels']}.issubset(SIGNALS):
            raise ValueError('Unknown recorded current type')
        indices.append((path, record))
    return tuple(indices)


def build_current_traces(indices, labels, kind, check=lambda: None):
    traces = []
    total = 0
    for path, record in indices:
        check()
        times = tuple(map(float, record['times_ms']))
        if any(not math.isfinite(t) or t < 0 for t in times) or any(b <= a for a,b in zip(times,times[1:])):
            raise ValueError('Invalid saved current clock')
        for c in record['channels']:
            check()
            if c['signal'] != kind:
                continue
            total += len(times)
            if total > MAX_CURRENT_VALUES:
                raise ValueError('Current display exceeds its memory budget; saved samples are unchanged')
            values = read_values(path, c, compact=True)
            check()
            if c['units'] != 'nA' or len(values) != len(times):
                raise ValueError('Current units or sample count differ from its saved clock')
            neuron = str(c['neuron_id'])
            site = f"SWC {c['source_compartment_id']}" if c['source_compartment_id'] is not None else f"section {c['section_id']}"
            identity = f"{c['condition']} · {labels.get(neuron,'body '+neuron)} · {site} · CV {c['cv_index']+1}/{c['nseg']}"
            identity += ' · '+str(c.get('point_process') or c.get('link') or 'center')+f" · rep {c['repetition']}"
            if c.get('pre_id'):
                identity += f" · {c['pre_id']} → {c['post_id']} · {c['contact_id']}"
            if c.get('neuron_a'):
                identity += f" · {c['neuron_a']} ↔ {c['neuron_b']} · {c['contact_id']}"
            traces.append(VoltageTrace(f"{c['condition']}:{c['repetition']}:{c['site_id']}:{kind}", identity,
                c['condition'], int(c['repetition']), neuron, times, values,
                TRACE_COLORS[len(traces)%len(TRACE_COLORS)], str(c['source_compartment_id'] or ''),
                (min(values),max(values))))
    return tuple(traces)


class CurrentTraceWorker(QThread):
    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, indices, labels, kind, parent=None):
        super().__init__(parent)
        self.indices, self.labels, self.kind = indices, dict(labels), kind

    def run(self):
        def check():
            if self.isInterruptionRequested():
                raise LoadCancelled()
        try:
            self.succeeded.emit(build_current_traces(self.indices,self.labels,self.kind,check))
        except LoadCancelled:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class CurrentPlotWidget(QWidget):
    time_selected = Signal(float)
    status_message = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("CurrentPlotWidget")
        self.indices = ()
        self.labels = {}
        self._worker = None
        self._generation = 0
        self._pending_kind = None
        self._loaded_kind = None
        self._background_loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        self.kind.setAccessibleName("Recorded current type")
        layout.addWidget(self.kind)
        self.note = QLabel("Positive outward for membrane/ionic/synaptic/gap; positive inward for injected current; axial is parent toward child. Current traces do not recolor the morphology.")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.load_status = QLabel('')
        self.load_status.setWordWrap(True)
        self.load_progress = QProgressBar()
        self.load_progress.setRange(0,0)
        self.load_progress.setAccessibleName('Current trace loading progress')
        self.load_button = QPushButton('Cancel current loading')
        self.load_button.clicked.connect(self._load_clicked)
        for control in (self.load_status,self.load_progress,self.load_button):
            layout.addWidget(control)
            control.hide()
        self.plot = VoltagePlotWidget()
        self.plot.set_quantity("current (nA)", "nA")
        self.plot.time_selected.connect(self.time_selected)
        self.plot.status_message.connect(self.status_message)
        layout.addWidget(self.plot, 1)
        self.kind.currentIndexChanged.connect(self._select)

    def load_run(self, root, labels=None):
        # Legacy/source callers can use the immediate small-fixture API.
        return self.set_prepared(prepare_current_indices(root),labels,background=False,select=True)

    def set_prepared(self, indices, labels=None, *, background=True, select=False):
        self.clear_run()
        self.indices = tuple(indices)
        self.labels = dict(labels or {})
        self._background_loading = background
        kinds = {c["signal"] for _, r in indices for c in r["channels"]}
        if not kinds.issubset(SIGNALS):
            raise ValueError("Unknown recorded current type")
        self.kind.blockSignals(True)
        for key, label in SIGNALS.items():
            if key in kinds:
                self.kind.addItem(label + " (nA)", key)
        self.kind.blockSignals(False)
        if select:
            self._select()
        return bool(kinds)

    def clear_run(self):
        self.cancel_loading()
        self.indices = ()
        self._loaded_kind = None
        self.kind.clear()
        self.plot.set_traces(())
        for control in (self.load_status,self.load_progress,self.load_button):
            control.hide()

    @property
    def loading(self):
        return self._worker is not None

    def cancel_loading(self):
        self._generation += 1
        self._pending_kind = None
        if self._worker is not None:
            self._worker.requestInterruption()
        self.load_status.setText('Current loading cancelled. Saved data is unchanged.')
        self.load_progress.hide()
        self.load_button.setText('Load current traces')

    def ensure_selected(self):
        if self._loaded_kind != self.kind.currentData():
            self._select()

    def _load_clicked(self):
        if self.load_button.text() == 'Cancel current loading':
            self.cancel_loading()
        else:
            # Retry may be clicked before an interrupted read has emitted its
            # finished signal. Queue it rather than cancelling a second time.
            self.ensure_selected()

    def _select(self, *_args):
        kind = self.kind.currentData()
        if not kind:
            return
        if self._background_loading:
            self._generation += 1
            self._pending_kind = kind
            self._loaded_kind = None
            self.plot.set_traces(())
            self.load_status.setText(f'Loading {SIGNALS[kind].lower()}…')
            self.load_progress.show()
            self.load_status.show()
            self.load_button.setText('Cancel current loading')
            self.load_button.show()
            if self._worker is not None:
                self._worker.requestInterruption()
            else:
                self._start_current_load()
            return
        try:
            cursor = self.plot.canvas.cursor_time_ms
            traces = build_current_traces(self.indices,self.labels,kind)
            self.plot.set_quantity(SIGNALS[kind] + " (nA)", "nA")
            self.plot.set_traces(tuple(traces))
            self.plot.set_time_cursor(cursor)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.plot.set_traces(())
            self.status_message.emit(f"Saved current could not be read: {exc}")

    def _start_current_load(self):
        kind = self._pending_kind
        if not kind or not self.indices:
            return
        self._pending_kind = None
        generation = self._generation
        worker = CurrentTraceWorker(self.indices,self.labels,kind,self)
        self._worker = worker
        worker.succeeded.connect(lambda traces: self._current_loaded(generation,kind,traces))
        worker.failed.connect(lambda error: self._current_failed(generation,error))
        worker.finished.connect(self._current_finished)
        worker.start()

    def _current_loaded(self,generation,kind,traces):
        if generation != self._generation:
            return
        cursor = self.plot.canvas.cursor_time_ms
        self.plot.set_quantity(SIGNALS[kind]+' (nA)','nA')
        self.plot.set_traces(traces)
        self.plot.set_time_cursor(cursor)
        self._loaded_kind = kind
        for control in (self.load_status,self.load_progress,self.load_button):
            control.hide()
        self.status_message.emit(f'Loaded {len(traces):,} {SIGNALS[kind].lower()} traces')

    def _current_failed(self,generation,error):
        if generation == self._generation:
            self.load_status.setText(f'Saved current could not be read: {error}')
            self.load_progress.hide()
            self.load_button.setText('Load current traces')

    def _current_finished(self):
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.deleteLater()
        self._start_current_load()
