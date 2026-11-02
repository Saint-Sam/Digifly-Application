"""Keep focused native checks explicit; never call them full-library passes."""
from pathlib import Path
import sys
import pytest
from scripts.check_swc_library_gui import child_command


@pytest.mark.parametrize('windows',[False,True])
def test_native_digest_budget_matches_real_input_snapshot_and_file_safety(tmp_path,monkeypatch,windows):
    import ast
    from types import SimpleNamespace
    import digifly_app.core.result_voltage as store
    import digifly_app.ui.result_loading as loading
    import digifly_app.ui.package_results_smoke as fixture
    from test_result_voltage_store import recording
    source=recording(tmp_path);calls=[];original=store._digest
    monkeypatch.setattr(store,'WINDOWS_FILE_CHECKS',windows)
    def digest(path,cancelled):
        if path.resolve()==source.resolve():calls.append(path)
        return original(path,cancelled)
    monkeypatch.setattr(store,'_digest',digest);monkeypatch.setattr(loading,'_digest',digest)
    snapshot=loading.result_input_snapshot(source.parent,tmp_path/'cache',lambda:None)
    table=store.load_voltage_table(source,cache_root=tmp_path/'cache',verified_source=snapshot[source.name])
    assert table.sample_count==24
    tree=ast.parse(Path(fixture.__file__).read_text())
    checks=[node for node in ast.walk(tree) if isinstance(node,ast.If)
        and 'len(source_digest_calls)' in ast.unparse(node.test)]
    assert len(checks)==1
    budget=checks[0].test.comparators[0]
    expected=eval(compile(ast.Expression(budget),'<native digest budget>','eval'),
                  {'voltage_store':SimpleNamespace(WINDOWS_FILE_CHECKS=windows)})
    assert len(calls)==expected


@pytest.mark.parametrize('platform_name,surface,accepted',[
    ('cocoa','RasterSurface',True),('cocoa','OpenGLSurface',False),
    ('windows','RasterSurface',False),('windows','OpenGLSurface',True),
    ('windows','RasterGLSurface',True),
])
def test_result_surface_guard_checks_each_platform_contract(platform_name,surface,accepted):
    from types import SimpleNamespace
    from PySide6.QtGui import QSurface
    from PySide6.QtWidgets import QApplication
    from digifly_app.ui.circuit_viewport import _WindowsCircuitViewport
    from digifly_app.ui.package_results_smoke import check_result_surface
    app=QApplication.instance() or QApplication([])
    viewport=_WindowsCircuitViewport(camera_only=True)
    window=SimpleNamespace(windowHandle=lambda:SimpleNamespace(
        surfaceType=lambda:getattr(QSurface.SurfaceType,surface)))
    try:
        if accepted:
            assert check_result_surface(window,viewport,platform_name) is (platform_name!='windows')
        else:
            with pytest.raises(ValueError):check_result_surface(window,viewport,platform_name)
    finally:
        viewport.deleteLater();app.processEvents()


def test_existing_full_library_route_is_unchanged(tmp_path):
    assert child_command(tmp_path,None,tmp_path/'out',source=True)==[
        sys.executable,str(tmp_path/'main.py'),'--package-library-test',str(tmp_path/'out')]


def test_focused_route_uses_actual_app_entrypoint_for_both_modes(tmp_path):
    executable=tmp_path/'app';output=tmp_path/'out'
    for source in (True,False):
        command=child_command(tmp_path,executable,output,source=source,results_only=True)
        assert command[-2:]==['--package-results-test',str(output)]
        assert '--package-library-test' not in command
        assert command[0]==(sys.executable if source else str(executable.resolve()))


def test_focused_fixture_failure_keeps_failure_receipt_and_settings(tmp_path,monkeypatch):
    import json
    import os
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV
    import digifly_app.ui.main_window as main_window
    from digifly_app.ui.package_results_smoke import run_results_smoke
    for key in (SETTINGS_ROOT_ENV,PROFILE_PATH_ENV):monkeypatch.setenv(key,'before-fixture')
    def refuse():raise ValueError('Owned startup counterexample')
    monkeypatch.setattr(main_window,'MainWindow',refuse)
    assert run_results_smoke(tmp_path/'fixture')==1
    record=json.loads((tmp_path/'fixture/receipt.json').read_text())
    assert not record['ok'] and 'Owned startup counterexample' in record['error']
    assert record['schema']=='digifly-native-results-check-v1'
    assert [p['stage'] for p in record['phases']]==['start','fixture_finished']
    assert all(os.environ[key]=='before-fixture' for key in (SETTINGS_ROOT_ENV,PROFILE_PATH_ENV))


@pytest.mark.parametrize('correct_scope',[False,True])
def test_runner_refuses_wrong_scope_even_with_successful_child(tmp_path,monkeypatch,correct_scope):
    import json,subprocess
    import scripts.check_swc_library_gui as runner
    output=tmp_path/'native'
    def child(command,project,environment):
        assert '--package-results-test' in command
        output.mkdir();(output/'settings').mkdir();(output/'settings/test.ini').write_text('owned fixture')
        (output/'receipt.json').write_text(json.dumps(dict(ok=True,schema=(
            'digifly-native-results-check-v1' if correct_scope else 'digifly-packaged-swc-library-check-v1'))))
        return subprocess.CompletedProcess(command,0,'',''),False
    monkeypatch.setattr(runner,'run_child',child);monkeypatch.setattr(runner,'fingerprint',lambda:'unchanged')
    monkeypatch.setattr(sys,'argv',['runner','--source','--results-only','--output',str(output)])
    assert runner.main()==(0 if correct_scope else 1)
    report=json.loads((output/'preference-postflight.json').read_text())
    assert report['ok'] is correct_scope and report['fixture_scope']=='results-and-safety'


@pytest.mark.parametrize('mode',['--package-library-test','--package-results-test'])
def test_actual_cli_keeps_both_routes_and_final_safety_guard(tmp_path,monkeypatch,mode):
    import json
    import digifly_app.app as app_entry
    import digifly_app.ui.package_library_smoke as library
    import digifly_app.ui.package_results_smoke as results
    import digifly_app.ui.student_readiness_check as safety
    import digifly_app.ui.fragment_review_check as fragments
    calls=[];destination=tmp_path/'native'
    def fixture(path,kind):
        calls.append(kind);path.mkdir();(path/'receipt.json').write_text(json.dumps(dict(ok=True)))
        return 0
    monkeypatch.setattr(library,'run_library_smoke',lambda path:fixture(path,'full'))
    monkeypatch.setattr(results,'run_results_smoke',lambda path:fixture(path,'focused'))
    monkeypatch.setattr(safety,'check_student_safety',lambda path:dict(owned_test_safety=True))
    monkeypatch.setattr(fragments,'check_fragment_record_roundoff',lambda path:None)
    monkeypatch.setattr(sys,'argv',['main.py',mode,str(destination)])
    assert app_entry.main()==0
    assert calls==(['focused'] if mode=='--package-results-test' else ['full'])
    record=json.loads((destination/'receipt.json').read_text())
    assert record['ok'] and record['owned_test_safety'] and record['fragment_derived_roundoff_guard']
    assert record['student_no_unhandled_qt_callbacks'] and record['qt_callback_errors']==[]
