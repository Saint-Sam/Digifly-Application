import hashlib
import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('mac_notices', Path(__file__).parents[1] / 'scripts/stage_macos_notices.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def inputs(tmp_path):
    source, bundle = tmp_path / 'notices', tmp_path / 'test.app'
    source.mkdir()
    bundle.mkdir()
    files = {}
    for name in ('THIRD_PARTY_COMPONENTS.json', 'THIRD_PARTY_README.txt', 'licenses/Qt/LICENSE.txt'):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'original license text')
        files[name] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (source / 'notice_files.json').write_text(json.dumps({'schema': 'digifly-macos-notice-files-v1', 'files': files}))
    return source, bundle


def test_exact_notices_staged_without_replacement(tmp_path):
    source, bundle = inputs(tmp_path)
    assert module.stage(source, bundle)['notice_files'] == 3
    assert (bundle / 'Contents/Resources/licenses/Qt/LICENSE.txt').read_bytes() == b'original license text'
    with pytest.raises(ValueError, match='already exists'):
        module.stage(source, bundle)


@pytest.mark.parametrize('change', ['tamper', 'extra', 'link', 'escape'])
def test_reject_changed_or_unsafe_notices(tmp_path, change):
    source, bundle = inputs(tmp_path)
    if change == 'tamper':
        (source / 'THIRD_PARTY_README.txt').write_text('changed')
    elif change == 'extra':
        (source / 'unknown').write_text('unexpected')
    elif change == 'link':
        (source / 'licenses/Qt/LICENSE.txt').unlink()
        (source / 'licenses/Qt/LICENSE.txt').symlink_to(source / 'THIRD_PARTY_README.txt')
    else:
        path = source / 'notice_files.json'
        doc = json.loads(path.read_text())
        doc['files']['../escape'] = doc['files'].pop('THIRD_PARTY_README.txt')
        path.write_text(json.dumps(doc))
    with pytest.raises(ValueError):
        module.stage(source, bundle)
    assert not (bundle / 'Contents').exists()
