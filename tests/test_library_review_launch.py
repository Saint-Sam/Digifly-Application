import os
from pathlib import Path
import signal
import sys
import pytest

from digifly_app.core.resource_profile import make_default_profile
from digifly_app.ui.package_library_smoke import prepare_library_review
from scripts.launch_library_review import launch, verify_live_review


def _protocol_python():
    # A Windows venv's python.exe redirects to another process. The protocol
    # fixture, like the packaged app, must own the PID it reports as ready.
    return getattr(sys, '_base_executable', sys.executable) if os.name == 'nt' else sys.executable


def test_explicit_profile_preserves_prior_settings_and_data_paths(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir()
    profile=make_default_profile(workspace_root=workspace,output_root=tmp_path/'runs',managed_data_root=tmp_path/'saved')
    path=profile.save(tmp_path/'config/resources-v2.json');before=path.read_bytes()
    output=tmp_path/'review'
    _,_,actual,p=prepare_library_review(output,workspace,path)
    assert actual==path and p.managed_data_root==profile.managed_data_root
    assert path.read_bytes()==before and not (output/'data').exists()
    with pytest.raises(FileExistsError):prepare_library_review(output,workspace,path)
    other=tmp_path/'other';other.mkdir()
    with pytest.raises(ValueError,match='another workspace'):prepare_library_review(tmp_path/'invalid',other,path)
    assert not (tmp_path/'invalid').exists() and path.read_bytes()==before


def test_bounded_start_does_not_duplicate_an_unconfirmed_process(tmp_path):
    output=tmp_path/'not-ready'
    command=[_protocol_python(),'-c','import time; time.sleep(10)']
    try:
        with pytest.raises(RuntimeError,match='not duplicate'):launch(command,output,timeout=.15)
        with pytest.raises(FileExistsError):launch(command,output,timeout=.15)
    finally:
        import json
        record=tmp_path/'not-ready-launch/launch.json'
        if record.exists():os.kill(json.loads(record.read_text())['pid'],signal.SIGTERM)


def test_manual_review_page_and_sidebar_start_in_agreement(tmp_path, monkeypatch):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide6.QtWidgets import QApplication
    from digifly_app.core.app_settings import SETTINGS_ROOT_ENV
    from digifly_app.core.resource_profile import PROFILE_PATH_ENV
    from digifly_app.ui.package_library_smoke import run_library_review
    from digifly_app.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv(SETTINGS_ROOT_ENV, str(tmp_path/'initial-settings'))
    monkeypatch.setenv(PROFILE_PATH_ENV, str(tmp_path/'initial-profile.json'))
    monkeypatch.setattr(QApplication, 'exec', lambda *_: 0)
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    before = set(app.topLevelWidgets())
    try:
        assert run_library_review(tmp_path/'review', workspace) == 0
        window, = [w for w in app.topLevelWidgets() if isinstance(w, MainWindow) and w not in before]
        assert window.pages.currentWidget() is window.data_library_page
        assert window.nav_buttons[1].isChecked()
        window.nav_buttons[0].click()
        assert window.pages.currentWidget() is window.overview_page
    finally:
        for window in set(app.topLevelWidgets())-before:
            window.close()
        app.processEvents()


def test_launch_protocol_positive_is_not_claimed_as_actual_gui_acceptance(tmp_path):
    output=tmp_path/'ready'
    code=('import json,os,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.mkdir(); '
          '(p/"window-ready.json").write_text(json.dumps({"window_visible":True,"pid":os.getpid(),'
          '"automatic_download_or_simulation":False})); time.sleep(10)')
    record=launch([_protocol_python(),'-c',code,str(output)],output,timeout=3)
    try:assert record['window_confirmed'] is True
    finally:os.kill(record['pid'],signal.SIGTERM)


def test_readiness_from_a_different_pid_is_rejected(tmp_path):
    import json
    output=tmp_path/'wrong-pid'
    code=('import json,os,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.mkdir(); '
          '(p/"window-ready.json").write_text(json.dumps({"window_visible":True,"pid":os.getpid()+1,'
          '"automatic_download_or_simulation":False})); time.sleep(10)')
    try:
        with pytest.raises(ValueError,match='actual app process'):
            launch([_protocol_python(),'-c',code,str(output)],output,timeout=3)
        with pytest.raises(FileExistsError):
            launch([_protocol_python(),'-c',code,str(output)],output,timeout=3)
    finally:
        record=tmp_path/'wrong-pid-launch/launch.json'
        if record.exists():os.kill(json.loads(record.read_text())['pid'],signal.SIGTERM)


@pytest.mark.skipif(os.name=='nt', reason='POSIX live command check; Windows is explicitly unqualified')
def test_held_launch_has_live_exact_owner_then_refuses_after_exit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import json
    import time
    output=tmp_path/'held'
    code=('import json,os,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.mkdir(); '
          '(p/"window-ready.json").write_text(json.dumps({"window_visible":True,"pid":os.getpid(),'
          '"automatic_download_or_simulation":False})); time.sleep(3)')
    with ThreadPoolExecutor(max_workers=1) as pool:
        result=pool.submit(launch,[_protocol_python(),'-c',code,str(output)],output,timeout=2,hold=True)
        claim=tmp_path/'held-launch/launch.json'
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if claim.is_file() and json.loads(claim.read_text()).get('window_confirmed'):
                break
            time.sleep(.02)
        live=verify_live_review(output)
        assert live['ok'] and not result.done()
        record=result.result(timeout=5)
        assert record['exit_code']==0
    with pytest.raises(ValueError,match='foreground-held owner'):
        verify_live_review(output)


@pytest.mark.parametrize('defect',['startup-only','wrong-pid','wrong-profile','wrong-command','wrong-parent'])
def test_live_guard_rejects_stale_or_wrong_identity(tmp_path,monkeypatch,defect):
    import json
    import subprocess
    output=tmp_path/'review';output.mkdir()
    claim=tmp_path/'review-launch';claim.mkdir()
    command=[_protocol_python(),'--existing-profile','/expected/profile','--workspace','/expected/workspace']
    record=dict(ownership_mode='foreground-held',window_confirmed=True,pid=os.getpid(),
                launcher_pid=os.getpid(),command=command)
    ready=dict(pid=os.getpid(),window_visible=True,profile='/expected/profile',workspace='/expected/workspace')
    if defect=='startup-only':record['ownership_mode']='startup-only'
    elif defect=='wrong-pid':ready['pid']+=1
    elif defect=='wrong-profile':ready['profile']='/other/profile'
    elif defect=='wrong-parent':record['launcher_pid']+=10_000_000
    (claim/'launch.json').write_text(json.dumps(record))
    (output/'window-ready.json').write_text(json.dumps(ready))
    monkeypatch.setattr('scripts.launch_library_review.subprocess.run',
        lambda *a,**k:subprocess.CompletedProcess(a,0,f'{os.getpid()} other app command\n',''))
    with pytest.raises(ValueError):verify_live_review(output)


def test_held_runner_itself_refuses_wrong_profile_before_reporting_ready(tmp_path):
    import json
    output=tmp_path/'wrong-held-profile'
    code=('import json,os,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.mkdir(); '
          '(p/"window-ready.json").write_text(json.dumps({"window_visible":True,"pid":os.getpid(),'
          '"automatic_download_or_simulation":False,"profile":"/wrong/profile",'
          '"workspace":"/expected/workspace"})); time.sleep(10)')
    command=[_protocol_python(),'-c',code,str(output),
             '--existing-profile','/expected/profile','--workspace','/expected/workspace']
    try:
        with pytest.raises(ValueError,match='wrong workspace or profile'):
            launch(command,output,timeout=2,hold=True)
        with pytest.raises(FileExistsError):
            launch(command,output,timeout=2,hold=True)
    finally:
        claim=tmp_path/'wrong-held-profile-launch/launch.json'
        if claim.exists():os.kill(json.loads(claim.read_text())['pid'],signal.SIGTERM)
