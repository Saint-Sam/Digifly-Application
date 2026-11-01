from pathlib import Path
import hashlib
import pytest
from digifly_app.core.circuit import CircuitSpec, HodgkinHuxleySpec
from digifly_app.core.mechanisms import MEMBRANE_MECHANISMS, ChannelAssignment, MembraneMechanismSpec
from digifly_app.workers import generic_experiment_worker as worker
from digifly_app.workers.arbor_channel_translation import translate, classic_hh_source

ROOT = Path(__file__).resolve().parents[1]


def test_standalone_channel_identities_match_authority():
    assert worker.NATIVE_CHANNELS == {d.key:(d.suffix,d.source_sha256) for d in MEMBRANE_MECHANISMS}


def test_classic_native_path_does_not_compile_unselected_channels(tmp_path):
    assert worker._prepare_native_channels({'circuit':CircuitSpec().to_dict()}, tmp_path) == {}
    assert not list(tmp_path.iterdir())


def test_classic_arbor_requires_hash_bound_table_translator(tmp_path):
    with pytest.raises(ValueError, match='translator identity'):
        worker._prepare_arbor_channels({'circuit':CircuitSpec().to_dict()}, tmp_path)
    assert not list(tmp_path.iterdir())


def test_classic_hh_uses_native_table_not_direct_builtin_rates():
    text = classic_hh_source()
    assert 'SUFFIX classic_hh_table' in text
    assert 'grid = v+100' in text and 'if (grid > 200)' in text
    assert 'if (index >= 200) { index = 199 }' in text
    for name in ('minf','mtau','hinf','htau','ninf','ntau'):
        assert f'{name} = low_{name}+fraction*({name}-low_{name})' in text
    assert '3^((celsius-6.3)/10)' in text
    assert 'TABLE ' not in text and 'trace' not in text


def test_arbor_decoration_selects_native_default_hh_table():
    from types import SimpleNamespace
    painted = []
    class Decor:
        def set_ion(self, *args, **kwargs): pass
        def paint(self, region, density=None, **kwargs):
            if density is not None: painted.append(density)
    arbor = SimpleNamespace(units=SimpleNamespace(mV=1.), density=lambda name, values:(name,values))
    worker._decorate_arbor_channels(arbor, Decor(), HodgkinHuxleySpec().to_dict(), {})
    assert len(painted) == 2
    assert {item[0] for item in painted} == {'digifly_channels_classic_hh_table'}


@pytest.mark.parametrize("engine", ("neuron", "arbor"))
def test_channel_builder_rejects_missing_sources_before_runtime_import(tmp_path, engine):
    d = next(d for d in MEMBRANE_MECHANISMS if d.key == "para")
    channel = ChannelAssignment(d.key,d.suffix,enabled=True,soma_gbar_s_cm2=.001)
    request = {"circuit":{"membrane":MembraneMechanismSpec(channels={d.key:channel}).to_dict()}}
    builder = worker._prepare_native_channels if engine=="neuron" else worker._prepare_arbor_channels
    with pytest.raises(ValueError, match="source set"):
        builder(request,tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("key", (d.key for d in MEMBRANE_MECHANISMS))
def test_valid_locked_assignment_is_accepted_and_tampering_rejected(key):
    d = next(d for d in MEMBRANE_MECHANISMS if d.key == key)
    assignment = ChannelAssignment(d.key,d.suffix,enabled=True,soma_gbar_s_cm2=.001).to_dict()
    worker._validated_native_assignment(key,assignment)
    with pytest.raises(ValueError, match="Unverified native"):
        worker._validated_native_assignment(key,{**assignment,"source_sha256":"0"*64})
    with pytest.raises(ValueError, match="Reserved native"):
        worker._validated_native_assignment(key,{**assignment,"parameters":{"celsius":25}})
    with pytest.raises(ValueError, match="Invalid .*density"):
        worker._validated_native_assignment(key,{**assignment,"soma_gbar_s_cm2":float("nan")})


def test_arbor_translation_passes_temperature_and_parameters_to_rates():
    source = """TITLE example
PARAMETER {\n v (mV)\n celsius (degC)\n q10 = 1\n}
INITIAL { rates(v) }
PROCEDURE rates(v (mV)) { x = q10^((celsius-23)/10) }
"""
    text = translate("shab",source)
    assert "rates(v, celsius, q10)" in text
    assert "PROCEDURE rates(v, celsius, q10)" in text
    assert "x = q10^((celsius-23)/10)" in text


@pytest.mark.parametrize("key", ("para","para_alt"))
def test_markov_stationary_init_is_explicit_not_artificial_warmup(key):
    d = next(d for d in MEMBRANE_MECHANISMS if d.key == key)
    source = ROOT.parent / "Digifly Public" / d.source_relpath
    if not source.is_file(): pytest.skip("Local pinned Phase 2 mechanism not available")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == d.source_sha256
    translated = translate(key,source.read_text())
    assert "STEADYSTATE" not in translated
    assert "C1 = w1/total" in translated and "I2 = w6/total" in translated
    assert "SOLVE kin METHOD sparse" in translated


def test_replacement_retains_combined_leak_and_reversal():
    hh = HodgkinHuxleySpec(g_pas_s_cm2=.002,e_pas_mV=-50,soma_gl_s_cm2=.001,soma_el_mV=-80).to_dict()
    values = worker._region_hh(hh,soma=True)
    assert values["gl"] == .003
    assert values["el"] == pytest.approx(-60)


def test_arbor_root_adapter_does_not_add_radius_scaled_soma_area():
    rows=((1,1,0.,0.,0.,5.,-1),(2,1,0.,0.,10.,5.,1))
    assert worker._arbor_root_stub_length(rows) == .001
    assert worker._arbor_root_stub_length(((1,1,0.,0.,0.,50.,-1),*rows[1:])) == .001


@pytest.mark.parametrize("engine", ("neuron", "arbor"))
def test_changed_channel_bytes_fail_before_import_or_build(tmp_path, monkeypatch, engine):
    import sys
    d = next(d for d in MEMBRANE_MECHANISMS if d.key == "para")
    source = tmp_path/'changed.mod'
    source.write_text('Not the locked mechanism source')
    output=tmp_path/'output';output.mkdir()
    assignment=ChannelAssignment(d.key,d.suffix,enabled=True,soma_gbar_s_cm2=.001)
    request={'circuit':{'membrane':MembraneMechanismSpec(channels={d.key:assignment}).to_dict()},
             'native_channel_sources':{d.key:{'path':str(source),'suffix':d.suffix,'sha256':d.source_sha256}}}
    monkeypatch.setitem(sys.modules,engine,None)
    builder=worker._prepare_native_channels if engine=='neuron' else worker._prepare_arbor_channels
    with pytest.raises(ValueError,match='source hash mismatch'):
        builder(request,output)
    assert not list(output.iterdir())
