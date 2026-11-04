"""Measured contact coordinates mapped onto real SWC cables, never filler links."""
import math
import heapq


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


class ContactProjector:
    """One preparation call's exact nearest-cable index; never retained on a model.

    Boxes bound entire cables, including long edges. Leaves use the exhaustive
    mapper's arithmetic and tuple ordering. Expanded boxes and downward-rounded
    distance bounds prevent pruning a tied or marginally nearer floating-point
    projection. The original exhaustive function remains an independent oracle.
    """

    def __init__(self, morphology):
        metadata = morphology.filler_metadata or {}
        excluded = set(metadata.get('filler_edge_child_ids', ()))
        synthetic = set(metadata.get('synthetic_node_ids', ()))
        entries = []
        for edge in morphology.segments:
            if edge.child_id in excluded or edge.child_id in synthetic or edge.parent_id in synthetic:
                continue
            direction = tuple(b-a for a,b in zip(edge.parent, edge.child))
            squared_length = sum(value*value for value in direction)
            if squared_length <= 0:
                continue
            # Include rounding in a + fraction*(b-a), for fraction in [0,1].
            # Overflow or nonfinite geometry gets an unbounded box, so it cannot
            # be silently discarded by the acceleration structure.
            bounds = []
            for a,b in zip(edge.parent, edge.child):
                padding = 32 * math.ulp(max(abs(a), abs(b), 1.))
                bounds.extend((min(a,b)-padding, max(a,b)+padding))
            if not all(math.isfinite(v) for v in (*direction, squared_length, *bounds)):
                bounds = [-math.inf, math.inf] * 3
            entries.append((edge, direction, squared_length, tuple(bounds)))
        self._nodes = []
        self._first = entries[0] if entries else None
        self._root = self._build(entries) if entries else None

    def _build(self, entries):
        bounds = tuple((min(e[3][axis] for e in entries) if axis % 2 == 0
                        else max(e[3][axis] for e in entries)) for axis in range(6))
        node_id = len(self._nodes)
        self._nodes.append(None)
        if len(entries) <= 16:
            self._nodes[node_id] = bounds, tuple(entries), ()
        else:
            axis = max(range(3), key=lambda a: bounds[2*a+1]-bounds[2*a])
            entries.sort(key=lambda e: e[3][2*axis]/2 + e[3][2*axis+1]/2
                         if math.isfinite(e[3][2*axis]) and math.isfinite(e[3][2*axis+1]) else 0.)
            middle = len(entries)//2
            children = self._build(entries[:middle]), self._build(entries[middle:])
            self._nodes[node_id] = bounds, (), children
        return node_id

    def _lower_bound(self, node_id, point):
        bounds = self._nodes[node_id][0]
        nearest = tuple(min(bounds[2*a+1], max(bounds[2*a], point[a])) for a in range(3))
        distance = math.dist(point, nearest)
        # A conservative bound also when the distance calculation rounds up.
        return math.nextafter(math.nextafter(distance, 0.), 0.)

    def project(self, point):
        point = tuple(float(value) for value in point)
        if len(point) != 3 or not all(math.isfinite(value) for value in point):
            raise ValueError('A contact needs three finite coordinates')
        if self._root is None:
            raise ValueError('No nonzero real SWC cable can receive this contact; filler links are excluded')
        queue = [(0., self._root)]
        # Seed with a real cable so every branch bound has a concrete candidate.
        # This also avoids nullable tuple state in the packaged compiler path.
        best = self._candidate(self._first, point)
        while queue:
            lower, node_id = heapq.heappop(queue)
            if lower > math.nextafter(best[0], math.inf):
                continue
            _, entries, children = self._nodes[node_id]
            for entry in entries:
                candidate = self._candidate(entry, point)
                if candidate < best:
                    best = candidate
            for child in children:
                lower = self._lower_bound(child, point)
                if lower <= math.nextafter(best[0], math.inf):
                    heapq.heappush(queue, (lower, child))
        distance, child, fraction, parent, projection = best
        return {'schema':'digifly-real-cable-contact-map-v1',
            'source_node_id':child, 'source_parent_id':parent, 'source_edge_fraction':fraction,
            'measured_coordinate_um':list(point), 'projected_coordinate_um':list(projection),
            'projection_distance_um':distance, 'filler_target':False,
            'policy':'Nearest point on an original nonzero SWC edge; deterministic child-ID tie break. '
                     'Measured coordinates stay unchanged. Finite-volume attachment is reported separately.'}

    @staticmethod
    def _candidate(entry, point):
        edge, direction, squared_length, _ = entry
        fraction = min(1., max(0., sum((v-a)*d for v,a,d in zip(point,edge.parent,direction))/squared_length))
        projection = tuple(a+fraction*d for a,d in zip(edge.parent,direction))
        return math.dist(point, projection), edge.child_id, fraction, edge.parent_id, projection
