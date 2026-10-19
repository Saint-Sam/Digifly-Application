"""Collect exact Mac build/runtime license documents; not legal certification."""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import tarfile
import tempfile
import urllib.request
import zipfile


FULL_URL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-aarch64-apple-darwin-pgo%2Blto-full.tar.zst'
FULL_SHA = 'dbefa04d4107b449e17022f9c78d3eacbd37a1aa8c166c6220dbd95addf3f318'
QT_ARCHIVE_SHA = '9d03f32e85db2c9bdd7440f0e5c055d95a44b2dc9eae0ff799c7903fbfe9df8f'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def collect(app: Path, qt_archive: Path, destination: Path, conda_root: Path):
    if destination.exists() or metadata.version('PySide6') != '6.10.3':
        raise ValueError('New notice destination and exact reviewed Qt version required')
    assert sha(qt_archive.read_bytes()) == QT_ARCHIVE_SHA
    destination.mkdir(parents=True)
    files, components = {}, []
    def save(name, data):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or len(data) > 2 * 1024**2:
            raise ValueError('Unsafe notice path/size')
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            assert target.read_bytes() == data
        else:
            target.write_bytes(data)
        value = {'path': name, 'sha256': sha(data), 'bytes': len(data)}
        files[name] = {k: v for k, v in value.items() if k != 'path'}
        return value
    with zipfile.ZipFile(qt_archive) as archive:
        original = json.loads(archive.read('THIRD_PARTY_COMPONENTS.json'))
        names = [name for name in archive.namelist() if name.startswith(('licenses/Qt-6.10.3/', 'licenses/Qt-PDF-6.10.3/'))]
        assert names
        for name in names:
            save(name, archive.read(name))
        for item in original['components']:
            kept = [n for n in item.get('notices', []) if n['path'] in names]
            if kept:
                components.append({'component': item['component'], 'version': item.get('version'),
                    'license': item.get('license') or item.get('license_route'), 'notices': kept,
                    'source': item.get('source', 'https://github.com/qt/qtbase/tree/v6.10.3'),
                    'scope': 'Version-matched upstream Qt texts reused; not Windows DLL binaries'})
    # Exact local Python/native library package notices, never the Anaconda
    # distribution as a whole, user environments, or credentials.
    wanted = {'python', 'openssl', 'libffi', 'bzip2', 'xz', 'sqlite', 'expat', 'zlib'}
    found = set()
    for path in (conda_root / 'conda-meta').glob('*.json'):
        item = json.loads(path.read_text())
        if item.get('name') not in wanted:
            continue
        name, version = item['name'], item['version']
        source = Path(item['extracted_package_dir']) / 'info/licenses'
        notices = [save('licenses/mac-python-native/' + name + '-' + version + '/' + p.relative_to(source).as_posix(), p.read_bytes())
                   for p in source.rglob('*') if p.is_file() and not p.is_symlink()]
        if not notices:
            raise ValueError('Missing native package license: ' + name)
        components.append({'component': name, 'version': version, 'source': item['url'],
                           'source_sha256': item.get('sha256'), 'notices': notices,
                           'scope': 'Exact Mac build-environment package identity; binary ABI is not independently inferred'})
        found.add(name)
    assert found == wanted
    for dist in metadata.distributions():
        name = dist.metadata.get('Name', 'unknown')
        if name in {'digifly-workstation'}:
            continue
        notices = []
        for entry in dist.files or ():
            path = dist.locate_file(entry)
            if PurePosixPath(str(entry)).name.casefold().startswith(('license', 'copying', 'notice', 'authors')) and path.is_file():
                relative = str(entry).replace('\\', '/').replace('../', '_/')
                notices.append(save('licenses/mac-build-packages/' + name + '-' + dist.version + '/' + relative, path.read_bytes()))
        components.append({'component': name, 'version': dist.version,
            'license': dist.metadata.get('License-Expression') or dist.metadata.get('License') or 'see package notices',
            'project_urls': dist.metadata.get_all('Project-URL', []), 'notices': notices,
            'scope': 'Exact Mac build environment; includes build/test-only entries, not a claim every package ships'})
    runtime = app / 'Contents/Resources/runtimes/arbor'
    marker = json.loads((runtime / 'DIGIFLY_RUNTIME.json').read_text())
    assert marker['version'] == '0.12.2' and marker['python'] == '3.12.14'
    runtime_notices = []
    for path in runtime.rglob('*'):
        if path.is_file() and not path.is_symlink() and path.name.casefold().startswith(('license', 'copying', 'notice', 'authors')):
            # Flat text names cannot be mistaken for executable site-packages.
            runtime_notices.append(save('licenses/included-runtime/NOTICE-' + path.relative_to(runtime).as_posix().replace('/', '__'), path.read_bytes()))
    assert any('__arbor__' in n['path'] or 'Arbor-BSD' in n['path'] for n in runtime_notices)
    components.append({'component': 'included Arbor/Python/NumPy runtime', 'version': 'Arbor0.12.2/Python3.12.14',
                       'source': 'https://github.com/arbor-sim/arbor/tree/v0.12.2', 'notices': runtime_notices,
                       'scope': 'Copies of all bundled runtime package license/notice files; runtime itself is unchanged'})
    # Install-only Python archives omit full build metadata and several static
    # dependency license files. Recover those from the same official full build.
    import zstandard
    with tempfile.TemporaryDirectory(prefix='digifly-mac-license-input-') as temporary:
        archive = Path(temporary) / 'python-full.tar.zst'
        request = urllib.request.Request(FULL_URL, headers={'User-Agent': 'Digifly-source-notice-collector'})
        with urllib.request.urlopen(request, timeout=45) as response, archive.open('xb') as handle:
            while block := response.read(1024**2):
                handle.write(block)
        assert sha(archive.read_bytes()) == FULL_SHA
        pbs_notices, pbs_metadata = [], None
        with archive.open('rb') as source, zstandard.ZstdDecompressor().stream_reader(source) as stream, tarfile.open(fileobj=stream, mode='r|') as tar:
            for entry in tar:
                if not entry.isfile():
                    continue
                if entry.name == 'python/PYTHON.json':
                    pbs_metadata = json.loads(tar.extractfile(entry).read())
                elif entry.name.startswith('python/licenses/'):
                    pbs_notices.append(save('licenses/python-standalone-20260901/' + entry.name.removeprefix('python/licenses/'), tar.extractfile(entry).read()))
        assert pbs_metadata and pbs_notices
        # Retain upstream identity/linkage/license records, not its builder's
        # machine-local sysconfig paths. Exact full input stays pinned above.
        public_metadata = {key: pbs_metadata[key] for key in (
            'version', 'python_version', 'target_triple', 'build_options',
            'licenses', 'license_path', 'libpython_link_mode', 'crt_features')}
        save('licenses/python-standalone-20260901/BUILD_LICENSES.json', (json.dumps(public_metadata, indent=2) + '\n').encode())
        components.append({'component': 'Python standalone static dependencies', 'version': '3.12.14+20260901',
                           'source': FULL_URL, 'source_sha256': FULL_SHA, 'notices': pbs_notices,
                           'scope': 'Same official full build as the pinned included install-only runtime; full license inventory retained'})
    text = ('Third-party libraries remain under their own licenses.\n'
            'Qt/PySide6/Shiboken6 UI libraries are dynamically linked. Digifly does not prohibit replacement or reverse engineering for debugging modifications to those libraries.\n'
            'Exact upstream Qt6.10.3 source: https://github.com/qt/qtbase/tree/v6.10.3 and https://github.com/pyside/pyside-setup/tree/v6.10.3\n'
            'This Mac arm64 tester is ad-hoc signed, not Apple-notarized. No Windows11, all-feature or scientific acceptance claim follows.\n')
    save('THIRD_PARTY_README.txt', text.encode())
    inventory = {'schema': 'digifly-macos-notice-inventory-v1', 'components': components,
                 'scope': 'Exact source texts/package identities and build metadata, not an independent legal opinion or full binary ABI census'}
    save('THIRD_PARTY_COMPONENTS.json', (json.dumps(inventory, indent=2) + '\n').encode())
    manifest = {'schema': 'digifly-macos-notice-files-v1', 'files': files}
    (destination / 'notice_files.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return {'ok': True, 'files': len(files), 'components': len(components), 'full_python_build_sha256': FULL_SHA}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-app', 'qt-archive', 'destination', 'conda-root'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(collect(args.source_app, args.qt_archive, args.destination, args.conda_root)))
