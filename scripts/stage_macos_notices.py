"""Verify and copy an explicitly reviewed notice-only bundle before signing."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def stage(source, bundle):
    source, bundle = Path(source).absolute(), Path(bundle).absolute()
    for root in (source, bundle):
        if any(p.is_symlink() for p in (root, *root.parents)):
            raise ValueError('Notice paths must not use symbolic links')
    manifest = json.loads((source / 'notice_files.json').read_text())
    if manifest.get('schema') != 'digifly-macos-notice-files-v1':
        raise ValueError('Unknown Mac notice manifest')
    files = manifest['files']
    if not {'THIRD_PARTY_COMPONENTS.json', 'THIRD_PARTY_README.txt'} <= files.keys():
        raise ValueError('Missing notice inventory or scope')
    actual = set()
    for path in source.rglob('*'):
        if path.is_symlink():
            raise ValueError('Notice inputs must not contain links')
        if path.is_file():
            actual.add(path.relative_to(source).as_posix())
    if actual != {*files, 'notice_files.json'}:
        raise ValueError('Unexpected or missing notice files')
    destination = bundle / 'Contents/Resources'
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                or not (name.startswith('licenses/') or name in {'THIRD_PARTY_COMPONENTS.json', 'THIRD_PARTY_README.txt'})):
            raise ValueError('Unsafe notice destination')
        data = (source / name).read_bytes()
        if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError('Notice input changed: ' + name)
        target = destination / name
        if target.exists() or any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError('Notice destination already exists or uses a link')
    for name in files:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / name, target)
    shutil.copyfile(source / 'notice_files.json', destination / 'notice_files.json')
    return {'ok': True, 'notice_files': len(files)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.source, args.bundle)))
