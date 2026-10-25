"""One bounded, cumulative directed-pair database per managed neuron library.

Source counts are observations, not conductances. The visible database grows;
internal immutable revisions keep saved circuits tied to their original bytes.
"""
from contextlib import closing
import csv
import json
from pathlib import Path
import shutil
import sqlite3

from .data_library import MANIFEST_FILENAME
from .pair_graph import sha
from .resource_profile import AccessMode, ResourceBinding, ResourceKind

SCHEMA = 'digifly-selected-connectivity-v1'
MAX_PARTICIPANTS = 5000
CHUNK = 500
COLUMNS = ('preId', 'preType', 'preInstance', 'preNt', 'postId', 'postType',
           'postInstance', 'postNt', 'total_weight')


def _ids(values):
    result = []
    for value in values:
        if isinstance(value, bool) or not str(value).isdigit() or not 0 <= int(value) < 2**63:
            raise ValueError('Connectivity body IDs must be nonnegative signed 64-bit integers')
        result.append(int(value))
    return sorted(set(result))


def review_connectivity(profile, server, dataset, selected, *, include_contact_sites=False):
    """Read only managed-library IDs, not every linked external SWC collection."""
    from .swc_library import library_root, _plain_path
    root = library_root(profile, server, dataset)
    _plain_path(root, profile.managed_data_root)
    manifest = root / MANIFEST_FILENAME
    _plain_path(manifest, profile.managed_data_root)
    saved = json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    if saved and (saved.get('server'), saved.get('dataset')) != (server.rstrip('/'), dataset):
        raise ValueError('Connectivity library identity differs from the selected server/release')
    selected = _ids(selected)
    participants = _ids([*selected, *(row['body_id'] for row in saved.get('files', []) if 'body_id' in row)])
    if not selected or len(selected) > CHUNK or len(participants) > MAX_PARTICIPANTS:
        raise ValueError(f'Selected connectivity supports up to {MAX_PARTICIPANTS:,} app-managed neurons '
                         'and 500 selected neurons per download. Use the separate full-release connectivity '
                         'feature for a larger graph; no existing data was changed')
    graph = root / 'connectivity/connectivity.sqlite'
    _plain_path(graph, profile.managed_data_root)
    previous = saved.get('connectivity', {})
    expected = previous.get('source_sha256', '')
    if graph.exists() != bool(expected) or (expected and sha(graph) != expected):
        raise ValueError('The shared connectivity file changed or is untracked; review it before updating')
    covered = _ids(previous.get('covered_body_ids', []))
    if not set(covered).issubset(participants):
        raise ValueError('Connectivity coverage does not match the managed neuron library')
    focus = sorted(set(selected) | (set(participants) - set(covered)))
    if include_contact_sites:
        contact_covered=set(previous.get('contact_covered_body_ids', []))
        if not contact_covered.issubset(participants):
            raise ValueError('Chemical contact coverage differs from the managed library')
        focus=sorted(set(focus) | (set(participants)-contact_covered))
    return {'schema': SCHEMA, 'server': server.rstrip('/'), 'dataset': dataset,
            'library_sha256': sha(manifest) if manifest.exists() else '',
            'previous_sha256': expected, 'participants': participants, 'focus': focus,
            'selected': selected, **({'include_contact_sites':True} if include_contact_sites else {})}


def require_current_review(profile, server, dataset, selected, review):
    current = review_connectivity(profile, server, dataset, selected,
        include_contact_sites=bool(review and review.get('include_contact_sites')))
    if review != current:
        raise ValueError('The connectivity library changed after review; preview and confirm again')
    return current


def fetch_updates(client, review, *, cancel, max_rows, max_bytes, on_bytes):
    """Disjoint rectangles cover both cross directions without repeated pairs."""
    from .neuprint import AcquisitionCancelled, NeuPrintError
    participants, focus = review['participants'], review['focus']
    old = sorted(set(participants) - set(focus))
    transferred, result, seen = 0, [], set()
    def count(amount):
        nonlocal transferred
        transferred += amount
        if transferred > max_bytes:
            raise NeuPrintError('Cumulative connectivity exceeded its total byte limit; existing graph unchanged')
        on_bytes(amount)
    def chunks(values):
        return (tuple(values[i:i + CHUNK]) for i in range(0, len(values), CHUNK))
    for sources, targets in ((focus, participants), (old, focus)):
        for a in chunks(sources):
            for b in chunks(targets):
                if cancel.is_set():
                    raise AcquisitionCancelled('Connectivity download cancelled; existing graph unchanged')
                rows = client.fetch_connectivity_between(a, b, cancel=cancel,
                    max_rows=max_rows, max_bytes=max_bytes - transferred, on_bytes=count)
                for row in rows:
                    key = (row.source_body_id, row.target_body_id)
                    if key in seen or key[0] not in a or key[1] not in b:
                        raise NeuPrintError('Unexpected or repeated directed pair in connectivity response')
                    seen.add(key)
                    result.append(row)
                    if len(result) > max_rows:
                        raise NeuPrintError('Cumulative connectivity exceeded its total row limit; existing graph unchanged')
    return tuple(result)


def stage_graph(root, history, resource, payload, check_cancel):
    """Build and verify a revision before publishing any shared database bytes."""
    from .swc_library import _plain_path, _write_json_atomic
    info = payload['connectivity']
    review = info['review']
    participants, focus = set(review['participants']), set(review['focus'])
    if not focus.issubset(participants):
        raise ValueError('Invalid connectivity refresh coverage')
    canonical = root / 'connectivity/connectivity.sqlite'
    folder = history / 'connectivity'
    folder.mkdir()
    snapshot = folder / 'connectivity.sqlite'
    annotations = {int(row['body_id']): row for row in payload['neurons']}
    previous_file = root / MANIFEST_FILENAME
    previous = json.loads(previous_file.read_text(encoding='utf-8')) if previous_file.exists() else {}
    for row in previous.get('files', []):
        if 'body_id' in row:
            annotations.setdefault(int(row['body_id']), row)
    with closing(sqlite3.connect(snapshot)) as db, db:
        db.execute('CREATE TABLE pairs (preId INTEGER NOT NULL, preType TEXT, preInstance TEXT, preNt TEXT, '
                   'postId INTEGER NOT NULL, postType TEXT, postInstance TEXT, postNt TEXT, total_weight INTEGER NOT NULL, '
                   'PRIMARY KEY(preId,postId))')
        if review['previous_sha256']:
            with closing(sqlite3.connect(canonical.as_uri() + '?mode=ro&immutable=1', uri=True)) as old:
                for row in old.execute('SELECT ' + ','.join(COLUMNS) + ' FROM pairs ORDER BY preId,postId'):
                    check_cancel()
                    if row[0] not in focus and row[4] not in focus:
                        if row[0] not in participants or row[4] not in participants:
                            raise ValueError('Existing connectivity contains an endpoint outside its coverage')
                        db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?)', row)
        source = resource.root / info['table_path']
        _plain_path(source, resource.root)
        count = 0
        with source.open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != ['source_body_id', 'target_body_id', 'weight']:
                raise ValueError('Invalid cumulative connectivity columns')
            for row in reader:
                check_cancel()
                a, b = _ids([row['source_body_id']]), _ids([row['target_body_id']])
                a, b = a[0], b[0]
                weight = row['weight']
                if not weight.isdigit() or not 0 <= int(weight) < 2**63:
                    raise ValueError('Connectivity counts must be nonnegative signed 64-bit integers')
                if a not in participants or b not in participants or not ({a, b} & focus):
                    raise ValueError('Connectivity row falls outside the reviewed refresh scope')
                aa, bb = annotations.get(a, {}), annotations.get(b, {})
                if any(len(str(meta.get(name) or '')) > 1024
                       for meta in (aa, bb) for name in ('type', 'instance')):
                    raise ValueError('Connectivity annotations exceed the 1,024-character safety limit; original download retained')
                try:
                    db.execute('INSERT INTO pairs VALUES (?,?,?,?,?,?,?,?,?)',
                        (a, aa.get('type'), aa.get('instance'), None, b, bb.get('type'), bb.get('instance'), None, int(weight)))
                except sqlite3.IntegrityError as exc:
                    raise ValueError('Repeated directed pair; no counts were added twice') from exc
                count += 1
                if count % 1024 == 0 and (db.execute('PRAGMA page_count').fetchone()[0] *
                        db.execute('PRAGMA page_size').fetchone()[0] > 128 * 1024**2):
                    raise ValueError('Shared connectivity exceeded its 128 MiB storage limit')
        if count != info['row_count'] or count > info['row_limit']:
            raise ValueError('Connectivity row census differs from its reviewed download')
        from .neuprint_contacts import stage_contacts
        contact_metadata=stage_contacts(db,canonical,resource,payload,review,check_cancel)
        if db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]>128*1024**2:
            raise ValueError('Shared connectivity/contact database exceeds its128MiB storage limit')
        rows, minimum, maximum, self_pairs = db.execute(
            'SELECT COUNT(*),MIN(total_weight),MAX(total_weight),'
            'COALESCE(SUM(preId=postId),0) FROM pairs').fetchone()
        total = sum(row[0] for row in db.execute('SELECT total_weight FROM pairs'))
        if rows > 250000:
            raise ValueError('Shared graph exceeded its 250,000-pair safety limit; existing graph unchanged')
        represented = db.execute('SELECT COUNT(*) FROM (SELECT preId FROM pairs UNION SELECT postId FROM pairs)').fetchone()[0]
        if db.execute('PRAGMA integrity_check').fetchone() != ('ok',):
            raise ValueError('Shared connectivity database integrity check failed')
    check_cancel()
    digest = sha(snapshot)
    manifest = {'schema': 'digifly-weighted-pair-graph-v1', 'storage_schema': SCHEMA,
        'source_path': str(snapshot), 'source_sha256': digest, 'index': snapshot.name,
        'index_sha256': digest, 'dataset': review['dataset'], 'server': review['server'],
        'rows': rows, 'represented_neurons': represented, 'min_weight': minimum, 'max_weight': maximum,
        'total_weight': total or 0, 'self_pairs': self_pairs, 'columns': list(COLUMNS),
        'covered_body_ids': sorted(participants), 'refreshed_body_ids': sorted(focus),
        'fetched_at': payload['fetched_at'], 'previous_sha256': review['previous_sha256'],
        'source_scope': 'Directed chemical connection counts among app-managed saved neurons; '
                        'new and selected endpoints refreshed in both directions. Not a whole-connectome graph.',
        'missing_pair_meaning': 'Absent only within covered_body_ids; all other pairs unknown',
        'weight_units': 'source connection count; not conductance',
        'morphologies_in_file': False, **contact_metadata,
        'simulation_policy': 'not_selected_by_import'}
    _write_json_atomic(folder / 'manifest.json', manifest)
    # Independent bytes: changing the visible file cannot mutate an old circuit's revision.
    candidate = history / 'incoming-connectivity.sqlite'
    shutil.copyfile(snapshot, candidate)
    if sha(candidate) != digest:
        raise ValueError('Connectivity changed while staging')
    return candidate, canonical, manifest


def graph_binding(identity, canonical, manifest):
    return ResourceBinding('selected-connectivity-' + identity, ResourceKind.DATA_SOURCE, str(canonical),
        AccessMode.READ_ONLY, manifest['dataset'] + ' · saved-neuron connectivity', False,
        {'format': 'digifly-weighted-pair-graph-v1', 'storage_schema': SCHEMA,
         'dataset': manifest['dataset'], 'server': manifest['server'],
         'index': manifest['source_path'], 'snapshot_path': manifest['source_path'],
         'source_sha256': manifest['source_sha256'], 'covered_body_ids': manifest['covered_body_ids'],
         'source_scope': manifest['source_scope']})
