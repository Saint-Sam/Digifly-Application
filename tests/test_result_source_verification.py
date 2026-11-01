"""Request-local digest reuse keeps source and cache mutation checks."""
import hashlib
import pytest
from test_result_voltage_store import recording
from digifly_app.core.result_voltage import load_voltage_table,_stamp


def identity(path):
    return dict(stamp=_stamp(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def test_verified_digest_avoids_only_the_duplicate_source_read(tmp_path,monkeypatch):
    import digifly_app.core.result_voltage as store
    path=recording(tmp_path);checked=identity(path);calls=[];original=store._digest
    def digest(candidate,cancelled):
        assert candidate.resolve()!=path.resolve(), 'Already verified source was hashed twice'
        calls.append(candidate.name);return original(candidate,cancelled)
    monkeypatch.setattr(store,'_digest',digest)
    first=load_voltage_table(path,cache_root=tmp_path/'cache',verified_source=checked)
    warm=load_voltage_table(path,cache_root=tmp_path/'cache',verified_source=checked)
    assert first.sample_count==warm.sample_count==24 and warm.cache_hit
    assert 'index.jsonl' in calls and 'values.bin' in calls


@pytest.mark.parametrize('fault',['changed','replaced','wrong-file','missing-digest','invalid-digest'])
def test_stale_or_invalid_verified_identity_refuses(tmp_path,fault):
    path=recording(tmp_path);checked=identity(path)
    if fault=='changed':path.write_text(path.read_text().replace('42.0','43.0'))
    elif fault=='replaced':
        retained=path.with_suffix('.retained');path.rename(retained);path.write_bytes(retained.read_bytes())
    elif fault=='wrong-file':checked['stamp'][0]=str(tmp_path/'elsewhere.csv')
    elif fault=='missing-digest':checked.pop('sha256')
    else:checked['sha256']='not-a-digest'
    with pytest.raises(ValueError,match='changed since input verification'):
        load_voltage_table(path,cache_root=tmp_path/'cache',verified_source=checked)
    assert not (tmp_path/'cache').exists()


def test_verified_source_still_checks_changes_during_cache_read(tmp_path,monkeypatch):
    import digifly_app.core.result_voltage as store
    path=recording(tmp_path);cache=tmp_path/'cache';load_voltage_table(path,cache_root=cache)
    checked=identity(path);original=store._read_cache
    def changed(*args,**kwargs):
        result=original(*args,**kwargs);path.write_text(path.read_text().replace('42.0','43.0'));return result
    monkeypatch.setattr(store,'_read_cache',changed)
    with pytest.raises(ValueError,match='changed during loading'):
        load_voltage_table(path,cache_root=cache,verified_source=checked)


def test_verified_source_never_bypasses_corrupt_cache_payload(tmp_path):
    path=recording(tmp_path);cache=tmp_path/'cache';checked=identity(path)
    first=load_voltage_table(path,cache_root=cache,verified_source=checked)
    expected=tuple(next(iter(first.series.values())))
    binary=next(cache.glob('*/values.bin'))
    with binary.open('r+b') as stream:stream.write(b'bad-data')
    rebuilt=load_voltage_table(path,cache_root=cache,verified_source=checked)
    assert not rebuilt.cache_hit and expected==tuple(next(iter(rebuilt.series.values())))


def test_verified_identity_uses_actual_compressed_input(tmp_path):
    import gzip
    path=recording(tmp_path);compressed=path.with_suffix(path.suffix+'.gz')
    with gzip.open(compressed,'wb') as stream:stream.write(path.read_bytes())
    path.unlink()  # only this tiny owned fixture, not a saved run
    checked=identity(compressed)
    table=load_voltage_table(path,cache_root=tmp_path/'cache',verified_source=checked)
    assert table.sample_count==24 and compressed.is_file()
