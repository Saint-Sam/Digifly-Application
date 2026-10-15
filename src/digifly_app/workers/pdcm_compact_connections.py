"""Opt-in, source-pinned connection storage experiment for native PDCM.

This changes only Python bookkeeping around the reviewed NetPyNE constructor.
It never replaces the constructor, sets a NetCon property, consumes randomness,
or releases a live NetCon.  Resource savings and simulation parity must still be
measured before this experiment can justify a smaller production allocation.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import inspect
import math
import numbers
from pathlib import Path
import struct
import threading


NETPYNE_VERSION = "1.1.1"
POINT_CELL_SHA256 = "a8d8ad8276d11010fe16b5162934874057960ff70ebda8271ff2991898f13b50"
CELL_SHA256 = "01b8dafc5980665859eddc517108cdf830562dbf59d78ff93472cc402ed0a01a"
ENVIRONMENT_FLAG = "DIGIFLY_PDCM_COMPACT_CONNECTIONS"
_ACTIVE_CLASSES: set[type] = set()
_CONTEXT_LOCK = threading.Lock()
_CONNECTION_KEYS = {
    "preGid", "sec", "loc", "synMech", "weight", "delay",
    "synsPerConn", "weightIndex", "label",
}
_RULE_KEYS = {
    "preConds", "postConds", "divergence", "probability", "connList",
    "weight", "delay",
}


def requested(environ: Mapping[str, str]) -> bool:
    """Absent means the legacy path; no truthiness-based accidental opt-in."""
    value = environ.get(ENVIRONMENT_FLAG)
    if value is None:
        return False
    if value != "1":
        raise ValueError(f"{ENVIRONMENT_FLAG} must be absent or exactly '1'")
    return True


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _method_source(method, expected_path: Path) -> None:
    code = getattr(method, "__code__", None)
    if code is None or Path(code.co_filename).resolve() != expected_path:
        raise ValueError("Compact connections require the unwrapped pinned NetPyNE methods")


def attest_runtime(point_cell_class: type, netpyne_version: str) -> dict:
    """Reject unreviewed package versions, source drift, and replaced methods."""
    if netpyne_version != NETPYNE_VERSION:
        raise ValueError("Compact connections require reviewed NetPyNE 1.1.1")
    if (point_cell_class.__module__, point_cell_class.__name__) != (
        "netpyne.cell.pointCell", "PointCell",
    ):
        raise ValueError("Compact connections require the native NetPyNE PointCell class")
    source_name = inspect.getsourcefile(point_cell_class)
    if not source_name:
        raise ValueError("Cannot attest the NetPyNE PointCell source")
    source = Path(source_name).resolve(strict=True)
    base_source = source.with_name("cell.py")
    if not base_source.is_file() or _sha256(source) != POINT_CELL_SHA256 or _sha256(base_source) != CELL_SHA256:
        raise ValueError("Compact connections require exact reviewed pointCell.py and cell.py bytes")
    _method_source(point_cell_class.addConn, source)
    _method_source(point_cell_class._connWeightsAndDelays, base_source)
    return {
        "netpyne_version": netpyne_version,
        "point_cell_source": str(source), "point_cell_sha256": POINT_CELL_SHA256,
        "cell_source": str(base_source), "cell_sha256": CELL_SHA256,
    }


def _validate_model(cfg, net_params) -> None:
    if cfg.createPyStruct is not True or cfg.createNEURONObj is not True:
        raise ValueError("Compact connections require normal Python/native object creation before entry")
    for name in ("subConnParams", "synMechParams", "stimSourceParams", "stimTargetParams"):
        if getattr(net_params, name, None):
            raise ValueError(f"Compact PDCM connections do not support {name}")
    populations = net_params.popParams
    if not isinstance(populations, Mapping) or not populations:
        raise ValueError("Compact connections require the native IntFire_PD PDCM populations")
    for values in populations.values():
        if not isinstance(values, Mapping) or values.get("cellModel") not in {"IntFire_PD", "NetStim"}:
            raise ValueError("Compact connections do not support other point mechanisms")
    if not any(values.get("cellModel") == "IntFire_PD" for values in populations.values()):
        raise ValueError("Compact connections require the native IntFire_PD PDCM populations")
    if not isinstance(net_params.connParams, Mapping):
        raise ValueError("Compact connections require ordinary PDCM connection rules")
    for rule in net_params.connParams.values():
        if not isinstance(rule, Mapping) or set(rule) - _RULE_KEYS:
            raise ValueError("Compact connections do not support plasticity, shape, gap or custom rules")


def _integer(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, numbers.Integral) or int(value) < 0:
        raise ValueError(f"Compact connections require a nonnegative integer {label}")
    return int(value)


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(float(value)):
        raise ValueError(f"Compact connections require a finite scalar {label}")
    return float(value)


def _validate_call(cell, params, net_stim_params) -> tuple[int, int]:
    if cell.tags.get("cellModel") != "IntFire_PD":
        raise ValueError("Compact connections support only IntFire_PD postsynaptic targets")
    if net_stim_params is not None or not isinstance(params, Mapping) or set(params) - _CONNECTION_KEYS:
        raise ValueError("Compact connections do not support inline stimuli, plasticity, shape, gap or custom parameters")
    pre = _integer(params.get("preGid"), "presynaptic GID")
    post = _integer(cell.gid, "postsynaptic GID")
    synapses = params.get("synsPerConn", 1)
    if synapses is not None and _integer(synapses, "synapses per connection") != 1:
        raise ValueError("Compact connections require exactly one synapse per connection")
    index = params.get("weightIndex", 0)
    if index is not None and _integer(index, "weight index") != 0:
        raise ValueError("Compact connections require weight index zero")
    _finite(params.get("weight"), "weight")
    if _finite(params.get("delay"), "delay") < 0:
        raise ValueError("Compact connections require nonnegative delays")
    if params.get("sec") is not None or params.get("synMech") is not None or "vref" in cell.tags:
        raise ValueError("Compact connections require native point targets without section/synapse overrides")
    return pre, post


@dataclass
class CompactAudit:
    runtime: dict
    contacts_created: int = 0
    autapses_skipped: int = 0

    def evidence(self) -> dict:
        return {
            "schema": "digifly-pdcm-compact-connections-v1",
            "mode": "experimental_opt_in", "runtime": dict(self.runtime),
            "contacts_created": self.contacts_created,
            "autapses_skipped": self.autapses_skipped,
            "retained_connection_keys": ["hObj", "preGid"],
            "helper_sha256": _sha256(Path(__file__).resolve()),
            "scientific_acceptance": False,
        }


@contextmanager
def compact_connections(cfg, point_cell_class: type, net_params, *, netpyne_version: str):
    """Keep the original native construction, with two-key connection records.

    The original source creates a fresh hObj-only Dict with createPyStruct=False.
    Adding preGid immediately retains the existing contact/convergence exports,
    without first allocating a rich per-contact dictionary.  No NetCon setter or
    random-number API is called here.  The class and configuration are restored
    even if network construction or the caller raises.
    """
    with _CONTEXT_LOCK:
        if point_cell_class in _ACTIVE_CLASSES:
            raise RuntimeError("Compact connection contexts cannot nest or overlap")
        runtime = attest_runtime(point_cell_class, netpyne_version)
        _validate_model(cfg, net_params)
        original = point_cell_class.addConn
        original_flag = cfg.createPyStruct
        audit = CompactAudit(runtime)

        def compact_add_conn(cell, params, netStimParams=None):
            pre, post = _validate_call(cell, params, netStimParams)
            previous = len(cell.conns)
            result = original(cell, params, netStimParams=netStimParams)
            added = len(cell.conns) - previous
            if pre == post:
                if added:
                    raise RuntimeError("Pinned native constructor unexpectedly created an autapse")
                audit.autapses_skipped += 1
                return result
            if added != 1:
                raise RuntimeError("Pinned native constructor did not create exactly one contact")
            connection = cell.conns[-1]
            if set(connection) != {"hObj"}:
                raise RuntimeError("Pinned native constructor produced unexpected compact metadata")
            connection["preGid"] = params["preGid"]
            audit.contacts_created += 1
            return result

        _ACTIVE_CLASSES.add(point_cell_class)
        point_cell_class.addConn = compact_add_conn
        cfg.createPyStruct = False
    try:
        yield audit
    finally:
        with _CONTEXT_LOCK:
            point_cell_class.addConn = original
            cfg.createPyStruct = original_flag
            _ACTIVE_CLASSES.discard(point_cell_class)


def realized_connection_fingerprint(cells, cortical_gid_limit: int) -> dict:
    """Stream all cortical-target contacts, including external and clock edges.

    Cell GID order followed by the unchanged per-cell construction order is the
    declared ordering.  The digest uses actual live NetCon weight[0] and delay,
    not duplicated Python values.  It neither sorts/copies the contact graph nor
    changes any native property; only the small cell list is ordered.
    """
    limit = _integer(cortical_gid_limit, "cortical GID limit")
    digest = hashlib.sha256(b"digifly-pdcm-live-netcon-v1\0")
    counts = {"total": 0, "recurrent": 0, "external": 0, "unclassified": 0}
    seen = set()
    for cell in sorted(cells, key=lambda item: int(item.gid)):
        if cell.tags.get("cellModel") != "IntFire_PD":
            continue
        post = _integer(cell.gid, "postsynaptic GID")
        if post >= limit or post in seen:
            raise ValueError("Realized-contact fingerprint has invalid/duplicate cortical GIDs")
        seen.add(post)
        for connection in cell.conns:
            pre = _integer(connection.get("preGid"), "presynaptic GID")
            native = connection["hObj"]
            weight = _finite(native.weight[0], "live NetCon weight")
            delay = _finite(native.delay, "live NetCon delay")
            if delay < 0:
                raise ValueError("Realized-contact fingerprint has negative delay")
            digest.update(struct.pack("<qqdd", post, pre, weight, delay))
            counts["total"] += 1
            counts["recurrent" if pre < limit else "external"] += 1
    return {
        "schema": "digifly-pdcm-live-netcon-fingerprint-v1",
        "sha256": digest.hexdigest(), "synaptic_contacts": counts,
        "ordering": "ascending cortical target GID, original per-cell contact order",
        "fields": ["post_gid", "pre_gid", "live_weight_index_0", "live_delay_ms"],
        "cortical_targets": len(seen), "includes_external_and_clock": True,
    }
