"""Native NEURON current recording and lossless, independently compressed traces.

Standard library only: this file also runs in an external simulator interpreter.
Recording does not change sections, nseg, mechanisms, electrodes or integration.
"""
from __future__ import annotations

from array import array
import hashlib
import gzip
import json
import math
from pathlib import Path
import sys
import shutil
import zlib

MAX_CURRENT_VALUES = 50_000_000
MAX_CURRENT_TRACE_SAMPLES = 5_000_000
SIGNALS = {
    "membrane": "Total membrane current",
    "axial": "Axial current · toward child",
    "sodium": "Sodium current",
    "potassium": "Potassium current",
    "calcium": "Calcium current",
    "leak": "HH/passive leak current",
    "capacitive": "Capacitive current",
    "injected": "Injected current",
    "synaptic": "Chemical synaptic current",
    "gap": "Gap-junction current",
}


def check_mode(recording, engine="neuron", workers=1, integration_dt=None):
    mode = recording.get("current_tracking", "off")
    if mode not in {"off", "sparse", "full"}:
        raise ValueError("Choose Off, Sparse or Full current tracking")
    if mode != "off" and (engine != "neuron" or int(workers) != 1):
        raise ValueError("Current tracking currently requires NEURON and one experiment worker; no current setting is silently ignored")
    if mode != "off" and integration_dt is not None:
        ratio = float(recording["sample_dt_ms"]) / float(integration_dt)
        if not math.isfinite(ratio) or ratio < 1 or not math.isclose(ratio, round(ratio), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("Current recording interval must be a whole multiple of the integration step, at least one step; no current samples are fabricated between solver steps")
    return mode


def plan_budget(plans, experiment):
    """Conservative Full-mode forecast before native construction/allocation."""
    recording = experiment.get("recording") or {}
    if recording.get("current_tracking", "off") != "full":
        return 0
    targets = recording.get("target_neuron_ids") or plans
    centers = sum(int(plans[n]["estimated_nseg"]) for n in targets)
    samples = math.ceil(float(experiment["duration_ms"]) / float(recording["sample_dt_ms"])) + 1
    estimate = centers * 12 * samples
    if samples > MAX_CURRENT_TRACE_SAMPLES or estimate > MAX_CURRENT_VALUES:
        raise ValueError(f"Full current recording forecast exceeds its per-trial local budget ({estimate:,} values). Choose Sparse, fewer target neurons, a shorter run or a larger sample interval.")
    return estimate


def distributed_indices(count, maximum=7):
    if count <= maximum:
        return tuple(range(count))
    return tuple(sorted({round(i * (count - 1) / (maximum - 1)) for i in range(maximum)}))


class CurrentRecorder:
    def __init__(self, h, built, cells, recording, duration_ms, clamps=(), synapses=(), gaps=(), *, synchronized=False):
        self.h = h
        self.synchronized = synchronized
        self.capture_sources = []
        self.mode = check_mode(recording)
        self.dt = float(recording["sample_dt_ms"])
        self.duration = float(duration_ms)
        self.channels = []
        self.voltages = {}
        self.coverage = []
        self.targets = set(recording.get("target_neuron_ids") or built)
        self.section_info = {}
        self.sections_by_id = {}
        self.all_sites = {}
        self.record_owners = {}
        for neuron_id, cell in built.items():
            section,x = cell['soma_site']
            owner = h.IClamp(section(x))
            owner.dur,owner.amp = 0,0
            self.record_owners[neuron_id] = owner
            specs = (cell.get("regional_plan") or {}).get("sections") or ()
            for section_id, section in enumerate(cell["sections"]):
                spec = specs[section_id] if specs else {}
                source = spec.get("source_node_id")
                self.section_info[section] = (neuron_id, section_id, source)
                self.sections_by_id[neuron_id,section_id] = section
                for cv, seg in enumerate(section):
                    key = (section, cv)
                    self.all_sites[key] = (section, seg.x, cv)
        selected = set()
        for neuron_id in built:
            if neuron_id not in self.targets:
                continue
            keys = [key for key in self.all_sites if self.section_info[key[0]][0] == neuron_id]
            selected.update(keys if self.mode == "full" else (keys[i] for i in distributed_indices(len(keys))))
            soma_sec, soma_x = built[neuron_id]["soma_site"]
            selected.add(self._site_key(soma_sec, soma_x))
            edits = set(str(k) for k in (cells[neuron_id].get("compartment_overrides") or {}))
            edits.update(str(k) for k in (cells[neuron_id].get("compartment_mechanism_overrides") or {}))
            for section_id, spec in enumerate((built[neuron_id].get("regional_plan") or {}).get("sections") or ()):
                if str(spec["node_id"]) in edits:
                    selected.add(self._site_key(built[neuron_id]["sections"][section_id], .5))
        points = []
        for kind, processes in (("injected", clamps), ("synaptic", synapses), ("gap", gaps)):
            for value in processes:
                point, details = value if isinstance(value, tuple) else (value, {})
                points.append((kind, point, details))
        for kind, point, details in points:
            seg = point.get_segment()
            if seg is not None and self.section_info[seg.sec][0] in self.targets:
                selected.add(self._site_key(seg.sec, seg.x))
        expected_samples = math.ceil(self.duration / self.dt) + 1
        upper_values = (len(selected)*12 + len(points)) * expected_samples
        if expected_samples > MAX_CURRENT_TRACE_SAMPLES or upper_values > MAX_CURRENT_VALUES:
            raise ValueError(f"Current recording forecast exceeds the local budget ({upper_values:,} values). Choose Sparse, fewer target neurons, a shorter run or a larger sample interval.")
        # This asks NEURON to expose its existing balance, not a new mechanism.
        h.CVode().use_fast_imem(1)
        self.junctions = []
        if self.mode == "full":
            for sec, (neuron_id, section_id, source) in self.section_info.items():
                if neuron_id not in self.targets:
                    continue
                if float(sec.orientation()) != 0.:
                    raise ValueError("Current recording does not support a reversed native section orientation")
                # Same unique-node traversal as NEURON's documented current
                # conservation example: child(0) aliases its parent junction.
                xs = (0., 1.) if sec.parentseg() is None else (1.,)
                for x in xs:
                    meta = dict(self._meta(sec, x), section_x=x, zero_area_junction=True,
                                site_id=f"{neuron_id}:section-{section_id}:junction-{int(x)}", link="junction")
                    self.junctions.append(meta)
                    self._native(meta, "membrane", sec(x)._ref_i_membrane_)
        for key in sorted(selected, key=lambda k: (*self.section_info[k[0]][:2], k[1])):
            section, x, cv = self.all_sites[key]
            meta = self._meta(section, x)
            self.coverage.append(meta)
            seg = section(x)
            self._native(meta, "membrane", seg._ref_i_membrane_)
            area_factor = float(seg.area()) * .01  # mA/cm2 * um2 -> nA
            self._native(meta, "capacitive", seg._ref_i_cap, area_factor)
            for kind, attribute in (("sodium", "ina"), ("potassium", "ik"), ("calcium", "ica")):
                if hasattr(seg, "_ref_" + attribute):
                    self._native(meta, kind, getattr(seg, "_ref_" + attribute), area_factor)
            leak = []
            if hasattr(seg, "hh"):
                leak.append(self._vector(seg.hh._ref_il,section))
            if hasattr(seg, "pas"):
                leak.append(self._vector(seg.pas._ref_i,section))
            if leak:
                self.channels.append((dict(meta, signal="leak"), leak, area_factor, None))
            # Every center has an incoming native solver link. At a section
            # junction the parent is sec(0), not an assumed parent-cell center.
            parent_x = 0. if cv == 0 else (cv - .5) / section.nseg
            resistance = float(seg.ri())
            self._axial(meta, section, parent_x, x, resistance)
            # Retain the last-center -> distal-junction link as well; this
            # matters where multiple daughter sections meet a zero-area node.
            if cv == section.nseg - 1:
                self._axial(dict(meta, site_id=meta["site_id"] + ":distal", link="distal"),
                            section, x, 1., float(section(1.).ri()))
        for point_index, (kind, point, details) in enumerate(points):
            seg = point.get_segment()
            if seg is None or self.section_info[seg.sec][0] not in self.targets:
                continue
            meta = dict(self._meta(seg.sec, seg.x), **details, requested_section_x=float(seg.x), point_process=str(point.hname()),
                        site_id=self._meta(seg.sec, seg.x)["site_id"] + f":{kind}:{point_index}")
            if not hasattr(point, "_ref_i"):
                raise ValueError(f"Current tracking cannot read the current of {point.hname()}")
            self._native(meta, kind, point._ref_i)
        values = len(self.channels) * (math.ceil(self.duration / self.dt) + 1)
        if values > MAX_CURRENT_VALUES:
            raise ValueError(f"Current recording requires {values:,} values in one trial; limit {MAX_CURRENT_VALUES:,}. Choose Sparse, fewer target neurons, a shorter run or a larger sample interval.")
        self.times = array('d') if synchronized else h.Vector()
        if not synchronized:
            self.times.record(next(iter(self.record_owners.values())),h._ref_t,self.dt)

    def _site_key(self, sec, x):
        return sec, min(sec.nseg - 1, max(0, int(float(x) * sec.nseg)))

    def _meta(self, sec, x):
        neuron_id, section_id, source = self.section_info[sec]
        cv = min(sec.nseg - 1, max(0, int(float(x) * sec.nseg)))
        return {"neuron_id": neuron_id, "section_id": section_id, "source_compartment_id": source,
                "cv_index": cv, "nseg": int(sec.nseg), "section_x": (cv + .5) / sec.nseg,
                "site_id": f"{neuron_id}:section-{section_id}:cv-{cv}"}

    def _vector(self, reference, section=None):
        if self.synchronized:
            vector = array('d')
            self.capture_sources.append((reference,vector))
            return vector
        vector = self.h.Vector()
        if section is None:
            vector.record(reference,self.dt)
        else:
            owner = self.record_owners[self.section_info[section][0]]
            vector.record(owner,reference,self.dt,sec=section)
        return vector

    def _voltage(self, sec, x):
        key = sec, float(x)
        if key not in self.voltages:
            self.voltages[key] = self._vector(sec(x)._ref_v,sec)
        return self.voltages[key]

    def _native(self, meta, kind, ref, factor=1.):
        section = self.sections_by_id[meta['neuron_id'],meta['section_id']]
        self.channels.append((dict(meta, signal=kind), [self._vector(ref,section)], factor, None))

    def _axial(self, meta, sec, parent_x, child_x, resistance):
        if not math.isfinite(resistance) or resistance <= 0:
            raise ValueError("Invalid native axial resistance")
        self.channels.append((dict(meta, signal="axial", parent_x=parent_x, child_x=child_x,
                                   resistance_MOhm=resistance, method="native voltage difference / native ri"),
                              [self._voltage(sec, parent_x), self._voltage(sec, child_x)], 1. / resistance, "difference"))

    def write(self, root, condition, repetition):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=False)
        times = tuple(map(float, self.times))
        if len(times) < 2 or any(not math.isfinite(t) for t in times):
            raise ValueError("Incomplete current recording clock")
        entries = []
        with (root / "values.bin").open("xb") as output:
            for meta, vectors, factor, operation in self.channels:
                if any(len(v) != len(times) for v in vectors):
                    raise ValueError("Incomplete native current recording")
                if operation == "difference":
                    values = array("d", ((a-b)*factor for a,b in zip(*vectors)))
                else:
                    values = array("d", (sum(v[i] for v in vectors)*factor for i in range(len(times))))
                if any(not math.isfinite(v) for v in values):
                    raise ValueError("Non-finite recorded current")
                if sys.byteorder != "little":
                    values.byteswap()
                raw = values.tobytes()
                payload = zlib.compress(raw)
                offset = output.tell()
                output.write(payload)
                entries.append(dict(meta, condition=condition, repetition=repetition, units="nA",
                    offset=offset, compressed_bytes=len(payload), samples=len(times),
                    sha256=hashlib.sha256(raw).hexdigest()))
        record = {"schema": "digifly-current-traces-v1", "mode": self.mode, "times_ms": times,
                  "encoding": "independent zlib blocks of little-endian float64", "data": "values.bin",
                  "signs": "Membrane/ion/leak/capacitive/synaptic/gap: positive outward. Injected: positive inward. Axial: positive parent toward child.",
                  "clock": "Native fixed-step observations at recorded NEURON t; no time shifts. Native mechanism currents retain their solver evaluation phase; axial currents are derived from the simultaneously saved node voltages.",
                  "sparse_policy": "Soma + up to seven evenly spaced electrical compartment indices per target neuron + edited compartment centers + active stimulation/connection sites. No random sites or copied values.",
                  "compartments": self.coverage, "junctions": self.junctions, "channels": entries,
                  "missing_ion_or_leak": "An absent channel is unavailable, not a fabricated zero; total membrane current still includes all membrane mechanisms and synapses, but excludes electrode stimulus.",
                  "total_values": len(entries)*len(times)}
        (root / "index.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record

    def check_storage(self, root):
        ancestor = Path(root).resolve()
        while not ancestor.exists():
            ancestor = ancestor.parent
        samples = math.ceil(self.duration/self.dt)+1
        required = len(self.channels)*(8*samples+1024) + 2*1024**3
        if shutil.disk_usage(ancestor).free < required:
            raise ValueError("Current recording would consume the 2 GiB disk reserve. Choose Sparse, fewer targets, a shorter run or a larger sample interval.")


def read_index(path):
    path = Path(path)
    compressed = path.with_name(path.name + ".gz")
    if path.is_file() and compressed.is_file():
        raise ValueError("Ambiguous current recording index")
    if compressed.is_file():
        with gzip.open(compressed, "rt", encoding="utf-8") as stream:
            record = json.load(stream)
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
    if record.get("schema") != "digifly-current-traces-v1" or record.get("data") != "values.bin":
        raise ValueError("Unsupported current recording")
    return record


def read_values(index_path, channel, *, compact=False):
    count = int(channel["samples"])
    size = int(channel["compressed_bytes"])
    offset = int(channel["offset"])
    if count < 2 or count > MAX_CURRENT_TRACE_SAMPLES or size < 1 or size > count*8+65536 or offset < 0:
        raise ValueError("Invalid current recording block")
    with Path(index_path).with_name("values.bin").open("rb") as source:
        source.seek(offset)
        payload = source.read(size)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(payload, count*8+1)
    if (len(raw) != count*8 or not decoder.eof or decoder.unused_data
            or hashlib.sha256(raw).hexdigest() != channel["sha256"]):
        raise ValueError("Current recording is incomplete or changed")
    values = array("d")
    values.frombytes(raw)
    if sys.byteorder != "little":
        values.byteswap()
    if any(not math.isfinite(v) for v in values):
        raise ValueError("Non-finite saved current")
    return values if compact else tuple(values)
