"""Include the exact Qt wheel's software graphics fallback before sealing."""
from __future__ import annotations
import argparse
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path
import struct

QT_VERSION = '6.10.3'
DLL = 'opengl32sw.dll'
DLL_BYTES = 20639544
DLL_SHA256 = '0100c5944e6516c6aa0a29cb8e5db0867926875a3cd1279853affe97d6f6bb95'
MARKER = 'digifly_windows_graphics.json'


def verified_bytes(path, expected_sha256, expected_bytes):
    if path.is_symlink():
        raise ValueError('Qt graphics dependency must be a regular source file')
    data = path.read_bytes()
    if len(data) != expected_bytes or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError('Qt software renderer differs from its reviewed source wheel')
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('Qt software renderer is not a Windows library')
    offset = struct.unpack_from('<I', data, 60)[0]
    if data[offset:offset+6] != b'PE\0\0\x64\x86':
        raise ValueError('Qt software renderer is not Windows x86-64')
    return data


def write_dependency(bundle, data):
    target = bundle/DLL
    if target.is_symlink():
        raise ValueError('Qt graphics destination must not be a symbolic link')
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError('Refusing to replace a different Qt graphics dependency')
    else:
        with target.open('xb') as stream:
            stream.write(data)


def stage(bundle):
    import PySide6
    if PySide6.__version__ != QT_VERSION:
        raise ValueError('Qt graphics dependency needs review for this wheel version')
    dist = metadata.distribution('PySide6_Essentials')
    if dist.version != QT_VERSION:
        raise ValueError('Qt graphics wheel metadata version differs')
    item, = [p for p in dist.files or () if str(p).replace('\\', '/') == 'PySide6/'+DLL]
    path = dist.locate_file(item)
    if path.resolve().parent != Path(PySide6.__file__).resolve().parent:
        raise ValueError('Qt graphics record does not belong to the selected wheel')
    data = verified_bytes(path, DLL_SHA256, DLL_BYTES)
    if (not item.hash or item.hash.mode != 'sha256'
            or base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode().rstrip('=') != item.hash.value):
        raise ValueError('Qt graphics dependency differs from the wheel RECORD')
    marker = bundle/MARKER
    if marker.exists():
        raise ValueError('Qt graphics inventory must be new')
    write_dependency(bundle, data)
    record = dict(schema='digifly-windows-graphics-v1', qt_version=QT_VERSION,
                  distribution='PySide6_Essentials', wheel_record=str(item),
                  file=DLL, bytes=DLL_BYTES, sha256=DLL_SHA256,
                  source='https://doc.qt.io/qt-6.10/windows-graphics.html',
                  scope='Untouched Qt UI software OpenGL fallback; selected automatically by Qt when needed.')
    with marker.open('x') as stream:
        json.dump(record, stream, indent=2)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    print(json.dumps(stage(parser.parse_args().bundle.resolve())))
