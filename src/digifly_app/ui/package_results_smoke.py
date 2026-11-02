"""Bounded saved-data UI fixtures; never downloads or runs a simulator."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import time


def check_result_surface(window, viewport, platform_name):
    """Verify the chosen platform backend; Mac keeps a raster owner window."""
    from PySide6.QtGui import QSurface
    from .circuit_viewport import _WindowsCircuitViewport
    surface_type = window.windowHandle().surfaceType()
    if platform_name == 'windows':
        if (not isinstance(viewport, _WindowsCircuitViewport)
                or surface_type not in {QSurface.SurfaceType.OpenGLSurface,
                                        QSurface.SurfaceType.RasterGLSurface}):
            raise ValueError('Windows Results lacks its widget-owned OpenGL renderer')
        return False
    if surface_type != QSurface.SurfaceType.RasterSurface:
        raise ValueError('The 3-D scene still forces whole-window OpenGL composition')
    return True


def run_results_smoke(destination: Path) -> int:
    """Exercise Results with tiny owned data, not unrelated download menus.

    The normal all-library check remains available unchanged. This entry point
    is a focused native engineering check, not general student acceptance.
    """
    import os
    import traceback
    from PySide6.QtWidgets import QApplication, QMessageBox
    from digifly_app.core.app_settings import app_settings, SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV, make_standalone_profile
    from .package_library_smoke import dispose_fixture_window
    from .main_window import MainWindow, ORGANIZATION_NAME, APPLICATION_NAME

    destination=destination.resolve();destination.mkdir(parents=True,exist_ok=False)
    record={'schema':'digifly-native-results-check-v1','ok':False,
        'scope':'Focused Results engineering fixtures and separate safety phase; no simulator, acquisition or general student acceptance'}
    started=time.monotonic();phases=[];window=None
    def journal(stage):
        phases.append({'stage':stage,'elapsed_seconds':time.monotonic()-started})
        record['phases']=phases
        (destination/'receipt.json').write_text(json.dumps(record,indent=2)+'\n')
    keys=(PROFILE_PATH_ENV,SETTINGS_ROOT_ENV)
    saved_env={key:os.environ.get(key) for key in keys}
    warning,critical=QMessageBox.warning,QMessageBox.critical
    messages=[]
    app=QApplication.instance() or QApplication([])
    try:
        profile_path=destination/'workspace/config/resources.json'
        make_standalone_profile(workstation_root=destination/'workspace').save(profile_path)
        os.environ[PROFILE_PATH_ENV]=str(profile_path)
        os.environ[SETTINGS_ROOT_ENV]=str(destination/'settings')
        settings=app_settings(ORGANIZATION_NAME,APPLICATION_NAME)
        settings.setValue('workspace_root','')
        settings.setValue('output_root',str(destination/'workspace/runs'))
        def capture(parent,title,text,*args,**kwargs):
            messages.append({'title':title,'text':text});return QMessageBox.StandardButton.Ok
        QMessageBox.warning=QMessageBox.critical=capture
        journal('start')
        window=MainWindow();window.show();app.processEvents()
        journal('window_ready')
        from .package_first_experiment_smoke import check_first_experiment_guide
        record.update(check_first_experiment_guide(window, destination))
        record.update(check_results_review(window,destination))
        journal('results_checked')
        record['ok']=True
    except Exception:
        record.update(ok=False,error=traceback.format_exc())
    finally:
        if window is not None:
            try:
                dispose_fixture_window(window,app)
                record['fixture_shutdown_checked']=True
            except Exception:
                record.update(ok=False,shutdown_error=traceback.format_exc())
        QMessageBox.warning,QMessageBox.critical=warning,critical
        for key,value in saved_env.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        record['dialogs']=messages
        journal('fixture_finished')
    return 0 if record['ok'] else 1


def check_results_review(window, destination: Path) -> dict[str, bool]:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QScrollArea
    from digifly_app.core.results import Artifact, ResultRecord
    from digifly_app.ui.voltage_plot import VOLTAGE_AXIS_LABEL, MODE_2D, MODE_3D_STACK
    from json import scanner
    if scanner.make_scanner.__module__ != '_json':
        raise ValueError('Packaged results lacks the standard-library C JSON reader')

    root = destination / 'dense-results-fixture'
    root.mkdir(exist_ok=False)
    paths = {}
    for body, edges in (('10000', 2), ('10068', 1426)):
        path = root / (body + '.swc')
        path.write_text('1 1 0 0 0 1 -1\n' + ''.join(
            f'{i+1} 3 {i*.2} 0 0 .5 {i}\n' for i in range(1, edges+1)))
        paths[body] = path
    (root / 'summary.json').write_text('{}')
    (root / 'circuit.json').write_text(json.dumps({'connectome':{'key':'manc:v1.2.1'}}))
    (root / 'worker_request.json').write_text(json.dumps({'morphologies':{
        '10000':{'family':'DN','neuron_type':'DNp01'},
        '10068':{'family':'MN','neuron_type':'TTMn'}}}))
    (root / 'experiment.json').write_text(json.dumps({
        'stimuli':[{'enabled':True,'target_neuron_ids':['10068']}],
        'recording':{'target_neuron_ids':['10068'],'target_region':'all',
                     'record_voltage':True,'detect_spikes':False}}))
    (root / 'voltage_traces.csv').write_text(
        'condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\n' + ''.join(
            f'{condition},1,10068,{edge},{t},{v}\n'
            for condition in ('Control','Stim OFF') for edge in range(2,1428)
            for t,v in ((0,-65),(1,-40+edge%20))))
    (root / 'spikes.csv').write_text('condition,repetition,neuron_id,compartment_id,spike_time_ms\n')
    result = ResultRecord(summary_path=str(root/'summary.json'),status='complete',
        completed_at='',title='Engineering dense/subset results fixture',metadata={},
        artifacts=tuple(Artifact('morphology',str(path),'Normalized morphology · '+body,True)
                        for body,path in paths.items()))
    from digifly_app.core.connectomes import NeuronRecord
    from digifly_app.core.morphology import load_swc
    from digifly_app.core.result_voltage import LoadCancelled
    neuron=NeuronRecord('10068','MN','TTMn',str(paths['10068']),'manc:v1.2.1')
    plain_geometry=load_swc(neuron)
    skeleton_checks=[]
    checked_geometry=load_swc(neuron,check=lambda:skeleton_checks.append(1))
    if (checked_geometry!=plain_geometry or len(skeleton_checks)<20
            or hasattr(checked_geometry.nodes[0],'__dict__')
            or hasattr(checked_geometry.segments[0],'__dict__')):
        raise ValueError('Saved skeleton lacks bounded checks or compact unchanged geometry')
    shared_geometry=load_swc(neuron,reuse_from=checked_geometry)
    if (shared_geometry!=plain_geometry
            or any(a is not b for a,b in zip(shared_geometry.nodes,checked_geometry.nodes))
            or any(a is not b for a,b in zip(shared_geometry.segments,checked_geometry.segments))):
        raise ValueError('Identical saved geometry rows are not shared safely')
    cancelled_checks=[]
    def interrupt_skeleton():
        cancelled_checks.append(1)
        if len(cancelled_checks)==3:
            raise LoadCancelled()
    try:
        load_swc(neuron,check=interrupt_skeleton)
    except LoadCancelled:
        pass
    else:
        raise ValueError('Saved skeleton ignores cancellation inside one neuron')
    previous = window.pages.currentIndex()
    nav = next(b for b in window.nav_buttons if 'Results' in b.text())
    QTest.mouseClick(nav,Qt.MouseButton.LeftButton)
    page = window.results_page
    original_morphology_provider = page._morphology_candidates
    page._morphology_candidates = lambda:(checked_geometry,)
    page._result_loading_progress(page._result_load_generation,'Checking saved files',-1)
    if (page.result_load_progress.maximum()!=100
            or page.result_load_progress.value()!=0
            or page.result_load_progress.format()!='Working…'
            or '%' in page.result_load_note.text()):
        raise ValueError('Unknown Results progress animates or claims a percentage')
    page._result_loading_progress(page._result_load_generation,'Reading saved voltage',37)
    if (page.result_load_progress.value()!=37
            or page.result_load_progress.format()!='%p%'):
        raise ValueError('Measured Results percentage does not replace static working status')
    def wait_for(predicate,detail):
        deadline=time.monotonic()+10
        while not predicate() and time.monotonic()<deadline:
            QApplication.processEvents();QTest.qWait(5);time.sleep(.003)
        QApplication.processEvents()
        if not predicate():
            raise ValueError(detail)

    def show_result(value):
        previous_help=page.activity_method.text()
        retaining=(page._displayed_result==value and page._prepared_result is not None
            and not page.loading and not page.current_plot.loading and page._voltage_table is not None)
        page.display_result(value)
        if retaining and page.activity_method.text()!=previous_help:
            raise ValueError('Retained Results replaces wrapped replay help during verification')
        if not all(control.sizePolicy().retainSizeWhenHidden() for control in
                (page.result_load_note,page.result_load_progress,page.result_load_button)):
            raise ValueError('Result progress changes the native scene layout when hidden')
        if not page.result_load_button.isVisible():
            raise ValueError('Result loading lacks its visible cancellation control')
        wait_for(lambda:page._result_load_worker is None,'Background result load did not finish')
        if 'Could not load' in page.result_load_note.text():
            raise ValueError(page.result_load_note.text())

    native_surface_checked=False
    graphics_backend_checked=False
    if QApplication.platformName() not in {'offscreen','minimal'}:
        wait_for(lambda:page.result_viewport.isValid() and page.result_viewport._gpu_ready,
                 'Native Results scene lacks its live graphics context or shader')
        native_surface_checked=check_result_surface(window,page.result_viewport,QApplication.platformName())
        graphics_backend_checked=True
    import digifly_app.core.result_voltage as voltage_store
    import digifly_app.ui.result_loading as loading_module
    original_store_digest=voltage_store._digest
    original_worker_digest=loading_module._digest
    source_digest_calls=[]
    def counted_source_digest(path,cancelled):
        if path.resolve()==(root/'voltage_traces.csv').resolve():source_digest_calls.append(str(path))
        return original_store_digest(path,cancelled)
    voltage_store._digest=loading_module._digest=counted_source_digest
    try:
        show_result(result)
    finally:
        voltage_store._digest=original_store_digest
        loading_module._digest=original_worker_digest
    if len(source_digest_calls)!=(3 if voltage_store.WINDOWS_FILE_CHECKS else 1):
        raise ValueError('Native Results repeats its completely verified voltage digest')
    if (page.load_stats.get('shared_geometry_nodes') != len(checked_geometry.nodes)
            or page.load_stats.get('shared_geometry_segments') != len(checked_geometry.segments)):
        raise ValueError('Results worker did not validate/share the candidate geometry rows')
    if page._voltage_table.cache_hit:
        raise ValueError('New isolated result fixture unexpectedly uses an existing cache')
    original_open=voltage_store.open_result_text
    csv_opens=[]
    def counted(path):
        csv_opens.append(str(path));return original_open(path)
    voltage_store.open_result_text=counted
    try:
        page.voltage_plot.legend_filter.setText('10068')
        show_result(result)
        if not page._voltage_table.cache_hit or csv_opens:
            raise ValueError('Cached Results reparses its voltage source')
        if not page.load_stats.get('prepared_reused'):
            raise ValueError('Unchanged completed Results lacks checked preparation reuse')
        if not page.load_stats.get('display_reused') or page.voltage_plot.legend_filter.text()!='10068':
            raise ValueError('Checked reload unnecessarily rebuilt the prior display')
        from .result_loading import request_heartbeat_gap
        if (abs(request_heartbeat_gap(1.,5.02,5.)-.02)>1e-9
                or abs(request_heartbeat_gap(1.,5.02,4.5)-.52)>1e-9):
            raise ValueError('Request clock confuses idle time with real loading stalls')
    finally:
        voltage_store.open_result_text=original_open
        page.voltage_plot.legend_filter.clear()
    # A damaged mapped payload is not protected by unchanged source-run files.
    # The real worker must invalidate the paused old view and rebuild from CSV.
    import struct
    cache_store = page._voltage_table.series
    payload = next(path for path,_ in cache_store.cache_files if path.name=='values.bin')
    original_value = page.voltage_plot.traces[0].voltages_mV[0]
    with payload.open('r+b') as stream:
        stream.seek(cache_store.offsets[0]*8)
        stream.write(struct.pack('d',original_value+1.))
    show_result(result)
    if (page.load_stats.get('prepared_reused') or page.load_stats.get('display_reused')
            or page.voltage_plot.traces[0].voltages_mV[0] != original_value):
        raise ValueError('Changed mapped display data bypassed verification or remained visible')
    from dataclasses import replace
    external=destination/'external-result-detail.txt'
    external.write_text('External result detail before change')
    external_result=replace(result,artifacts=result.artifacts+(
        Artifact('file',str(external),'External result detail',True),))
    show_result(external_result)
    external.write_text('External result detail after change')
    show_result(external_result)
    if page.load_stats.get('prepared_reused') or page.load_stats.get('display_reused'):
        raise ValueError('External result input bypassed the saved-run verification boundary')
    show_result(result)
    from digifly_app.core.result_bindings import load_replay_bindings
    binding_input=root/'binding-input.json';binding_input.write_text('Engineering ID-cache fixture')
    binding_calls=[]
    def binding_factory():
        binding_calls.append(1)
        return {'10068':{2:2,3:3}},{'10068':'2'}
    identity=(('10068',str(paths['10068']),(2,3)),)
    first_binding=load_replay_bindings(destination/'id-cache-fixture',[binding_input],identity,binding_factory)
    next_binding=load_replay_bindings(destination/'id-cache-fixture',[binding_input],identity,binding_factory)
    if first_binding[2] or not next_binding[2] or first_binding[:2]!=next_binding[:2] or len(binding_calls)!=1:
        raise ValueError('Compiled replay ID cache does not preserve checked bindings')
    # Actual native Cancel control, including late completion suppression. This
    # remains a tiny inert read fixture, not a user experiment or speed claim.
    import digifly_app.ui.result_loading as loading_module
    original_load=loading_module.load_voltage_table
    def delayed(*args,**kwargs):
        time.sleep(.1)
        return original_load(*args,**kwargs)
    loading_module.load_voltage_table=delayed
    before_voltage=hashlib.sha256((root/'voltage_traces.csv').read_bytes()).hexdigest()
    try:
        page.display_result(result)
        QTest.mouseClick(page.result_load_button,Qt.MouseButton.LeftButton)
        wait_for(lambda:page._result_load_worker is None,'Cancelled native result load did not stop')
        if page.voltage_plot.trace_count or 'cancelled' not in page.result_load_note.text():
            raise ValueError('Cancelled native result load commits stale curves')
        if hashlib.sha256((root/'voltage_traces.csv').read_bytes()).hexdigest()!=before_voltage:
            raise ValueError('Cancellation changes saved voltage')
    finally:
        loading_module.load_voltage_table=original_load
    QTest.mouseClick(page.result_load_button,Qt.MouseButton.LeftButton)
    wait_for(lambda:page._result_load_worker is None,'Native result restart did not finish')
    if not page.load_stats.get('cache_hit') or page.load_stats.get('prepared_reused'):
        raise ValueError('Changed fixture input did not exercise the checked disk-table reader')
    QApplication.processEvents()
    if ('2 circuit neuron(s)' not in page.circuit_info.text()
            or '1 recording target(s)' not in page.circuit_info.text()
            or '1 with saved voltage' not in page.circuit_info.text()
            or 'body 10000 · not a recording target' not in page.circuit_identity.text()
            or 'body 10068 · stimulus target · recorded voltage' not in page.circuit_identity.text()):
        raise ValueError('Results confuses saved circuit context with recorded cells')
    plot = page.voltage_plot
    keys = {trace.key for trace in plot.traces}
    if any(hasattr(trace, '__dict__') for trace in plot.traces):
        raise ValueError('Result trace metadata lacks bounded compact records')
    if plot._prepared.traces is not plot.traces or plot.sample_count!=5704:
        raise ValueError('Compiled plot lacks shared precomputed metadata')
    if (not isinstance(page._voltage_table.series,voltage_store.MappedSeries)
            or page._voltage_table.series.materialized_count > 512):
        raise ValueError('Dense recorded data eagerly materializes every trace')
    if any(trace is not page._voltage_table.series[
            (trace.condition,trace.repetition,trace.neuron_id,trace.compartment_id)]
            or trace.voltages_mV is not trace.values for trace in plot.traces):
        raise ValueError('Cached replay and plot retain duplicate trace records')
    if (len({id(trace._trace_metadata) for trace in plot.traces}) != 1
            or any(len(column) != len(plot.traces)
                   for column in plot.traces[0]._trace_metadata.columns)):
        raise ValueError('Cached plot lacks shared primitive metadata columns')
    if len(keys)!=2852 or plot.sample_count!=5704 or len(plot.legend_buttons)>60:
        raise ValueError('Dense Results legend is unbounded or drops data')
    scroll = next(s for s in page.findChildren(QScrollArea)
                  if s.widget().objectName()=='PageContent')
    scroll.ensureWidgetVisible(plot.legend_next_button)
    QTest.qWait(80)
    first_key = next(iter(plot.legend_buttons))
    QTest.mouseClick(plot.legend_buttons[first_key],Qt.MouseButton.LeftButton)
    QTest.mouseClick(plot.legend_next_button,Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    if (len(plot.legend_buttons)>60 or first_key in plot.legend_buttons
            or plot.canvas.visible_keys!=set(plot.legend_buttons) or first_key in plot.canvas.active_keys):
        raise ValueError('Dense trace paging loses its page or emphasis')
    if (any(not b.isVisible() or b.height()<b.sizeHint().height() for b in plot.legend_buttons.values())
            or plot.legend_scroll.verticalScrollBar().maximum()<=0):
        raise ValueError('Legend page buttons collapsed or cannot be scrolled')
    window.grab().save(str(destination/'dense-results-page-2.png'))
    QTest.mouseClick(plot.legend_previous_button,Qt.MouseButton.LeftButton)
    if plot.legend_buttons[first_key].isChecked():
        raise ValueError('Returning to a legend page loses its selection')
    plot.legend_buttons[first_key].setChecked(True)
    if first_key not in plot.canvas.active_keys:
        raise ValueError('State-only legend activation changes the check mark but not the plot')
    plot.legend_buttons[first_key].setChecked(False)
    if first_key in plot.canvas.active_keys:
        raise ValueError('State-only legend deactivation leaves the trace emphasized')
    plot.legend_filter.setText('SWC 1427')
    QApplication.processEvents()
    if len(plot.legend_buttons)!=2 or len(plot.canvas.visible_keys)!=2:
        raise ValueError('Legend search cannot reach records beyond its first page')
    isolated = next(iter(plot.legend_buttons))
    scroll.ensureWidgetVisible(plot.legend_buttons[isolated])
    QTest.qWait(80)
    QTest.mouseClick(plot.legend_buttons[isolated],Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.ShiftModifier)
    if plot.canvas.active_keys!={isolated}:
        raise ValueError('Shift-isolate does not apply across every legend page')
    plot.legend_filter.clear()
    QTest.mouseClick(plot.show_all_button,Qt.MouseButton.LeftButton)
    if (plot.canvas.active_keys!=keys or plot.canvas.visible_keys!=set(plot.legend_buttons)
            or len(plot.legend_buttons)>60 or len(plot.traces)!=2852):
        raise ValueError('Dense trace emphasis loses saved data or exceeds the page')
    scroll.ensureWidgetVisible(page.circuit_info)
    QTest.qWait(80)
    window.grab().save(str(destination/'results-recording-scope.png'))
    if VOLTAGE_AXIS_LABEL != 'membrane voltage (mV)':
        raise ValueError('Regional voltage plot is incorrectly labeled as soma voltage')
    page.voltage_3d_button.setChecked(True)
    if plot.mode != MODE_3D_STACK:
        raise ValueError('State-only 3D view activation does not change the displayed mode')
    page.voltage_2d_button.setChecked(True)
    if plot.mode != MODE_2D:
        raise ValueError('State-only 2D view activation does not change the displayed mode')
    scroll.ensureWidgetVisible(plot.canvas)
    QTest.qWait(80)
    window.grab().save(str(destination/'regional-voltage-axis.png'))
    spike_root = destination/'spike-only-results-fixture'
    folder = spike_root/'morphologies/10068'
    folder.mkdir(parents=True,exist_ok=False)
    swc = folder/'normalized_input.swc'
    swc.write_text('1 1 0 0 0 1 -1\n2 3 5 0 0 .5 1\n')
    (folder/'regional_map.json').write_text(json.dumps({'neuron_id':'10068',
        'site_policy':'Source SWC edge midpoint; recorded values map to source SWC edge midpoint without spreading to unrecorded edges',
        'sections':[{'node_id':2,'source_node_id':2,'is_target_soma':True}]}))
    (folder/'morphology_node_map.csv').write_text('source_node_id,run_node_id\n1,1\n2,2\n')
    (spike_root/'run_manifest.json').write_text(json.dumps({'morphologies':{
        '10068':{'simulation_swc_sha256':hashlib.sha256(swc.read_bytes()).hexdigest()}}}))
    (spike_root/'worker_request.json').write_text(json.dumps({'morphologies':{
        '10068':{'family':'MN','neuron_type':'TTMn'}}}))
    (spike_root/'summary.json').write_text('{}')
    document={'duration_ms':4,'stimuli':[], 'recording':{
        'target_neuron_ids':['10068'],'target_region':'soma',
        'record_voltage':False,'detect_spikes':True}}
    (spike_root/'experiment.json').write_text(json.dumps(document))
    voltage_header='condition,repetition,neuron_id,compartment_id,time_ms,voltage_mV\n'
    spike_header='condition,repetition,neuron_id,compartment_id,spike_time_ms\n'
    (spike_root/'voltage_traces.csv').write_text(voltage_header)
    (spike_root/'spikes.csv').write_text(spike_header+'Control,1,10068,2,1.3\n')
    spike_result=ResultRecord(summary_path=str(spike_root/'summary.json'),status='complete',
        completed_at='',title='Engineering spike-output selection fixture',metadata={},
        artifacts=(Artifact('morphology',str(swc),'Normalized morphology · 10068',True),))
    show_result(spike_result)
    QApplication.processEvents()
    if (page.voltage_plot.trace_count or page.voltage_2d_button.isEnabled()
            or page.voltage_morphology_button.isEnabled() or not page.activity_slider.isEnabled()
            or len(page._flow_tracks)!=1 or page._flow_tracks[0].spikes_by_neuron!={'10068':(1.3,)}
            or 'Inferred' not in page.activity_method.text()):
        raise ValueError('Spike-only Results loses saved events or retains previous voltage')
    page.activity_slider.setValue(page._flow_tracks[0].frame_times_ms.index(1.3))
    if page.activity_time_label.text()!='1.30 ms':
        raise ValueError('Spike-only Results does not reach its actual saved event')
    scroll.ensureWidgetVisible(page.activity_time_label)
    QTest.qWait(80)
    window.grab().save(str(destination/'spike-only-results.png'))
    document['recording'].update(record_voltage=True,detect_spikes=False)
    (spike_root/'experiment.json').write_text(json.dumps(document))
    (spike_root/'voltage_traces.csv').write_text(voltage_header+
        'Control,1,10068,2,0,-60\nControl,1,10068,2,1,20\nControl,1,10068,2,2,-40\n')
    (spike_root/'spikes.csv').write_text(spike_header)
    show_result(spike_result)
    QApplication.processEvents()
    if (page.voltage_plot.trace_count!=1 or page.activity_slider.isEnabled()
            or any(any(t.spikes_by_neuron.values()) for t in page._flow_tracks)):
        raise ValueError('Voltage-only Results invents events or retains previous spike playback')
    page.voltage_morphology_button.setChecked(True)
    if 'Measured' not in page.activity_method.toolTip():
        raise ValueError('Measured Results lacks its current playback help')
    page.voltage_2d_button.setChecked(True)
    if page.activity_method.toolTip()!=page.activity_method.text():
        raise ValueError('Leaving measured playback retains stale help')
    page.voltage_morphology_button.setChecked(True)
    measured_help=page.activity_method.text()
    page.display_result(spike_result)
    if (page.activity_slider.isEnabled()
            or page.activity_method.text()!=measured_help
            or 'paused' not in page.activity_method.toolTip()
            or 'previous view' not in page.result_load_note.text()):
        raise ValueError('Verified-view reload loses its explicit paused-help state')
    wait_for(lambda:page._result_load_worker is None,'Replacement result load did not finish')
    page._reset_activity_playback()
    if page.activity_method.toolTip()!=page.activity_method.text():
        raise ValueError('Resetting Results retains stale playback help')
    from digifly_app.workers.result_storage import compress_file
    expected = page.voltage_plot.traces
    for path in (folder/'regional_map.json', spike_root/'voltage_traces.csv'):
        if not compress_file(path, minimum_bytes=0):
            raise ValueError('Native compressed-results fixture failed to compress')
    show_result(spike_result)
    page.voltage_morphology_button.setChecked(True)
    page.activity_slider.setValue(1)
    if (page.voltage_plot.traces!=expected or not page.activity_slider.isEnabled()
            or page.activity_time_label.text()!='1.00 ms'
            or page.result_viewport.activity_segment_count!=1):
        raise ValueError('Compressed recording/map cannot drive exact measured playback')
    # Inert saved-current fixture: checks display/clock/units only, not a solver.
    import zlib
    from array import array
    from dataclasses import replace
    current_root = spike_root/'currents'/'fixture'
    current_root.mkdir(parents=True)
    channels=[]
    with (current_root/'values.bin').open('xb') as out:
        for kind,values in (('membrane',(0.,.001,.002)),('axial',(0.,-.001,.0005))):
            raw=array('d',values).tobytes(); block=zlib.compress(raw)
            channels.append({'signal':kind,'neuron_id':'10068','condition':'Control','repetition':1,
                'source_compartment_id':2,'section_id':0,'section_x':.5,'cv_index':0,'nseg':1,
                'site_id':'10068:section-0:cv-0','units':'nA','samples':3,'offset':out.tell(),
                'compressed_bytes':len(block),'sha256':hashlib.sha256(raw).hexdigest()})
            out.write(block)
    (current_root/'index.json').write_text(json.dumps({'schema':'digifly-current-traces-v1',
        'mode':'full','times_ms':[0,1,2],'data':'values.bin','channels':channels}))
    (spike_root/'current_recording.json').write_text(json.dumps({'schema':'digifly-current-recording-v1',
        'mode':'full','indices':['currents/fixture/index.json']}))
    show_result(spike_result)
    QTest.mouseClick(page.current_traces_button,Qt.MouseButton.LeftButton)
    wait_for(lambda:page.current_plot._worker is None,'Background current read did not finish')
    if (page.figure_stack.currentWidget() is not page.current_plot
            or page.current_plot.plot.trace_count!=1
            or page.current_plot.plot.canvas.quantity_unit!='nA'
            or page.current_plot.plot.canvas._voltage_bounds[1]>.01):
        raise ValueError('Current plot loses its samples, units, selection or small-current scale')
    page.activity_slider.setValue(1)
    page.current_plot.kind.setCurrentIndex(page.current_plot.kind.findData('axial'))
    wait_for(lambda:page.current_plot._worker is None,'Background axial current read did not finish')
    if (page.current_plot.plot.canvas.cursor_time_ms!=1.
            or tuple(page.current_plot.plot.traces[0].voltages_mV)!=(0.,-.001,.0005)
            or page.activity_time_label.text()!='1.00 ms'
            or 'Measured' not in page.activity_method.text()):
        raise ValueError('Current-type change breaks measured voltage replay synchronization')
    current_status=page.current_plot.load_status.text()
    show_result(spike_result)
    if (not page.load_stats.get('display_reused')
            or page.current_plot.load_status.text()!=current_status
            or page.current_plot.kind.currentData()!='axial'
            or tuple(page.current_plot.plot.traces[0].voltages_mV)!=(0.,-.001,.0005)):
        raise ValueError('Checked reload relabels or loses completed current traces')
    QApplication.processEvents()
    page.current_plot.plot.render_high_resolution().save(str(destination/'current-traces-fixture.png'))
    QTest.mouseClick(page.voltage_2d_button,Qt.MouseButton.LeftButton)
    if (page.figure_stack.currentWidget() is not page.voltage_plot
            or 'nA' in page.image_info.text() or page.figure_title.text()!='Voltage traces'):
        raise ValueError('Returning to voltage retains the current view or current units')
    QTest.mouseClick(page.current_traces_button,Qt.MouseButton.LeftButton)
    if page.figure_title.text()!='Current traces':
        raise ValueError('Current view is labelled as voltage')
    QTest.mouseClick(page.voltage_morphology_button,Qt.MouseButton.LeftButton)
    if page.figure_stack.currentWidget() is not page.voltage_plot or 'nA' in page.image_info.text():
        raise ValueError('Morphology voltage retains the current plot/description')
    # Real request path: unchanged metadata is used only after all input bytes
    # are checked; changed summary details refresh in the background reader.
    import threading
    request_root=destination/'verified-request-details';request_root.mkdir()
    request_summary=request_root/'summary.json'
    request_payload=dict(workflow='experiment_builder_v1',schema_version=1,
                         title='original details',artifacts=[])
    request_summary.write_text(json.dumps(request_payload))
    (request_root/'voltage_traces.csv').write_text(voltage_header+
        'Control,1,10068,2,0,-60\nControl,1,10068,2,1,20\nControl,1,10068,2,2,-40\n')
    original_loader=page._load_result;request_reads=[];main_thread=threading.get_ident()
    def request_loader(path):
        request_reads.append(threading.get_ident());return original_loader(path)
    page._load_result=request_loader
    try:
        page._request_result(request_summary,requested_at=time.perf_counter())
        wait_for(lambda:not page.loading,'Initial request details did not finish')
        page._request_result(request_summary,requested_at=time.perf_counter())
        wait_for(lambda:not page.loading,'Verified request details did not finish')
        if (len(request_reads)!=1 or request_reads[0]==main_thread
                or not page.load_stats.get('prepared_reused')):
            raise ValueError('Unchanged request rereads metadata before complete input verification')
        request_payload['title']='changed details';request_summary.write_text(json.dumps(request_payload))
        page._request_result(request_summary,requested_at=time.perf_counter())
        wait_for(lambda:not page.loading,'Changed request details did not finish')
        if (len(request_reads)!=2 or request_reads[-1]==main_thread
                or page._displayed_result.title!='changed details'
                or page.load_stats.get('prepared_reused') or page.voltage_plot.trace_count!=1):
            raise ValueError('Changed request metadata is stale or refreshed on the window thread')
    finally:
        page._load_result=original_loader
    # A direct display record is not proof that Open summary validated its JSON.
    (request_root/'summary.json').write_text('{}')
    show_result(ResultRecord(summary_path=str(request_summary),status='complete',
        completed_at='',title='unvalidated fixture',metadata={},artifacts=()))
    page._request_result(request_summary,requested_at=time.perf_counter())
    wait_for(lambda:not page.loading,'Invalid request refusal did not finish')
    if ('supported run summary' not in page.result_load_note.text()
            or page.voltage_plot.trace_count or page._voltage_table is not None):
        raise ValueError('Request metadata reuse trusts an unvalidated display record')
    import digifly_app.ui.result_loading as loading
    original_snapshot=loading.result_input_snapshot
    unexpected_scan=[]
    def forbidden_snapshot(*args,**kwargs):
        unexpected_scan.append(args[0]);raise ValueError('Unvalidated summary triggered directory scanning')
    loading.result_input_snapshot=forbidden_snapshot
    try:
        page._request_result(request_summary,requested_at=time.perf_counter())
        wait_for(lambda:not page.loading,'Summary admission refusal did not finish')
        if unexpected_scan or 'supported run summary' not in page.result_load_note.text():
            raise ValueError('Unsupported summary scans its containing directory before refusal')
    finally:
        loading.result_input_snapshot=original_snapshot
    request_summary.write_text(json.dumps(request_payload))
    latest_root=request_root/'latest';job=latest_root/'jobs'/'completed';job.mkdir(parents=True)
    (job/'status.json').write_text(json.dumps({'state':'completed'}))
    (job/'resolved_plan.json').write_text(json.dumps({'expected_summary_path':str(request_summary)}))
    old_output=window.overview_page.output_edit.text()
    original_discovery=page._find_latest_summary;discovery_threads=[]
    entered=threading.Event()
    def slow_discovery(root,check):
        discovery_threads.append(threading.get_ident());entered.set();time.sleep(.15)
        return original_discovery(root,check)
    window.overview_page.output_edit.setText(str(latest_root));page._find_latest_summary=slow_discovery
    try:
        started=time.perf_counter();page.load_latest()
        if time.perf_counter()-started>=.1:
            raise ValueError('Latest-result discovery blocks the window thread')
        wait_for(entered.is_set,'Background discovery did not start')
        page.result_load_button.click()
        wait_for(lambda:not page.loading,'Cancelled discovery did not finish')
        if page.voltage_plot.trace_count or 'cancelled' not in page.result_load_note.text():
            raise ValueError('Cancelled discovery publishes a stale recording')
        page.load_latest();wait_for(lambda:not page.loading,'Latest-result retry did not finish')
        if (not discovery_threads or any(t==main_thread for t in discovery_threads)
                or page._displayed_result.summary_path!=str(request_summary)
                or page._last_result_operation['summary']!=str(request_summary)
                or page._displayed_result.title!='changed details' or page.voltage_plot.trace_count!=1):
            raise ValueError('Background discovery loses its request identity or recording')
    finally:
        page._find_latest_summary=original_discovery
        window.overview_page.output_edit.setText(old_output)
    config=window.experiment_page.config()
    for mode in ('sparse','full','off'):
        window.experiment_page.set_config(replace(config, recording=replace(config.recording,current_tracking=mode)))
        if window.experiment_page.config().recording.current_tracking!=mode:
            raise ValueError('Current recording choice does not survive form roundtrip')
    window.experiment_page.set_config(config)
    show_result(result)
    if page.current_traces_button.isEnabled() or page.current_plot.indices:
        raise ValueError('A voltage-only result retains stale current recordings')
    page._morphology_candidates = original_morphology_provider
    QTest.mouseClick(window.nav_buttons[previous],Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    return {'dense_results_legend_paged':True,'results_recording_scope':True,
            'regional_voltage_axis_label':True,'results_state_actions':True,
            'spike_output_selection':True,'results_activity_help_resets':True,
            'compressed_compartment_playback':True,'current_trace_units_and_scale':True,
            'current_replay_clock_and_stale_reset':True,'current_tracking_form_roundtrip':True,
            'result_cache_reopen_without_csv':True,'result_cancel_stops_reader':True,
            'result_cancel_preserves_source':True,'result_cancel_restart':True,
            'background_current_read':True,'native_json_reader':True,
            'compact_trace_records':True,'verified_prepared_reuse':True,
            'replay_id_cache_roundtrip':True,'prepared_trace_plot':True,
            'verified_disk_table_cache':True,'verified_display_reuse':True,
            'explicit_result_request_clock':True,'current_reload_preserves_ready_state':True,
            'external_result_preparation_not_reused':True,
            'verified_request_metadata_reuse':True,'changed_request_metadata_background_refresh':True,
            'unvalidated_result_metadata_not_reused':True,
            'background_result_discovery':True,'result_discovery_cancel':True,
            'summary_admission_before_directory_scan':True,
            'result_unknown_progress_is_static':True,
            'result_loading_layout_stable':True,
            'bounded_saved_skeleton_preparation':True,
            'shared_replay_plot_records':True,
            'verified_geometry_record_sharing':True,
            'columnar_voltage_rows':True,
            'verified_mapped_cache_reuse':True,
            'single_verified_source_digest':True,
            'native_result_surface_isolated':native_surface_checked,
            'result_graphics_backend_checked':graphics_backend_checked}
