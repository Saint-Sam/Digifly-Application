"""Shared read-only voltage table and disposable, lossless indexed display cache.

Saved runs are never changed. Cache payloads are checked against the source and
their own complete digests. The parser runs once; traces and replay share views
of the same float64 samples. Standard library only, including legacy CSV/gzip.
"""
from __future__ import annotations

from array import array
from collections import OrderedDict
from collections.abc import Mapping
import csv
from dataclasses import dataclass, field
import hashlib
import json
from math import isfinite
import mmap
import os
from pathlib import Path
import shutil
import sys
import tempfile
from weakref import WeakValueDictionary
from typing import Callable

# Keep the standard library C JSON reader in standalone apps too. Nuitka's
# default optional-import policy otherwise leaves only its slow fallback.
try:
    import _json as _json_accelerator
except ImportError:
    _json_accelerator = None

from digifly_app.workers.result_storage import (
    MAX_PLAYBACK_VOLTAGE_SAMPLES, SampleSeries, open_result_text, result_path,
)

SCHEMA = 'digifly-voltage-display-v2'
REPLAY_BINDING_SCHEMA = 'digifly-replay-bindings-v1'
CACHE_BYTES = 2 * 1024**3
RESERVE_BYTES = 2 * 1024**3
WINDOWS_FILE_CHECKS = sys.platform == 'win32'


class LoadCancelled(Exception):
    """An interrupted display load is not a failed simulation."""


def _check(cancelled):
    if cancelled():
        raise LoadCancelled('Result loading cancelled; saved data is unchanged')


def _digest(path, cancelled):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(1024 * 1024):
            _check(cancelled)
            h.update(block)
    return h.hexdigest()


def _stamp(path):
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns,
            stat.st_dev, stat.st_ino]


def _check_source_bytes(path, digest, cancelled):
    # Windows can give a same-length, rapid rewrite the same stat timestamps.
    # Keep the request's digest check even when that metadata appears unchanged.
    if WINDOWS_FILE_CHECKS and _digest(path, cancelled) != digest:
        raise ValueError('Saved voltage changed during loading; open the completed result again')


class MappedTraceMetadata:
    """One set of primitive display columns, not a container for every row."""
    __slots__ = ('columns', '_frozen')

    def __init__(self):
        self.columns = tuple([] for _ in range(7))
        self._frozen = False

    def append(self, values):
        if self._frozen:
            raise ValueError('Prepared trace metadata is read-only')
        index = len(self.columns[0])
        for column, value in zip(self.columns, values):
            column.append(value)
        return index

    def freeze(self):
        if not self._frozen:
            self.columns = tuple(tuple(column) for column in self.columns)
            self._frozen = True


class MappedSamples:
    """Shared replay/plot record backed by read-only float64 views.

    Display labels bind once, while the preparation worker owns this record.
    Shared primitive columns avoid another metadata container per trace.
    A different later label request must use its own plot record instead of
    mutating a record that another view may already be displaying.
    """
    __slots__ = ('times', 'values', 'unique', 'bounds', '_trace_metadata', '_trace_index', '__weakref__')

    def __init__(self, times, values, unique, bounds):
        self.times, self.values, self.unique, self.bounds = times, values, unique, bounds
        self._trace_metadata = None
        self._trace_index = -1

    def bind_trace_metadata(self, owner, key, label, condition, repetition, neuron_id,
                            color, compartment_id):
        metadata = key, label, condition, repetition, neuron_id, color, compartment_id
        if self._trace_metadata is None:
            self._trace_index = owner.append(metadata)
            self._trace_metadata = owner
            return True
        return all(self._trace_field(index) == value for index, value in enumerate(metadata))

    def bind_prepared_trace_metadata(self, owner, index):
        if self._trace_metadata is None:
            self._trace_metadata, self._trace_index = owner, index
            return True
        if self._trace_metadata is owner and self._trace_index == index:
            return True
        return all(self._trace_field(field) == owner.columns[field][index]
                   for field in range(7))

    def _trace_field(self, index):
        if self._trace_metadata is None:
            raise AttributeError('Plot metadata has not been bound')
        return self._trace_metadata.columns[index][self._trace_index]

    @property
    def key(self): return self._trace_field(0)

    @property
    def label(self): return self._trace_field(1)

    @property
    def condition(self): return self._trace_field(2)

    @property
    def repetition(self): return self._trace_field(3)

    @property
    def neuron_id(self): return self._trace_field(4)

    @property
    def color(self): return self._trace_field(5)

    @property
    def compartment_id(self): return self._trace_field(6)

    @property
    def times_ms(self): return self.times

    @property
    def voltages_mV(self): return self.values

    @property
    def value_bounds(self): return self.bounds

    def __len__(self):
        return len(self.times)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(zip(self.times[index], self.values[index]))
        return self.times[index], self.values[index]

    def __iter__(self):
        return zip(self.times, self.values)

    def __eq__(self, other):
        fields = ('key','label','condition','repetition','neuron_id','color',
                  'compartment_id','value_bounds')
        if self._trace_metadata is None or not all(hasattr(other,name) for name in fields):
            return NotImplemented
        if any(getattr(self,name) != getattr(other,name) for name in fields):
            return False
        for name in ('times_ms','voltages_mV'):
            left,right = getattr(self,name),getattr(other,name)
            if left is right:
                continue
            if len(left) != len(right) or any(a != b for a,b in zip(left,right)):
                return False
        return True


class MappedSeries(Mapping):
    """Primitive row index over one checked mapping; samples materialize on use.

    A bounded recent window plus weak references preserves identity for rows
    held by a view, without retaining an object/memoryview for every trace.
    Values, clocks, ordering and the on-disk cache format are unchanged.
    """
    RECENT_ROWS = 128

    def __init__(self, flat, clocks, *, payload_sha256='', clock_blocks=(), cache_files=(), cache_stamps=()):
        self.flat = flat
        self.clocks = tuple(clocks)
        self.payload_sha256 = payload_sha256
        self.clock_blocks = tuple(tuple(block) for block in clock_blocks)
        self.cache_files = tuple(cache_files)
        self.cache_stamps = tuple(tuple(stamp) for stamp in cache_stamps)
        self.keys_in_order = []
        self._lookup = {}
        self.offsets = array('Q')
        self.clock_ids = array('I')
        self.unique = array('b')
        self.bounds = array('d')
        self._live = WeakValueDictionary()
        self._recent = OrderedDict()

    def add(self, key, clock_id, offset, unique, bounds):
        if len(bounds) != 2:
            raise ValueError('Invalid display cache value bounds')
        row = len(self.keys_in_order)
        self._lookup[key] = row
        self.keys_in_order.append(key)
        self.offsets.append(offset // 8)
        self.clock_ids.append(clock_id)
        self.unique.append(bool(unique))
        self.bounds.extend(bounds)

    def __len__(self): return len(self.keys_in_order)

    def __iter__(self): return iter(self.keys_in_order)

    def __contains__(self, key): return key in self._lookup

    def __getitem__(self, key): return self.row(self._lookup[key])

    def row_index(self, key): return self._lookup[key]

    @property
    def materialized_count(self): return len(self._live)

    def cache_reference_unchanged(self):
        if not self.cache_files or not self.cache_stamps:
            return False
        try:
            return self.cache_stamps == tuple(tuple(_stamp(path)) for path,_ in self.cache_files)
        except OSError:
            return False

    def verify_cache_identity(self, cancelled):
        if not self.cache_reference_unchanged():
            return False
        try:
            for path,expected in self.cache_files:
                if _digest(path,cancelled) != expected:
                    return False
            return self.cache_reference_unchanged()
        except OSError:
            return False

    def row(self, index):
        item = self._live.get(index)
        if item is None:
            clock = self.clocks[self.clock_ids[index]]
            offset = self.offsets[index]
            item = MappedSamples(clock, self.flat[offset:offset+len(clock)],
                                 bool(self.unique[index]),
                                 (self.bounds[2*index],self.bounds[2*index+1]))
            self._live[index] = item
        self._recent[index] = item
        self._recent.move_to_end(index)
        if len(self._recent) > self.RECENT_ROWS:
            self._recent.popitem(last=False)
        return item


@dataclass
class VoltageTable:
    series: dict = field(default_factory=dict)
    clocks: dict = field(default_factory=dict)
    sample_count: int = 0
    has_compartments: bool = False
    morphology_error: str = ''
    cache_hit: bool = False
    cache_note: str = ''
    # These retain the read-only file mapping; views themselves also own it.
    mapping: object = None


def _parse(path, cancelled, progress, maximum_samples):
    table = VoltageTable()
    previous = None
    samples = None
    with open_result_text(path) as stream:
        rows = csv.reader(stream)
        fields = next(rows, [])
        if len(fields) != len(set(fields)):
            raise ValueError('Ambiguous saved voltage column names')
        if not {'time_ms', 'voltage_mV', 'neuron_id'}.issubset(fields):
            raise ValueError('Saved voltage table lacks its time, voltage or neuron columns')
        indices = {name: fields.index(name) for name in fields}
        table.has_compartments = 'compartment_id' in indices
        ti, vi, ni = (indices[name] for name in ('time_ms','voltage_mV','neuron_id'))
        ci, ri, ei = (indices.get(name) for name in ('condition','repetition','compartment_id'))
        width = len(fields)
        for count, row in enumerate(rows, 1):
            if count % 8192 == 1:
                _check(cancelled)
                # Bytes consumed from the compressed input give bounded progress,
                # unlike a forecast based on a possibly missing run summary.
                buffer = stream.buffer
                raw = getattr(buffer, 'fileobj', buffer)
                progress('Reading saved voltage', min(raw.tell(), path.stat().st_size), path.stat().st_size)
            if count > maximum_samples:
                raise ValueError('Voltage traces exceed the local sample budget')
            try:
                if len(row) < width:
                    row += [''] * (width-len(row))
                t, v = float(row[ti]), float(row[vi])
                if not isfinite(t) or not isfinite(v):
                    raise ValueError('Non-finite saved voltage')
                identity = ((row[ci].strip() if ci is not None else '') or 'Recorded run',
                            (row[ri] if ri is not None else '') or '0', row[ni].strip(),
                            row[ei].strip() if ei is not None else '')
                if not identity[2]:
                    raise ValueError('Missing recorded neuron')
                if identity != previous:
                    key = (identity[0], int(float(identity[1])), identity[2], identity[3])
                    samples = table.series.get(key)
                    if samples is None:
                        samples = table.series[key] = SampleSeries()
                    previous = identity
                samples.append(t, v)
                table.sample_count += 1
                if t < 0 or (table.has_compartments and not identity[3]):
                    table.morphology_error = 'Invalid compartment voltage identity or timestamp'
            except (TypeError, ValueError, OverflowError):
                table.morphology_error = 'Invalid or non-finite compartment voltage sample'
                previous = None
    shared_clocks = {}
    condition_clocks = {}
    prepared = {}
    for index, (key, samples) in enumerate(table.series.items()):
        if index % 256 == 0:
            _check(cancelled)
            progress('Indexing saved traces', index, len(table.series))
        samples.finish()
        clock_bytes = samples.times.tobytes()
        clock = shared_clocks.get(clock_bytes)
        if clock is None:
            clock = shared_clocks[clock_bytes] = samples.times
        unique = all(b > a for a, b in zip(clock, clock[1:]))
        bounds = (min(samples.values), max(samples.values))
        prepared[key] = MappedSamples(clock, samples.values, unique, bounds)
        condition_clocks.setdefault(key[:2], {})[clock_bytes] = clock
    table.series = prepared
    for key, clocks in condition_clocks.items():
        table.clocks[key] = tuple(sorted({t for clock in clocks.values() for t in clock}))
    _check(cancelled)
    return table


def _owned_cache(path):
    return (path.is_dir() and not path.is_symlink()
            and set(p.name for p in path.iterdir()).issubset({'index.jsonl', 'ready.json', 'values.bin'})
            and all(not p.is_symlink() for p in path.iterdir()))


def _marked_cache(path):
    if not _owned_cache(path):
        return False
    try:
        marker = path/'ready.json'
        return marker.stat().st_size < 65536 and json.loads(marker.read_text()).get('schema') in {SCHEMA, REPLAY_BINDING_SCHEMA}
    except (OSError,ValueError,AttributeError):
        return False


def _read_cache(folder, source, digest, cancelled, maximum_samples):
    cache_paths = tuple(folder/name for name in ('ready.json','index.jsonl','values.bin'))
    cache_stamps = tuple(tuple(_stamp(path)) for path in cache_paths)
    if not _owned_cache(folder):
        raise ValueError('Unrecognized display cache')
    if (folder/'ready.json').stat().st_size >= 65536:
        raise ValueError('Invalid display cache marker')
    ready_bytes = (folder/'ready.json').read_bytes()
    ready = json.loads(ready_bytes)
    if ready.get('schema') != SCHEMA or ready['source_sha256'] != digest:
        raise ValueError('Display cache source differs')
    if (folder/'index.jsonl').stat().st_size > 128 * 1024**2:
        raise ValueError('Display cache index exceeds its limit')
    if _digest(folder/'index.jsonl', cancelled) != ready['index_sha256']:
        raise ValueError('Display cache index changed')
    # A single enormous JSON decode holds Python's GIL even in a QThread.
    # Stream one small trace entry at a time to keep the window responsive.
    with (folder/'index.jsonl').open() as stream:
        record = json.loads(stream.readline())
    if record['source'] != source or record['byteorder'] != sys.byteorder:
        raise ValueError('Display cache identity changed')
    count = int(record['sample_count'])
    if count < 0 or count > maximum_samples:
        raise ValueError('Voltage traces exceed the local sample budget')
    size = (folder/'values.bin').stat().st_size
    if size > maximum_samples * 16 or size != record['payload_bytes']:
        raise ValueError('Display cache payload size differs')
    if _digest(folder/'values.bin', cancelled) != ready['values_sha256']:
        raise ValueError('Display cache samples changed')
    with (folder/'values.bin').open('rb') as stream:
        mapping = mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) if size else None
    table = VoltageTable(sample_count=count, has_compartments=record['has_compartments'],
                         morphology_error=record['morphology_error'], cache_hit=True, mapping=mapping)
    # One mapped buffer owns all views. Constructing a new buffer plus byte
    # slice plus cast per trace creates hundreds of thousands of temporary
    # GC-tracked objects and pauses even the main thread during collection.
    flat = memoryview(mapping).cast('d') if mapping is not None else ()
    def view(offset, length):
        if not isinstance(offset, int) or not isinstance(length, int) or offset < 0 or offset%8 or length < 0 or offset+length*8 > size:
            raise ValueError('Invalid display cache block')
        return flat[offset//8:offset//8+length] if length else ()
    clocks = [view(*block) for block in record['clocks']]
    table.series = MappedSeries(flat, clocks, payload_sha256=ready['values_sha256'],
        clock_blocks=record['clocks'],cache_files=tuple(zip(cache_paths,
            (hashlib.sha256(ready_bytes).hexdigest(),ready['index_sha256'],ready['values_sha256']))),
        cache_stamps=cache_stamps)
    total = 0
    with (folder/'index.jsonl').open() as stream:
        stream.readline()
        for index, line in enumerate(stream):
            if index % 1024 == 0:
                _check(cancelled)
            entry = json.loads(line)
            key = tuple(entry['key'])
            if len(key) != 4 or key in table.series or not isinstance(key[1], int):
                raise ValueError('Invalid display cache trace identity')
            clock_id = entry['clock']
            if not isinstance(clock_id,int) or not 0 <= clock_id < len(clocks):
                raise ValueError('Invalid display cache clock')
            clock = clocks[clock_id]
            offset = entry['offset']
            if (not isinstance(offset,int) or offset < 0 or offset%8
                    or offset+len(clock)*8 > size):
                raise ValueError('Invalid display cache block')
            total += len(clock)
            table.series.add(key, clock_id, offset, entry['unique'], entry['bounds'])
    if total != count:
        raise ValueError('Incomplete display cache')
    table.clocks = {(c, r): tuple(times) for c, r, times in record['condition_clocks']}
    if cache_stamps != tuple(tuple(_stamp(path)) for path in cache_paths):
        raise ValueError('Display cache changed during loading')
    os.utime(folder, None)
    return table


def _write_cache(folder, table, source, digest, cancelled, progress):
    record = dict(schema=SCHEMA, source=source, byteorder=sys.byteorder,
                  sample_count=table.sample_count, has_compartments=table.has_compartments,
                  morphology_error=table.morphology_error, clocks=[],
                  condition_clocks=[[c, r, t] for (c, r), t in table.clocks.items()])
    clock_ids = {}
    entries = []
    hasher = hashlib.sha256()
    with (folder/'values.bin').open('wb') as stream:
        def write(values):
            raw = values.tobytes()
            offset = stream.tell()
            stream.write(raw); hasher.update(raw)
            return offset
        for index, (key, samples) in enumerate(table.series.items()):
            if index % 256 == 0:
                progress('Saving reusable display data', index, len(table.series))
                _check(cancelled)
            clock_id = clock_ids.get(id(samples.times))
            if clock_id is None:
                clock_id = len(clock_ids); clock_ids[id(samples.times)] = clock_id
                record['clocks'].append([write(samples.times), len(samples)])
            entries.append(json.dumps(dict(key=key, clock=clock_id, offset=write(samples.values),
                                           unique=samples.unique, bounds=samples.bounds),separators=(',',':')))
        record['payload_bytes'] = stream.tell()
        stream.flush(); os.fsync(stream.fileno())
    header = json.dumps(record,separators=(',',':'))
    if len(header)+sum(len(entry)+1 for entry in entries) > 128*1024**2:
        raise OSError('Display index exceeds its bounded cache size')
    with (folder/'index.jsonl').open('w') as stream:
        stream.write(header+'\n')
        for index,entry in enumerate(entries):
            if index%1024==0:
                _check(cancelled)
            stream.write(entry+'\n')
    ready = dict(schema=SCHEMA, source_sha256=digest, values_sha256=hasher.hexdigest(),
                 index_sha256=_digest(folder/'index.jsonl', cancelled))
    (folder/'ready.json').write_text(json.dumps(ready))
    _check(cancelled)


def _remove_cache(path):
    if not _marked_cache(path):
        raise OSError('Unrecognized cache folder; leaving it unchanged')
    # Delete the mapped payload first. If Windows still has an open view, the
    # failure leaves the complete marker/index for a later safe retirement.
    for name in ('values.bin', 'index.jsonl', 'ready.json'):
        (path/name).unlink(missing_ok=True)
    path.rmdir()


def _prune(root, keep):
    entries = [p for p in root.iterdir() if len(p.name) == 64 and all(c in '0123456789abcdef' for c in p.name) and _marked_cache(p)]
    total = sum(sum(f.stat().st_size for f in p.iterdir()) for p in entries)
    for path in sorted(entries, key=lambda p: p.stat().st_mtime_ns):
        if total <= CACHE_BYTES:
            break
        if path == keep:
            continue
        size = sum(f.stat().st_size for f in path.iterdir())
        try:
            _remove_cache(path)
            total -= size
        except OSError:
            pass  # Windows can retain a mapped/open cache until its reader closes.


def load_voltage_table(path, *, cache_root, cancelled=lambda: False,
                       progress=lambda label, done, total: None,
                       maximum_samples=MAX_PLAYBACK_VOLTAGE_SAMPLES,
                       verified_source=None):
    """Load one source once, or reopen its verified lossless indexed cache."""
    _check(cancelled)
    path = result_path(Path(path).resolve())
    if not path.is_file():
        return VoltageTable()
    source = _stamp(path)
    progress('Checking saved recording', 0, 0)
    # The Results worker has already digested every saved input. Reuse only
    # that request-local identity for this exact still-unchanged file; normal
    # callers continue to hash it here. Cache payload checks are never skipped.
    if verified_source is None:
        digest = _digest(path, cancelled)
    else:
        digest = verified_source.get('sha256')
        if (verified_source.get('stamp') != source or not isinstance(digest,str)
                or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)
                or (WINDOWS_FILE_CHECKS and _digest(path,cancelled)!=digest)):
            raise ValueError('Saved voltage changed since input verification; reopen the completed result')
    _check(cancelled)
    root = Path(cache_root).resolve()
    key = hashlib.sha256(json.dumps([source, digest, SCHEMA]).encode()).hexdigest()
    folder = root/key
    if folder.is_dir():
        cached = None
        try:
            progress('Opening reusable display data', 0, 0)
            cached = _read_cache(folder, source, digest, cancelled, maximum_samples)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            pass  # Never trust a partial, changed or incompatible derived copy.
        if _stamp(path) != source:
            raise ValueError('Saved voltage changed during loading; open the completed result again')
        if cached is not None:
            _check(cancelled)
            _check_source_bytes(path,digest,cancelled)
            return cached
    table = _parse(path, cancelled, progress, maximum_samples)
    if _stamp(path) != source:
        raise ValueError('Saved voltage changed during loading; open the completed result again')
    try:
        root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(root).free < RESERVE_BYTES + table.sample_count*16 + 128*1024**2:
            table.cache_note = 'Display cache skipped to preserve free disk space'
            _check_source_bytes(path,digest,cancelled)
            return table
        with tempfile.TemporaryDirectory(prefix='.partial-', dir=root) as temporary:
            staging = Path(temporary)
            _write_cache(staging, table, source, digest, cancelled, progress)
            if _stamp(path) != source:
                raise ValueError('Saved voltage changed during loading')
            _check(cancelled)
            if folder.exists():
                if not _marked_cache(folder):
                    raise OSError('Unrecognized cache folder; leaving it unchanged')
                _remove_cache(folder)
            staging.rename(folder)
        _prune(root, folder)
        # Retiring hundreds of thousands of separate cold arrays while
        # replacing a view otherwise briefly blocks the window thread. Return
        # mapped data on the first open too; this remains one CSV scan.
        mapped = _read_cache(folder, source, digest, cancelled, maximum_samples)
        mapped.cache_hit = False
        if _stamp(path) != source:
            raise ValueError('Saved voltage changed during loading')
        progress('Saved recording ready', 1, 1)
        table = mapped
    except OSError as exc:
        table.cache_note = f'Display cache unavailable: {exc}'
    _check_source_bytes(path,digest,cancelled)
    _check(cancelled)
    return table
