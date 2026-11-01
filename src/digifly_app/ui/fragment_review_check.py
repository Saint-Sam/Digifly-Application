"""Tiny native engineering check of saved bridge-record portability."""
import copy
import json
import math
from pathlib import Path

from digifly_app.core.swc_fragments import AXON_AWARE_POLICY, original_for_bridge_review, plan_fragment_join, save_fragment_join
from digifly_app.core.swc_quality import analyze_swc


def check_fragment_record_roundoff(destination: Path) -> dict:
    root=destination/'bridge-record-roundoff'
    root.mkdir()
    source=root/'1.swc'
    source.write_text('# units: um\n1 2 0 0 0 1 -1\n2 2 2 0 0 1 1\n20 2 8 0 0 .8 -1\n21 2 10 0 0 .8 20\n')
    raw=source.read_bytes()
    joined=save_fragment_join(plan_fragment_join(analyze_swc(source),policy=AXON_AWARE_POLICY))
    kept=joined.read_bytes()
    sidecar=joined.with_suffix('.swc.quality.json')
    record=json.loads(sidecar.read_text())
    for key in ('original_length_um','original_volume_um3','added_volume_um3','added_volume_fraction'):
        record['plan'][key]=math.nextafter(record['plan'][key],math.inf)
    for bridge in record['plan']['bridges']:
        for key in ('volume_um3','axial_resistance_MOhm_at_100_ohm_cm'):
            bridge[key]=math.nextafter(bridge[key],math.inf)
    sidecar.write_text(json.dumps(record))
    if original_for_bridge_review(joined)!=source.resolve():
        raise ValueError('Compiled bridge verifier rejected report-only rounding')
    def refused(changed):
        sidecar.write_text(json.dumps(changed))
        try:
            original_for_bridge_review(joined)
        except ValueError:
            return True
        raise ValueError('Compiled bridge verifier accepted a material or geometry change')
    changed=copy.deepcopy(record)
    changed['plan']['filler_nodes'][0][2]=math.nextafter(changed['plan']['filler_nodes'][0][2],math.inf)
    geometry_refused=refused(changed)
    changed=copy.deepcopy(record)
    changed['plan']['bridges'][0]['volume_um3']*=1+1e-8
    material_refused=refused(changed)
    changed=copy.deepcopy(record)
    changed['plan']['policy']='unknown_policy'
    policy_refused=refused(changed)
    sidecar.write_text(json.dumps(record))
    if source.read_bytes()!=raw or joined.read_bytes()!=kept:
        raise ValueError('Roundoff check changed its source or generated geometry')
    result={'ok':True,'report_only_roundoff_accepted':True,
        'one_bit_filler_geometry_change_refused':geometry_refused,
        'material_summary_change_refused':material_refused,'unknown_policy_refused':policy_refused,
        'source_and_joined_SWCs_unchanged':True,'scope':'Generated engineering fixture, no simulation or acquired neuron.'}
    (root/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
