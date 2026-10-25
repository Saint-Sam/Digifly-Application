"""Bounded, exact-release chemical contact locations from neuPrint.

No electrical anatomy or conductance is inferred here. The confidence rule
matches the official client's SynapseCriteria default, not an agent-selected
cutoff. Coordinates are retained in native dataset space, without rounding.
"""
from dataclasses import astuple, dataclass
import json
import math


COLUMNS = ('source_body_id', 'target_body_id', 'pre_synapse_id', 'post_synapse_id',
           'pre_x', 'pre_y', 'pre_z', 'post_x', 'post_y', 'post_z',
           'pre_confidence', 'post_confidence')
METHOD_SOURCE = 'https://github.com/connectome-neuprint/neuprint-python/blob/master/neuprint/queries/synapses.py'
THRESHOLD_SOURCE = 'https://github.com/connectome-neuprint/neuprint-python/blob/master/neuprint/queries/synapsecriteria.py'


@dataclass(frozen=True)
class ChemicalContact:
    source_body_id: int
    target_body_id: int
    pre_synapse_id: int
    post_synapse_id: int
    pre_x: float
    pre_y: float
    pre_z: float
    post_x: float
    post_y: float
    post_z: float
    pre_confidence: float | None
    post_confidence: float | None

    def to_row(self):
        return astuple(self)


def _integer(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**63:
        raise ValueError('Contact identities must be nonnegative signed 64-bit integers')
    return value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Chemical contacts require finite numeric coordinates')
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError('Chemical contact coordinate exceeds numeric range') from exc
    if not math.isfinite(result):
        raise ValueError('Chemical contacts require finite numeric coordinates')
    if isinstance(value, int) and int(result) != value:
        raise ValueError('Chemical contact coordinate cannot be retained exactly')
    return result


def validate_method(method):
    """Do not let a file claim locations without its exact acquisition rule."""
    if (not isinstance(method, dict)
            or method.get('schema') != 'digifly-neuprint-contact-method-v1'
            or method.get('confidence_comparison') != 'strict_greater_when_nonzero'
            or method.get('roi_filter') is not None
            or method.get('coordinate_units') != 'native_dataset_space'
            or method.get('coordinates_rounded') is not False
            or method.get('electrical_anatomy_inferred') is not False
            or method.get('method_source') != METHOD_SOURCE
            or method.get('threshold_source') != THRESHOLD_SOURCE):
        raise ValueError('Chemical contact download method is missing or unsupported')
    threshold = _number(method.get('confidence_threshold'))
    if not 0 <= threshold <= 1:
        raise ValueError('Chemical contact confidence threshold is invalid')
    return threshold


def _table(raw, columns, max_rows):
    from .neuprint import NeuPrintError
    try:
        data = json.loads(raw.decode('utf-8'))
        rows = data['data']
        if data['columns'] != list(columns) or not isinstance(rows, list):
            raise ValueError()
        if len(rows) > max_rows:
            raise NeuPrintError('Contact response exceeds its row limit; no partial locations were accepted')
        if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
            raise ValueError()
        return rows
    except (UnicodeError, KeyError, TypeError, ValueError) as exc:
        raise NeuPrintError('neuPrint returned an invalid chemical contact table') from exc


def fetch_between(client, source_ids, target_ids, *, cancel=None,
                  max_rows=250_000, max_bytes=128*1024**2, on_bytes=None):
    from .neuprint import AcquisitionCancelled, NeuPrintError
    from .selected_connectivity import _ids
    sources, targets = _ids(source_ids), _ids(target_ids)
    if (not client.dataset or not sources or not targets
            or max(len(sources), len(targets)) > 500):
        raise ValueError('Contact downloads require an exact dataset and 1–500 body IDs on each side')
    if (isinstance(max_rows, bool) or not isinstance(max_rows, int)
            or isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
            or not 1 <= max_rows <= 250_000 or not 1 <= max_bytes <= 128*1024**2):
        raise ValueError('Invalid chemical contact download limits')
    transferred = 0
    def count(amount):
        nonlocal transferred
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 0:
            raise NeuPrintError('Invalid contact transfer byte count')
        transferred += amount
        if transferred > max_bytes:
            raise NeuPrintError('Chemical contact byte budget exceeded; original data is unchanged')
        if on_bytes:
            on_bytes(amount)
    def query(cypher):
        if cancel is not None and cancel.is_set():
            raise AcquisitionCancelled('Chemical contact download cancelled')
        if transferred >= max_bytes:
            raise NeuPrintError('Chemical contact byte budget exhausted')
        return client._request('POST', '/api/custom/custom',
            payload={'cypher':cypher, 'dataset':client.dataset}, cancel=cancel,
            max_bytes=max_bytes-transferred, on_bytes=count)
    identity = (getattr(client, 'server', ''), client.dataset)
    cached = getattr(client, '_digifly_contact_threshold', None)
    threshold = cached[1] if cached is not None and cached[0] == identity else None
    if threshold is None:
        meta = _table(query('MATCH (m:Meta) RETURN m.postHighAccuracyThreshold AS threshold LIMIT 2'),
                      ('threshold',), 1)
        if len(meta) != 1:
            raise NeuPrintError('Cannot determine the dataset contact confidence rule')
        # Same missing-property default as the official client. Its use remains
        # explicit in the saved download method, never an unrecorded filter.
        threshold = 0.0 if meta[0][0] is None else _number(meta[0][0])
        if not 0 <= threshold <= 1:
            raise NeuPrintError('Dataset contact confidence threshold is invalid')
        client._digifly_contact_threshold = (identity, threshold)
    confidence = (f' AND pre.confidence > {threshold:.17g} AND post.confidence > {threshold:.17g}'
                  if threshold else '')
    s, t = ','.join(map(str, sources)), ','.join(map(str, targets))
    returns = ('source.bodyId', 'target.bodyId', 'id(pre)', 'id(post)',
               'pre.location.x', 'pre.location.y', 'pre.location.z',
               'post.location.x', 'post.location.y', 'post.location.z',
               'pre.confidence', 'post.confidence')
    cypher = (
        'MATCH (source:Neuron)-[edge:ConnectsTo]->(target:Neuron), '
        '(source)-[:Contains]->(ss:SynapseSet)-[:ConnectsTo]->(ts:SynapseSet)<-[:Contains]-(target), '
        '(ss)-[:Contains]->(pre:Synapse)-[:SynapsesTo]->(post:Synapse)<-[:Contains]-(ts) '
        f'WHERE source.bodyId IN [{s}] AND target.bodyId IN [{t}] AND edge.weight >= 1 '
        "AND pre.type = 'pre' AND post.type = 'post'" + confidence +
        ' WITH DISTINCT source, target, pre, post RETURN ' +
        ', '.join(f'{value} AS {name}' for value, name in zip(returns, COLUMNS)) +
        f' ORDER BY source_body_id, target_body_id, pre_synapse_id, post_synapse_id LIMIT {max_rows+1}')
    result, seen = [], set()
    for row in _table(query(cypher), COLUMNS, max_rows):
        try:
            ids = tuple(_integer(x) for x in row[:4])
            coordinates = tuple(_number(x) for x in row[4:10])
            confidences = tuple(None if x is None else _number(x) for x in row[10:])
            if (ids[0] not in sources or ids[1] not in targets or ids in seen
                    or any(x is not None and not 0 <= x <= 1 for x in confidences)
                    or (threshold and any(x is None or x <= threshold for x in confidences))):
                raise ValueError()
        except ValueError as exc:
            raise NeuPrintError('Invalid, duplicated or out-of-scope chemical contact; no locations accepted') from exc
        seen.add(ids)
        result.append(ChemicalContact(*ids, *coordinates, *confidences))
    return tuple(result), {'schema':'digifly-neuprint-contact-method-v1',
        'confidence_threshold':threshold, 'confidence_comparison':'strict_greater_when_nonzero',
        'confidence_source':'Meta.postHighAccuracyThreshold; official-client missing-property default0',
        'roi_filter':None, 'deduplication':'directed body and native synapse identity pair',
        'coordinate_units':'native_dataset_space', 'coordinates_rounded':False,
        'method_source':METHOD_SOURCE, 'threshold_source':THRESHOLD_SOURCE,
        'electrical_anatomy_inferred':False}


def fetch_updates(client, review, *, cancel, max_rows, max_bytes, on_bytes):
    """The same disjoint refresh rectangles as the cumulative pair graph."""
    from .neuprint import AcquisitionCancelled, NeuPrintError
    from .selected_connectivity import CHUNK
    participants, focus = review['participants'], review['focus']
    old = sorted(set(participants)-set(focus))
    result, seen, transferred, method = [], set(), 0, None
    def count(amount):
        nonlocal transferred
        transferred += amount
        if transferred > max_bytes:
            raise NeuPrintError('Cumulative contact byte limit exceeded; existing library unchanged')
        on_bytes(amount)
    def chunks(values):
        return (values[i:i+CHUNK] for i in range(0,len(values),CHUNK))
    for sources, targets in ((focus,participants),(old,focus)):
        for a in chunks(sources):
            for b in chunks(targets):
                if cancel.is_set():
                    raise AcquisitionCancelled('Contact download cancelled; existing library unchanged')
                rows, current = client.fetch_contact_sites_between(a,b,cancel=cancel,
                    max_rows=max_rows,max_bytes=max_bytes-transferred,on_bytes=count)
                if method is not None and current != method:
                    raise NeuPrintError('Contact confidence or coordinate policy changed within the download')
                method = current
                for row in rows:
                    key = row.to_row()[:4]
                    if (key in seen or row.source_body_id not in a or row.target_body_id not in b):
                        raise NeuPrintError('Repeated or out-of-scope chemical contact')
                    seen.add(key); result.append(row)
                    if len(result)>max_rows:
                        raise NeuPrintError('Cumulative contact row limit exceeded; existing library unchanged')
    return tuple(result), method


def stage_contacts(db, canonical, resource, payload, review, check_cancel):
    """Stage into the SAME SQLite graph; publish nothing on a failed census."""
    from contextlib import closing
    import csv
    import sqlite3
    from .pair_graph import sha
    from .swc_library import _plain_path
    info = payload['connectivity'].get('contact_sites') or {}
    requested = bool(review.get('include_contact_sites'))
    if bool(info.get('included')) != requested:
        raise ValueError('Reviewed contact download scope differs from the saved data')
    focus = set(review['focus']); participants = set(review['participants'])
    previous = {}
    if canonical.exists():
        from .data_library import MANIFEST_FILENAME
        previous = json.loads((canonical.parent.parent/MANIFEST_FILENAME).read_text())['connectivity']
    old_covered = set(previous.get('contact_covered_body_ids', []))
    if not requested and not old_covered:
        return {'contact_coordinates_in_file':False, 'contact_covered_body_ids':[]}
    db.execute('CREATE TABLE contacts ('+
        ','.join(f'{name} '+('INTEGER NOT NULL' if i<4 else 'REAL NOT NULL' if i<10 else 'REAL')
                 for i,name in enumerate(COLUMNS))+
        ',PRIMARY KEY(source_body_id,target_body_id,pre_synapse_id,post_synapse_id))')
    if old_covered:
        with closing(sqlite3.connect(canonical.as_uri()+'?mode=ro&immutable=1',uri=True)) as old:
            if [row[1] for row in old.execute('PRAGMA table_info(contacts)')] != list(COLUMNS):
                raise ValueError('Existing contact schema is invalid')
            for row in old.execute('SELECT '+','.join(COLUMNS)+' FROM contacts'):
                check_cancel()
                if row[0] not in focus and row[1] not in focus:
                    if row[0] not in participants or row[1] not in participants:
                        raise ValueError('Existing contacts fall outside the library coverage')
                    db.execute('INSERT INTO contacts VALUES ('+','.join('?' for _ in COLUMNS)+')',row)
    covered = participants if requested else old_covered-focus
    methods = list(previous.get('contact_methods', []))
    convention = previous.get('contact_coordinate_convention')
    if requested:
        records = [row for row in payload['files'] if row.get('data_role')=='chemical_contact_sites']
        if len(records)!=1 or records[0]['path']!=info.get('table_path'):
            raise ValueError('Contact download needs exactly one checked location table')
        source = resource.root/info['table_path']; _plain_path(source,resource.root)
        expected = records[0]['sha256']
        if sha(source)!=expected:
            raise ValueError('Chemical contact table changed before staging')
        incoming_convention = info.get('coordinate_convention')
        if convention is not None and convention!=incoming_convention:
            raise ValueError('Chemical contact coordinate conventions differ')
        convention = incoming_convention
        method = info.get('method')
        threshold = validate_method(method)
        if method not in methods:
            methods.append(method)
        count=0
        with source.open(newline='',encoding='utf-8') as stream:
            reader=csv.DictReader(stream)
            if reader.fieldnames!=list(COLUMNS):
                raise ValueError('Chemical contact columns differ from the declared schema')
            for raw in reader:
                check_cancel()
                ids=tuple(_integer(int(raw[name])) for name in COLUMNS[:4])
                xyz=tuple(_number(float(raw[name])) for name in COLUMNS[4:10])
                confidence=tuple(None if raw[name]=='' else _number(float(raw[name])) for name in COLUMNS[10:])
                if (ids[0] not in participants or ids[1] not in participants
                        or not ({ids[0],ids[1]} & focus)
                        or any(x is not None and not 0<=x<=1 for x in confidence)
                        or (threshold and any(x is None or x<=threshold for x in confidence))):
                    raise ValueError('Chemical contact lies outside its reviewed scope')
                try:
                    db.execute('INSERT INTO contacts VALUES ('+','.join('?' for _ in COLUMNS)+')',
                               (*ids,*xyz,*confidence))
                except sqlite3.IntegrityError as exc:
                    raise ValueError('Repeated chemical contact identity; existing data unchanged') from exc
                count+=1
                if count>250_000:
                    raise ValueError('Chemical contact storage row limit exceeded')
        if count!=info.get('row_count') or count!=records[0].get('row_count') or sha(source)!=expected:
            raise ValueError('Chemical contact file identity or census changed')
    # Coverage is not an inference from successful HTTP completion. Require
    # the complete site census to equal every covered source pair count.
    for a,b in db.execute('SELECT DISTINCT source_body_id,target_body_id FROM contacts'):
        if a not in covered or b not in covered:
            raise ValueError('Chemical contact endpoints are outside the checked location coverage')
    counts={(a,b):n for a,b,n in db.execute(
        'SELECT source_body_id,target_body_id,COUNT(*) FROM contacts GROUP BY source_body_id,target_body_id')}
    for a,b,total in db.execute('SELECT preId,postId,total_weight FROM pairs'):
        if a in covered and b in covered and counts.pop((a,b),0)!=total:
            raise ValueError(f'Chemical contact census differs from source pair count for {a} → {b}')
        counts.pop((a,b),None)
    if counts:
        raise ValueError('Chemical contacts have no corresponding directed pair')
    n=db.execute('SELECT COUNT(*) FROM contacts').fetchone()[0]
    if n>250_000:
        raise ValueError('Cumulative contact storage exceeds its safety limit')
    return {'contact_coordinates_in_file':True, 'contact_storage_schema':'digifly-selected-contact-sites-v1',
            'contact_rows':n, 'contact_covered_body_ids':sorted(covered),
            'contact_coordinate_convention':convention, 'contact_methods':methods,
            'electrical_anatomy_in_file':False}
