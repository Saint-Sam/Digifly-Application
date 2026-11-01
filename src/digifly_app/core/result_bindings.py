"""Checked compact replay IDs; no recording, geometry or scientific edits."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .result_voltage import (
    REPLAY_BINDING_SCHEMA, RESERVE_BYTES, _digest, _stamp, _owned_cache, _prune,
)


def load_replay_bindings(cache_root, files, identity, factory, *, check=lambda: None):
    """Reuse validated ID maps only while every exact input remains unchanged.

    The factory is the ordinary geometry validator. Its output is written only
    after success; cache failure falls back to that validator, never to guessed
    edges. Both cache types share the existing 2 GiB retention budget.
    """
    paths=tuple(sorted({Path(p).resolve() for p in files}))
    stamps={p: _stamp(p) for p in paths}
    hasher=hashlib.sha256(REPLAY_BINDING_SCHEMA.encode())
    def cancelled():
        check()
        return False
    for path in paths:
        check()
        hasher.update(json.dumps([stamps[path],_digest(path,cancelled)],separators=(',',':')).encode())
    visible={str(body): set(nodes) for body,_,nodes in identity}
    for body,path,nodes in identity:
        hasher.update(json.dumps([body,path],separators=(',',':')).encode())
        for index,node in enumerate(nodes):
            if index%1024==0:check()
            hasher.update(str(node).encode()+b'\n')
    key=hasher.hexdigest();root=Path(cache_root).resolve();folder=root/key
    def unchanged():
        check()
        if any(_stamp(p)!=stamp for p,stamp in stamps.items()):
            raise ValueError('Recorded geometry changed during loading; reopen the completed result')
    def read_cache():
        if not _owned_cache(folder) or (folder/'ready.json').stat().st_size>=65536:
            raise ValueError('Unrecognized replay ID cache')
        ready=json.loads((folder/'ready.json').read_text())
        index=folder/'index.jsonl'
        if (ready.get('schema')!=REPLAY_BINDING_SCHEMA or ready.get('input_key')!=key
                or index.stat().st_size>128*1024**2 or _digest(index,cancelled)!=ready['index_sha256']):
            raise ValueError('Replay ID cache changed')
        seen={}
        with index.open() as stream:
            header=json.loads(next(stream));soma=header['soma_edges']
            bodies=header['bodies']
            if not isinstance(bodies,list) or len(bodies)!=len(set(bodies)) or any(b not in visible for b in bodies):
                raise ValueError('Invalid cached replay neurons')
            maps={body:{} for body in bodies}
            for count,line in enumerate(stream):
                if count%1024==0:check()
                body,source,node=json.loads(line)
                if (body not in visible or type(source) is not int or type(node) is not int
                        or node not in visible[body]):
                    raise ValueError('Invalid cached replay edge identity')
                mapping=maps.setdefault(body,{})
                if source in mapping or node in seen.setdefault(body,set()):
                    raise ValueError('Ambiguous cached replay edge identity')
                mapping[source]=node;seen[body].add(node)
        if not isinstance(soma,dict) or any(body not in maps or int(source) not in maps[body]
                                             for body,source in soma.items()):
            raise ValueError('Invalid cached soma identity')
        unchanged()
        os.utime(folder,None)
        return maps,soma,True
    if folder.is_dir():
        try:
            return read_cache()
        except (OSError,ValueError,KeyError,TypeError,IndexError,StopIteration):
            unchanged()
    edge_maps,soma_edges=factory()
    unchanged()
    partial=None
    try:
        root.mkdir(parents=True,exist_ok=True)
        estimate=sum(len(v) for v in edge_maps.values())*100+65536
        if estimate>128*1024**2 or shutil.disk_usage(root).free<RESERVE_BYTES+estimate:
            return edge_maps,soma_edges,False
        partial=Path(tempfile.mkdtemp(prefix='.partial-bindings-',dir=root))
        with (partial/'index.jsonl').open('w') as stream:
            stream.write(json.dumps({'soma_edges':soma_edges,'bodies':list(edge_maps)},separators=(',',':'))+'\n')
            for body,mapping in edge_maps.items():
                for count,(source,node) in enumerate(mapping.items()):
                    if count%1024==0:
                        check()
                        if stream.tell()>128*1024**2:
                            raise OSError('Replay ID index exceeds its bounded size')
                    stream.write(json.dumps([body,source,node],separators=(',',':'))+'\n')
            stream.flush();os.fsync(stream.fileno())
        (partial/'ready.json').write_text(json.dumps(dict(schema=REPLAY_BINDING_SCHEMA,
            input_key=key,index_sha256=_digest(partial/'index.jsonl',cancelled))))
        unchanged()
        if folder.exists():
            if not _owned_cache(folder):
                return edge_maps,soma_edges,False
            try:
                marker=json.loads((folder/'ready.json').read_text())
                if marker.get('schema')!=REPLAY_BINDING_SCHEMA or marker.get('input_key')!=key:
                    return edge_maps,soma_edges,False
            except (OSError,ValueError):
                return edge_maps,soma_edges,False
            shutil.rmtree(folder)  # Recognized derived IDs only; never run files.
        partial.rename(folder);partial=None
        _prune(root,folder)
    except OSError:
        pass  # Read-only/low-space caches never prevent viewing the saved run.
    finally:
        if partial is not None:
            shutil.rmtree(partial)
    return edge_maps,soma_edges,False
