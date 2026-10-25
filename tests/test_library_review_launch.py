import os
from pathlib import Path
import signal
import sys
import pytest

from digifly_app.core.resource_profile import make_default_profile
from digifly_app.ui.package_library_smoke import prepare_library_review
from scripts.launch_library_review import launch


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
    command=[sys.executable,'-c','import time; time.sleep(10)']
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
    record=launch([sys.executable,'-c',code,str(output)],output,timeout=3)
    try:assert record['window_confirmed'] is True
    finally:os.kill(record['pid'],signal.SIGTERM)
