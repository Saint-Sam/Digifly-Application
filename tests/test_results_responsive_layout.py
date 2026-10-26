"""Results geometry checks use offscreen Qt; no simulator or real GUI launch."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import pytest
from PySide6.QtCore import QPoint, QSettings
from PySide6.QtWidgets import QApplication, QScrollArea

from digifly_app.ui.main_window import MainWindow
from digifly_app.ui.voltage_plot import VoltagePlotWidget, VoltageTrace


@pytest.fixture
def results_window(tmp_path,monkeypatch):
    app=QApplication.instance() or QApplication([])
    previous=QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat,QSettings.Scope.UserScope,str(tmp_path))
    monkeypatch.setenv('DIGIFLY_WORKSTATION_PROFILE',str(tmp_path/'absent.json'))
    monkeypatch.setenv('DIGIFLY_WORKSTATION_SETTINGS_ROOT',str(tmp_path/'settings'))
    window=MainWindow()
    window.pages.setCurrentWidget(window.results_page)
    try:
        yield app,window,window.results_page
    finally:
        window.close(); window.deleteLater(); app.processEvents()
        QSettings.setDefaultFormat(previous)


@pytest.mark.parametrize('width',(1120,1440))
def test_results_split_controls_fit_with_long_labels(results_window,width):
    app,window,page=results_window
    label='Control '+('very-long-neuron-condition-name-'*12)
    trace=VoltageTrace(key='a',label=label,condition='Control',repetition=1,neuron_id='a',times_ms=(0.,1.),voltages_mV=(-65.,-60.),color='#5eafff')
    page.voltage_plot.set_traces((trace,))
    page.figure_stack.setCurrentWidget(page.voltage_plot)
    page.image_info.setText('16 traces · 1,600 samples · interactive')
    page.activity_method.setText('Measured edge-center voltage · blue −90 to red +50 mV (display clipped) · gray = unrecorded · temporal interpolation only within recorded support')
    page.result_status.setText('Loaded result '+('very-long-unbroken-run-path-'*15))
    page.activity_condition_combo.addItem(label)
    window.resize(width,940); window.show()
    for _ in range(3): app.processEvents()
    # Find the page scroll rather than nested figure/legend/list scroll areas.
    scroll=next(s for s in page.findChildren(QScrollArea) if s.widget().objectName()=='PageContent')
    assert scroll.widget().width()<=scroll.viewport().width()
    assert page.preview_splitter.minimumSizeHint().width()<850
    assert page.figure_panel.width()>=440
    assert page.circuit_panel.width()>=300
    assert page.figure_panel.width()+page.circuit_panel.width()<=page.preview_splitter.width()
    for button,parent in ((page.save_trace_button,page.figure_panel),(page.full_resolution_button,page.figure_panel),(page.expand_trace_button,page.figure_panel),(page.voltage_morphology_button,page.figure_panel),(page.save_result_visualization_button,page.circuit_panel),(page.result_full_skeletons_button,page.circuit_panel),(page.activity_play_button,page.circuit_panel)):
        point=button.mapTo(parent,QPoint(0,0))
        assert point.x()>=0 and point.x()+button.width()<=parent.width(), button.text()
    assert label in page.voltage_plot.legend_buttons['a'].toolTip()
    page.voltage_plot.legend_buttons['a'].click()
    assert 'a' not in page.voltage_plot.canvas.active_keys


def test_expansion_restores_split_and_actions(results_window):
    app,window,page=results_window
    window.resize(1120,940); window.show(); app.processEvents()
    page.expand_trace_button.click(); app.processEvents()
    assert not page.circuit_panel.isVisible()
    assert page.expand_trace_button.text()=='Restore split'
    page.expand_trace_button.click(); app.processEvents()
    assert page.circuit_panel.isVisible()
    assert page.expand_trace_button.text()=='Expand traces'
    assert page.figure_panel.width()+page.circuit_panel.width()<=page.preview_splitter.width()


def test_legend_columns_follow_width_without_resetting_trace_selection(results_window):
    app,_,_=results_window
    plot=VoltagePlotWidget()
    traces=tuple(VoltageTrace(key=key,label=key,condition='Control',repetition=1,neuron_id=key,times_ms=(0.,1.),voltages_mV=(-65.,-60.),color='#5eafff') for key in ('a','b','c'))
    plot.set_traces(traces)
    plot.resize(440,600); plot.show(); app.processEvents()
    assert plot.legend_layout.getItemPosition(1)[:2]==(1,0)
    plot.legend_buttons['b'].click()
    assert plot.canvas.active_keys=={'a','c'}
    plot.resize(900,600); app.processEvents()
    assert plot.legend_layout.getItemPosition(1)[:2]==(0,1)
    assert plot.canvas.active_keys=={'a','c'}
    assert not plot.legend_buttons['b'].isChecked()
    plot.resize(440,600); app.processEvents()
    assert plot.legend_layout.getItemPosition(2)[:2]==(2,0)
    assert plot.canvas.active_keys=={'a','c'}
    plot.close(); plot.deleteLater(); app.processEvents()


def test_dense_legend_pages_do_not_drop_traces_or_selection():
    app=QApplication.instance() or QApplication([])
    traces=tuple(VoltageTrace(key=str(i),label=f'Control body 10068 SWC {i}',
        condition='Control',repetition=1,neuron_id='10068',compartment_id=str(i),
        times_ms=(0.,1.),voltages_mV=(-65.,float(i%20)-40.),color='#5eafff')
        for i in range(2852))
    plot=VoltagePlotWidget()
    try:
        plot.set_traces(traces);plot.show();app.processEvents()
        assert len(plot.legend_buttons)<=60
        assert plot.trace_count==2852 and plot.sample_count==5704
        assert plot.canvas.visible_keys=={t.key for t in traces}
        old_button=plot.legend_buttons['0']
        old_button.click()
        assert '0' not in plot.canvas.active_keys
        plot.legend_next_button.click()
        assert '60' in plot.legend_buttons and '0' not in plot.legend_buttons
        assert len(plot.legend_buttons)<=60
        assert len(plot.canvas.visible_keys)==2852
        # A retained old page widget must not change the current page/data.
        old_button.click()
        assert '0' not in plot.canvas.active_keys
        app.processEvents()
        assert all(b.isVisible() and b.height()>=b.sizeHint().height() for b in plot.legend_buttons.values())
        assert plot.legend_scroll.verticalScrollBar().maximum()>0
        plot.legend_previous_button.click();app.processEvents()
        assert not plot.legend_buttons['0'].isChecked()
        plot.legend_filter.setText('SWC 2851');app.processEvents()
        assert set(plot.legend_buttons)=={'2851'}
        assert plot.canvas.visible_keys=={'2851'}
        plot.legend_filter.clear();app.processEvents()
        assert len(plot.canvas.visible_keys)==2852
        assert '0' not in plot.canvas.active_keys
        plot.show_all_button.click()
        assert len(plot.canvas.active_keys)==2852
        assert len(plot.legend_buttons)<=60
    finally:
        plot.close();plot.deleteLater();app.processEvents()


@pytest.mark.parametrize('voltage_enabled',(True,False))
def test_run_context_is_not_labeled_as_all_recorded(results_window,tmp_path,voltage_enabled):
    import json
    from digifly_app.core.results import Artifact,ResultRecord
    app,_,page=results_window
    run=tmp_path/'subset-result';run.mkdir()
    ids=('10000','10002','10068','10110','11446','11654')
    morphologies={};artifacts=[]
    for body in ids:
        path=run/(body+'.swc');path.write_text('1 1 0 0 0 2 -1\n2 3 1 0 0 .5 1\n')
        morphologies[body]={'family':'MN' if body=='10068' else 'DN',
            'neuron_type':'TTMn' if body=='10068' else 'test context'}
        artifacts.append(Artifact('morphology',str(path),'Normalized morphology · '+body,True))
    (run/'summary.json').write_text('{}')
    (run/'worker_request.json').write_text(json.dumps({'morphologies':morphologies}))
    (run/'experiment.json').write_text(json.dumps({'stimuli':[{'enabled':True,'target_neuron_ids':['10068']}],
        'recording':{'target_neuron_ids':['10068'],'record_voltage':voltage_enabled,'detect_spikes':True}}))
    (run/'voltage_traces.csv').write_text('condition,repetition,neuron_id,time_ms,voltage_mV\n'+
        ('Control,1,10068,0,-65\nControl,1,10068,1,-30\n' if voltage_enabled else ''))
    (run/'spikes.csv').write_text('condition,repetition,neuron_id,spike_time_ms\n')
    page.display_result(ResultRecord(summary_path=str(run/'summary.json'),status='complete',
        completed_at='',title='subset fixture',metadata={},artifacts=tuple(artifacts)))
    app.processEvents()
    assert page.result_viewport.neuron_count==6
    assert '6 circuit neuron(s)' in page.circuit_info.text()
    assert '1 recording target(s)' in page.circuit_info.text()
    assert f'{int(voltage_enabled)} with saved voltage' in page.circuit_info.text()
    for body in set(ids)-{'10068'}:
        assert f'body {body} · not a recording target' in page.circuit_identity.text()
    target=next(line for line in page.circuit_identity.text().splitlines() if 'body 10068 ' in line)
    assert 'stimulus target' in target
    assert ('recorded voltage' if voltage_enabled else 'recording target') in target
