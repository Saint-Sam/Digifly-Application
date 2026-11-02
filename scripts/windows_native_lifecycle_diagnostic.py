"""Finite native-widget diagnosis. No neuron downloads, installs or simulation."""
from __future__ import annotations
import argparse
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys

CASES=('plain-widget','native-viewport-force','native-viewport-loop',
       'native-viewport-detach','legacy-viewport-force','empty-main-force',
       'model-main-force','model-main-loop','model-main-detach','dialog-main-force')
NEURON_CASES=('neuron-native','neuron-loop','neuron-detach','neuron-legacy',
              'neuron-offscreen','neuron-no-capture','neuron-release-buffers')


def neuron_child(case,output):
    if case=='neuron-offscreen':os.environ['QT_QPA_PLATFORM']='offscreen'
    from digifly_app.ui import circuit_viewport as scene
    from digifly_app.ui import package_neuron_smoke as check
    from PySide6.QtCore import QCoreApplication,QEvent,Signal
    from PySide6.QtWidgets import QRubberBand
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    if case=='neuron-no-capture':
        from PySide6.QtWidgets import QWidget
        from digifly_app.ui.main_window import MainWindow
        MainWindow.grab=QWidget.grab  # Diagnostic isolation only; no app edit.
    if case=='neuron-legacy':
        class Band(QRubberBand):
            def __init__(self,parent):super().__init__(QRubberBand.Shape.Rectangle,parent)
        scene._NativeRubberBand=Band
        class Legacy(scene._CircuitScene,QOpenGLWidget):
            neuron_selected=Signal(str);neurons_selected=Signal(object)
            compartments_changed=Signal(str,object);isolation_changed=Signal(bool)
            display_mode_changed=Signal(str);status_message=Signal(str)
            def __init__(self,parent=None,*,camera_only=False):
                super().__init__(parent,camera_only=camera_only)
                self._surface=None
        scene.CircuitViewport=Legacy
    original=check.dispose_neuron_fixture
    if case=='neuron-release-buffers':
        def released(window,app):
            for view in window.findChildren(scene.CircuitViewport):
                if not view.isValid():continue
                view.makeCurrent()
                try:
                    for name in ('_vertex_buffer','_point_buffer','_preview_buffer',
                                 '_selection_buffer','_highlight_buffer','_activity_buffer'):
                        buffer=getattr(view,name,None)
                        if buffer is not None:buffer.destroy();setattr(view,name,None)
                    if view._program is not None:
                        view._program.release();view._program.removeAllShaders()
                    view._gpu_ready=False
                finally:view.doneCurrent()
            original(window,app)
        check.dispose_neuron_fixture=released
    if case=='neuron-detach':
        def detached(window,app):
            for view in window.findChildren(scene.CircuitViewport):
                surface=view._surface
                if surface is None:continue
                surface._owner=None;surface.setParent(None);view._surface=None
                surface.close();surface.deleteLater()
                QCoreApplication.sendPostedEvents(surface,QEvent.Type.DeferredDelete)
            original(window,app)
        check.dispose_neuron_fixture=detached
    if case=='neuron-loop':
        from shiboken6 import isValid
        def natural(window,app):
            page=window.results_page
            page.cancel_loading(quiet=True)
            window.data_library_page.neuron_browser.shutdown()
            window.circuit_builder_page.shutdown()
            if not check._wait_until(lambda:not page.loading and not page.current_plot.loading,3):
                raise RuntimeError('Readers did not stop')
            window.hide();window.deleteLater()
            if not check._wait_until(lambda:not isValid(window),3):
                raise RuntimeError('Event-loop window deletion did not finish')
        check.dispose_neuron_fixture=natural
    return check.run_neuron_package_smoke(output/'neuron')


def child(case,output):
    output.mkdir(parents=True,exist_ok=False)
    if case in NEURON_CASES:return neuron_child(case,output)
    stacks=(output/'stacks.txt').open('w')
    faulthandler.enable(file=stacks,all_threads=True)
    from PySide6.QtCore import QCoreApplication,QEvent,QTimer,Qt,Signal
    from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QMessageBox,QRubberBand
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
    from shiboken6 import isValid
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV,make_standalone_profile
    from digifly_app.ui import circuit_viewport as scene
    profile=output/'workspace/config/resources.json'
    make_standalone_profile(workstation_root=output/'workspace').save(profile)
    os.environ[PROFILE_PATH_ENV]=str(profile)
    os.environ[SETTINGS_ROOT_ENV]=str(output/'settings')
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    record=dict(case=case,ok=False,platform=app.platformName(),pid=os.getpid())
    def mark(phase,**extra):
        record.update(phase=phase,**extra)
        (output/'receipt.json').write_text(json.dumps(record,indent=2))
    mark('construct')
    QMessageBox.question=lambda *a,**k:QMessageBox.StandardButton.Discard
    if 'main' in case:
        from digifly_app.ui.main_window import MainWindow
        root=MainWindow();root.first_experiment_guide.pause()
        if case.startswith('model'):
            from digifly_app.core.connectomes import NeuronRecord
            from digifly_app.core.morphology import load_swc
            from digifly_app.core.circuit import CircuitSpec,ConnectomeRef
            swc=output/'fixture.swc';swc.write_text('1 1 0 0 0 5 -1\n2 1 0 0 5 5 1\n3 3 0 0 10 2 2\n4 3 0 0 50 1 3\n')
            morphology=load_swc(NeuronRecord('1','test','software fixture',str(swc),'test:v1'))
            circuit=CircuitSpec(connectome=ConnectomeRef('test:v1','Software fixture',str(output)),neuron_ids=('1',))
            root.experiment_page.set_circuit_snapshot(circuit,(morphology,))
            root.pages.setCurrentWidget(root.experiment_page)
        if case.startswith('dialog'):
            from digifly_app.ui.runtime_setup import RuntimeSetupDialog
            dialog=RuntimeSetupDialog(parent=root);dialog.show()
    else:
        root=QWidget()
        if case!='plain-widget':
            if case.startswith('legacy'):
                class Band(QRubberBand):
                    def __init__(self,parent):super().__init__(QRubberBand.Shape.Rectangle,parent)
                scene._NativeRubberBand=Band
                class Legacy(scene._CircuitScene,QOpenGLWidget):
                    neuron_selected=Signal(str);neurons_selected=Signal(object)
                    compartments_changed=Signal(str,object);isolation_changed=Signal(bool)
                    display_mode_changed=Signal(str);status_message=Signal(str)
                view=Legacy(root,camera_only=True)
            else:view=scene.CircuitViewport(root,camera_only=True)
            QVBoxLayout(root).addWidget(view)
    root.resize(1120,760);root.show()
    def detach():
        views=root.findChildren(scene.CircuitViewport)
        if isinstance(root,scene.CircuitViewport):views.append(root)
        for view in views:
            surface=view._surface
            if surface is None:continue
            mark('detach native window')
            surface._owner=None
            surface.setParent(None)
            view._surface=None
            surface.close();surface.deleteLater()
            QCoreApplication.sendPostedEvents(surface,QEvent.Type.DeferredDelete)
    def retire():
        views=root.findChildren(scene.CircuitViewport)
        mark('before deletion',native_viewports=len(views),
             renderer_states=[dict(valid=v.isValid(),gpu_ready=v._gpu_ready) for v in views])
        if case.endswith('detach'):detach()
        if 'main' in case:
            root.results_page.cancel_loading(quiet=True)
            root.data_library_page.neuron_browser.shutdown()
            root.circuit_builder_page.shutdown()
        root.hide();root.deleteLater()
        if case.endswith('loop'):
            QTimer.singleShot(150,finished)
        else:
            QCoreApplication.sendPostedEvents(root,QEvent.Type.DeferredDelete)
            app.processEvents();finished()
    def finished():
        mark('after deletion',ok=not isValid(root),widgets_deleted=not isValid(root))
        app.quit()
    QTimer.singleShot(250,retire)
    QTimer.singleShot(5000,app.quit)
    result=app.exec()
    if record['phase']!='after deletion':mark('event loop ended before verified deletion')
    faulthandler.disable();stacks.close()
    return 0 if record['ok'] and result==0 else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--child',choices=CASES+NEURON_CASES)
    args=parser.parse_args();out=args.output.resolve()
    if args.child:return child(args.child,out)
    out.mkdir(parents=True,exist_ok=False)
    results=[]
    cases=CASES+(NEURON_CASES if os.environ.get('DIGIFLY_NEURON_PAYLOAD') else ())
    for case in cases:
        folder=out/case
        command=[sys.executable,str(Path(__file__).resolve()),'--child',case,'--output',str(folder)]
        try:
            result=subprocess.run(command,capture_output=True,text=True,timeout=60 if case in NEURON_CASES else 20)
            code=result.returncode;stdout,stderr=result.stdout,result.stderr
        except subprocess.TimeoutExpired as exc:
            code=None;stdout=str(exc.stdout or '');stderr=str(exc.stderr or '')+'\nDiagnostic child limit exceeded.'
        folder.mkdir(exist_ok=True)
        (folder/'stdout.txt').write_text(stdout);(folder/'stderr.txt').write_text(stderr)
        actual=folder/'neuron/receipt.json' if case in NEURON_CASES else folder/'receipt.json'
        receipt=json.loads(actual.read_text()) if actual.is_file() else {}
        results.append(dict(case=case,exit_code=code,receipt=receipt))
        print(json.dumps(dict(case=case,exit_code=code,phase=receipt.get('phase'),ok=receipt.get('ok'))),flush=True)
    (out/'diagnostic.json').write_text(json.dumps(dict(diagnostic_complete=True,
        qualification=False,scope='Native window lifetime only; failures are diagnostic evidence.',results=results),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
