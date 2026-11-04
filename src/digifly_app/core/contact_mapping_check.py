"""Explicit packaged numerical fixture; no network, user files or simulator."""
import json
import math
from pathlib import Path
import random
from types import SimpleNamespace
import traceback

from .contact_mapping import ContactProjector, project_contact
from .morphology import SwcSegment


def run(destination):
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    results=[]
    try:
        for seed in range(8):
            rng=random.Random(seed)
            edges=[SwcSegment(i+1,i,tuple(rng.uniform(-100,100) for _ in range(3)),
                     tuple(rng.uniform(-100,100) for _ in range(3)),1.,3) for i in range(180)]
            rng.shuffle(edges)
            model=SimpleNamespace(segments=tuple(edges),filler_metadata={})
            index=ContactProjector(model)
            assert all(node is not None for node in index._nodes)
            assert sum(len(node[1]) for node in index._nodes)==len(edges)
            maximum=0.
            for _ in range(90):
                point=tuple(rng.uniform(-200,200) for _ in range(3))
                expected=project_contact(model,point)
                actual=index.project(point)
                for field in expected:
                    a,b=expected[field],actual[field]
                    if isinstance(a,float):
                        maximum=max(maximum,abs(a-b))
                        assert math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12),(seed,field,a,b)
                    else:assert a==b,(seed,field,a,b)
            results.append(dict(seed=seed,queries=90,cables=180,max_error=maximum))
        # Many equidistant endpoints must retain the lowest source child ID.
        edges=tuple(SwcSegment(i+2,i+1,(10.,0.,0.),(0.,0.,0.),1.,3) for i in reversed(range(80)))
        model=SimpleNamespace(segments=edges,filler_metadata={})
        assert ContactProjector(model).project((5.,1.,0.))==project_contact(model,(5.,1.,0.))
        # Meaningful damaged/empty/filler-only cases must still refuse.
        for model,point in ((SimpleNamespace(segments=(),filler_metadata={}), (0.,0.,0.)),
                 (model,(0.,math.inf,0.)),
                 (SimpleNamespace(segments=edges,filler_metadata={'filler_edge_child_ids':[e.child_id for e in edges]}),(0.,0.,0.))):
            try:ContactProjector(model).project(point)
            except ValueError:pass
            else:raise AssertionError('Invalid contact fixture was accepted')
        receipt=dict(ok=True,random_queries=720,results=results,tie_case=True,invalid_cases=3,
                     scope='Packaged projection versus exhaustive geometry; no simulation')
    except Exception:
        receipt=dict(ok=False,results=results,error=traceback.format_exc())
    (destination/'receipt.json').write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))
    return 0 if receipt['ok'] else 1
