import hashlib
import struct
import pytest
from scripts.stage_windows_graphics import verified_bytes, write_dependency, DLL


def pe(machine=b'\x64\x86'):
    data=bytearray(128);data[:2]=b'MZ';struct.pack_into('<I',data,60,80)
    data[80:86]=b'PE\0\0'+machine
    return bytes(data)


def test_exact_x64_source_is_accepted(tmp_path):
    source=tmp_path/'source.dll';data=pe();source.write_bytes(data)
    assert verified_bytes(source,hashlib.sha256(data).hexdigest(),len(data))==data


@pytest.mark.parametrize('damage',['bytes','size','architecture','format','symlink'])
def test_changed_or_wrong_source_refuses(tmp_path,damage):
    source=tmp_path/'source.dll';data=pe();source.write_bytes(data)
    pin=hashlib.sha256(data).hexdigest();size=len(data)
    if damage=='bytes':source.write_bytes(data+b'x')
    if damage=='size':size+=1
    if damage=='architecture':
        data=pe(b'\x4c\x01');source.write_bytes(data);pin=hashlib.sha256(data).hexdigest()
    if damage=='format':
        data=b'x'*size;source.write_bytes(data);pin=hashlib.sha256(data).hexdigest()
    if damage=='symlink':
        target=tmp_path/'link.dll'
        try:target.symlink_to(source)
        except OSError:pytest.skip('Account cannot create symbolic links')
        source=target
    with pytest.raises(ValueError):verified_bytes(source,pin,size)


def test_new_and_matching_dependency_are_preserved(tmp_path):
    data=pe();write_dependency(tmp_path,data);write_dependency(tmp_path,data)
    assert (tmp_path/DLL).read_bytes()==data


def test_different_existing_dependency_refuses_without_replacement(tmp_path):
    target=tmp_path/DLL;target.write_bytes(b'keep')
    with pytest.raises(ValueError):write_dependency(tmp_path,pe())
    assert target.read_bytes()==b'keep'
