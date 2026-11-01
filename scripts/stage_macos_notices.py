"""Verify and copy an explicitly reviewed notice-only bundle before signing."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil


def validate_source(source):
    """Read-only check shared by early build admission and final staging."""
    source = Path(source).absolute()
    if any(p.is_symlink() for p in (source, *source.parents)):
        raise ValueError('Notice paths must not use symbolic links')
    if not (source / 'notice_files.json').is_file():
        raise ValueError('Select a notice-only folder containing notice_files.json, not an app bundle')
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
    for name, expected in files.items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                or not (name.startswith('licenses/') or name in {'THIRD_PARTY_COMPONENTS.json', 'THIRD_PARTY_README.txt'})):
            raise ValueError('Unsafe notice destination')
        data = (source / name).read_bytes()
        if len(data) != expected['bytes'] or hashlib.sha256(data).hexdigest() != expected['sha256']:
            raise ValueError('Notice input changed: ' + name)
    return source, files


def stage(source, bundle):
    source, files = validate_source(source)
    bundle = Path(bundle).absolute()
    if any(p.is_symlink() for p in (bundle, *bundle.parents)):
        raise ValueError('Notice paths must not use symbolic links')
    destination = bundle / 'Contents/Resources'
    for name in files:
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
    parser.add_argument('--bundle', type=Path)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only == bool(args.bundle):
        parser.error('Choose --validate-only or provide --bundle, not both')
    try:
        if args.validate_only:
            _, files = validate_source(args.source)
            print(json.dumps({'ok': True, 'notice_files': len(files), 'validation_only': True}))
        else:
            print(json.dumps(stage(args.source, args.bundle)))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
