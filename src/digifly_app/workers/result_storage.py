"""Lossless storage for new generic experiment results; legacy files stay readable.

Standard library only. This sibling also runs in external simulator Python.
Never compacts an accepted result or a source dataset.
"""
from __future__ import annotations

import gzip
from array import array
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys
import math

# Compact float64 arrays in both trace and morphology readers bound numeric
# payload to 16 bytes/sample per reader, not a dict and tuple for every point.
# This is still a finite ceiling; it is not permission to discard samples.
MAX_PLAYBACK_VOLTAGE_SAMPLES = 64_000_000
BINARY_VOLTAGE_SCHEMA = 'digifly-voltage-float64-v1'


def float64_samples(values):
    """Copy a native double buffer in C when available, retaining exact values."""
    if hasattr(values,'as_numpy'):
        values = values.as_numpy()
    try:
        view = memoryview(values)
        if view.ndim==1 and view.format in ('d','=d','@d') and view.itemsize==8:
            result = array('d')
            result.frombytes(view.tobytes())
            return result
    except TypeError:
        pass
    return array('d',values)


class SampleSeries:
    """Compact, lossless paired samples with the legacy sequence interface."""
    __slots__ = ("times", "values", "_ordered")

    def __init__(self):
        self.times = array('d')
        self.values = array('d')
        self._ordered = True

    def append(self, time, value):
        if self.times and time <= self.times[-1]:
            self._ordered = False
        self.times.append(time)
        self.values.append(value)

    def finish(self, *, unique=False):
        if not self._ordered:
            indices = sorted(range(len(self)), key=self.times.__getitem__)
            self.times = array('d', (self.times[i] for i in indices))
            self.values = array('d', (self.values[i] for i in indices))
        if unique and any(a == b for a, b in zip(self.times, self.times[1:])):
            raise ValueError("Duplicate compartment voltage timestamp")
        self._ordered = True
        return self

    def __len__(self):
        return len(self.times)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return tuple(zip(self.times[index], self.values[index]))
        return self.times[index], self.values[index]

    def __iter__(self):
        return zip(self.times, self.values)

    def __eq__(self, other):
        if isinstance(other, SampleSeries):
            return self.times == other.times and self.values == other.values
        return tuple(self) == other


class VoltageRows:
    """Store traces as float64 blocks; materialize CSV rows only while writing."""
    def __init__(self):
        self.traces = []
        self.count = 0
        self._clocks = {}

    def add(self, condition, repetition, neuron, compartment, times, values):
        # NEURON shares its clock across edges. Other engines may use per-edge
        # lists; bind by complete values without changing any timestamps.
        key = tuple(times)
        clock = self._clocks.get(key)
        if clock is None:
            clock = array('d', times)
            self._clocks[key] = clock
        values = float64_samples(values)
        if len(clock) != len(values):
            raise ValueError("Incomplete voltage trace")
        self.traces.append((str(condition), repetition, neuron, compartment, clock, values))
        self.count += len(values)

    def __len__(self):
        return self.count

    @staticmethod
    def _rows(traces):
        for condition, repetition, neuron, compartment, times, values in traces:
            for time, value in zip(times, values):
                yield dict(condition=condition, repetition=repetition, neuron_id=neuron,
                           compartment_id=compartment, time_ms=time, voltage_mV=value)

    def __iter__(self):
        return self._rows(self.traces)

    def overview(self, maximum=24):
        traces = self.traces
        if len(traces) > maximum:
            traces = [traces[round(i*(len(traces)-1)/(maximum-1))] for i in range(maximum)]
        return self._rows(traces)

    def write_binary(self, root: Path, *, check_cancel=lambda: None):
        """Publish one lossless, directly readable recording, without a text copy.

        Clocks are shared within this recording only. No live model is cached.
        The ready marker is written last in a private staging folder; a reader
        never admits a partial recording. All payload hashes cover exact bytes.
        """
        root = Path(root)
        target = root / 'voltage'
        if target.exists() or target.is_symlink():
            raise ValueError('Refusing to replace an existing voltage recording')
        if any((root/name).exists() for name in ('voltage_traces.csv', 'voltage_traces.csv.gz')):
            raise ValueError('Ambiguous voltage recording destination')
        size = self.count*8 + sum(len(c)*8 for c in self._clocks.values())
        if shutil.disk_usage(root).free < size + 2*1024**3:
            raise OSError('Insufficient disk space for voltage recording and the 2 GiB reserve')
        with tempfile.TemporaryDirectory(prefix='.voltage-', dir=root) as temporary:
            folder = Path(temporary)
            payload_hash = hashlib.sha256()
            clock_ids, clock_blocks = {}, []
            condition_clocks = {}
            offset = 0
            with (folder/'values.bin').open('xb') as payload:
                def write(values):
                    nonlocal offset
                    start = offset
                    # Explicit endian metadata supports recordings moved between
                    # machines. Float64 is retained with no quantization.
                    for i in range(0, len(values), 131072):
                        check_cancel()
                        block = values[i:i+131072].tobytes()
                        payload.write(block)
                        payload_hash.update(block)
                        offset += len(block)
                    return start
                for clock in self._clocks.values():
                    if not clock or any(not math.isfinite(t) or t < 0 for t in clock) or any(b <= a for a,b in zip(clock,clock[1:])):
                        raise ValueError('Invalid voltage recording clock')
                    clock_ids[id(clock)] = len(clock_blocks)
                    clock_blocks.append([write(clock),len(clock)])
                # The header needs only clock IDs, avoiding duplicated clocks for
                # every edge or condition in the recording.
                for condition,repetition,neuron,compartment,clock,values in self.traces:
                    key = condition,int(repetition)
                    condition_clocks.setdefault(key,set()).add(clock_ids[id(clock)])
                header = dict(schema=BINARY_VOLTAGE_SCHEMA,byteorder=sys.byteorder,
                    sample_count=self.count,trace_count=len(self.traces),payload_bytes=size,
                    has_compartments=all(bool(t[3]) for t in self.traces),morphology_error='',
                    clocks=clock_blocks,condition_clock_ids=[[*key,sorted(ids)] for key,ids in condition_clocks.items()])
                with (folder/'index.jsonl').open('x',encoding='utf-8',newline='\n') as index:
                    index.write(json.dumps(header,separators=(',',':'))+'\n')
                    identities = set()
                    for condition,repetition,neuron,compartment,clock,values in self.traces:
                        check_cancel()
                        key = condition,int(repetition),str(neuron),str(compartment)
                        if key in identities or not key[2] or len(clock)!=len(values) or not values or any(not math.isfinite(v) for v in values):
                            raise ValueError('Invalid or duplicate voltage trace')
                        identities.add(key)
                        entry = dict(key=key,clock=clock_ids[id(clock)],offset=write(values),unique=True,
                                     bounds=[min(values),max(values)])
                        index.write(json.dumps(entry,separators=(',',':'))+'\n')
            if offset != size:
                raise ValueError('Incomplete binary voltage recording')
            with (folder/'index.jsonl').open('rb') as stream:
                index_hash = _digest(stream)
            ready = dict(schema=BINARY_VOLTAGE_SCHEMA,index_sha256=index_hash,
                         values_sha256=payload_hash.hexdigest())
            with (folder/'ready.json').open('x',encoding='utf-8') as stream:
                json.dump(ready,stream,separators=(',',':'))
            check_cancel()
            if target.exists() or target.is_symlink():
                raise ValueError('Refusing to replace a racing voltage recording')
            folder.rename(target)
        return target/'ready.json'


def result_path(path: Path) -> Path:
    path = Path(path)
    if path.suffix == ".gz":
        return path
    compressed = path.with_name(path.name + ".gz")
    if path.name == 'voltage_traces.csv':
        binary = path.parent/'voltage'/'ready.json'
        if binary.is_file():
            if path.exists() or compressed.exists():
                raise ValueError('Ambiguous saved result: binary and text voltage recordings')
            return binary
    if path.is_file() and compressed.is_file():
        raise ValueError(f"Ambiguous saved result: both {path.name} and {compressed.name}")
    return compressed if compressed.is_file() else path


def iter_binary_traces(root: Path, *, check_cancel=lambda: None):
    """Stream completed child recordings in external simulator interpreters."""
    root = Path(root)
    paths = [root/name for name in ('ready.json','index.jsonl','values.bin')]
    if root.is_symlink() or any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError('Incomplete binary child recording')
    stamps = [(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns) for p in paths]
    ready = json.loads(paths[0].read_text(encoding='utf-8'))
    if ready.get('schema') != BINARY_VOLTAGE_SCHEMA:
        raise ValueError('Unsupported binary child recording')
    for path,key in zip(paths[1:],('index_sha256','values_sha256')):
        with path.open('rb') as stream:
            if _digest(stream) != ready[key]:
                raise ValueError('Changed binary child recording')
        check_cancel()
    with paths[1].open(encoding='utf-8') as index,paths[2].open('rb') as payload:
        record = json.loads(index.readline())
        size = paths[2].stat().st_size
        if (record.get('schema')!=BINARY_VOLTAGE_SCHEMA or record['byteorder'] not in ('little','big')
                or not 0<=record['sample_count']<=MAX_PLAYBACK_VOLTAGE_SAMPLES or size!=record['payload_bytes']
                or size>MAX_PLAYBACK_VOLTAGE_SAMPLES*16):
            raise ValueError('Invalid binary child recording size')
        offset = 0
        def read(start,length):
            if type(start) is not int or type(length) is not int or start<0 or start%8 or length<1 or start+length*8>size:
                raise ValueError('Invalid binary child recording block')
            payload.seek(start)
            values = array('d');values.frombytes(payload.read(length*8))
            if record['byteorder'] != sys.byteorder:
                values.byteswap()
            if any(not math.isfinite(v) for v in values):
                raise ValueError('Non-finite binary child recording')
            return values
        clocks = []
        for start,length in record['clocks']:
            if start != offset:
                raise ValueError('Invalid binary child clock offset')
            values = read(start,length)
            if values[0]<0 or any(b<=a for a,b in zip(values,values[1:])):
                raise ValueError('Invalid binary child clock')
            clocks.append(values);offset+=length*8
        count = traces = 0
        identities = set()
        for line in index:
            check_cancel()
            entry = json.loads(line);key = tuple(entry['key']);clock_id = entry['clock']
            if (len(key)!=4 or key in identities or type(key[1]) is not int or key[1]<0
                    or any(not isinstance(v,str) for v in (key[0],key[2],key[3])) or not key[2]
                    or type(clock_id) is not int or not 0<=clock_id<len(clocks) or entry['offset']!=offset or entry['unique'] is not True):
                raise ValueError('Invalid binary child trace')
            identities.add(key);clock=clocks[clock_id]
            values = read(offset,len(clock))
            if entry['bounds'] != [min(values),max(values)]:
                raise ValueError('Invalid binary child trace bounds')
            count+=len(clock);traces+=1;offset+=len(clock)*8
            yield key,clock,values
        if offset!=size or count!=record['sample_count'] or traces!=record['trace_count']:
            raise ValueError('Incomplete binary child traces')
    if stamps != [(p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns) for p in paths]:
        raise ValueError('Binary child recording changed during aggregation')


def open_result_text(path: Path):
    actual = result_path(path)
    return (gzip.open(actual, "rt", encoding="utf-8", newline="")
            if actual.suffix == ".gz" else actual.open(encoding="utf-8", newline=""))


def read_result_json(path: Path):
    with open_result_text(path) as stream:
        payload = json.load(stream)
    if isinstance(payload,dict) and payload.get('storage_schema') == 'digifly-shared-regional-materials-v1':
        payload = dict(payload)
        materials = payload.pop('materials')
        payload.pop('storage_schema')
        sections = []
        for raw in payload['sections']:
            section = dict(raw)
            index = section.pop('material')
            if type(index) is not int or not 0<=index<len(materials) or 'hh' in section or 'membrane' in section:
                raise ValueError('Invalid regional material reference')
            sections.append(dict(section,hh=materials[index]['hh'],membrane=materials[index]['membrane']))
        payload['sections'] = sections
    return payload


def shared_regional_materials(plan):
    """Save identical membrane settings once, retaining every geometry field."""
    materials,sections = [],[]
    for section in plan['sections']:
        material = {k:section[k] for k in ('hh','membrane')}
        try:
            index = materials.index(material)
        except ValueError:
            index = len(materials)
            materials.append(material)
        sections.append({**{k:v for k,v in section.items() if k not in ('hh','membrane')},'material':index})
    return {**plan,'storage_schema':'digifly-shared-regional-materials-v1','materials':materials,'sections':sections}


def result_maps(root: Path):
    names = {p.with_suffix("") if p.suffix == ".gz" else p
             for pattern in ("regional_map.json", "regional_map.json.gz")
             for p in Path(root).rglob(pattern)}
    return tuple(result_path(p) for p in sorted(names))


def _digest(stream):
    digest = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(block)
    return digest.hexdigest()


def compress_file(path: Path, *, minimum_bytes=512 * 1024, check_cancel=None):
    """Verify exact decompressed bytes before retiring this new run's plain copy."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("Storage compression requires an ordinary run-owned file")
    size = path.stat().st_size
    if size < minimum_bytes:
        return None
    if shutil.disk_usage(path.parent).free < size + 2 * 1024**3:
        return None
    target = path.with_name(path.name + ".gz")
    if target.exists() or target.is_symlink():
        raise ValueError("Refusing to replace an existing compressed result")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".compact-", delete=False) as raw:
            temporary = Path(raw.name)
            with path.open("rb") as source, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as output:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    if check_cancel:
                        check_cancel()
                    output.write(block)
        with path.open("rb") as source:
            original = _digest(source)
        with gzip.open(temporary, "rb") as restored:
            if _digest(restored) != original:
                raise ValueError("Compressed result failed exact byte verification")
        if temporary.stat().st_size >= size:
            return None
        if check_cancel:
            check_cancel()
        with path.open("rb") as source:
            if path.stat().st_size != size or _digest(source) != original:
                raise ValueError("Result changed during compression; original retained")
        # Same-directory exclusive publication; never replace a racing file.
        try:
            os.link(temporary, target)
        except FileExistsError:
            raise ValueError("Refusing to replace a racing compressed result")
        except OSError:
            # Some external filesystems cannot publish a hard link. Keep the
            # valid plain file; storage optimization must not invalidate a run.
            return None
        path.unlink()
        return {"original_path": path.name, "path": target.name, "original_bytes": size,
                "stored_bytes": target.stat().st_size, "decoded_sha256": original,
                "exact_decoded_bytes_verified": True}
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def compact_outputs(root: Path, artifacts: list, *, check_cancel=None):
    root = Path(root).resolve()
    manifest = read_result_json(root / "run_manifest.json")
    if manifest.get("state") != "running" or (root / "summary.json").exists():
        raise ValueError("Only a new unpublished running result may be compacted")
    paths = [root / name for name in ("voltage_traces.csv", "spikes.csv", "condition_execution.json")]
    paths += list((root / "morphologies").glob("*/regional_map.json"))
    paths += list((root / "currents").glob("*/index.json"))
    # Preserve dense native observations and pinned selection receipts too;
    # their original logical names/hashes identify the exact decoded bytes.
    paths += [p for p in (root / "arbor_native_reports").glob("*")
              if p.suffix in {".csv", ".json"}]
    records = []
    for path in paths:
        if not path.exists():
            continue
        if path.resolve() != path or not path.is_relative_to(root):
            raise ValueError("Result storage path leaves the run folder")
        record = compress_file(path, check_cancel=check_cancel)
        if record:
            old = path.relative_to(root).as_posix()
            new = old + ".gz"
            record.update(original_path=old, path=new)
            for artifact in artifacts:
                if Path(artifact["path"]).as_posix() == old:
                    artifact["path"] = new
            records.append(record)
    receipt = {"schema": "digifly-lossless-result-storage-v1", "files": records,
               "original_bytes": sum(r["original_bytes"] for r in records),
               "stored_bytes": sum(r["stored_bytes"] for r in records),
               "policy": "Lossless gzip; exact decoded bytes verified. No samples, source geometry or precision removed.",
               "logical_path_rule": "Original names in native receipts resolve through this file map; their hashes identify the exact decoded original bytes."}
    with (root / "result_storage.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
    artifacts.append({"kind": "document", "path": "result_storage.json", "label": "Recording storage details"})
    return receipt


def compress_native_voltage(path: Path, h5py, *, check_cancel=None):
    """Retain the entire BioNet report, compressing its unchanged numeric data."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size < 512 * 1024:
        return None
    size = path.stat().st_size
    if shutil.disk_usage(path.parent).free < size + 2 * 1024**3:
        return None  # Keep valid uncompressed output; never consume the reserve.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".compact-h5-", delete=False) as handle:
            temporary = Path(handle.name)
        with h5py.File(path, "r") as source, h5py.File(temporary, "w") as target:
            for key, value in source.attrs.items():
                target.attrs[key] = value
            def copy_group(group, destination):
                for key, old in group.items():
                    if isinstance(old, h5py.Group):
                        output = destination.create_group(key)
                        for attr, value in old.attrs.items():
                            output.attrs[attr] = value
                        copy_group(old, output)
                    elif (old.name.startswith("/report/") and key == "data"
                          and old.ndim == 2 and old.size and old.dtype.kind in "fiu"):
                        output = destination.create_dataset(key, shape=old.shape, dtype=old.dtype,
                            chunks=(min(64, old.shape[0]), min(1024, old.shape[1])),
                            compression="gzip", compression_opts=4, shuffle=True)
                        for attr, value in old.attrs.items():
                            output.attrs[attr] = value
                        for start in range(0, old.shape[0], 64):
                            if check_cancel:
                                check_cancel()
                            block = old[start:start + 64]
                            output[start:start + 64] = block
                            if output[start:start + 64].tobytes() != block.tobytes():
                                raise ValueError("Native voltage compression changed recorded values")
                    else:
                        group.copy(key, destination)
            copy_group(source, target)
        if temporary.stat().st_size >= size:
            return None
        if check_cancel:
            check_cancel()
        os.replace(temporary, path)
        return {"original_bytes": size, "stored_bytes": path.stat().st_size,
                "all_native_values_preserved": True}
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
