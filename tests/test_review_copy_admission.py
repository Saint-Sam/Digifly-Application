"""Never launch an old app while the new review copy is still pending."""
import hashlib
import json
from pathlib import Path
import pytest
import scripts.launch_library_review as runner


def example(tmp_path):
    app=tmp_path/'Owned.app';exe=app/'Contents/MacOS/main';exe.parent.mkdir(parents=True)
    exe.write_bytes(b'owned engineering executable')
    manifest=app/'Contents/Resources/digifly_build_sources.json';manifest.parent.mkdir()
    manifest.write_text(json.dumps(dict(build='fixture14')))
    receipt=tmp_path/'copy.json';record=dict(ok=True,mode='review',build='fixture14',
        current_app=str(app),actual_executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    receipt.write_text(json.dumps(record));return exe,receipt,record


def test_pending_copy_is_refused_before_claim_or_child(tmp_path,monkeypatch):
    monkeypatch.setattr(runner.subprocess,'Popen',lambda *a,**k:pytest.fail('Pending copy spawned a child'))
    output=tmp_path/'review'
    with pytest.raises(ValueError,match='not complete'):
        runner.launch([str(tmp_path/'preceding-app')],output,copy_receipt=tmp_path/'pending.json')
    assert not output.exists() and not output.with_name('review-launch').exists()


@pytest.mark.parametrize('fault',[None,'bytes','build','path','not-completed'])
def test_exact_closed_copy_identity_and_counterexamples(tmp_path,fault):
    exe,receipt,record=example(tmp_path)
    if fault=='bytes':exe.write_bytes(b'wrong preceding executable')
    elif fault=='build':record['build']='previous13'
    elif fault=='path':record['current_app']=str(tmp_path/'Other.app')
    elif fault=='not-completed':record['ok']=False
    receipt.write_text(json.dumps(record))
    if fault:
        with pytest.raises(ValueError,match='does not match|wrong embedded build'):
            runner.checked_review_copy(exe,receipt)
    else:
        checked=runner.checked_review_copy(exe,receipt)
        assert checked['build']=='fixture14' and checked['receipt']==str(receipt.resolve())
