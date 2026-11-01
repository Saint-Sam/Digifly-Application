"""Cancellable preparation of shared result data, never a simulator launch."""
from pathlib import Path
import json
import time
from PySide6.QtCore import QThread, Signal
from digifly_app.core.result_voltage import load_voltage_table, LoadCancelled, MappedSeries, _digest, _stamp
from dataclasses import replace
from .result_playback import (
    load_activity_flow_tracks, load_morphology_voltage_tracks, segment_distances_from_soma,
    cached_morphology_voltage_bindings,
)
from .voltage_plot import load_voltage_traces, prepare_trace_plot
from .current_plot import prepare_current_indices
from digifly_app.core.connectomes import NeuronRecord
from digifly_app.core.morphology import load_swc
from .circuit_viewport import CircuitViewport
from digifly_app.workers.result_storage import result_path


def install_review_metrics(page, destination):
    """Observe explicit manual-review loads, not ordinary app usage or actions."""
    from PySide6.QtCore import QTimer
    destination = Path(destination)/'result-loads'
    destination.mkdir(exist_ok=False)
    last = time.perf_counter()
    worker = None
    active = None
    counter = 0

    def tick():
        nonlocal last,worker,active,counter
        now = time.perf_counter()
        gap = now-last
        last = now
        operation = page._last_result_operation
        if operation is not None and operation is not worker:
            worker = operation
            if page._displayed_result is not None:
                # The old observer charged the complete preceding timer gap,
                # including time BEFORE the user's request (e.g. native picker).
                # Keep every interval overlapping the actual request, including
                # synchronous metadata/setup, but never unrelated idle time.
                start = operation['requested_at']
                active = dict(start=start,gaps=[request_heartbeat_gap(now-gap,now,start)],
                              setup_seconds=operation['started_at']-start,
                              summary=operation['summary'])
        elif active is not None:
            active['gaps'].append(gap)
        if active is not None and not page.loading:
            active['summary'] = worker['summary']
            gaps = sorted(active['gaps'])
            counter += 1
            record = dict(schema='digifly-manual-result-load-v2',
                summary=active['summary'],elapsed_seconds=now-active['start'],
                request_boundary='Explicit Results handler after native picker selection; includes metadata/setup and final display',
                setup_seconds=active['setup_seconds'],heartbeat_samples=len(gaps),
                heartbeat_p99_seconds=gaps[min(len(gaps)-1,int(len(gaps)*.99))],
                heartbeat_max_seconds=max(gaps),load_stats=page.load_stats,
                status=page.result_load_note.text(),trace_count=page.voltage_plot.trace_count,
                sample_count=page.voltage_plot.sample_count,
                measured_tracks=len(page._morphology_voltage_tracks),
                scope='Read-only timing observation; not proof of manual interaction or scientific accuracy')
            with (destination/f'{counter:04d}.json').open('x') as stream:
                json.dump(record,stream,indent=2)
            active = None
    timer = QTimer(page)
    timer.setInterval(20)
    timer.timeout.connect(tick)
    timer.start()
    return timer


def request_heartbeat_gap(previous_tick, now, requested_at):
    """Only the timer interval overlapping the actual loading request."""
    return max(0.,now-max(previous_tick,requested_at))


def read_recorded_morphologies(result, check=lambda: None, *, reuse_morphologies=()):
    root = Path(result.summary_path).resolve().parent
    def read(name):
        path=root/name
        if not path.is_file():
            return {}
        try:
            value=json.loads(path.read_text())
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError,TypeError):
            return {}
    records=read('worker_request.json').get('morphologies')
    records=records if isinstance(records,dict) else {}
    connectome=read('circuit.json').get('connectome')
    connectome=connectome if isinstance(connectome,dict) else {}
    key=str(connectome.get('key') or connectome.get('dataset') or '')
    stimulus_ids=set()
    stimuli=read('experiment.json').get('stimuli')
    for stimulus in stimuli if isinstance(stimuli,list) else ():
        if isinstance(stimulus,dict) and stimulus.get('enabled',True):
            targets=stimulus.get('target_neuron_ids')
            if isinstance(targets,list):
                stimulus_ids.update(map(str,targets))
    items=[];failures=[]
    candidates = {}
    for candidate in reuse_morphologies:
        check()
        body = candidate.record.neuron_id
        candidates[body] = candidate if body not in candidates else None
    artifacts=[a for a in result.artifacts if a.kind=='morphology' and a.exists and Path(a.path).suffix.casefold()=='.swc']
    for artifact in artifacts:
        check()
        path=Path(artifact.path).resolve()
        body=recorded_neuron_id(artifact,records)
        meta=records.get(body) or {}
        meta=meta if isinstance(meta,dict) else {}
        kind=str(meta.get('neuron_type') or 'Unknown')
        family=str(meta.get('family') or '')
        if not family and kind[:2].upper() in {'AN','DN','IN','MN','SN'}:
            family=kind[:2].upper()
        try:
            reuse = candidates.get(body)
            options = {'reuse_from':reuse} if reuse is not None else {}
            items.append(load_swc(NeuronRecord(body,family,kind,str(path),key), check=check, **options))
        except (OSError,ValueError) as exc:
            failures.append(f'{body}: {exc}')
    return tuple(items),failures,stimulus_ids,bool(artifacts)


def recorded_neuron_id(artifact,records):
    matches=[str(body) for body in records if artifact.label==f'Normalized morphology · {body}']
    if len(matches)==1:
        return matches[0]
    if len(records)==1:
        return str(next(iter(records)))
    parent=Path(artifact.path).resolve().parent.name.strip()
    if parent in records:
        return parent
    suffix=artifact.label.rsplit('·',1)[-1].strip()
    return suffix if suffix in records else parent or suffix


def result_input_snapshot(run_root, cache_root, check, *, hashes=True):
    """Fingerprint the complete saved input, excluding only this display cache."""
    root=Path(run_root).resolve();cache=Path(cache_root).resolve();snapshot={}
    if root.is_relative_to(cache):
        raise ValueError('Display cache must not contain the saved result directory')
    def cancelled():check();return False
    for path in sorted(root.rglob('*')):
        check()
        if not path.is_file() or path.is_relative_to(cache):
            continue
        stamp=_stamp(path.resolve())
        entry={'stamp':stamp}
        if hashes:
            entry['sha256']=_digest(path,cancelled)
            if _stamp(path.resolve())!=stamp:
                raise ValueError('Saved result changed during verification; reopen the completed result')
        snapshot[path.relative_to(root).as_posix()]=entry
    check()
    return snapshot


class ResultLoadWorker(QThread):
    # A QThread still shares Python's interpreter lock with UI callbacks.
    # With the scene isolated and its layout stable, this measured local
    # quantum keeps cached reconstruction from starving a whole paint cycle.
    _reader_cpu_quantum_seconds = .008
    _reader_yield_seconds = .004
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    invalidated_cache = Signal(object)
    progress = Signal(str, int)

    def __init__(self, run_root, morphologies, cache_root, parent=None, *, result=None, previous=None,
                 result_loader=None,result_discovery=None,reuse_morphologies=()):
        super().__init__(parent)
        self.run_root = Path(run_root)
        self.morphologies = tuple(morphologies)
        self._provided_morphology_ids = tuple(id(m) for m in self.morphologies)
        self.cache_root = Path(cache_root)
        self.result = result
        self.previous = previous
        self.result_loader = result_loader
        self.result_discovery = result_discovery
        self._reuse_morphologies = tuple(reuse_morphologies)
        self.operation = None
        self._last_progress = ('', 0.)
        self._last_progress_percent = None
        self._last_reader_yield = None

    def cancel(self):
        self.requestInterruption()

    def _check(self):
        if self.isInterruptionRequested():
            raise LoadCancelled()
        now=time.perf_counter()
        if self._last_reader_yield is None:
            self._last_reader_yield=now
        elif now-self._last_reader_yield >= self._reader_cpu_quantum_seconds:
            time.sleep(self._reader_yield_seconds)
            self._last_reader_yield=time.perf_counter()
            if self.isInterruptionRequested():
                raise LoadCancelled()

    def _cancelled(self):
        self._check()
        return False

    def _progress(self, label, done, total):
        now = time.monotonic()
        percent = min(100,int(done*100/total)) if total else -1
        if label != self._last_progress[0] or (percent != self._last_progress_percent
                and now-self._last_progress[1] >= .1):
            # Repeated identical percentages only enqueue redraw work. Keep
            # the existing cadence for changed values and immediate phases.
            self.progress.emit(label,percent)
            self._last_progress = label, now
            self._last_progress_percent = percent

    def run(self):
        start = time.perf_counter()
        try:
            if self.result_discovery is not None:
                self.progress.emit('Finding the latest completed result',-1)
                path=Path(self.result_discovery(self._check)).expanduser().resolve()
                self._check()
                self.run_root=path.parent
                if str(path)!=self.result.summary_path:
                    self.result=replace(self.result,summary_path=str(path),status='loading',
                        title='Opening saved result',metadata={},artifacts=(),checks=())
                if self.operation is not None:self.operation['summary']=str(path)
            previous=self.previous
            checked_digest=None
            # A user can select a project or unrelated JSON in the picker.
            # Validate that one file BEFORE traversing any containing folder.
            # Known, previously validated run roots still use full verification
            # before deciding whether their prior metadata may be reused.
            known_request = (previous is not None and previous.get('request_metadata_checked',False)
                and previous.get('result_record') is not None and self.result is not None
                and previous['result_record'].summary_path==self.result.summary_path)
            if self.result_loader is not None and not known_request:
                self.progress.emit('Checking the selected run summary',-1)
                before=_digest(Path(self.result.summary_path),self._cancelled)
                self.result=self.result_loader(Path(self.result.summary_path))
                self.run_root=Path(self.result.summary_path).resolve().parent
                checked_digest=_digest(Path(self.result.summary_path),self._cancelled)
                if before!=checked_digest:
                    raise ValueError('Saved summary changed during validation; reopen the completed result')
                if self.operation is not None:self.operation['summary']=self.result.summary_path
                self._check()
            self.progress.emit('Checking saved result files',-1)
            snapshot=result_input_snapshot(self.run_root,self.cache_root,self._check)
            if checked_digest is not None:
                entry=snapshot.get(Path(self.result.summary_path).name,{})
                if entry.get('sha256')!=checked_digest:
                    raise ValueError('Saved summary changed during verification; reopen the completed result')
            # The snapshot covers saved-run files, not arbitrary external files
            # referenced by legacy summaries or mutable caller-owned geometry.
            # Such results must prepare afresh through their ordinary readers.
            same_input = (previous is not None and previous.get('run_root')==str(self.run_root.resolve())
                and previous.get('cache_root')==str(self.cache_root.resolve())
                and previous.get('result_record')==self.result
                and previous.get('provided_morphology_ids')==self._provided_morphology_ids
                and previous.get('input_snapshot')==snapshot)
            contained = (previous['inputs_contained'] if same_input and 'inputs_contained' in previous
                else not self.morphologies and (self.result is None or all(
                Path(artifact.path).resolve().is_relative_to(self.run_root.resolve())
                for artifact in self.result.artifacts)))
            can_reuse = contained and same_input and (self.result_loader is None
                    or previous.get('request_metadata_checked',False))
            if can_reuse and isinstance(previous['table'].series,MappedSeries):
                self.progress.emit('Checking reusable display data',-1)
                if not previous['table'].series.verify_cache_identity(self._cancelled):
                    self.invalidated_cache.emit(self.result)
                    can_reuse = False
            if can_reuse:
                self._check()
                current=result_input_snapshot(self.run_root,self.cache_root,self._check,hashes=False)
                if current!={name:{'stamp':entry['stamp']} for name,entry in snapshot.items()}:
                    raise ValueError('Saved result changed during verification; reopen the completed result')
                elapsed=time.perf_counter()-start
                self.succeeded.emit(dict(previous,table=replace(previous['table'],cache_hit=True),
                    seconds=elapsed,table_seconds=elapsed,cache_hit=True,prepared_reused=True))
                return
            if self.result_loader is not None and checked_digest is None:
                self.progress.emit('Refreshing saved result details',-1)
                self.result = self.result_loader(Path(self.result.summary_path))
                if self.operation is not None:self.operation['summary']=self.result.summary_path
                contained = (not self.morphologies and all(
                    Path(artifact.path).resolve().is_relative_to(self.run_root.resolve())
                    for artifact in self.result.artifacts))
                self._check()
            geometry = None
            failures = []
            stimulus_ids = set()
            has_morphology_artifacts = False
            if self.result is not None:
                self.progress.emit('Preparing recorded circuit',-1)
                self.morphologies,failures,stimulus_ids,has_morphology_artifacts = read_recorded_morphologies(
                    self.result,self._check,reuse_morphologies=self._reuse_morphologies)
                geometry = CircuitViewport.prepare_morphologies(self.morphologies,self._check)
            shared_geometry_nodes = shared_geometry_segments = 0
            candidates = {m.record.neuron_id:m for m in self._reuse_morphologies}
            for morphology in self.morphologies:
                candidate = candidates.get(morphology.record.neuron_id)
                if candidate is None:
                    continue
                for index,(left,right) in enumerate(zip(morphology.nodes,candidate.nodes)):
                    if index % 256 == 0: self._check()
                    shared_geometry_nodes += left is right
                for index,(left,right) in enumerate(zip(morphology.segments,candidate.segments)):
                    if index % 256 == 0: self._check()
                    shared_geometry_segments += left is right
            self._check()
            binding_error = ''
            bindings_cache_hit = False
            edge_maps = soma_edges = None
            try:
                edge_maps, soma_edges, bindings_cache_hit = cached_morphology_voltage_bindings(
                    self.run_root, self.morphologies, self.cache_root, check=self._check)
            except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
                binding_error = str(exc)
            voltage_path=result_path(self.run_root.resolve()/'voltage_traces.csv')
            # Reuse the complete digest already obtained by this request, not
            # a profile/cache claim. The table reader checks the original stamp
            # before use; its end check and the final whole-run check remain.
            voltage_identity=snapshot.get(voltage_path.relative_to(self.run_root.resolve()).as_posix())
            table = load_voltage_table(self.run_root/'voltage_traces.csv',
                cache_root=self.cache_root, cancelled=self._cancelled,
                progress=self._progress,verified_source=voltage_identity)
            loaded = time.perf_counter()
            self._check()
            labels = {m.record.neuron_id: f'{m.record.neuron_type} · body {m.record.neuron_id}'
                      for m in self.morphologies}
            self.progress.emit('Preparing shared traces and replay', -1)
            traces = load_voltage_traces(self.run_root/'voltage_traces.csv',
                                         neuron_labels=labels, table=table,check=self._check)
            plot_data = prepare_trace_plot(traces,check=self._check)
            self._check()
            warnings = []
            try:
                if binding_error:
                    raise ValueError(binding_error)
                morphology = load_morphology_voltage_tracks(self.run_root, self.morphologies,
                    table=table, edge_maps=edge_maps,check=self._check)
            except (OSError, EOFError, ValueError, KeyError, TypeError) as exc:
                morphology = ()
                warnings.append(f'Morphology voltage unavailable: {exc}')
            self._check()
            flow = load_activity_flow_tracks(self.run_root, table=table, soma_edges=soma_edges,check=self._check)
            self._check()
            distances = {m.record.neuron_id: segment_distances_from_soma(m,check=self._check) for m in self.morphologies}
            try:
                currents = prepare_current_indices(self.run_root)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                currents = ()
                warnings.append(f'Saved current could not be read: {exc}')
            self._check()
            final_stamps=result_input_snapshot(self.run_root,self.cache_root,self._check,hashes=False)
            if final_stamps!={name:{'stamp':entry['stamp']} for name,entry in snapshot.items()}:
                raise ValueError('Saved result changed during loading; reopen the completed result')
            self.succeeded.emit(dict(table=table, traces=traces, plot_data=plot_data, morphology=morphology,
                run_root=str(self.run_root.resolve()),cache_root=str(self.cache_root.resolve()),
                result_record=self.result,provided_morphology_ids=self._provided_morphology_ids,
                input_snapshot=snapshot,prepared_reused=False,
                inputs_contained=contained,
                request_metadata_checked=self.result_loader is not None,
                bindings_cache_hit=bindings_cache_hit,
                flow=flow, distances=distances, currents=currents, labels=labels,
                warnings=warnings, seconds=time.perf_counter()-start,
                table_seconds=loaded-start, cache_hit=table.cache_hit,
                morphologies=self.morphologies,geometry=geometry,failures=failures,
                shared_geometry_nodes=shared_geometry_nodes,
                shared_geometry_segments=shared_geometry_segments,
                stimulus_ids=stimulus_ids,has_morphology_artifacts=has_morphology_artifacts))
        except LoadCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
