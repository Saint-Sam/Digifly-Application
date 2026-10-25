import os
from pathlib import Path
import signal
import sys
import pytest

from digifly_app.core.resource_profile import make_default_profile
from digifly_app.ui.package_library_smoke import prepare_library_review
from scripts.launch_library_review import launch


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
