from dataclasses import replace
import pytest
from PySide6.QtWidgets import QApplication

from digifly_app.core.experiment import ExperimentSpec
from digifly_app.workers import independent_trials as compute
from digifly_app.workers.result_storage import shared_regional_materials,read_result_json


def test_thread_settings_legacy_default_and_explicit_roundtrip():
    assert not ExperimentSpec.from_dict({}).neuron_multithreading
    settings=replace(ExperimentSpec(),engine='neuron',neuron_multithreading=True,neuron_threads=4)
    assert ExperimentSpec.from_dict(settings.to_dict())==settings
    for value in (True,0,257,2.5):
        assert replace(settings,neuron_threads=value).errors()


def test_runtime_checks_explicit_enable_and_host_limit(monkeypatch):
    monkeypatch.setattr(compute,'logical_cpu_count',lambda:8)
    assert compute.neuron_thread_count({'neuron_threads':16})==1
    assert compute.neuron_thread_count({'neuron_multithreading':True,'neuron_threads':4})==4
    with pytest.raises(ValueError,match='exposes'):
        compute.neuron_thread_count({'neuron_multithreading':True,'neuron_threads':16})
    with pytest.raises(ValueError,match='whole number'):
        compute.neuron_thread_count({'neuron_multithreading':True,'neuron_threads':2.5})


def test_thread_controls_roundtrip_engine_availability_and_capacity():
    from digifly_app.ui.experiment_builder import ExperimentBuilderPage
    app=QApplication.instance() or QApplication([])
    page=ExperimentBuilderPage()
    page.set_config(replace(ExperimentSpec(),engine='neuron',neuron_multithreading=True,neuron_threads=4))
    assert page.neuron_threads.isEnabled()
    assert page.neuron_multithreading.isChecked()
    assert page.config().neuron_threads==4 and page.config().neuron_multithreading
    assert 'logical CPUs' in page.cpu_capacity.text()
    page.neuron_multithreading.setChecked(False)
    assert not page.neuron_threads.isEnabled() and page.config().neuron_threads==4
    page.engine_combo.setCurrentIndex(page.engine_combo.findData('bmtk'))
    assert not page.neuron_multithreading.isEnabled()
    page.close()


def test_shared_regional_settings_roundtrip_every_edge_and_override(tmp_path):
    import json
    plan={'neuron_id':'a','node_sites':{'2':{'section_id':0,'section_pos':1}},'sections':[
        {'section_id':0,'path':[1,2],'hh':{'cm':1.2345678901234567},'membrane':{'channels':{}}},
        {'section_id':1,'path':[2,3],'hh':{'cm':1.2345678901234567},'membrane':{'channels':{}}},
        {'section_id':2,'path':[3,4],'hh':{'cm':2.5},'membrane':{'channels':{'custom':{'g':.123}}}}]}
    packed=shared_regional_materials(plan)
    assert len(packed['materials'])==2 and 'hh' not in packed['sections'][0]
    path=tmp_path/'regional_map.json';path.write_text(json.dumps(packed))
    assert read_result_json(path)==plan
    packed['sections'][1]['material']=-1;path.write_text(json.dumps(packed))
    with pytest.raises(ValueError,match='reference'):read_result_json(path)
