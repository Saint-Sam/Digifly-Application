"""Actual Qt signal/lifecycle tests; no model runs or real app preference writes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import time
import threading
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pytest
from digifly_app.core.models import ResultRecord
from digifly_app.ui.main_window import MainWindow
from digifly_app.ui.result_loading import ResultLoadWorker
from test_result_voltage_store import recording


def wait_for(app,predicate,seconds=5):
    deadline=time.monotonic()+seconds
    while not predicate() and time.monotonic()<deadline:
        app.processEvents(); QTest.qWait(5)
        # Windows Qt's test wait can keep Python's worker from acquiring its
        # interpreter lock. Yield explicitly without extending the deadline.
        time.sleep(.001)
    app.processEvents()
    assert predicate()


def test_worker_opens_voltage_once_and_views_share_values(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    path=recording(tmp_path)
    import digifly_app.core.result_voltage as module
    original=module.open_result_text; calls=[]
    def opened(p):
        calls.append(p)
        return original(p)
    monkeypatch.setattr(module,'open_result_text',opened)
    worker=ResultLoadWorker(path.parent,(),tmp_path/'cache')
    received=[]; errors=[]
    worker.succeeded.connect(received.append); worker.failed.connect(errors.append)
    worker.start(); wait_for(app,lambda:not worker.isRunning())
    assert not errors and len(received)==1
    result=received[0]
    assert calls==[path]
    assert len(result['traces'])==8
    assert result['traces'][0].voltages_mV is next(iter(result['table'].series.values())).values
    assert not result['morphology']  # no verified run geometry: never invent it
    assert result['flow'][0].frame_times_ms==(0.,.02,.04)


def test_worker_does_not_digest_the_same_voltage_input_twice(tmp_path,monkeypatch):
    import digifly_app.ui.result_loading as loading
    import digifly_app.core.result_voltage as store
    app=QApplication.instance() or QApplication([]);path=recording(tmp_path)
    original=store._digest;calls=[]
    def counted(candidate,cancelled):
        if Path(candidate).resolve()==path.resolve():calls.append(str(candidate))
        return original(candidate,cancelled)
    monkeypatch.setattr(loading,'_digest',counted)
    monkeypatch.setattr(store,'_digest',counted)
    worker=ResultLoadWorker(path.parent,(),tmp_path/'cache');received=[];errors=[]
    worker.succeeded.connect(received.append);worker.failed.connect(errors.append)
    worker.start();wait_for(app,lambda:not worker.isRunning())
    assert not errors and len(received)==1
    assert len(calls)==(3 if store.WINDOWS_FILE_CHECKS else 1), 'Unexpected saved-input digest count'
    assert received[0]['table'].sample_count==24


def test_request_digest_reuse_preserves_external_voltage_symlinks(tmp_path):
    app=QApplication.instance() or QApplication([]);path=recording(tmp_path)
    outside=tmp_path/'external-voltage.csv';path.rename(outside);path.symlink_to(outside)
    worker=ResultLoadWorker(path.parent,(),tmp_path/'cache');received=[];errors=[]
    worker.succeeded.connect(received.append);worker.failed.connect(errors.append)
    worker.start();wait_for(app,lambda:not worker.isRunning())
    assert not errors and len(received)==1
    assert received[0]['table'].sample_count==24 and outside.is_file() and path.is_symlink()


def test_verified_completed_preparation_reuses_values_but_never_changed_run_files(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([]);path=recording(tmp_path)
    import digifly_app.ui.result_loading as module
    calls=[];original=module.load_voltage_table
    def counted(*args,**kwargs):calls.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(module,'load_voltage_table',counted)
    def run(previous=None):
        worker=ResultLoadWorker(path.parent,(),tmp_path/'cache',previous=previous)
        received=[];errors=[]
        worker.succeeded.connect(received.append);worker.failed.connect(errors.append)
        worker.start();wait_for(app,lambda:not worker.isRunning())
        assert not errors and len(received)==1
        return received[0]
    first=run();second=run(first)
    assert not first['prepared_reused'] and second['prepared_reused'] and len(calls)==1
    assert second['traces'] is first['traces'] and second['plot_data'] is first['plot_data']
    assert second['table'].series is first['table'].series and second['table'].cache_hit
    (path.parent/'new-saved-detail.txt').write_text('A changed saved input must force fresh preparation')
    third=run(second)
    assert not third['prepared_reused'] and len(calls)==2
    assert third['table'].sample_count==24 and len(third['traces'])==8


def test_result_snapshot_does_not_exclude_real_input_symlinks_or_allow_cache_ancestor(tmp_path):
    from digifly_app.ui.result_loading import result_input_snapshot
    cache=tmp_path/'cache';cache.mkdir();target=cache/'linked-source';target.write_text('saved bytes')
    source=tmp_path/'source';source.symlink_to(target)
    snapshot=result_input_snapshot(tmp_path,cache,lambda:None)
    assert set(snapshot)=={'source'} and snapshot['source']['sha256']
    with pytest.raises(ValueError,match='must not contain'):
        result_input_snapshot(tmp_path,tmp_path,lambda:None)


def test_external_artifact_cannot_bypass_saved_input_verification(tmp_path):
    from dataclasses import replace
    from digifly_app.core.models import Artifact
    app=QApplication.instance() or QApplication([]);path=recording(tmp_path)
    outside=tmp_path/'external-detail.txt';outside.write_text('Original external artifact')
    record=replace(result(path),artifacts=(Artifact('file',str(outside),'External detail',True),))
    def run(previous=None):
        worker=ResultLoadWorker(path.parent,(),tmp_path/'cache',result=record,previous=previous)
        received=[];errors=[]
        worker.succeeded.connect(received.append);worker.failed.connect(errors.append)
        worker.start();wait_for(app,lambda:not worker.isRunning())
        assert not errors and len(received)==1
        return received[0]
    first=run();outside.write_text('Changed outside the saved run folder')
    second=run(first)
    assert not second['prepared_reused']


@pytest.mark.parametrize('changed_geometry', [False, True])
def test_worker_prepares_bindings_once_before_large_table_and_keeps_geometry_guard(
        tmp_path, monkeypatch, changed_geometry):
    from test_result_playback import _measured_run, _recorded_morphology
    import digifly_app.ui.result_loading as loading
    import digifly_app.ui.result_playback as playback
    app=QApplication.instance() or QApplication([])
    folder=_measured_run(tmp_path)
    if changed_geometry:
        with (folder/'normalized_input.swc').open('a') as stream:
            stream.write('4 3 30 0 0 .4 3\n')
    events=[]
    original_json=playback.read_result_json
    def read_map(path):
        events.append('map')
        return original_json(path)
    monkeypatch.setattr(playback,'read_result_json',read_map)
    original_table=loading.load_voltage_table
    def read_table(*args,**kwargs):
        events.append('table')
        return original_table(*args,**kwargs)
    monkeypatch.setattr(loading,'load_voltage_table',read_table)
    worker=ResultLoadWorker(tmp_path,(_recorded_morphology(tmp_path),),tmp_path/'cache')
    received=[];errors=[]
    worker.succeeded.connect(received.append);worker.failed.connect(errors.append)
    worker.start();wait_for(app,lambda:not worker.isRunning())
    assert not errors and len(received)==1
    prepared=received[0]
    assert events.index('map')<events.index('table')
    assert len(prepared['traces'])==2 and prepared['table'].sample_count==4
    assert not hasattr(prepared['traces'][0],'__dict__')
    if changed_geometry:
        assert not prepared['morphology']
        assert any('geometry identity changed' in note for note in prepared['warnings'])
    else:
        assert events==['map','table']
        assert playback.measured_segment_voltages(prepared['morphology'][0],2.)=={'10000':{2:-20.,3:-45.}}


@pytest.fixture
def window(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE',str(tmp_path/'missing-profile.json'))
    monkeypatch.setenv('DIGIFLY_WORKSTATION_SETTINGS_ROOT',str(tmp_path/'settings'))
    window=MainWindow()
    yield app,window,window.results_page
    window.results_page.cancel_loading(quiet=True)
    wait_for(app,lambda:not window.results_page.loading and not window.results_page.current_plot.loading)
    app.processEvents(); window.close(); window.deleteLater(); app.processEvents()


def result(path):
    return ResultRecord(summary_path=str(path.parent/'summary.json'),status='complete',
        completed_at='',title='async fixture',metadata={},artifacts=())


def test_latest_request_reuses_verified_metadata_and_refreshes_changes_in_worker(window,tmp_path,monkeypatch):
    app,owner,page=window
    root=tmp_path/'results';root.mkdir()
    path=recording(root)
    # The production latest-result discovery accepts only completed manifests.
    run=root/'experiments'/'recording';run.parent.mkdir();path.parent.rename(run)
    path=run/'voltage_traces.csv'
    summary=run/'summary.json'
    payload=dict(workflow='experiment_builder_v1',schema_version=1,
                 title='first checked title',artifacts=[])
    summary.write_text(json.dumps(payload))
    (run/'run_manifest.json').write_text(json.dumps({'state':'completed'}))
    owner.overview_page.output_edit.setText(str(root));owner._mark_project_clean()
    calls=[];original=page._load_result;main=threading.get_ident()
    def loaded(p):
        calls.append(threading.get_ident());return original(p)
    monkeypatch.setattr(page,'_load_result',loaded)
    page.load_latest();wait_for(app,lambda:not page.loading)
    assert page._displayed_result.title=='first checked title'
    assert len(calls)==1 and calls[0]!=main
    page.load_latest();wait_for(app,lambda:not page.loading)
    assert len(calls)==1 and page.load_stats['prepared_reused']
    payload['title']='changed checked title';summary.write_text(json.dumps(payload))
    page.load_latest();wait_for(app,lambda:not page.loading)
    assert page._displayed_result.title=='changed checked title'
    assert len(calls)==2 and calls[-1]!=main
    assert not page.load_stats['prepared_reused'] and page.voltage_plot.trace_count==8


def test_summary_picker_cannot_trust_a_record_from_an_unvalidated_display_call(window,tmp_path):
    app,owner,page=window
    path=recording(tmp_path);summary=path.parent/'summary.json';summary.write_text('{}')
    page.display_result(result(path));wait_for(app,lambda:not page.loading)
    page._request_result(summary,requested_at=time.perf_counter());wait_for(app,lambda:not page.loading)
    assert 'supported run summary' in page.result_load_note.text()
    assert page.voltage_plot.trace_count==0 and page._voltage_table is None


def test_unsupported_summary_is_rejected_before_scanning_its_directory(window,tmp_path,monkeypatch):
    app,owner,page=window
    selected=tmp_path/'project.json';selected.write_text(json.dumps({'workflow':'not-a-result'}))
    import digifly_app.ui.result_loading as loading
    scans=[]
    def forbidden(*args,**kwargs):
        scans.append(args[0]);raise AssertionError('Unvalidated summary triggered directory scanning')
    monkeypatch.setattr(loading,'result_input_snapshot',forbidden)
    page._request_result(selected,requested_at=time.perf_counter())
    wait_for(app,lambda:not page.loading)
    assert not scans
    assert 'supported run summary' in page.result_load_note.text()


def test_summary_alias_uses_its_validated_run_root(window,tmp_path):
    app,owner,page=window
    path=recording(tmp_path);summary=path.parent/'summary.json'
    summary.write_text(json.dumps(dict(workflow='experiment_builder_v1',schema_version=1,artifacts=[])))
    other=tmp_path/'other-folder';other.mkdir();alias=other/'saved-result.json';alias.symlink_to(summary)
    page._request_result(alias,requested_at=time.perf_counter());wait_for(app,lambda:not page.loading)
    assert page._displayed_result.summary_path==str(summary)
    assert page._prepared_result['run_root']==str(summary.parent)
    assert page.voltage_plot.trace_count==8 and page.voltage_plot.sample_count==24


def test_summary_change_during_admission_is_refused_before_directory_scan(window,tmp_path,monkeypatch):
    app,owner,page=window
    path=recording(tmp_path);summary=path.parent/'summary.json'
    summary.write_text(json.dumps(dict(workflow='experiment_builder_v1',schema_version=1,artifacts=[])))
    original=page._load_result
    def changed(p):
        record=original(p);summary.write_text('Changed during validation');return record
    monkeypatch.setattr(page,'_load_result',changed)
    import digifly_app.ui.result_loading as loading
    def forbidden(*args,**kwargs):raise AssertionError('Changed summary triggered directory scanning')
    monkeypatch.setattr(loading,'result_input_snapshot',forbidden)
    page._request_result(summary,requested_at=time.perf_counter());wait_for(app,lambda:not page.loading)
    assert 'summary changed during validation' in page.result_load_note.text()
    assert page.voltage_plot.trace_count==0


def test_latest_discovery_io_does_not_block_window_or_ignore_cancel(window,tmp_path,monkeypatch):
    app,owner,page=window
    root=tmp_path/'results';job=root/'jobs'/'completed';job.mkdir(parents=True)
    summary=tmp_path/'summary.json'
    summary.write_text(json.dumps(dict(workflow='experiment_builder_v1',schema_version=1,artifacts=[])))
    (job/'status.json').write_text(json.dumps({'state':'completed'}))
    (job/'resolved_plan.json').write_text(json.dumps({'expected_summary_path':str(summary)}))
    owner.overview_page.output_edit.setText(str(root));owner._mark_project_clean()
    original=Path.read_text;entered=threading.Event();calls=[];main=threading.get_ident()
    def delayed(path,*args,**kwargs):
        if path==job/'status.json':
            calls.append(threading.get_ident());entered.set();time.sleep(.2)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',delayed)
    ticks=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:ticks.append(1));timer.start()
    start=time.perf_counter();page.load_latest();elapsed=time.perf_counter()-start
    assert elapsed<.1
    wait_for(app,entered.is_set);page.result_load_button.click()
    wait_for(app,lambda:not page.loading);timer.stop()
    assert calls and all(thread!=main for thread in calls) and len(ticks)>=3
    assert page.voltage_plot.trace_count==0 and 'cancelled' in page.result_load_note.text()


@pytest.mark.parametrize('change',['removed','symlink','invalid_summary'])
def test_deferred_metadata_cannot_retain_stale_artifacts_or_invalid_summary(window,tmp_path,change):
    app,owner,page=window
    path=recording(tmp_path);run=path.parent;summary=run/'summary.json'
    first=run/'first.txt';first.write_text('first artifact')
    second=run/'second.txt';second.write_text('second artifact')
    selected=run/'selected.txt';selected.symlink_to(first)
    payload=dict(workflow='experiment_builder_v1',schema_version=1,title='checked',
                 artifacts=[dict(kind='file',path='selected.txt',label='selected')])
    summary.write_text(json.dumps(payload))
    page._request_result(summary,requested_at=time.perf_counter())
    wait_for(app,lambda:not page.loading)
    assert page._displayed_result.artifacts[0].path==str(first)
    if change=='removed':first.unlink()
    elif change=='symlink':selected.unlink();selected.symlink_to(second)
    else:summary.write_text('not a valid saved summary')
    page._request_result(summary,requested_at=time.perf_counter())
    wait_for(app,lambda:not page.loading)
    if change=='invalid_summary':
        assert 'Could not load' in page.result_load_note.text()
        assert page.voltage_plot.trace_count==0 and page._voltage_table is None
    else:
        artifact=page._displayed_result.artifacts[0]
        assert not page.load_stats['prepared_reused']
        assert artifact.exists is (change!='removed')
        if change=='symlink':assert artifact.path==str(second)
        assert page.voltage_plot.trace_count==8


def test_cancel_and_retry_during_changed_metadata_refresh(window,tmp_path,monkeypatch):
    app,owner,page=window
    path=recording(tmp_path);summary=path.parent/'summary.json'
    payload=dict(workflow='experiment_builder_v1',schema_version=1,title='first',artifacts=[])
    summary.write_text(json.dumps(payload))
    page._request_result(summary,requested_at=time.perf_counter())
    wait_for(app,lambda:not page.loading)
    payload['title']='changed';summary.write_text(json.dumps(payload))
    original=page._load_result;entered=threading.Event()
    def delayed(p):
        entered.set();time.sleep(.12);return original(p)
    monkeypatch.setattr(page,'_load_result',delayed)
    page._request_result(summary,requested_at=time.perf_counter())
    wait_for(app,entered.is_set)
    page.result_load_button.click();wait_for(app,lambda:not page.loading)
    assert page.voltage_plot.trace_count==0 and 'cancelled' in page.result_load_note.text()
    page.result_load_button.click();wait_for(app,lambda:not page.loading)
    assert page._displayed_result.title=='changed' and page.voltage_plot.trace_count==8
    assert 'Loaded ' in page.result_load_note.text()


def test_cancel_is_responsive_and_late_success_cannot_commit(window,tmp_path,monkeypatch):
    app,owner,page=window
    path=recording(tmp_path)
    import digifly_app.ui.result_loading as module
    original=module.load_voltage_table; entered=threading.Event()
    def delayed(*args,**kwargs):
        entered.set()
        # Deliberately ignore interruption briefly, exercising stale-result
        # suppression even for a read that returns after Cancel.
        time.sleep(.15)
        return original(*args,**{**kwargs,'cancelled':lambda:False})
    monkeypatch.setattr(module,'load_voltage_table',delayed)
    ticks=[]; timer=QTimer(); timer.setInterval(5); timer.timeout.connect(lambda:ticks.append(1)); timer.start()
    page.display_result(result(path))
    wait_for(app,entered.is_set)
    page.result_load_button.click()
    assert 'cancelled' in page.result_load_note.text()
    wait_for(app,lambda:page._result_load_worker is None)
    timer.stop()
    assert len(ticks)>=3 and page.voltage_plot.trace_count==0
    assert page._voltage_table is None
    page.result_load_button.click()
    wait_for(app,lambda:page._result_load_worker is None)
    assert page.voltage_plot.trace_count==8 and page.load_stats['cache_hit']


def test_switch_result_and_close_during_read(window,tmp_path,monkeypatch):
    app,owner,page=window
    path=recording(tmp_path)
    import digifly_app.ui.result_loading as module
    original=module.load_voltage_table
    def slow(*args,**kwargs):
        time.sleep(.1)
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'load_voltage_table',slow)
    page.display_result(result(path))
    missing=tmp_path/'second'/'voltage_traces.csv';missing.parent.mkdir()
    (missing.parent/'summary.json').write_text('{}')
    page.display_result(result(missing))
    wait_for(app,lambda:page._result_load_worker is None)
    assert page.voltage_plot.trace_count==0 and page._displayed_result.summary_path==str(missing.parent/'summary.json')
    page.display_result(result(path))
    owner.show(); owner.close()
    assert owner.isVisible()  # first close requests safe cancellation
    wait_for(app,lambda:page._result_load_worker is None)
    owner.close(); assert not owner.isVisible()


def current_fixture(tmp_path):
    def channel(kind):
        return dict(signal=kind,units='nA',neuron_id='1',source_compartment_id=20,
                    section_id='s',cv_index=0,nseg=1,condition='Control',
                    repetition=1,site_id='1-20',samples=3)
    return ((tmp_path/'index.json',dict(times_ms=[0.,.02,.04],
                                      channels=[channel('membrane'),channel('axial')])),)


def test_background_current_cancel_immediate_retry_and_kind_switch(window,tmp_path,monkeypatch):
    app,owner,page=window;plot=page.current_plot
    import digifly_app.ui.current_plot as module
    entered=threading.Event();release=threading.Event();calls=[]
    def read(path,channel,**kwargs):
        calls.append(channel['signal']);entered.set()
        if len(calls)==1 and not release.wait(5):
            raise AssertionError('The fixture did not release its first reader')
        from array import array
        return array('d',(0.,1.,2.))
    monkeypatch.setattr(module,'read_values',read)
    plot.set_prepared(current_fixture(tmp_path))
    assert not calls and not plot.plot.trace_count  # metadata is genuinely lazy
    try:
        plot.ensure_selected();wait_for(app,entered.is_set)
        plot.load_button.click()
        assert 'cancelled' in plot.load_status.text()
        plot.load_button.click()  # retry while the first read is definitely held
        assert plot.loading and plot._pending_kind==plot.kind.currentData()
    finally:
        release.set()
    wait_for(app,lambda:not plot.loading)
    assert plot.plot.trace_count==1 and len(calls)==2
    initial=plot.kind.currentData()
    plot.kind.setCurrentIndex(1-plot.kind.currentIndex())
    wait_for(app,lambda:not plot.loading)
    assert plot._loaded_kind==plot.kind.currentData()!=initial
    assert plot.plot.canvas.quantity_unit=='nA'


def test_background_current_failure_retry_and_close_are_safe(window,tmp_path,monkeypatch):
    app,owner,page=window;plot=page.current_plot
    import digifly_app.ui.current_plot as module
    def failed(*args,**kwargs):raise ValueError('changed block')
    monkeypatch.setattr(module,'read_values',failed)
    plot.set_prepared(current_fixture(tmp_path));plot.ensure_selected()
    wait_for(app,lambda:not plot.loading)
    assert 'changed block' in plot.load_status.text() and not plot.plot.trace_count
    from array import array
    def slow(*args,**kwargs):time.sleep(.12);return array('d',(0.,1.,2.))
    monkeypatch.setattr(module,'read_values',slow)
    plot.load_button.click();owner.show();owner.close()
    assert owner.isVisible()
    wait_for(app,lambda:not plot.loading)
    assert not plot.plot.trace_count
    owner.close();assert not owner.isVisible()


def test_hidden_artifact_rows_are_lazy_and_show_only_current_result(window,tmp_path):
    from dataclasses import replace
    from digifly_app.core.models import Artifact
    app,owner,page=window;path=recording(tmp_path)
    first=replace(result(path),artifacts=(Artifact('file',str(path),'first file',True),))
    page.display_result(first);wait_for(app,lambda:not page.loading)
    assert page.artifact_rows.count()==0
    second=replace(result(path),artifacts=(Artifact('file',str(path),'second file',True),))
    page.display_result(second);wait_for(app,lambda:not page.loading)
    page.artifact_section.toggle_button.click()
    assert page.artifact_rows.count()==1
    from PySide6.QtWidgets import QLabel
    labels=[l.text() for l in page.artifact_rows.itemAt(0).widget().findChildren(QLabel)]
    assert any('second file' in text for text in labels) and not any('first file' in text for text in labels)


def test_queued_cancel_cannot_relabel_completed_recording(window,tmp_path):
    app,owner,page=window;path=recording(tmp_path)
    page.display_result(result(path));wait_for(app,lambda:not page.loading)
    before=page.result_load_note.text()
    assert before.startswith('Loaded ')
    page.result_load_button.click()  # Simulates a click already queued when loading ended.
    assert page.result_load_note.text()==before and page.voltage_plot.sample_count==24


def test_unchanged_reload_keeps_display_only_after_full_input_check(window,tmp_path,monkeypatch):
    app,owner,page=window;path=recording(tmp_path);record=result(path)
    page.display_result(record);wait_for(app,lambda:not page.loading)
    calls=[];original=page._reset_result_display
    def reset(value):calls.append(value);return original(value)
    monkeypatch.setattr(page,'_reset_result_display',reset)
    page.voltage_plot.legend_filter.setText('not-a-neuron')
    page.display_result(record)
    assert not calls and page.voltage_plot.trace_count==8
    assert 'previous view' in page.result_load_note.text()
    assert not page.activity_slider.isEnabled() and not page.activity_play_button.isEnabled()
    assert 'paused' in page.activity_method.toolTip()
    wait_for(app,lambda:not page.loading)
    assert not calls and page.load_stats['display_reused']
    assert page.voltage_plot.legend_filter.text()=='not-a-neuron'
    (path.parent/'changed-input.txt').write_text('Must not keep stale displayed bindings')
    page.display_result(record);wait_for(app,lambda:not page.loading)
    assert len(calls)==1 and not page.load_stats['display_reused']


def test_retained_reload_does_not_reflow_replay_help_and_progress_row(window,tmp_path):
    app,owner,page=window;path=recording(tmp_path);record=result(path)
    page.display_result(record);wait_for(app,lambda:not page.loading)
    label=page.activity_method.text()
    page.display_result(record)
    assert page.activity_method.text()==label
    assert 'paused' in page.result_load_note.text()
    assert all(control.sizePolicy().retainSizeWhenHidden() for control in
        (page.result_load_note,page.result_load_progress,page.result_load_button))
    wait_for(app,lambda:not page.loading)
    assert page.activity_method.text()==label


@pytest.mark.parametrize('cancel',[False,True])
def test_verification_failure_or_cancel_clears_previous_view(window,tmp_path,monkeypatch,cancel):
    app,owner,page=window;path=recording(tmp_path);record=result(path)
    page.display_result(record);wait_for(app,lambda:not page.loading)
    import digifly_app.ui.result_loading as module
    entered=threading.Event()
    def failed(*args,**kwargs):
        entered.set();time.sleep(.08);raise ValueError('saved input no longer readable')
    monkeypatch.setattr(module,'result_input_snapshot',failed)
    page.display_result(record);wait_for(app,entered.is_set)
    if cancel:page.result_load_button.click()
    wait_for(app,lambda:not page.loading)
    assert page.voltage_plot.trace_count==0 and page._voltage_table is None
    assert not page._keeping_result_display
    assert ('cancelled' if cancel else 'no longer readable') in page.result_load_note.text()


def test_request_clock_excludes_pre_request_idle_but_keeps_actual_setup_stall():
    from digifly_app.ui.result_loading import request_heartbeat_gap
    assert request_heartbeat_gap(1.,5.02,5.)==pytest.approx(.02)
    assert request_heartbeat_gap(1.,5.02,4.5)==pytest.approx(.52)
    assert request_heartbeat_gap(5.,5.02,4.)==pytest.approx(.02)


def test_reader_fairness_is_bounded_and_keeps_cancellation_checks(tmp_path,monkeypatch):
    import digifly_app.ui.result_loading as module
    clock=[0.]
    monkeypatch.setattr(module.time,'perf_counter',lambda:clock[0])
    worker=ResultLoadWorker(tmp_path,(),tmp_path/'cache')
    yields=[]
    monkeypatch.setattr(module.time,'sleep',lambda seconds:yields.append(seconds))
    for clock[0] in (0.,.002,.007):worker._check()
    assert not yields
    clock[0]=.009;worker._check()
    assert yields==[.004]
    for _ in range(5):worker._check()
    assert yields==[.004]
    clock[0]=.018;worker._check()
    assert yields==[.004,.004]
    monkeypatch.setattr(worker,'isInterruptionRequested',lambda:True)
    with pytest.raises(module.LoadCancelled):worker._check()
    assert yields==[.004,.004]


def test_reader_cancellation_during_its_short_yield_is_not_delayed(tmp_path,monkeypatch):
    import digifly_app.ui.result_loading as module
    clock=[0.];cancelled=[False]
    monkeypatch.setattr(module.time,'perf_counter',lambda:clock[0])
    worker=ResultLoadWorker(tmp_path,(),tmp_path/'cache')
    monkeypatch.setattr(worker,'isInterruptionRequested',lambda:cancelled[0])
    worker._check()
    monkeypatch.setattr(module.time,'sleep',lambda seconds:cancelled.__setitem__(0,True))
    clock[0]=.009
    with pytest.raises(module.LoadCancelled):worker._check()


def test_reader_does_not_queue_duplicate_progress_redraws(tmp_path,monkeypatch):
    import digifly_app.ui.result_loading as module
    app=QApplication.instance() or QApplication([]);clock=[0.]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    worker=ResultLoadWorker(tmp_path,(),tmp_path/'cache');events=[]
    worker.progress.connect(lambda label,percent:events.append((label,percent)))
    for clock[0] in (0.,.11,.22,.33):worker._progress('Reading saved voltage',5,100)
    assert events==[('Reading saved voltage',5)]
    clock[0]=.44;worker._progress('Reading saved voltage',6,100)
    clock[0]=.45;worker._progress('Preparing replay',0,0)
    clock[0]=.66;worker._progress('Preparing replay',0,0)
    assert events==[('Reading saved voltage',5),('Reading saved voltage',6),('Preparing replay',-1)]


def test_reader_cancellation_callback_never_ignores_an_interruption(tmp_path,monkeypatch):
    import digifly_app.ui.result_loading as module
    clock=[0.];interrupted=[False]
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    worker=ResultLoadWorker(tmp_path,(),tmp_path/'cache')
    monkeypatch.setattr(worker,'isInterruptionRequested',lambda:interrupted[0])
    assert worker._cancelled() is False
    interrupted[0]=True;clock[0]=.006
    with pytest.raises(module.LoadCancelled):worker._cancelled()


def test_unknown_loading_progress_is_static_and_never_claims_a_percentage(window):
    app,owner,page=window;generation=page._result_load_generation
    page._result_loading_progress(generation,'Checking saved files',-1)
    assert page.result_load_progress.maximum()==100
    assert page.result_load_progress.value()==0
    assert page.result_load_progress.format()=='Working…'
    assert '%' not in page.result_load_note.text()
    page._result_loading_progress(generation,'Reading saved voltage',37)
    assert page.result_load_progress.value()==37
    assert page.result_load_progress.format()=='%p%'
    assert page.result_load_note.text()=='Reading saved voltage · 37%'
    page._result_loading_progress(generation-1,'Stale operation',-1)
    assert page.result_load_progress.value()==37


@pytest.mark.parametrize('stage',['traces','plot_indices','measured_replay','inferred_replay','distances'])
def test_shared_view_preparation_keeps_cancellation_checkpoints(tmp_path,stage):
    from types import SimpleNamespace
    from digifly_app.core.result_voltage import load_voltage_table,LoadCancelled
    from digifly_app.ui.voltage_plot import load_voltage_traces,prepare_trace_plot
    from digifly_app.ui.result_playback import (
        load_morphology_voltage_tracks,load_activity_flow_tracks,segment_distances_from_soma)
    path=recording(tmp_path);table=load_voltage_table(path,cache_root=tmp_path/'cache')
    traces=load_voltage_traces(path,table=table)
    morphology=SimpleNamespace(nodes=(SimpleNamespace(node_id=1,x=0.,y=0.,z=0.),),segments=())
    def cancelled():raise LoadCancelled('bounded preparation checkpoint')
    actions={
        'traces':lambda:load_voltage_traces(path,table=table,check=cancelled),
        'plot_indices':lambda:prepare_trace_plot(traces,check=cancelled),
        'measured_replay':lambda:load_morphology_voltage_tracks(path.parent,(),table=table,
            edge_maps={'1':{20:20,30:30},'2':{20:20,30:30}},check=cancelled),
        'inferred_replay':lambda:load_activity_flow_tracks(path.parent,table=table,
            soma_edges={'1':'20','2':'20'},check=cancelled),
        'distances':lambda:segment_distances_from_soma(morphology,check=cancelled),
    }
    with pytest.raises(LoadCancelled,match='bounded preparation checkpoint'):actions[stage]()


def test_checked_reload_does_not_relabel_already_loaded_current_as_cancelled(window,tmp_path):
    app,owner,page=window;path=recording(tmp_path);record=result(path)
    page.display_result(record);wait_for(app,lambda:not page.loading)
    page.current_plot.set_prepared(current_fixture(tmp_path))
    page.current_plot._loaded_kind=page.current_plot.kind.currentData()
    page.current_plot.load_status.setText('Current traces ready')
    page.current_plot.load_status.show()
    page.display_result(record);wait_for(app,lambda:not page.loading)
    assert page.load_stats['display_reused']
    assert page.current_plot.load_status.text()=='Current traces ready'


def test_reload_during_current_read_uses_safe_replacement_not_retained_view(window,tmp_path,monkeypatch):
    app,owner,page=window;path=recording(tmp_path);record=result(path)
    page.display_result(record);wait_for(app,lambda:not page.loading)
    import digifly_app.ui.current_plot as module
    entered=threading.Event()
    def delayed(*args,**kwargs):
        from array import array
        entered.set();time.sleep(.1);return array('d',(0.,1.,2.))
    monkeypatch.setattr(module,'read_values',delayed)
    page.current_plot.set_prepared(current_fixture(tmp_path))
    page.current_plot.ensure_selected();wait_for(app,entered.is_set)
    page.display_result(record)
    assert not page._keeping_result_display
    wait_for(app,lambda:not page.loading and not page.current_plot.loading)
    assert not page.load_stats['display_reused'] and not page.current_plot.indices


def test_manual_review_metrics_observe_only_explicit_result_load(window,tmp_path,monkeypatch):
    from digifly_app.ui.result_loading import install_review_metrics
    app,owner,page=window;path=recording(tmp_path)
    destination=tmp_path/'review';destination.mkdir()
    timer=install_review_metrics(page,destination)
    try:
        assert not list((destination/'result-loads').iterdir())
        page.display_result(result(path))
        wait_for(app,lambda:bool(list((destination/'result-loads').glob('*.json'))))
        receipt=json.loads(next((destination/'result-loads').glob('*.json')).read_text())
        assert receipt['schema']=='digifly-manual-result-load-v2'
        assert receipt['setup_seconds']>=0 and receipt['heartbeat_samples']>0
        assert receipt['sample_count']==24 and receipt['trace_count']==8
        assert receipt['summary']==str(path.parent/'summary.json')
        assert receipt['heartbeat_max_seconds']>0 and receipt['load_stats']['cache_hit'] is False
        import digifly_app.ui.result_loading as module
        original=module.load_voltage_table;entered=threading.Event()
        def delayed(*args,**kwargs):
            entered.set();time.sleep(.1);return original(*args,**kwargs)
        monkeypatch.setattr(module,'load_voltage_table',delayed)
        (path.parent/'changed-input.txt').write_text('Explicit delayed-reader fixture')
        page.display_result(result(path));wait_for(app,entered.is_set)
        page.cancel_loading()
        wait_for(app,lambda:len(list((destination/'result-loads').glob('*.json')))==2)
        generation=page._result_load_generation
        page.result_load_button.click()
        wait_for(app,lambda:len(list((destination/'result-loads').glob('*.json')))==3)
        assert page._result_load_generation==generation
        retry=json.loads((destination/'result-loads/0003.json').read_text())
        assert retry['sample_count']==24 and retry['load_stats']['cache_hit']
    finally:
        timer.stop()
