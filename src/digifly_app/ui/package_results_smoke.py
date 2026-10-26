"""Bounded saved-data UI fixtures; never downloads or runs a simulator."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path


def check_results_review(window, destination: Path) -> dict[str, bool]:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QScrollArea
    from digifly_app.core.results import Artifact, ResultRecord
    from digifly_app.ui.voltage_plot import VOLTAGE_AXIS_LABEL, MODE_2D, MODE_3D_STACK

    root = destination / 'dense-results-fixture'
    root.mkdir(exist_ok=False)
    paths = {}
    for body, edges in (('10000', 2), ('10068', 1426)):
        path = root / (body + '.swc')
        path.write_text('1 1 0 0 0 1 -1\n' + ''.join(
            f'{i+1} 3 {i*.2} 0 0 .5 {i}\n' for i in range(1, edges+1)))
        paths[body] = path
    (root / 'summary.json').write_text('{}')
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
    previous = window.pages.currentIndex()
    nav = next(b for b in window.nav_buttons if 'Results' in b.text())
    QTest.mouseClick(nav,Qt.MouseButton.LeftButton)
    page = window.results_page
    page.display_result(result)
    QApplication.processEvents()
    if ('2 circuit neuron(s)' not in page.circuit_info.text()
            or '1 recording target(s)' not in page.circuit_info.text()
            or '1 with saved voltage' not in page.circuit_info.text()
            or 'body 10000 · not a recording target' not in page.circuit_identity.text()
            or 'body 10068 · stimulus target · recorded voltage' not in page.circuit_identity.text()):
        raise ValueError('Results confuses saved circuit context with recorded cells')
    plot = page.voltage_plot
    keys = {trace.key for trace in plot.traces}
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
            or plot.canvas.visible_keys!=keys or first_key in plot.canvas.active_keys):
        raise ValueError('Legend paging changes plotted trace membership or emphasis')
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
    if plot.canvas.active_keys!=keys or plot.canvas.visible_keys!=keys or len(plot.legend_buttons)>60:
        raise ValueError('Show all fails to restore every dense trace')
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
    page.display_result(spike_result)
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
    page.display_result(spike_result)
    QApplication.processEvents()
    if (page.voltage_plot.trace_count!=1 or page.activity_slider.isEnabled()
            or any(any(t.spikes_by_neuron.values()) for t in page._flow_tracks)):
        raise ValueError('Voltage-only Results invents events or retains previous spike playback')
    QTest.mouseClick(window.nav_buttons[previous],Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    return {'dense_results_legend_paged':True,'results_recording_scope':True,
            'regional_voltage_axis_label':True,'results_state_actions':True,
            'spike_output_selection':True}
