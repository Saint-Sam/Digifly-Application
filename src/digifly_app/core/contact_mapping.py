"""Measured contact coordinates mapped onto real SWC cables, never filler links."""
import math


def project_contact(morphology, point):
    point = tuple(float(value) for value in point)
    if len(point) != 3 or not all(math.isfinite(value) for value in point):
        raise ValueError('A contact needs three finite coordinates')
    metadata = morphology.filler_metadata or {}
    excluded = set(metadata.get('filler_edge_child_ids', ()))
    synthetic = set(metadata.get('synthetic_node_ids', ()))
    candidates = []
    for edge in morphology.segments:
        if edge.child_id in excluded or edge.child_id in synthetic or edge.parent_id in synthetic:
            continue
        direction = tuple(b-a for a,b in zip(edge.parent, edge.child))
        squared_length = sum(value*value for value in direction)
        if squared_length <= 0:
            continue  # A zero-length cable has no defined fractional position.
        fraction = min(1., max(0., sum((v-a)*d for v,a,d in zip(point,edge.parent,direction))/squared_length))
        projection = tuple(a+fraction*d for a,d in zip(edge.parent,direction))
        distance = math.dist(point, projection)
        candidates.append((distance, edge.child_id, fraction, edge.parent_id, projection))
    if not candidates:
        raise ValueError('No nonzero real SWC cable can receive this contact; filler links are excluded')
    distance, child, fraction, parent, projection = min(candidates)
    return {'schema':'digifly-real-cable-contact-map-v1',
        'source_node_id':child, 'source_parent_id':parent, 'source_edge_fraction':fraction,
        'measured_coordinate_um':list(point), 'projected_coordinate_um':list(projection),
        'projection_distance_um':distance, 'filler_target':False,
        'policy':'Nearest point on an original nonzero SWC edge; deterministic child-ID tie break. '
                 'Measured coordinates stay unchanged. Finite-volume attachment is reported separately.'}
