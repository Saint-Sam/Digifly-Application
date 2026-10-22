"""Bounded-memory data conversion; no simulator or morphology dependency."""
import json
from pathlib import Path
import sqlite3
import shutil

from digifly_app.workers.pair_graph_import_worker import build_index, COLUMNS, sha


def convert_connectivity(source, destination, *, dataset='', server='', scope='', verified_export=False, cancel=lambda: False):
    import duckdb
    import pyarrow as pa
    import pyarrow.ipc as ipc
    import pyarrow.parquet as pq
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or source.is_symlink() or destination.exists() or source.is_relative_to(destination):
        raise ValueError('Choose a regular connectivity file and a new conversion folder')
    available_parent = destination.parent
    while not available_parent.exists():
        available_parent = available_parent.parent
    required = max(512 * 1024**2, source.stat().st_size * 12 + 256 * 1024**2)
    if shutil.disk_usage(available_parent).free < required:
        raise ValueError(f'Not enough free disk for conversion and lookup: allow {required/1024**3:.1f} GiB; existing connectivity unchanged')
    before = sha(source)
    from .bundled_pair_graph import PAIR_SHA256, PAIR_DATASET
    if before == PAIR_SHA256 and dataset != PAIR_DATASET:
        raise ValueError('The included MANC pair file belongs to MANC v1.2.1, not this release')
    if source.suffix.lower() == '.parquet':
        metadata = pq.read_schema(source).metadata or {}
        for key, expected in ((b'digifly.dataset', dataset), (b'digifly.server', server)):
            if metadata.get(key) and metadata[key].decode() != expected:
                raise ValueError('Connectivity file declares a different release or server')
        scope = scope or metadata.get(b'digifly.scope', b'').decode()
    destination.mkdir()
    db = duckdb.connect(':memory:')
    db.execute("SET threads=1")
    db.execute("SET memory_limit='512MB'")
    db.execute('SET temp_directory=?', [str(destination / 'scratch')])
    try:
        if source.suffix.lower() in {'.feather', '.arrow'}:
            # mmap + record batches avoid materializing a whole connectome.
            with pa.memory_map(str(source), 'r') as mapped:
                reader = ipc.open_file(mapped)
                intermediate = destination / 'source-columns.parquet'
                with pq.ParquetWriter(intermediate, reader.schema) as writer:
                    for i in range(reader.num_record_batches):
                        if cancel():
                            raise InterruptedError('Conversion cancelled')
                        writer.write_batch(reader.get_batch(i))
            db.read_parquet(str(intermediate)).create_view('raw')
        elif source.suffix.lower() == '.parquet':
            db.read_parquet(str(source)).create_view('raw')
        elif source.suffix.lower() == '.csv':
            db.read_csv(str(source), header=True, all_varchar=True).create_view('raw')
        else:
            raise ValueError('Supported connectivity files: Parquet, Feather/Arrow IPC, CSV')
        schema = {name: kind for name, kind, *_ in db.execute('DESCRIBE raw').fetchall()}
        def field(names):
            found = [name for name in names if name in schema]
            if len(found) != 1:
                raise ValueError('Connectivity needs one unambiguous column from: ' + ', '.join(names))
            return found[0]
        pre = field(('preId', 'body_pre', 'bodyId_pre', 'sourceBodyId'))
        post = field(('postId', 'body_post', 'bodyId_post', 'targetBodyId'))
        weight = field(('total_weight', 'weight', 'syn_count'))
        integers = {'BIGINT', 'INTEGER', 'SMALLINT', 'TINYINT', 'UBIGINT', 'UINTEGER', 'USMALLINT', 'UTINYINT'}
        for name in (pre, post, weight):
            kind = schema[name]
            if kind not in integers and not (source.suffix.lower() == '.csv' and kind == 'VARCHAR'):
                raise ValueError('IDs and weights must be integers, never floating point: ' + name)
            if kind == 'VARCHAR' and db.execute(f'SELECT COUNT(*) FROM raw WHERE "{name}" IS NULL OR NOT regexp_full_match("{name}", \'-?[0-9]+\')').fetchone()[0]:
                raise ValueError('CSV IDs and weights must be non-null integer text: ' + name)
        expressions = [f'CAST("{pre}" AS BIGINT) AS preId', f'CAST("{post}" AS BIGINT) AS postId',
                       f'CAST("{weight}" AS BIGINT) AS total_weight']
        for name in COLUMNS:
            if name in {'preId', 'postId', 'total_weight'}:
                continue
            if name in schema and schema[name] != 'VARCHAR':
                raise ValueError('Annotation columns must be strings or absent: ' + name)
            expressions.append(f'CAST("{name}" AS VARCHAR) AS "{name}"' if name in schema else f'CAST(NULL AS VARCHAR) AS "{name}"')
        extras = [f'"{name.replace(chr(34), chr(34)*2)}"' for name in schema if name not in COLUMNS and name not in {pre, post, weight}]
        db.execute('CREATE VIEW normalized AS SELECT ' + ','.join(expressions + extras) + ' FROM raw')
        if cancel():
            raise InterruptedError('Conversion cancelled')
        output = destination / 'connectivity.parquet'
        reader = db.execute('SELECT * FROM normalized').fetch_record_batch(65536)
        metadata = {b'digifly.dataset': dataset.encode(), b'digifly.server': server.encode(),
                    b'digifly.scope': scope.encode()}
        with pq.ParquetWriter(output, reader.schema.with_metadata(metadata), compression='zstd') as writer:
            for batch in reader:
                if cancel():
                    raise InterruptedError('Conversion cancelled')
                writer.write_batch(batch)
        db.close()
        result = build_index(output, destination / 'index')
        if sha(source) != before:
            raise ValueError('Input changed during conversion; no registration')
        result.update(dataset=dataset or 'user-supplied; release not declared', server=server,
            release=dataset, release_verification='official_generation_pinned' if verified_export else 'user_associated_not_file_verified',
            source_scope=scope or 'All supplied rows retained; original filters unknown',
            raw_source_path=str(source), raw_source_sha256=before,
            missing_annotations=[n for n in COLUMNS if n not in schema and n not in {'preId','postId','total_weight'}])
        manifest = destination / 'index/manifest.json'
        manifest.write_text(json.dumps(result, indent=2) + '\n')
        # Intermediate duplicates are disposable; raw source, ready Parquet,
        # index and details remain. Never delete a user-selected source.
        intermediate = destination / 'source-columns.parquet'
        if intermediate.exists():
            intermediate.unlink()
        return {'path': str(output), 'index': str(destination / 'index/pairs.sqlite'),
                'sha256': result['source_sha256'], 'rows': result['rows'],
                'represented_neurons': result['represented_neurons'], 'dataset': dataset,
                'server': server, 'scope': result['source_scope']}
    finally:
        db.close()


def run_task(request, response_file=None):
    """A separate app-owned process; progress has no credentials or SWC data."""
    def emit(phase, received, total):
        print(json.dumps({'phase': phase, 'received': received, 'total': total}), flush=True)
    if request['kind'] == 'download':
        from .connectivity_download import run_download
        result = run_download(request, emit)
    elif request['kind'] == 'import':
        from .connectivity_download import connectivity_root
        import uuid
        root = connectivity_root(request['managed_root'], request['dataset'])
        root.mkdir(parents=True, exist_ok=True)
        result = convert_connectivity(request['source'], root / ('import-' + uuid.uuid4().hex),
            dataset=request['dataset'], server=request['server'])
    else:
        raise ValueError('Unknown connectivity action')
    message = {'result': result}
    # Windows GUI executables need not expose a stdout console. The same
    # unique request has a separate result file on every platform.
    if response_file is not None:
        with Path(response_file).open('x', encoding='utf-8') as stream:
            json.dump(message, stream)
    print(json.dumps(message), flush=True)
    return 0
