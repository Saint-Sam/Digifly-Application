"""Version-pinned public connectivity exports, independent of SWC downloads."""
from dataclasses import dataclass, asdict, replace
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
import urllib.parse
import urllib.request
import uuid

SERVER = 'https://neuprint.janelia.org'


@dataclass(frozen=True)
class ConnectivityExport:
    dataset: str
    bucket: str = ''
    object_name: str = ''
    size: int = 0
    md5: str = ''
    generation: str = ''
    scope: str = ''
    info: str = ''
    unavailable: str = ''

    @property
    def available(self):
        return bool(self.object_name and self.generation and self.md5 and self.size)

    @property
    def url(self):
        return ('https://storage.googleapis.com/' + self.bucket + '/' +
                urllib.parse.quote(self.object_name, safe='/') + '?generation=' + self.generation)


EXPORTS = {
    'male-cns:v1.0': ConnectivityExport('male-cns:v1.0', 'flyem-male-cns',
        'v1.0/connectome-data/flat-connectome/connectome-weights-male-cns-v1.0-minconf-0.5.feather',
        1051241946, '8w6dzKJc/QIb8eez2XVZng==', '1780494887545976',
        'All segment pairs in the published graph; synapse confidence ≥0.5. Not restricted to Traced neurons.',
        'https://male-cns.janelia.org/download/'),
    'male-cns:v0.9': ConnectivityExport('male-cns:v0.9', 'flyem-male-cns',
        'v0.9/connectome-data/flat-connectome/connectome-weights-male-cns-v0.9-minconf-0.5.feather',
        1102276498, 'BBkGs3ssP1EmdUenxtXMpw==', '1759286829125072',
        'All segment pairs in the published graph; synapse confidence ≥0.5. Not restricted to Traced neurons.',
        'https://github.com/flyconnectome/2025malecns/blob/main/README_RELEASE_BUCKET.md'),
    'manc:v1.0': ConnectivityExport('manc:v1.0', 'flyem-manc-exports',
        'v1.0/manc-traced-adjacencies-v1.0/traced-connections.csv',
        75262163, 'xUPcmgw2fZ98iJAdiS14oA==', '1685678227570342',
        'Published Traced-neuron adjacency table only; not all segments. No additional weight filter applied by Digifly.',
        'https://www.janelia.org/project-team/flyem/manc-connectome'),
    'optic-lobe:v1.1': ConnectivityExport('optic-lobe:v1.1', 'flyem-optic-lobe',
        'v1.1/optic-lobe-v1.1-flat-connectome/connectome-weights-2024-09-11-a7d912-minconf-0.5-ol.feather',
        152653330, 'Ymmj4g+q2hsjeu/c4jTSTQ==', '1727146314674506',
        'Published optic-lobe graph; synapse confidence ≥0.5. Includes non-primary ROI connections in that export.',
        'https://www.janelia.org/project-team/flyem/optic-lobe'),
    'optic-lobe:v1.0.1': ConnectivityExport('optic-lobe:v1.0.1', 'flyem-optic-lobe',
        'v1.0/optic-lobe-v1.0.1-flat-connectome/connectome-weights-2023-10-31-27a052-minconf-0.5-ol.feather',
        151012018, 'vWNxDXfzaDtUDGzV+HWSxA==', '1724359677853375',
        'Published optic-lobe v1.0.1 graph; synapse confidence ≥0.5. Includes non-primary ROI connections in that export.',
        'https://www.janelia.org/project-team/flyem/optic-lobe'),
}
for _dataset, _info in (
        ('manc:v1.2.1', 'https://www.janelia.org/project-team/flyem/manc-connectome'),
        ('manc:v1.2.3', 'https://www.janelia.org/project-team/flyem/manc-connectome'),
        ('hemibrain:v1.2.1', 'https://www.janelia.org/project-team/flyem/hemibrain'),
        ('mushroombody', 'https://neuprint.janelia.org/')):
    EXPORTS[_dataset] = ConnectivityExport(_dataset, info=_info,
        unavailable='No verified bulk export for this exact release is configured. Import a matching file or consult the official source; another version will not be substituted.')


def export_for(dataset):
    return EXPORTS.get(dataset, ConnectivityExport(dataset, unavailable='Unknown connectome release'))


def connectivity_root(managed_root, dataset):
    if dataset not in EXPORTS:
        raise ValueError('Unknown connectome release')
    from .swc_library import dataset_library_root
    return dataset_library_root(Path(managed_root), SERVER, dataset) / 'connectivity'


def free_space_required(export):
    # Raw export + Parquet + SQLite index + temporary conversion/sort files.
    return max(512 * 1024**2, export.size * 12 + 256 * 1024**2)


def _no_links(path):
    for item in (path, *path.parents):
        if item.is_symlink() or (item.exists() and
                getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Connectivity attempt must not traverse a file or folder link')


def attempt_path(request):
    """Only a direct, UUID-named child of the exact release's managed root."""
    kind, token = request.get('kind'), request.get('attempt_id', '')
    if kind not in {'download', 'import'} or not re.fullmatch('[0-9a-f]{32}', token):
        raise ValueError('Invalid connectivity attempt identity')
    if request.get('server', SERVER) != SERVER:
        raise ValueError('Connectivity attempt server differs')
    path = connectivity_root(request['managed_root'], request['dataset']) / (kind + '-' + token)
    _no_links(path)
    return path


def _attempt_owner(request):
    return {key: request[key] for key in ('kind', 'dataset', 'attempt_id')} | {
        'schema': 'digifly-connectivity-attempt-v1', 'server': SERVER}


def checked_attempt(request):
    path = attempt_path(request)
    marker = path / 'attempt.json'
    _no_links(marker)
    if not path.is_dir() or json.loads(marker.read_text(encoding='utf-8')) != _attempt_owner(request):
        raise ValueError('Connectivity attempt ownership differs; cleanup refused')
    return path


def prepare_attempt(request):
    request = dict(request)
    if 'attempt_id' in request:
        checked_attempt(request)
        return request
    request['attempt_id'] = uuid.uuid4().hex
    path = attempt_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir()  # Exclusive: an existing generation can never become this attempt.
    with (path / 'attempt.json').open('x', encoding='utf-8') as stream:
        json.dump(_attempt_owner(request), stream)
    return request


def remove_cancelled_attempt(request, protected=()):
    """Call only after the owning worker stopped (or unwound cooperatively)."""
    path = attempt_path(request)
    if not path.exists():
        return
    checked_attempt(request)
    if (path / 'registered.json').exists():
        raise ValueError('Completed connectivity must not be removed')
    for name in (*protected, request.get('source', '')):
        if name and Path(name).resolve().is_relative_to(path.resolve()):
            raise ValueError('An original input or selected graph is inside this attempt; cleanup refused')
    # Refuse symlinks/reparse points and unusual files before deleting anything.
    # rmtree unlinks rather than following a subsequently substituted symlink.
    for parent, directories, files in os.walk(path, followlinks=False):
        for name in (*directories, *files):
            item = Path(parent) / name
            _no_links(item)
            mode = item.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ValueError('Unexpected connectivity attempt file; cleanup refused')
    shutil.rmtree(path)


def checked_download(export, destination, progress=lambda *_: None, cancel=lambda: False, opener=None):
    if not export.available:
        raise ValueError(export.unavailable)
    if not destination.parent.is_dir() or destination.exists() or destination.is_symlink():
        raise ValueError('Download destination must be a new file in the reviewed folder')
    request = urllib.request.Request(export.url, headers={'User-Agent': 'Digifly-connectivity-download', 'Accept-Encoding': 'identity'})
    digest, checksum, count = hashlib.sha256(), hashlib.md5(), 0
    with (opener or urllib.request.urlopen)(request, timeout=30) as response:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != 'https' or final.hostname != 'storage.googleapis.com':
            raise ValueError('Connectivity download redirected outside official public storage')
        length = response.headers.get('Content-Length')
        if length is not None and int(length) != export.size:
            raise ValueError('Official file size changed; download refused')
        generation = response.headers.get('x-goog-generation')
        if generation is not None and generation != export.generation:
            raise ValueError('Official object generation changed; download refused')
        with destination.open('xb') as stream:
            while True:
                if cancel():
                    raise InterruptedError('Download cancelled; existing connectivity unchanged')
                block = response.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > export.size:
                    raise ValueError('Official download exceeded its reviewed size')
                stream.write(block)
                digest.update(block)
                checksum.update(block)
                progress('Downloading', count, export.size)
    if count != export.size or base64.b64encode(checksum.digest()).decode() != export.md5:
        raise ValueError('Downloaded file checksum or size differs; no registration')
    return {'sha256': digest.hexdigest(), 'bytes': count, 'md5': export.md5}


def run_download(request, progress=lambda *_: None, cancel=lambda: False, opener=None):
    """Worker transaction: new generation only; never replace an existing file."""
    from .connectivity_conversion import convert_connectivity
    dataset = request['dataset']
    export = export_for(dataset)
    if not export.available:
        raise ValueError(export.unavailable)
    root = connectivity_root(request['managed_root'], dataset)
    if root.is_symlink() or any(p.is_symlink() for p in root.parents):
        raise ValueError('Connectivity download destination must not traverse a folder link')
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < free_space_required(export):
        raise ValueError(f'Not enough free disk: allow {free_space_required(export)/1024**3:.1f} GiB for this export, conversion and index; existing data unchanged')
    request = prepare_attempt(dict(request, kind='download'))
    destination = checked_attempt(request)
    raw = destination / Path(export.object_name).name
    try:
        downloaded = checked_download(export, raw, progress, cancel, opener)
        if cancel():
            raise InterruptedError('Cancelled before conversion')
        progress('Converting and indexing', 0, 0)
        result = convert_connectivity(raw, destination / 'ready', dataset=dataset,
            server=SERVER, scope=export.scope, verified_export=True, cancel=cancel)
        if cancel():
            raise InterruptedError('Cancelled before registration')
        details = {'schema': 'digifly-connectivity-download-v1', 'dataset': dataset,
            'server': SERVER, 'source': asdict(export), 'download': downloaded,
            'ready': result, 'completed_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        (destination / 'download-details.json').write_text(json.dumps(details, indent=2) + '\n')
        return {**result, 'details': str(destination / 'download-details.json')}
    except InterruptedError:
        remove_cancelled_attempt(request)
        raise
    except Exception:
        # Preserve partial bytes and diagnostic state, never label them ready.
        (destination / 'incomplete.json').write_text(json.dumps({'dataset': dataset, 'ready': False}) + '\n')
        raise
