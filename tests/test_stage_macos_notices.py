import hashlib
import importlib.util
import json
from pathlib import Path
import pytest
import os
import subprocess
import sys

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


def test_notice_validate_only_checks_bytes_without_staging(tmp_path):
    source, bundle = inputs(tmp_path)
    command = [sys.executable, str(Path(module.__file__)), '--source', str(source), '--validate-only']
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'ok': True, 'notice_files': 3, 'validation_only': True}
    assert not (bundle / 'Contents').exists()
    (source / 'THIRD_PARTY_README.txt').write_text('tampered')
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and 'changed' in result.stderr


@pytest.mark.parametrize('valid', [False, True])
def test_actual_build_runner_validates_notices_before_first_project_copy(tmp_path, valid):
    """Use the real shell runner; an inert first-copy sentinel prevents compiling."""
    root = Path(__file__).parents[1]
    project = tmp_path / 'project'
    (project / 'scripts').mkdir(parents=True)
    for name in ('build_macos.sh', 'stage_macos_notices.py'):
        (project / 'scripts' / name).write_bytes((root / 'scripts' / name).read_bytes())
    (project / '.venv/bin').mkdir(parents=True)
    (project / '.venv/bin/python').symlink_to(sys.executable)
    (project / 'pysidedeploy.spec').write_text('inert fixture; no compiler may run')
    notices, _ = inputs(tmp_path)
    selected = notices if valid else tmp_path / 'Wrong.app'
    if not valid:
        selected.mkdir()
    shims = tmp_path / 'shims'
    shims.mkdir()
    marker = tmp_path / 'first-copy-reached'
    stub = shims / 'rsync'
    stub.write_text('#!/bin/bash\n/usr/bin/touch "$DIGIFLY_COPY_SENTINEL"\nexit 71\n')
    stub.chmod(0o700)
    env = {k: v for k, v in os.environ.items() if not k.startswith('DIGIFLY_')}
    env.update(PATH=str(shims) + ':/usr/bin:/bin', DIGIFLY_MAC_NOTICE_SOURCE=str(selected),
               DIGIFLY_COPY_SENTINEL=str(marker))
    result = subprocess.run(['/bin/bash', str(project / 'scripts/build_macos.sh')],
                            env=env, capture_output=True, text=True, timeout=20)
    if valid:
        assert result.returncode == 71 and marker.exists(), result.stderr
        assert '"validation_only": true' in result.stdout
    else:
        assert result.returncode != 0 and not marker.exists(), result.stderr
        assert 'notice-only folder' in result.stderr
