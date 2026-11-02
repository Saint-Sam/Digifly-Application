"""Inspect and add the wheel's untouched graphics fallback to a failed test copy."""
import argparse
import base64
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    import PySide6
    qt = Path(PySide6.__file__).resolve().parent
    candidates = sorted(qt.rglob('opengl32sw.dll'))
    record = dict(publication_accepted=False, candidates=[str(p) for p in candidates], qt=PySide6.__version__)
    (output/'diagnosis.json').write_text(json.dumps(record, indent=2))
    assert len(candidates) == 1, 'Expected the exact wheel software graphics fallback'
    dll, = candidates
    owned = []
    for name in ('PySide6_Essentials', 'PySide6_Addons'):
        distribution = importlib.metadata.distribution(name)
        for item in distribution.files or ():
            if distribution.locate_file(item).resolve() == dll:
                assert item.hash and item.hash.mode == 'sha256'
                assert base64.urlsafe_b64encode(hashlib.sha256(dll.read_bytes()).digest()).decode().rstrip('=') == item.hash.value
                owned.append(dict(distribution=name, version=distribution.version, record=str(item)))
    assert len(owned) == 1 and owned[0]['version'] == '6.10.3'
    archive, = args.artifact_directory.glob('dist/*.zip')
    raw = archive.read_bytes()
    import io
    app = output/'app'
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        marker = json.loads(zipped.read('digifly_windows_package.json'))
        assert set(zipped.namelist()) == {*marker['files'], 'digifly_windows_package.json'}
        assert zipped.testzip() is None
        for name, pin in marker['files'].items():
            path = Path(name)
            assert not path.is_absolute() and '..' not in path.parts and '\\' not in name
            data = zipped.read(name)
            assert hashlib.sha256(data).hexdigest() == pin['sha256'] and len(data) == pin['bytes']
        zipped.extractall(app)
    assert not (app/dll.name).exists()
    shutil.copyfile(dll, app/dll.name)
    record.update(base_archive_sha256=hashlib.sha256(raw).hexdigest(), owner=owned[0],
        added_file=dll.name, added_sha256=hashlib.sha256(dll.read_bytes()).hexdigest(), added_bytes=dll.stat().st_size,
        executable_sha256=hashlib.sha256((app/'Digifly Workstation.exe').read_bytes()).hexdigest(),
        original_payload_verified=len(marker['files']), original_app_bytes_unchanged=True)
    (output/'diagnosis.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record))


if __name__ == '__main__':main()
