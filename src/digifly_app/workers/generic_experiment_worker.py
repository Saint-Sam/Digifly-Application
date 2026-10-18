#!/usr/bin/env python3
"""Standalone HH and source-bound membrane worker for Experiment Builder.

This worker and its bundled sibling scheduler are executed by the user's
selected Arbor or NEURON Python, which need not have Digifly installed.
"""

from __future__ import annotations

import argparse
import bisect
import copy
import csv
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import shutil
import struct
import sys
import subprocess
import tempfile
from typing import Any, Mapping, Sequence

try:
    from . import independent_trials as _trials
except ImportError:
    # CPython's private Windows ._pth isolation intentionally omits the script
    # directory. Load this app-owned sibling explicitly, never from CWD or an
    # ambient PYTHONPATH. This changes helper discovery, not model execution.
    _trials_spec = importlib.util.spec_from_file_location("digifly_independent_trials", Path(__file__).with_name("independent_trials.py"))
    if _trials_spec is None or _trials_spec.loader is None:
        raise ImportError("The app-owned independent-trial helper is missing")
    _trials = importlib.util.module_from_spec(_trials_spec)
    _trials_spec.loader.exec_module(_trials)


REQUEST_SCHEMA_VERSION = 1
RESULT_SCHEMA_VERSION = 1
EDGE_MANIFEST_SCHEMA_VERSION = 2
LEGACY_EDGE_MANIFEST_SCHEMA_VERSION = 1
ARBOR_ROOT_STUB_MIN_UM = 0.001
NEURON_TARGET_SEGMENT_UM = 40.0
ELECTRICAL_CONTACT_TARGET_SEGMENT_UM = 2.5
NEURON_MAX_TOTAL_SEGMENTS = 2_000_000
# Operational local limits, not scientific acceptance margins. Forecast before
# generating exact native clocks/envelopes; never silently coarsen a waveform.
ARBOR_MAX_NATIVE_EPOCHS = 100_000
ARBOR_MAX_RAMP_KNOTS = 250_000
ARBOR_MAX_NATIVE_REPORT_VALUES = 25_000_000
ARBOR_MAX_NATIVE_DETECTOR_EVENTS = 1_000_000
NEURON_GAP_SOURCE_HASHES = {
    "Gap": "e3ab9d0a37811314d3baa8461050e6d65fbb9b8e163ff6618a6d9f2f2549c1f2",
    "RectGap": "d9fa308ff0433ad0eb424017fff4384d5c06915ca9a2cbe25485ad176db3a8d6",
    "HeteroRectGap": "22b505af799076bb8079f204d222adfa72432f8609bd89fd050f7ffb325c9c2f",
}
_LOADED_NEURON_MECHANISM_LIBRARIES: set[str] = set()
NATIVE_CHANNELS = {
    "augustin_nat": ("nat", "609f7786edf939ae7ac44f2e06216a99fc3217fd2ee5fbb449982516da1ab925"),
    "augustin_nap": ("nap", "582dfd3fad7505554de5742a2f59c01093124ee4fd183440a4a5e7cfa8e31126"),
    "augustin_k": ("k", "73f5d0a0f1baaf575430c9744c6a5e060567831e6b94d1e1add2df6defd30da7"),
    "para": ("na16a", "fa6bfeba0d840a8871097f9275a2bf14883a02833dce84d031e91455c5e922c5"),
    "para_alt": ("na14a", "530eb3922de00528b8ff99073f9eb5c2785081ac4e0f19733d82c0f883c76d6a"),
    "shaker": ("kv14sh", "c33e0476afbc638e32d38f02931ed4909d4787d05199340dbd3fc45d6e9e698a"),
    "shal": ("kv42shal", "9100f7532effefa4c9b4d46842eabfff4d4c78d2839599784211c9fc7d0a14d0"),
    "shab": ("kv21shab", "c70493b3611722548f6a29ff864fdb2562b016bffb180befaa82764d92628c2e"),
    "shaw": ("kv31shaw", "7beacdda766d17fb05b69f3db89a2e68d1e2b4ee091b284e43290f7b160cf6e5"),
    "cacophony": ("cav21cac", "3ba1367cb1ea0c83f1f086656352fe74c6e0c4d609919473c81aa5fa535baca5"),
    "ca_alpha1t": ("cav31t", "24bae2629d65f3671ae8681acbd9245fcee5c664b75ce4882525b88f13013dd7"),
}

HH_CONDUCTANCE_SCALES = {
    "hh_na": ("soma_gnabar_s_cm2", "branch_gnabar_s_cm2"),
    "hh_k": ("soma_gkbar_s_cm2", "branch_gkbar_s_cm2"),
    "hh_leak": ("soma_gl_s_cm2", "branch_gl_s_cm2"),
    "pas": ("g_pas_s_cm2",),
}


def _validate_condition_manipulations(circuit: Mapping[str, Any], condition: Mapping[str, Any]) -> None:
    ids = {str(value) for value in circuit.get("neuron_ids") or ()}
    disabled = {str(value) for value in condition.get("disabled_neuron_ids") or ()}
    if disabled - ids:
        raise ValueError("Condition ablation targets a neuron outside the circuit")
    allowed = set(NATIVE_CHANNELS) | set(HH_CONDUCTANCE_SCALES) | {"hh"}
    for key, value in dict(condition.get("mechanism_scales") or {}).items():
        if key not in allowed:
            raise ValueError(f"Unknown conductance-scale mechanism {key!r}; use a locked channel key, hh, hh_na, hh_k, hh_leak or pas")
        if not math.isfinite(float(value)) or float(value) < 0:
            raise ValueError(f"Invalid conductance scale for {key!r}; require a finite nonnegative multiplier")


def _scale_cell_biophysics(cell: Mapping[str, Any], scales: Mapping[str, Any]) -> dict[str, Any]:
    """Apply multipliers after defaults/neuron/edge precedence, without changing kinetics."""
    result = copy.deepcopy(dict(cell))
    def scaled_hh(payload: Mapping[str, Any]) -> dict[str, Any]:
        hh = dict(payload)
        for group, fields in HH_CONDUCTANCE_SCALES.items():
            factor = float(scales.get(group, 1.0)) * (float(scales.get("hh", 1.0)) if group != "pas" else 1.0)
            for field in fields:
                if field in hh:
                    hh[field] = float(hh[field]) * factor
        return hh
    def scaled_membrane(payload: Mapping[str, Any]) -> dict[str, Any]:
        membrane = copy.deepcopy(dict(payload))
        for key, assignment in dict(membrane.get("channels") or {}).items():
            factor = float(scales.get(key, 1.0))
            for field in ("soma_gbar_s_cm2", "branch_gbar_s_cm2"):
                assignment[field] = float(assignment.get(field, 0.0)) * factor
        return membrane
    result["hh"] = scaled_hh(result.get("hh") or {})
    result["membrane"] = scaled_membrane(result.get("membrane") or {})
    result["compartment_overrides"] = {key: scaled_hh(value) for key, value in dict(result.get("compartment_overrides") or {}).items()}
    result["compartment_mechanism_overrides"] = {key: scaled_membrane(value) for key, value in dict(result.get("compartment_mechanism_overrides") or {}).items()}
    return result


def _condition_cells(cells: Mapping[str, Mapping[str, Any]], condition: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    disabled = {str(value) for value in condition.get("disabled_neuron_ids") or ()}
    result = {neuron_id: _scale_cell_biophysics(cell, dict(condition.get("mechanism_scales") or {}))
              for neuron_id, cell in cells.items() if neuron_id not in disabled}
    for cell in result.values():
        if cell.get("regional"):
            cell["regional_plan"] = _regional_plan(_swc_rows(Path(cell["swc_path"])), cell)
    return result


def _condition_edges(edge_manifest: Mapping[str, Any], active_ids: Sequence[str]) -> dict[str, Any]:
    result = copy.deepcopy(dict(edge_manifest))
    active = set(active_ids)
    result["neuron_ids"] = list(active_ids)
    result["chemical_edges"] = [edge for edge in result.get("chemical_edges") or ()
                                if str(edge["pre_id"]) in active and str(edge["post_id"]) in active]
    result["electrical_edges"] = [edge for edge in result.get("electrical_edges") or ()
                                  if str(edge["neuron_a"]) in active and str(edge["neuron_b"]) in active]
    return result


def _validate_gap_contact_placement(contact: Mapping[str, Any], policy: Mapping[str, Any]) -> None:
    placement = str(policy.get("placement_policy") or "imported_contact_sites")
    if placement not in {"imported_contact_sites", "soma_pair", "ais_pair"}:
        raise ValueError("Unknown electrical contact placement policy")
    for end in ("pre", "post"):
        provenance = dict(contact.get("coordinate_sources") or {}).get(end)
        if placement == "imported_contact_sites":
            if provenance not in {f"{end}_xyz", f"{'post' if end=='pre' else 'pre'}_xyz_fallback"}:
                raise ValueError("Electrical coordinate source details is invalid")
            if f"{end}_source_edge_fraction" in contact:
                raise ValueError("Imported contacts cannot acquire a synthetic edge fraction")
        elif provenance != f"synthetic_geometric_{placement}" or float(contact.get(f"{end}_source_edge_fraction", -1)) != .5:
            raise ValueError("Synthetic electrical placement requires an explicit geometric edge midpoint")
    if placement != "imported_contact_sites":
        assumption = dict(contact.get("placement_assumption") or {})
        rowids = list(contact.get("source_contact_rowids") or ())
        if assumption.get("biological_contact") is not False or assumption.get("policy") != placement or not rowids or int(contact.get("source_contact_count", 0)) != len(rowids):
            raise ValueError("Synthetic electrical placement must retain imported pair evidence and label its modelling assumption")


def _geometric_pair_site(rows: Sequence[Any], cell: Mapping[str, Any], placement: str) -> dict[str, Any]:
    """One declared soma or proximal type-2 AIS-proxy midpoint, never an anatomical contact."""
    plan = _regional_plan(rows, cell)
    region = "ais" if placement == "ais_pair" else "soma"
    candidates = _regional_sites(plan, region, cell)
    by_id = {row[0]:row for row in rows}
    _, soma_point = _target_soma_node(rows, prefer_rostral=bool(cell.get("prefer_rostral_soma", False)))
    spec = min(candidates, key=lambda s: (min(math.dist(by_id[s["node_id"]][2:5],soma_point),
                    math.dist(by_id.get(by_id[s["node_id"]][6],by_id[s["node_id"]])[2:5],soma_point)),s["node_id"]))
    row = by_id[spec["node_id"]]
    parent = by_id.get(row[6], row)
    return {"source_node_id":int(spec["source_node_id"]), "source_edge_fraction":.5,
        "coordinate_um":[(float(parent[a])+float(row[a]))/2 for a in (2,3,4)],
        "selection":"Nearest geometric type-2 AIS-proxy incoming edge" if region == "ais" else "Shared geometric soma-proxy incoming edge"}


def _regional_contact_site(plan: Mapping[str, Any], run_node_id: int, fraction: float) -> tuple[int, float]:
    if not math.isfinite(float(fraction)) or float(fraction) != .5:
        raise ValueError("Synthetic contacts require the declared central CV")
    spec = next((s for s in plan["sections"] if int(s["node_id"]) == run_node_id), None)
    if spec is None:
        raise ValueError("Synthetic contact has no mapped incoming SWC edge")
    return int(spec["section_id"]), float(fraction)


def _arbor_gap_contact_locset(plan: Mapping[str, Any] | None, node_locset: str, run_node_id: int,
                              contact: Mapping[str, Any], endpoint: str) -> str:
    if f"{endpoint}_source_edge_fraction" not in contact:
        return node_locset
    if not plan:
        raise ValueError("Synthetic electrical placement requires the shared regional mesh")
    _, fraction = _regional_contact_site(plan, run_node_id, float(contact[f"{endpoint}_source_edge_fraction"]))
    return f'(on-components {fraction:.17g} (tag {1_000_000+run_node_id}))'


def _native_payloads(circuit: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(circuit.get("membrane") or {})] + [
        dict(value or {}) for value in dict(circuit.get("neuron_mechanism_overrides") or {}).values()
    ] + [
        dict(value or {}) for neuron_map in dict(circuit.get("compartment_mechanism_overrides") or {}).values()
        for value in dict(neuron_map or {}).values()
    ]


def _validated_native_assignment(key: str, channel: Mapping[str, Any]) -> None:
    expected = NATIVE_CHANNELS.get(key)
    if expected is None or (channel.get("suffix"), channel.get("source_sha256")) != expected:
        raise ValueError(f"Unverified native channel identity: {key}")
    for field in ("soma_gbar_s_cm2", "branch_gbar_s_cm2"):
        value = float(channel.get(field, 0))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {key} density: {field}")
    for name, value in dict(channel.get("parameters") or {}).items():
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) or not math.isfinite(float(value)):
            raise ValueError(f"Invalid native channel parameter: {key}.{name}")
        # Global solver/environment parameters must not be treated as per-channel knobs.
        if name in {"gbar", "v", "celsius", "ena", "ek", "eca", "ina", "ik", "ica"}:
            raise ValueError(f"Reserved native channel parameter: {key}.{name}")


def _prepare_native_channels(request: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    """Compile exact sources with the selected interpreter's compiler, never a shared cwd library."""
    sources = dict(request.get("native_channel_sources") or {})
    required = {
        key for membrane in _native_payloads(dict(request["circuit"]))
        for key in _active_native_channels(membrane)
    }
    if set(sources) != required:
        raise ValueError("Native channel source set does not match the selected circuit")
    if not required:
        return {}
    for key, record in sources.items():
        expected = NATIVE_CHANNELS.get(key)
        if expected is None or (record.get("suffix"), record.get("sha256")) != expected:
            raise ValueError(f"Unverified native channel source: {key}")
        if _sha256(Path(record["path"])) != expected[1]:
            raise ValueError(f"Native channel source hash mismatch: {key}")
    builder_record = dict(request.get("native_builder") or {})
    builder_path = Path(str(builder_record.get("path") or ""))
    if not builder_path.is_file() or _sha256(builder_path) != builder_record.get("sha256"):
        raise ValueError("Native channel compiler helper identity mismatch")
    spec = importlib.util.spec_from_file_location("digifly_native_compiler", builder_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    from neuron import h
    import neuron
    # Parse standard hoc morphology code before registering Augustin's SUFFIX
    # k, which otherwise collides with Import3d's local variable spelling.
    if not h.load_file("import3d.hoc"):
        raise RuntimeError("NEURON morphology importer could not initialize")
    python = Path(sys.executable).absolute()
    runtime, environment = helper._probe_runtime(python)
    if runtime["neuron_version"] != neuron.__version__ or Path(runtime["neuron_file"]).resolve() != Path(neuron.__file__).resolve():
        raise RuntimeError("Selected NEURON import differs from its isolated compiler runtime; choose a compatible isolated interpreter")
    compiler = helper._resolve_nrnivmodl(None, runtime=runtime, probe_environment=environment)
    build = output_dir / "native_channels"
    build.mkdir(exist_ok=False)
    staged = build / "sources"
    staged.mkdir()
    for key, record in sources.items():
        target = staged / f"{key}.mod"
        target.write_bytes(Path(record["path"]).read_bytes())
        if _sha256(target) != record["sha256"]:
            raise ValueError(f"Staged native channel hash mismatch: {key}")
    environment = helper._build_environment(python=python, nrnivmodl=compiler, runtime=runtime, output=build)
    command = [str(compiler), "sources"]
    completed = subprocess.run(command, cwd=build, env=environment, capture_output=True, text=True, timeout=300)
    (build / "build.log").write_text(completed.stdout + completed.stderr)
    if completed.returncode:
        raise RuntimeError("Native channel compilation failed; see native_channels/build.log")
    library = helper._find_library(build)
    # Do not allow an ambient mechanism with the same name to replace our locked source.
    for key in required:
        probe = h.Section()
        try:
            probe.insert(NATIVE_CHANNELS[key][0])
        except ValueError:
            pass
        else:
            raise RuntimeError(f"Runtime already contains an unverified channel: {key}")
        finally:
            h.delete_section(sec=probe)
    if not h.nrn_load_dll(str(library)):
        raise RuntimeError("Native channel library could not load into the selected NEURON runtime")
    for key in required:
        probe = h.Section()
        try:
            probe.insert(NATIVE_CHANNELS[key][0])
        finally:
            h.delete_section(sec=probe)
    evidence = {
        "engine": "neuron", "python": str(python), "neuron_version": neuron.__version__,
        "sources": sources, "builder": builder_record, "command": command,
        "compiler_sha256": _sha256(compiler), "library": str(library), "library_sha256": _sha256(library),
    }
    _atomic_json(build / "manifest.json", evidence)
    return evidence


def _load_precompiled_native_channels(evidence: Mapping[str, Any]) -> None:
    """Fresh trial interpreters load the parent's locked library, never compile."""
    if not evidence:
        return
    import neuron
    from neuron import h
    library = Path(evidence["library"])
    if neuron.__version__ != evidence["neuron_version"] or _sha256(library) != evidence["library_sha256"]:
        raise ValueError("Independent trial native-channel library/runtime mismatch")
    builder = evidence["builder"]
    if _sha256(Path(builder["path"])) != builder["sha256"]:
        raise ValueError("Independent trial native-channel builder changed")
    for key, record in evidence["sources"].items():
        if (record.get("suffix"), record.get("sha256")) != NATIVE_CHANNELS.get(key) or _sha256(Path(record["path"])) != record["sha256"]:
            raise ValueError("Independent trial native-channel source changed")
    if not h.load_file("import3d.hoc"):
        raise RuntimeError("NEURON morphology importer could not initialize")
    for key in evidence["sources"]:
        probe = h.Section()
        try:
            probe.insert(NATIVE_CHANNELS[key][0])
        except ValueError:
            pass
        else:
            raise ValueError("Independent trial runtime contains an unverified channel collision")
        finally:
            h.delete_section(sec=probe)
    if not h.nrn_load_dll(str(library)):
        raise RuntimeError("Independent trial native-channel library could not load")


def _decorate_neuron_section(section: Any, hh: Mapping[str, Any], membrane: Mapping[str, Any], *, soma: bool) -> None:
    section.Ra = float(hh["ra_ohm_cm"])
    section.cm = float(hh["cm_uF_cm2"])
    section.insert("hh")
    section.ena = float(hh["ena_mV"])
    section.ek = float(hh["ek_mV"])
    values = _region_hh(hh, soma=soma)
    if membrane.get("replace_builtin_hh", False):
        values = {**values, "gnabar": 0.0, "gkbar": 0.0}
    for segment in section:
        for key, value in values.items():
            setattr(segment.hh, key, value)
    active = set(_active_native_channels(membrane))
    for key, raw in dict(membrane.get("channels") or {}).items():
        if key not in active:
            continue
        channel = dict(raw)
        _validated_native_assignment(key, channel)
        suffix = channel["suffix"]
        section.insert(suffix)
        if key in {"cacophony", "ca_alpha1t"}:
            from neuron import h
            # Honor the editor's fixed reversal rather than temperature-dependent
            # runtime default Nernst initialization overwriting it.
            h.ion_style("ca_ion", 0, 1, 0, 0, 0, sec=section)
            section.eca = float(hh["eca_mV"])
        density = float(channel["soma_gbar_s_cm2" if soma else "branch_gbar_s_cm2"])
        if hh.get("active_scope") == "soma" and not soma:
            density = 0.0
        for segment in section:
            mechanism = getattr(segment, suffix)
            mechanism.gbar = density
            for name, value in dict(channel.get("parameters") or {}).items():
                if not hasattr(mechanism, name):
                    raise ValueError(f"Unknown native channel parameter: {key}.{name}")
                setattr(mechanism, name, float(value))


def _prepare_arbor_channels(request: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    sources = dict(request.get("native_channel_sources") or {})
    required = {key for membrane in _native_payloads(dict(request["circuit"]))
                for key in _active_native_channels(membrane)}
    if set(sources) != required:
        raise ValueError("Native channel source set does not match the selected circuit")
    if not required:
        return {}
    for key, record in sources.items():
        if (record.get("suffix"), record.get("sha256")) != NATIVE_CHANNELS.get(key):
            raise ValueError(f"Unverified native channel source: {key}")
        if _sha256(Path(record["path"])) != record["sha256"]:
            raise ValueError(f"Native channel source hash mismatch: {key}")
    translator = dict(request.get("arbor_channel_translator") or {})
    translator_path = Path(str(translator.get("path") or ""))
    if not translator_path.is_file() or _sha256(translator_path) != translator.get("sha256"):
        raise ValueError("Arbor channel translator identity mismatch")
    import arbor
    if arbor.__version__ != "0.12.2":
        raise RuntimeError("Channel translations are tested against Arbor 0.12.2; select that runtime")
    spec = importlib.util.spec_from_file_location("digifly_arbor_translation", translator_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    build = output_dir / "arbor_channels"
    build.mkdir(exist_ok=False)
    staged = build / "sources"
    staged.mkdir()
    translations = {}
    for key, record in sources.items():
        data = Path(record["path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"Native channel source changed while staging: {key}")
        target = staged / f"{record['suffix']}.mod"
        target.write_text(helper.translate(key, data.decode("utf-8")), encoding="utf-8")
        translations[key] = {"path": str(target), "sha256": _sha256(target)}
    arbor_root = Path(arbor.__file__).resolve().parent
    modcc = arbor_root / "bin" / "modcc"
    compiler = shutil.which("c++")
    if not modcc.is_file() or not compiler:
        raise RuntimeError("Arbor catalogue compiler tools are missing")
    environment = os.environ.copy()
    generated = build / "generated"
    generated.mkdir()
    command = [str(modcc), "-t", "cpu", "-N", "arb", "-c", "digifly_channels", "-o", str(generated),
               *map(str, sorted(staged.glob("*.mod")))]
    compiled = subprocess.run(command, cwd=build, env=environment, capture_output=True, text=True, timeout=300)
    (build / "build.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        raise RuntimeError("Arbor channel source generation failed; see arbor_channels/build.log")
    # modcc emits a self-contained C-ABI plugin; the generated CPU code uses
    # header-only math and stdlib, not Arbor's static solver. Avoid linking a
    # second solver (and incompatible wheel LTO) into a mechanism catalogue.
    library = build / "digifly_channels-catalogue.so"
    link_command = [compiler, "-std=c++17", "-O2", "-shared", "-fPIC", "-DSTANDALONE=1",
                    "-I", str(arbor_root / "include"), *map(str, sorted(generated.glob("*.cpp"))),
                    "-o", str(library)]
    linked = subprocess.run(link_command, cwd=build, env=environment, capture_output=True, text=True, timeout=300)
    with (build / "build.log").open("a") as log:
        log.write(linked.stdout + linked.stderr)
    if linked.returncode or not library.is_file():
        raise RuntimeError("Arbor channel ABI plugin linking failed; see arbor_channels/build.log")
    catalogue = arbor.load_catalogue(str(library))
    for record in sources.values():
        if record["suffix"] not in catalogue:
            raise RuntimeError("Compiled Arbor catalogue is missing a selected channel")
    evidence = {"engine": "arbor", "arbor_version": arbor.__version__, "sources": sources,
                "translations": translations, "translator": translator, "command": command,
                "link_command": link_command, "build_method": "modcc_generated_cpu_C_ABI_plugin_no_static_solver",
                "modcc_sha256": _sha256(modcc),
                "headers": {str(p.relative_to(arbor_root)): _sha256(p) for p in (arbor_root / "include" / "arbor").glob("*.h*")},
                "compiler_path": compiler, "compiler_sha256": _sha256(Path(compiler)),
                "library": str(library), "library_sha256": _sha256(library)}
    _atomic_json(build / "manifest.json", evidence)
    return evidence


def _arbor_channel_properties(arbor: Any, catalogue_path: Path | None) -> Any:
    properties = arbor.neuron_cable_properties()
    if catalogue_path is not None:
        manifest = json.loads((catalogue_path.parent / "manifest.json").read_text())
        if manifest.get("arbor_version") != arbor.__version__ or manifest.get("library_sha256") != _sha256(catalogue_path):
            raise ValueError("Arbor channel catalogue ABI/hash mismatch")
        properties.catalogue.extend(arbor.load_catalogue(str(catalogue_path)), "digifly_channels_")
    return properties


def _decorate_arbor_channels(arbor: Any, decor: Any, hh: Mapping[str, Any], membrane: Mapping[str, Any], *, edge_region: str | None = None, edge_soma: bool = False) -> None:
    for ion, key in (("na","ena_mV"),("k","ek_mV"),("ca","eca_mV")):
        if edge_region is None:
            decor.set_ion(ion, rev_pot=float(hh[key]) * arbor.units.mV)
        else:
            decor.paint(edge_region, ion=ion, rev_pot=float(hh[key]) * arbor.units.mV)
    active = set(_active_native_channels(membrane))
    for region, soma in (((edge_region, edge_soma),) if edge_region else (("soma", True), ("branches", False))):
        region_expression = region if edge_region else f'"{region}"'
        values = _region_hh(hh, soma=soma)
        if membrane.get("replace_builtin_hh", False):
            values = {**values, "gnabar": 0.0, "gkbar": 0.0}
        decor.paint(region_expression, arbor.density("hh", values))
        for key in active:
            channel = dict(membrane["channels"][key])
            _validated_native_assignment(key, channel)
            density = float(channel["soma_gbar_s_cm2" if soma else "branch_gbar_s_cm2"])
            if hh.get("active_scope") == "soma" and not soma:
                density = 0.0
            values = {"gbar": density, **dict(channel.get("parameters") or {})}
            decor.paint(region_expression, arbor.density("digifly_channels_" + channel["suffix"], values))
_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}\Z")


def _required_sha256(value: Any, *, label: str) -> str:
    """Return a canonical digest or reject an unfrozen input identity."""

    digest = str(value or "").strip()
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{label} SHA-256 is missing or invalid")
    return digest.lower()


def _neuron_nseg_for_length(length_um: float, *, target_spacing_um: float | None = None) -> int:
    """Apply Digifly's uncapped ~40-um odd-segment discretization rule."""

    length = float(length_um)
    if not math.isfinite(length) or length < 0.0:
        raise RuntimeError(f"NEURON section length is invalid: {length_um!r}")
    spacing = NEURON_TARGET_SEGMENT_UM if target_spacing_um is None else float(target_spacing_um)
    if not math.isfinite(spacing) or spacing <= 0:
        raise ValueError("Invalid target segment spacing")
    target = max(1, int(math.ceil(length / spacing)))
    return target if target % 2 else target + 1


def _require_neuron_segment_budget(
    current_segments: int,
    additional_segments: int,
    *,
    segment_budget: int,
) -> None:
    """Reject a discretization before it can exceed the explicit run budget."""

    if (
        current_segments < 0
        or additional_segments < 0
        or segment_budget < 0
        or current_segments + additional_segments > segment_budget
    ):
        raise RuntimeError(
            "The selected NEURON circuit exceeds the explicit "
            f"{NEURON_MAX_TOTAL_SEGMENTS:,}-segment safety limit at "
            f"{NEURON_TARGET_SEGMENT_UM:g} um target spacing; reduce the selected circuit."
        )


def _neuron_discretization_plan(
    section_lengths_um: Sequence[float],
    *,
    segment_budget: int = NEURON_MAX_TOTAL_SEGMENTS,
) -> tuple[int, ...]:
    """Plan all section segment counts and fail before mutating any section."""

    plan = tuple(_neuron_nseg_for_length(length) for length in section_lengths_um)
    _require_neuron_segment_budget(0, sum(plan), segment_budget=segment_budget)
    return plan


def _apply_neuron_discretization(
    sections: Sequence[Any],
    *,
    segment_budget: int = NEURON_MAX_TOTAL_SEGMENTS,
) -> int:
    """Apply one fully budget-checked plan to imported NEURON sections."""

    plan = _neuron_discretization_plan(
        tuple(float(section.L) for section in sections),
        segment_budget=segment_budget,
    )
    for section, target_nseg in zip(sections, plan):
        section.nseg = target_nseg
    return sum(plan)


def _safe_neuron_path_component(neuron_id: str) -> str:
    """Return a bounded, deterministic directory name for a biological ID.

    Biological IDs remain unchanged in documents and result tables. Only the
    run-owned filesystem component is encoded here, so an ID containing an
    absolute path, ``..``, or a path separator can never select a destination.
    """

    raw = str(neuron_id)
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:32]
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"cell-{slug or 'neuron'}-{digest}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _emit(stage: str, message: str, **details: Any) -> None:
    print(
        json.dumps(
            {"at": _now(), "stage": stage, "message": message, **details},
            sort_keys=True,
        ),
        flush=True,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_swc(
    source: Path,
    output_dir: Path,
    *,
    expected_source_sha256: str | None = None,
    source_label: str = "Source SWC",
    coordinate_transform: Mapping[str, Any] | None = None,
) -> tuple[Path, Path, str]:
    """Write an isomorphic parent-before-child SWC plus an ID source details map.

    When an expected digest is supplied, it is checked against the same byte
    stream that is parsed. The resulting normalized file is therefore a frozen
    snapshot of the preflight-approved morphology rather than a second,
    potentially changed read of the source path.
    """

    transform = dict(coordinate_transform or {})
    if not transform and any(root.name == "export_swc"
                             and root.parent.name == "male-cns_v0.9"
                             for root in source.resolve().parents):
        raise ValueError("Male CNS SWC requires an explicit coordinate transform; rebuild the request")
    rule_scales = {
        "identity_um": 1.0,
        "male_cns_v09_phase1_legacy": 8.0,
        "male_cns_v09_release_um": 1.0,
        "male_cns_v09_neuprint_raw": 0.008,
    }
    scale = 1.0
    if transform:
        rule = transform.get("rule")
        if (transform.get("schema_version") != 1 or rule not in rule_scales
                or transform.get("scale_to_um") != rule_scales[rule]
                or transform.get("output_units") != "um"):
            raise ValueError("Unsupported SWC coordinate transform")
        scale = rule_scales[rule]
    nodes: dict[int, tuple[int, float, float, float, float, int]] = {}
    source_digest = hashlib.sha256()
    with source.open("rb") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            source_digest.update(raw_line)
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"SWC is not valid UTF-8 at {source}:{line_number}"
                ) from exc
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            if len(fields) < 7:
                raise ValueError(
                    f"SWC row has fewer than seven fields at {source}:{line_number}"
                )
            try:
                node_value = float(fields[0])
                type_value = float(fields[1])
                parent_value = float(fields[6])
                if not (
                    node_value.is_integer()
                    and type_value.is_integer()
                    and parent_value.is_integer()
                ):
                    raise ValueError("SWC identifiers and types must be integers")
                node_id = int(node_value)
                parent_id = int(parent_value)
                values = (
                    int(type_value),
                    float(fields[2]) * scale,
                    float(fields[3]) * scale,
                    float(fields[4]) * scale,
                    float(fields[5]) * scale,
                    parent_id,
                )
            except ValueError as exc:
                raise ValueError(f"Invalid SWC row at {source}:{line_number}") from exc
            if node_id in nodes:
                raise ValueError(f"Duplicate SWC node ID {node_id} in {source}")
            if parent_id == node_id or parent_id < -1:
                raise ValueError(f"Invalid parent {parent_id} for SWC node {node_id}")
            if not all(math.isfinite(value) for value in values[1:5]):
                raise ValueError(f"Invalid SWC geometry at {source}:{line_number}")
            if values[4] <= 0.0:
                raise ValueError(
                    f"Non-positive SWC radius at {source}:{line_number}; "
                    "repair the source morphology before simulation"
                )
            nodes[node_id] = values
    actual_source_sha256 = source_digest.hexdigest()
    if transform and transform.get("source_sha256") != actual_source_sha256:
        raise ValueError("SWC coordinate transform source checksum changed")
    if expected_source_sha256 is not None:
        expected_digest = _required_sha256(
            expected_source_sha256,
            label=source_label,
        )
        if actual_source_sha256 != expected_digest:
            raise ValueError(
                f"{source_label} checksum changed: expected {expected_digest}, "
                f"found {actual_source_sha256}"
            )
    roots = [node_id for node_id, values in nodes.items() if values[-1] == -1]
    if len(roots) != 1:
        raise ValueError(f"SWC must contain exactly one root; found {len(roots)}")
    children: dict[int, list[int]] = {node_id: [] for node_id in nodes}
    for node_id, values in nodes.items():
        parent_id = values[-1]
        if parent_id == -1:
            continue
        if parent_id not in nodes:
            raise ValueError(f"SWC node {node_id} has missing parent {parent_id}")
        children[parent_id].append(node_id)

    ordered: list[int] = []
    visited: set[int] = set()
    stack = [roots[0]]
    while stack:
        node_id = stack.pop()
        if node_id in visited:
            raise ValueError(f"Cycle detected at SWC node {node_id}")
        visited.add(node_id)
        ordered.append(node_id)
        stack.extend(reversed(sorted(children[node_id])))
    if len(ordered) != len(nodes):
        raise ValueError("SWC contains nodes disconnected from its root")
    remap = {old_id: new_id for new_id, old_id in enumerate(ordered, start=1)}

    normalized_path = output_dir / "normalized_input.swc"
    temporary_path = normalized_path.with_suffix(".swc.tmp")
    with temporary_path.open("w", encoding="utf-8") as handle:
        handle.write(
            "# Digifly parent-before-child topology normalization\n"
            f"# source_sha256 {actual_source_sha256}\n"
            f"# coordinate_scale_to_um {scale:.17g}\n"
            f"# coordinate_rule {transform.get('rule', 'identity_um')}\n"
        )
        for old_id in ordered:
            swc_type, x, y, z, radius, old_parent = nodes[old_id]
            new_parent = -1 if old_parent == -1 else remap[old_parent]
            handle.write(
                f"{remap[old_id]} {swc_type} {x:.17g} {y:.17g} {z:.17g} "
                f"{radius:.17g} {new_parent}\n"
            )
    os.replace(temporary_path, normalized_path)

    mapping_path = output_dir / "morphology_node_map.csv"
    with mapping_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("source_node_id", "run_node_id"))
        writer.writeheader()
        writer.writerows(
            {"source_node_id": old_id, "run_node_id": remap[old_id]}
            for old_id in ordered
        )
    return normalized_path, mapping_path, _sha256(normalized_path)


def _swc_rows(
    path: Path,
) -> tuple[tuple[int, int, float, float, float, float, int], ...]:
    rows: list[tuple[int, int, float, float, float, float, int]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            fields = stripped.split()
            rows.append(
                (
                    int(fields[0]),
                    int(fields[1]),
                    float(fields[2]),
                    float(fields[3]),
                    float(fields[4]),
                    float(fields[5]),
                    int(fields[6]),
                )
            )
    return tuple(rows)


def _target_soma_node(
    rows: Sequence[tuple[int, int, float, float, float, float, int]],
    *,
    prefer_rostral: bool,
) -> tuple[int, tuple[float, float, float]]:
    non_root = [row for row in rows if row[6] != -1]
    candidates = [row for row in non_root if row[1] == 1] or non_root
    if not candidates:
        raise ValueError("The morphology has no non-root cable segment for stimulation")
    target = (
        max(candidates, key=lambda row: (row[4], row[5]))
        if prefer_rostral
        else max(candidates, key=lambda row: row[5])
    )
    return target[0], (target[2], target[3], target[4])


def _arbor_root_stub_length(
    rows: Sequence[tuple[int, int, float, float, float, float, int]],
) -> float:
    # A topology adapter, not an extra biological soma cable. A radius-scaled
    # stub added backend-specific membrane area and changed spike timing.
    return ARBOR_ROOT_STUB_MIN_UM


def _arbor_morphology_and_segments_from_swc(
    arbor: Any,
    path: Path,
    *,
    prefer_rostral_soma: bool,
) -> tuple[Any, dict[int, int]]:
    """Build a cable tree directly, avoiding loader-specific SWC soma rules."""

    rows = _swc_rows(path)
    target_node_id, _point = _target_soma_node(
        rows,
        prefer_rostral=prefer_rostral_soma,
    )
    root = next(row for row in rows if row[6] == -1)
    root_id, root_type, root_x, root_y, root_z, root_radius, _parent = root
    direction_row = next(
        (
            row
            for row in rows
            if row[0] != root_id
            and (row[2] - root_x) ** 2
            + (row[3] - root_y) ** 2
            + (row[4] - root_z) ** 2
            > 0.0
        ),
        None,
    )
    if direction_row is None:
        unit = (1.0, 0.0, 0.0)
    else:
        delta = (
            direction_row[2] - root_x,
            direction_row[3] - root_y,
            direction_row[4] - root_z,
        )
        magnitude = math.sqrt(sum(value * value for value in delta))
        unit = tuple(value / magnitude for value in delta)
    stub_length = _arbor_root_stub_length(rows)
    root_proximal = arbor.mpoint(
        root_x - unit[0] * stub_length,
        root_y - unit[1] * stub_length,
        root_z - unit[2] * stub_length,
        root_radius,
    )
    root_distal = arbor.mpoint(root_x, root_y, root_z, root_radius)
    tree = arbor.segment_tree()
    segment_by_node: dict[int, int] = {}
    for node_id, swc_type, x, y, z, radius, parent_id in rows:
        point = arbor.mpoint(x, y, z, radius)
        tag = 1001 if node_id == target_node_id else swc_type
        if parent_id == -1:
            segment_id = tree.append(
                arbor.mnpos,
                root_proximal,
                root_distal,
                root_type,
            )
        else:
            if parent_id not in segment_by_node:
                raise ValueError(
                    f"Normalized SWC node {node_id} precedes parent {parent_id}"
                )
            segment_id = tree.append(segment_by_node[parent_id], point, tag)
        segment_by_node[node_id] = segment_id
    return arbor.morphology(tree), segment_by_node


def _arbor_morphology_from_swc(
    arbor: Any,
    path: Path,
    *,
    prefer_rostral_soma: bool,
) -> Any:
    morphology, _segments = _arbor_morphology_and_segments_from_swc(
        arbor,
        path,
        prefer_rostral_soma=prefer_rostral_soma,
    )
    return morphology


def _arbor_network_morphology_from_swc(
    arbor: Any,
    path: Path,
    *,
    prefer_rostral_soma: bool,
    contact_node_ids: set[int],
) -> tuple[Any, dict[int, str], dict[str, str]]:
    """Build a cable tree with unambiguous locsets for imported contacts.

    SWC node/segment indices are not Arbor branch indices. Contact-bearing
    segments therefore receive collision-free temporary morphology tags, and
    each junction is placed at the distal end of its exact tagged segment.
    """

    rows = _swc_rows(path)
    target_node_id, _point = _target_soma_node(
        rows,
        prefer_rostral=prefer_rostral_soma,
    )
    root = next(row for row in rows if row[6] == -1)
    root_id, _root_type, root_x, root_y, root_z, root_radius, _parent = root
    direction_row = next(
        (
            row
            for row in rows
            if row[0] != root_id
            and (row[2] - root_x) ** 2
            + (row[3] - root_y) ** 2
            + (row[4] - root_z) ** 2
            > 0.0
        ),
        None,
    )
    if direction_row is None:
        unit = (1.0, 0.0, 0.0)
    else:
        delta = (
            direction_row[2] - root_x,
            direction_row[3] - root_y,
            direction_row[4] - root_z,
        )
        magnitude = math.sqrt(sum(value * value for value in delta))
        unit = tuple(value / magnitude for value in delta)
    stub_length = _arbor_root_stub_length(rows)
    root_proximal = arbor.mpoint(
        root_x - unit[0] * stub_length,
        root_y - unit[1] * stub_length,
        root_z - unit[2] * stub_length,
        root_radius,
    )
    tree = arbor.segment_tree()
    segment_by_node: dict[int, int] = {}
    contact_locset_by_node: dict[int, str] = {}
    soma_tags = {1, 1001}
    target_tag = 1001
    for node_id, swc_type, x, y, z, radius, parent_id in rows:
        point = arbor.mpoint(x, y, z, radius)
        tag = 1001 if node_id == target_node_id else swc_type
        if node_id in contact_node_ids:
            tag = 1_000_000 + node_id
            contact_locset_by_node[node_id] = f"(distal (tag {tag}))"
            if swc_type == 1:
                soma_tags.add(tag)
        if node_id == target_node_id:
            target_tag = tag
        if parent_id == -1:
            segment_id = tree.append(
                arbor.mnpos,
                root_proximal,
                point,
                tag,
            )
        else:
            if parent_id not in segment_by_node:
                raise ValueError(
                    f"Normalized SWC node {node_id} precedes parent {parent_id}"
                )
            segment_id = tree.append(segment_by_node[parent_id], point, tag)
        segment_by_node[node_id] = segment_id

    soma_terms = [f"(tag {tag})" for tag in sorted(soma_tags)]
    soma_region = soma_terms[0]
    for term in soma_terms[1:]:
        soma_region = f"(join {soma_region} {term})"
    labels = {
        "all": "(all)",
        "soma": soma_region,
        "branches": f"(difference (all) {soma_region})",
        "soma-center": f"(distal (tag {target_tag}))",
    }
    return arbor.morphology(tree), contact_locset_by_node, labels


def _merged_hh(circuit: Mapping[str, Any], neuron_id: str) -> dict[str, Any]:
    values = dict(circuit.get("hh") or {})
    values.update(dict((circuit.get("neuron_overrides") or {}).get(neuron_id) or {}))
    return values


def _combined_leak(hh: Mapping[str, Any], *, soma: bool) -> tuple[float, float]:
    region = "soma" if soma else "branch"
    hh_g = float(hh[f"{region}_gl_s_cm2"])
    hh_e = float(hh[f"{region}_el_mV"])
    passive_g = float(hh["g_pas_s_cm2"])
    passive_e = float(hh["e_pas_mV"])
    total = hh_g + passive_g
    reversal = (hh_g * hh_e + passive_g * passive_e) / total if total else hh_e
    return total, reversal


def _region_hh(hh: Mapping[str, Any], *, soma: bool) -> dict[str, float]:
    region = "soma" if soma else "branch"
    gl, el = _combined_leak(hh, soma=soma)
    active = soma or str(hh.get("active_scope") or "all") == "all"
    return {
        "gnabar": float(hh[f"{region}_gnabar_s_cm2"]) if active else 0.0,
        "gkbar": float(hh[f"{region}_gkbar_s_cm2"]) if active else 0.0,
        "gl": gl,
        "el": el,
    }


def _pulse_windows(stimulus: Mapping[str, Any]) -> tuple[tuple[float, float], ...]:
    delay = float(stimulus["delay_ms"])
    duration = float(stimulus["pulse_width_ms"])
    if str(stimulus.get("waveform")) in {"step", "ramp"}:
        return ((delay, duration),)
    period = 1000.0 / float(stimulus["frequency_hz"])
    return tuple(
        (delay + index * period, duration)
        for index in range(int(stimulus["pulse_count"]))
    )


def _ramp_envelope(stimulus: Mapping[str, Any], amplitude: float) -> list[tuple[float, float]]:
    """One linear 0-to-peak ramp with an instantaneous drop at its declared end."""
    start = float(stimulus["delay_ms"])
    stop = start + float(stimulus["pulse_width_ms"])
    return [(start, 0.0), (stop, amplitude), (stop, 0.0)]


def _physical_ramp_current(stimulus: Mapping[str, Any], amplitude: float, time_ms: float) -> float:
    start = float(stimulus["delay_ms"])
    width = float(stimulus["pulse_width_ms"])
    if not all(math.isfinite(value) for value in (start, width, amplitude, time_ms)) or start < 0 or width <= 0:
        raise ValueError("Ramp parameters must be finite, with nonnegative onset and positive width")
    return amplitude*(time_ms-start)/width if start <= time_ms < start+width else 0.0


def _arbor_ramp_epoch_interval(edges: Mapping[str, Any], chemical_enabled: bool) -> float | None:
    # Arbor0.12.2 recipe.hpp stores connection delays as float32; simulation.cpp
    # sets epoch length to half the minimum delay. No chemical connections means
    # one finite run epoch (the native no-connection interval is effectively infinite).
    delays = [struct.unpack("f", struct.pack("f", float(contact["delay_ms"])))[0]
        for contact in edges.get("chemical_edges") or ()] if chemical_enabled else []
    return min(delays)/2 if delays else None


def _arbor_native_clock_forecast(duration_ms: float, dt_ms: float,
                                  epoch_interval_ms: float | None = None) -> dict[str, Any]:
    final, dt = float(duration_ms), float(dt_ms)
    interval = final if epoch_interval_ms is None else float(epoch_interval_ms)
    if not all(math.isfinite(v) and v > 0 for v in (final,dt,interval)) or (epoch_interval_ms is not None and dt-interval > 10*2**-23):
        raise ValueError("Arbor native quadrature clock requires positive finite duration/dt and an epoch accommodating dt")
    if not math.isfinite(final/interval) or not math.isfinite(final/dt):
        raise ValueError("Arbor exact native clock forecast exceeds finite local bounds")
    epochs = math.ceil(final/interval)+1  # conservative repeated-addition allowance
    if epochs > ARBOR_MAX_NATIVE_EPOCHS:
        raise ValueError(f"Arbor exact native clock requires up to {epochs:,} epochs; local limit {ARBOR_MAX_NATIVE_EPOCHS:,}. No clock or waveform was coarsened.")
    steps = math.ceil(final/dt)+epochs  # at most one adjusted final step per epoch
    return {"duration_ms":final,"integration_dt_ms":dt,"epoch_interval_ms":interval,
            "epoch_count_upper_bound":epochs,"step_count_upper_bound":steps}


def _arbor_native_steps(duration_ms: float, dt_ms: float, epoch_interval_ms: float | None = None,
                         *, include_source_operands: bool = False):
    """Exact pinned Arbor0.12.2 epoch/timestep_range arithmetic, without integration."""
    forecast = _arbor_native_clock_forecast(duration_ms,dt_ms,epoch_interval_ms)
    final,dt,interval = (forecast[k] for k in ("duration_ms","integration_dt_ms","epoch_interval_ms"))
    start,epochs = 0.0,0
    while start < final:
        epochs += 1
        if epochs > ARBOR_MAX_NATIVE_EPOCHS:
            raise ValueError("Arbor exact native clock exceeded its declared epoch limit")
        stop = min(start+interval,final)
        if stop <= start:
            raise ValueError("Arbor exact native clock cannot advance")
        delta = stop-start
        full = int(delta/dt)
        count = full if full*dt+sys.float_info.epsilon*stop*full >= delta else full+1
        for index in range(count):
            left = start+index*dt
            right = stop if index+1 >= count else start+(index+1)*dt
            yield (left,right,start,index) if include_source_operands else (left,right)
        start = stop


def _arbor_probe_request(lefts: Sequence[float], rights: Sequence[float],
                         previous: float | None = None) -> float:
    """One representable query strictly inside both source arithmetic intervals."""
    if len(lefts) != 2 or len(rights) != 2 or not all(math.isfinite(t) for t in (*lefts,*rights)):
        raise ValueError("Arbor observation intervals must have two finite source candidates")
    lower,upper = max(lefts),min(rights)
    if min(lefts) < 0 or any(right <= left for left,right in zip(lefts,rights)) or lower >= upper:
        raise ValueError("Arbor observation intervals have no common strict interior")
    request = math.nextafter(lower,math.inf)
    if not lower < request < upper or (previous is not None and (not math.isfinite(previous) or request <= previous)):
        raise ValueError("Arbor observation request has no representable, strictly increasing common interior")
    return request


def _arbor_native_probe_clock(duration_ms: float, dt_ms: float,
                              epoch_interval_ms: float | None = None) -> tuple[list[float],list[float]]:
    """Source-derived API queries; returned report timestamps remain native lefts.

    Querying a separately rounded left can land one ULP before a compiled FMA
    left and make timestep_range::find observe the preceding state. Use one
    strictly interior representable query in the intersection of the separate
    and single-rounded source intervals. Require the pinned find algorithm to
    select this ordinal in BOTH variants, or fail before dispatch. This changes
    only observation requests, never integration, detector events or output time.
    """
    forecast = _arbor_native_clock_forecast(duration_ms,dt_ms,epoch_interval_ms)
    final,dt,interval = (forecast[k] for k in ("duration_ms","integration_dt_ms","epoch_interval_ms"))
    dt_fraction = Fraction(dt)
    lefts,requests = [],[]
    previous_start = None
    for left,right,start,index in _arbor_native_steps(final,dt,epoch_interval_ms,include_source_operands=True):
        if start != previous_start:
            stop = min(start+interval,final)
            full = int((stop-start)/dt)
            count = full if full*dt+sys.float_info.epsilon*stop*full >= stop-start else full+1
            start_fraction = Fraction(start)
            previous_start = start
        fused_left = float(start_fraction+index*dt_fraction)
        fused_right = stop if index+1 >= count else float(start_fraction+(index+1)*dt_fraction)
        request = _arbor_probe_request((left,fused_left),(right,fused_right),requests[-1] if requests else None)
        guess = min(int((request-start)/dt),count-1)
        for fused in (False,True):
            candidate_left = float(start_fraction+guess*dt_fraction) if fused else start+guess*dt
            candidate_right = stop if guess+1 >= count else (
                float(start_fraction+(guess+1)*dt_fraction) if fused else start+(guess+1)*dt)
            found = guess if candidate_left <= request < candidate_right else guess-1 if request < candidate_left else guess+1
            if found != index:
                raise ValueError("Arbor strict-interior observation request has ambiguous native find projection")
        lefts.append(left)
        requests.append(request)
    return lefts,requests


def _arbor_ramp_forecast(stimulus: Mapping[str, Any], dt_ms: float, duration_ms: float,
                          epoch_interval_ms: float | None = None) -> dict[str, Any]:
    _physical_ramp_current(stimulus,0.,0.)
    start = float(stimulus["delay_ms"])
    stop = start+float(stimulus["pulse_width_ms"])
    horizon = min(float(duration_ms),stop+float(dt_ms))
    forecast = _arbor_native_clock_forecast(horizon,dt_ms,epoch_interval_ms)
    window = max(0.,min(float(duration_ms),stop)-max(0.,start-float(dt_ms)))
    interval = forecast["epoch_interval_ms"]
    knots = 2*(math.ceil(window/float(dt_ms))+6*(math.ceil(window/interval)+2)+2)
    if knots > ARBOR_MAX_RAMP_KNOTS:
        raise ValueError(f"Arbor ramp requires up to {knots:,} exact-grid envelope knots; local limit {ARBOR_MAX_RAMP_KNOTS:,}. No current, timing or dt was substituted.")
    forecast["envelope_knot_count_upper_bound"] = knots
    return forecast


def _arbor_ramp_envelope(stimulus: Mapping[str, Any], amplitude: float, integration_dt_ms: float,
                         duration_ms: float, epoch_interval_ms: float | None = None) -> list[tuple[float, float]]:
    """API-only quadrature map: Arbor E(t_left) = physical I(t_midpoint).

    Arbor0.12.2 samples i_clamp at its step's left endpoint; NEURON9.0.1
    continuous Vector.play is evaluated at the midpoint before matrix setup.
    Bind physical midpoint values to the exact native left-step times. A plain
    -dt/2 envelope shift is insufficient at floating-point cutoff boundaries
    (e.g. .035-.01/2 is greater than .03). No physical input or output timestamp
    is shifted. Duplicate points retain the right-continuous numerical cutoff.
    """
    dt, final = float(integration_dt_ms), float(duration_ms)
    if not math.isfinite(dt) or dt <= 0 or not math.isfinite(final) or final <= 0:
        raise ValueError("Ramp quadrature requires a finite positive integration step and duration")
    _physical_ramp_current(stimulus, amplitude, 0.0)
    interval = final if epoch_interval_ms is None else float(epoch_interval_ms)
    if not math.isfinite(interval) or interval <= 0 or (epoch_interval_ms is not None and dt-interval > 10*2**-23):
        raise ValueError("Ramp quadrature epoch interval must accommodate the integration step")
    _arbor_ramp_forecast(stimulus,dt,final,epoch_interval_ms)
    samples = {}
    epoch_start = 0.0
    physical_start = float(stimulus["delay_ms"])
    physical_stop = float(stimulus["delay_ms"])+float(stimulus["pulse_width_ms"])
    while epoch_start < min(final, physical_stop+dt):
        epoch_stop = min(epoch_start+interval, final)
        if epoch_stop <= epoch_start:
            raise ValueError("Ramp quadrature epoch time cannot advance")
        delta = epoch_stop-epoch_start
        full = int(delta/dt)
        # Exact v0.12.2 timestep_range.hpp final-step roundoff convention.
        count = full if full*dt+sys.float_info.epsilon*epoch_stop*full >= delta else full+1
        if count:
            # Only the source ramp window and surrounding zero-current steps
            # require knots, not the entire potentially long recording window.
            first = max(0, int(math.floor((physical_start-epoch_start)/dt))-2)
            last = min(count, int(math.ceil((physical_stop-epoch_start)/dt))+2)
            indices = range(first,last)
            if epoch_start == 0.0:
                indices = sorted(set(indices) | {0})
            for index in indices:
                left = epoch_start+index*dt
                right = epoch_stop if index+1 >= count else epoch_start+(index+1)*dt
                midpoint = left+.5*(right-left)  # native timestep::midpoint()
                samples[left] = _physical_ramp_current(stimulus, amplitude, midpoint)
        epoch_start = epoch_stop
    samples[final] = 0.0
    envelope = []
    previous = 0.0
    for left,value in sorted(samples.items()):
        if previous != 0.0 and value == 0.0:
            envelope.append((left, amplitude))
        envelope.append((left,value))
        previous = value
    return envelope


def _ramp_quadrature_record(engine: str, experiment: Mapping[str, Any], stimulus: Mapping[str, Any],
                            condition: Mapping[str, Any], cells: Mapping[str, Any], edges: Mapping[str, Any]) -> dict[str, Any]:
    dt = float(experiment["integration_dt_ms"])
    interval = _arbor_ramp_epoch_interval(edges, bool(condition.get("chemical_synapses_enabled",True))) if engine == "arbor" else None
    targets = set(stimulus.get("target_neuron_ids") or cells)
    sites = []
    for neuron_id,cell in cells.items():
        if neuron_id not in targets:
            continue
        mapped = _regional_sites(cell["regional_plan"], stimulus["target_region"], cell) if cell.get("regional") else [None]
        amplitude = float(stimulus["amplitude_nA"])*float(condition.get("stimulus_scale",1.0))/len(mapped)
        for site in mapped:
            sites.append({"neuron_id":neuron_id,"source_node_id":site["source_node_id"] if site else None,
                "site":"source-edge midpoint" if site else "soma-center", "amplitude_nA":amplitude,
                "physical_envelope_ms_nA":_ramp_envelope(stimulus,amplitude),
                "api_envelope_ms_nA":_arbor_ramp_envelope(stimulus,amplitude,dt,float(experiment["duration_ms"]),interval)
                    if engine == "arbor" else _ramp_envelope(stimulus,amplitude)})
    return {"condition":str(condition["name"]),"engine":engine,"physical_stimulus":copy.deepcopy(dict(stimulus)),
        "integration_dt_ms":dt,"arbor_epoch_interval_ms":interval,"quadrature":"physical midpoint current",
        "api_time_offset_ms":0.0,"nominal_midpoint_offset_ms":dt/2,
        "api_envelope_kind":"Arbor i_clamp envelope" if engine == "arbor" else "Equivalent continuous Vector.play plus IClamp duration cutoff",
        "mapping":"Arbor E(t_left)=I(t_left+(t_right-t_left)/2) at exact native grid knots; NEURON continuous Vector.play evaluates I(t_midpoint)",
        "floating_boundary_policy":"No epsilon or rounded cutoff; native midpoint arithmetic and right-continuous exact-grid zero knots",
        "boundaries":"Physical current is zero before onset and at/after cutoff; duplicate-time cutoff is right-continuous",
        "short_step_policy":"Actual final/epoch step midpoint; saved waveform, onset/end, dt, voltage and report timestamps unchanged",
        "sites":sites}


def _arbor_current_clamps(arbor: Any, stimulus: Mapping[str, Any], amplitude: float, *,
                          integration_dt_ms: float | None = None, duration_ms: float | None = None,
                          epoch_interval_ms: float | None = None) -> list[Any]:
    units = arbor.units
    if stimulus.get("waveform") == "ramp":
        if integration_dt_ms is None or duration_ms is None:
            raise ValueError("Arbor ramp requires explicit integration step and duration for midpoint quadrature")
        return [arbor.i_clamp([(t*units.ms, a*units.nA) for t,a in
            _arbor_ramp_envelope(stimulus, amplitude, integration_dt_ms, duration_ms, epoch_interval_ms)])]
    return [arbor.i_clamp(delay*units.ms, duration*units.ms, amplitude*units.nA)
            for delay,duration in _pulse_windows(stimulus)]


def _neuron_current_clamps(h: Any, site: Any, stimulus: Mapping[str, Any], amplitude: float) -> list[Any]:
    objects = []
    for delay,duration in _pulse_windows(stimulus):
        clamp = h.IClamp(site)
        clamp.delay, clamp.dur, clamp.amp = delay, duration, amplitude
        objects.append(clamp)
        if stimulus.get("waveform") == "ramp":
            # Clamp duration supplies the instantaneous end cutoff. Vector.play
            # interpolates the physical current continuously between endpoints.
            times = [0.0, delay, delay+duration] if delay > 0 else [0.0, duration]
            values = [0.0, 0.0, amplitude] if delay > 0 else [0.0, amplitude]
            time_vector, amp_vector = h.Vector(times), h.Vector(values)
            amp_vector.play(clamp._ref_amp, time_vector, 1)
            objects.extend((time_vector, amp_vector))
    return objects


def _arbor_sampling_plan(experiment: Mapping[str, Any], edges: Mapping[str, Any],
                         condition: Mapping[str, Any], probe_count: int, *, detector_count: int = 0,
                         allocate: bool = True) -> dict[str, Any]:
    """Dense native observation clock; it does not change integration or events."""
    final,dt = float(experiment["duration_ms"]),float(experiment["integration_dt_ms"])
    sample_dt = float(experiment["recording"]["sample_dt_ms"])
    if not math.isfinite(sample_dt) or sample_dt < dt or probe_count < 1 or detector_count < 0:
        raise ValueError("Arbor native sampling requires positive probes and a sample interval at least dt")
    interval = _arbor_ramp_epoch_interval(edges,bool(condition.get("chemical_synapses_enabled",True)))
    forecast = _arbor_native_clock_forecast(final,dt,interval)
    values = forecast["step_count_upper_bound"]*probe_count
    events = forecast["step_count_upper_bound"]*detector_count
    if values > ARBOR_MAX_NATIVE_REPORT_VALUES:
        raise ValueError(f"Arbor dense native report requires up to {values:,} values; local limit {ARBOR_MAX_NATIVE_REPORT_VALUES:,}. No probes were dispatched.")
    if events > ARBOR_MAX_NATIVE_DETECTOR_EVENTS:
        raise ValueError(f"Arbor retained native detector observations require up to {events:,} events; local limit {ARBOR_MAX_NATIVE_DETECTOR_EVENTS:,}. No clock, recipe or detector recorder was dispatched.")
    native_lefts,probe_requests = _arbor_native_probe_clock(final,dt,interval) if allocate else (None,None)
    return {"schema":"digifly-arbor-native-sampling-v2", **forecast,
        "source_epoch_interval_ms":interval,
        "requested_sample_dt_ms":sample_dt,"probe_count":probe_count,
        "native_value_count_upper_bound":values,
        "native_detector_count":detector_count,"native_detector_event_count_upper_bound":events,
        "native_step_left_ms":native_lefts,"probe_request_ms":probe_requests,
        "probe_request_policy":"nextafter(max(exact separate/FMA lefts), +infinity), strictly below min(exact separate/FMA rights); pinned find must resolve the intended ordinal in both complete source variants",
        "probe_request_note":"API query times identify native step-left observations; they are NOT report timestamps or a physical/event-clock shift",
        "match_bound_ms_exclusive":dt/2,
        "match_roundoff_guard":"32 ULPs of requested/native time and dt; reject boundary-near matches and near-ties, never enlarge dt/2",
        "selection":"Nearest observed native state, strictly less than dt/2; ambiguous ties fail. No voltage interpolation or timestamp relabeling.",
        "output_timestamp_adjustment_ms":0.0,
        "physical_event_delay_threshold_policy":"Unchanged native Arbor detector interpolation and event delivery",
        "native_clock_equality":"Entire observed clock must exactly equal either the complete separate-product-plus-sum or complete correctly single-rounded fused start+index*dt source sequence; mixed candidates fail; no isclose/ULP time tolerance",
        "chemical_detector_threshold_mV":float(dict(edges.get("chemical_synapse_policy") or {}).get("spike_threshold_mV",0.0)),
        "exported_spike_threshold_mV":float(experiment["recording"].get("spike_threshold_mV",-20.0)),
        "native_clock_source":"https://github.com/arbor-sim/arbor/blob/v0.12.2/arbor/timestep_range.hpp",
        "native_sampling_source":"https://github.com/arbor-sim/arbor/blob/v0.12.2/arbor/backends/multicore/shared_state.cpp#L253"}


def _select_arbor_native_samples(data: Sequence[Any], plan: Mapping[str, Any]) -> tuple[list[float],list[float],list[dict[str, Any]]]:
    times = [float(row[0]) for row in data]
    values = [float(row[1]) for row in data]
    expected = list(plan["native_step_left_ms"])
    if len(times) != len(expected) or not all(math.isfinite(time) for time in times):
        raise ValueError("Arbor actual native sample clock differs from the pinned exact-step sampling plan")
    if not all(math.isfinite(value) for value in values) or any(b <= a for a,b in zip(times,times[1:])):
        raise ValueError("Arbor native report is nonfinite or not strictly time ordered")
    clock_arithmetic = "matches_complete_separately_rounded_source_sequence"
    if times != expected:
        # Native timestep_range::operator[] is t0_ + i*dt_. Its compiled C++
        # expression may contract to FMA; Python separately rounds i*dt_ then
        # adds t0_. Require the WHOLE clock to equal one complete exact source
        # sequence, never an arbitrary per-row mixture or nearness tolerance.
        # Exact rational conversion implements a single rounding without
        # compiler/libm/platform dependencies; stream it, no second full clock.
        final,dt = (float(plan[key]) for key in ("duration_ms","integration_dt_ms"))
        interval = plan.get("source_epoch_interval_ms",plan["epoch_interval_ms"])
        for ordinal,(_left,_right,start,index) in enumerate(_arbor_native_steps(final,dt,interval,include_source_operands=True)):
            fused = float(Fraction(start)+index*Fraction(dt))
            if times[ordinal] != fused:
                raise ValueError(f"Arbor actual native sample clock differs at ordinal {ordinal}; whole clock equals neither complete exact source arithmetic sequence")
        clock_arithmetic = "matches_complete_single_rounded_fused_source_sequence"
    sample_dt = float(plan["requested_sample_dt_ms"])
    final = float(plan["duration_ms"])
    bound = float(plan["match_bound_ms_exclusive"])
    selected = []
    last_index = -1
    for grid_index in range(math.ceil(final/sample_dt)):
        desired = grid_index*sample_dt
        if not times or desired < times[0] or desired > times[-1]:
            raise ValueError("Arbor requested sample lies outside actual native observed support; no endpoint was invented")
        insertion = bisect.bisect_left(times,desired)
        candidates = [index for index in (insertion-1,insertion) if 0 <= index < len(times)]
        ranked = sorted((abs(times[index]-desired),index) for index in candidates)
        roundoff = 32*max(math.ulp(desired),math.ulp(float(plan["integration_dt_ms"])),
            *(math.ulp(times[index]) for index in candidates))
        if not ranked or ranked[0][0] >= bound-roundoff or (len(ranked)>1 and abs(ranked[0][0]-ranked[1][0]) <= roundoff):
            raise ValueError(f"Arbor requested sample {desired:.17g}ms has no unambiguous native observation strictly within dt/2; no interpolation or extrapolation was performed")
        distance,index = ranked[0]
        if index <= last_index:
            raise ValueError("Arbor requested sample grid reuses or reverses a native observation")
        last_index = index
        selected.append({"requested_time_ms":desired,"actual_time_ms":times[index],
                         "offset_ms":times[index]-desired,"native_row_index":index,
                         "native_clock_arithmetic":clock_arithmetic})
    indices = [row["native_row_index"] for row in selected]
    return [times[i] for i in indices],[values[i] for i in indices],selected


def _persist_arbor_sampling_plan(report_path: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    """Pin the full prospective schedule before any native recipe/integration."""
    plan_path = report_path.with_suffix(".plan.json")
    plan_path.parent.mkdir(parents=True,exist_ok=True)
    if plan_path.exists():
        raise FileExistsError(f"Prospective Arbor sampling plan already exists: {plan_path}")
    _atomic_json(plan_path,plan)
    return dict(plan) | {"sampling_plan_path":plan_path.name,"sampling_plan_sha256":_sha256(plan_path)}


def _arbor_native_report(path: Path, data_by_probe: Mapping[Any, Sequence[Any]],
                          plan: Mapping[str, Any], *, native_detector_events: Sequence[Mapping[str, Any]] = ()) -> dict[Any, tuple[list[float],list[float]]]:
    """Preserve every observation before any requested-grid selection."""
    path.parent.mkdir(parents=True,exist_ok=True)
    result,selections = {},[]
    with path.open("x",encoding="utf-8",newline="") as handle:
        writer = csv.DictWriter(handle,fieldnames=("neuron_id","compartment_id","native_row_index","time_ms","voltage_mV"))
        writer.writeheader()
        for probe,data in data_by_probe.items():
            neuron_id,compartment_id = probe if isinstance(probe,tuple) else (probe,"")
            writer.writerows({"neuron_id":neuron_id,"compartment_id":compartment_id,"native_row_index":i,
                "time_ms":float(row[0]),"voltage_mV":float(row[1])} for i,row in enumerate(data))
    observations = {key:value for key,value in plan.items() if key not in ("native_step_left_ms","probe_request_ms")} | {
        "raw_report_path":path.name,"raw_report_sha256":_sha256(path),
        "native_step_count":len(plan["native_step_left_ms"]),
        "observed_rows_per_probe":[{"probe":list(probe) if isinstance(probe,tuple) else [probe],"rows":len(data)} for probe,data in data_by_probe.items()],
        "native_chemical_detector_events":[dict(event) for event in native_detector_events],
        "detector_event_note":"Actual native chemical-source crossings, NOT relabeled exported spike-threshold detections; no event or delay adjustment"}
    # Observation-only bytes/events survive even a later clock/support guard
    # rejection. Never write a canonical successful selection on failure.
    observation_path = path.with_suffix(".observations.json")
    _atomic_json(observation_path,observations | {"state":"unvalidated_native_observations","canonical_output_complete":False})
    try:
        if "sampling_plan_path" in plan:
            plan_name = str(plan["sampling_plan_path"])
            plan_path = path.parent/plan_name
            if Path(plan_name).name != plan_name or _sha256(plan_path) != plan.get("sampling_plan_sha256"):
                raise ValueError("Arbor prospective sampling plan identity changed after pinning")
            pinned = json.loads(plan_path.read_text(encoding="utf-8"))
            if pinned != {key:value for key,value in plan.items() if key not in ("sampling_plan_path","sampling_plan_sha256")}:
                raise ValueError("Arbor in-memory sampling plan differs from the pinned prospective plan")
        for probe,data in data_by_probe.items():
            times,values,selected = _select_arbor_native_samples(data,plan)
            result[probe] = times,values
            neuron_id,compartment_id = probe if isinstance(probe,tuple) else (probe,"")
            selections.append({"neuron_id":neuron_id,"compartment_id":compartment_id,"selected_rows":selected})
        if any(not math.isfinite(float(event["time_ms"])) or not 0 <= float(event["time_ms"]) <= float(plan["duration_ms"])
               for event in native_detector_events):
            raise ValueError("Arbor native detector observations contain invalid event times")
    except Exception as error:
        _atomic_json(path.with_suffix(".rejection.json"),{
            "schema":"digifly-arbor-native-sampling-rejection-v1","canonical_output_complete":False,
            "error_type":type(error).__name__,"message":str(error),"raw_report_sha256":observations["raw_report_sha256"],
            "observations_path":observation_path.name,"observations_sha256":_sha256(observation_path)})
        raise
    _atomic_json(path.with_suffix(".json"), observations | {"state":"validated_native_selection","selections":selections})
    return result


def _arbor_execution_resource_forecast(cells: Mapping[str, Any], experiment: Mapping[str, Any],
                                        edges: Mapping[str, Any]) -> dict[str, Any]:
    """Aggregate exact envelope/report cost BEFORE clocks, recipes or compilers."""
    repetitions = int(experiment.get("repetitions",1))
    native_values,detector_events,ramp_knots,records = 0,0,0,[]
    recording = dict(experiment["recording"])
    for condition in experiment.get("conditions") or ():
        if not condition.get("enabled",True):
            continue
        active_cells = _condition_cells(cells,condition)
        active_edges = _condition_edges(edges,tuple(active_cells))
        targets = set(recording.get("target_neuron_ids") or active_cells)
        probes = sum(len(_regional_sites(c["regional_plan"],recording["target_region"],c))
            if c.get("regional") else 1 for n,c in active_cells.items() if n in targets)
        interval = _arbor_ramp_epoch_interval(active_edges,bool(condition.get("chemical_synapses_enabled",True)))
        item = {"condition":str(condition["name"]),"probe_count":probes,"repetitions":repetitions}
        if interval is not None and probes:
            # Recipe places one chemical-source detector on EVERY active cell,
            # not just selected recording targets. At most one rising crossing
            # per native step/detector (pinned threshold_watcher.hpp).
            plan = _arbor_sampling_plan(experiment,active_edges,condition,probes,
                                        detector_count=len(active_cells),allocate=False)
            native_values += plan["native_value_count_upper_bound"]*repetitions
            detector_events += plan["native_detector_event_count_upper_bound"]*repetitions
            item["dense_native_report_value_upper_bound"] = plan["native_value_count_upper_bound"]*repetitions
            item["native_detector_count"] = len(active_cells)
            item["retained_native_detector_event_upper_bound"] = plan["native_detector_event_count_upper_bound"]*repetitions
        for stimulus in experiment.get("stimuli") or ():
            if not stimulus.get("enabled",True) or stimulus.get("waveform") != "ramp":
                continue
            stimulus_targets = set(stimulus.get("target_neuron_ids") or active_cells)
            sites = sum(len(_regional_sites(c["regional_plan"],stimulus["target_region"],c))
                if c.get("regional") else 1 for n,c in active_cells.items() if n in stimulus_targets)
            if sites:
                forecast = _arbor_ramp_forecast(stimulus,float(experiment["integration_dt_ms"]),float(experiment["duration_ms"]),interval)
                ramp_knots += forecast["envelope_knot_count_upper_bound"]*sites*(repetitions+1)  # runtime plus preserved receipt
                item["ramp_forecast"] = forecast | {"site_count":sites}
        records.append(item)
    if native_values > ARBOR_MAX_NATIVE_REPORT_VALUES:
        raise ValueError(f"Arbor dense native probes across conditions/repetitions require up to {native_values:,} values; local limit {ARBOR_MAX_NATIVE_REPORT_VALUES:,}. No clock, recipe or compiler was dispatched.")
    if detector_events > ARBOR_MAX_NATIVE_DETECTOR_EVENTS:
        raise ValueError(f"Arbor retained native detectors across conditions/repetitions require up to {detector_events:,} events; local limit {ARBOR_MAX_NATIVE_DETECTOR_EVENTS:,}. No clock, recipe, compiler or detector recorder was dispatched.")
    if ramp_knots > ARBOR_MAX_RAMP_KNOTS:
        raise ValueError(f"Arbor exact ramp envelopes/receipts across sites/trials require up to {ramp_knots:,} knots; local limit {ARBOR_MAX_RAMP_KNOTS:,}. No waveform was substituted.")
    return {"schema":"digifly-arbor-execution-resource-forecast-v1","conditions":records,
        "native_report_value_upper_bound":native_values,"ramp_knot_upper_bound":ramp_knots,
        "retained_native_detector_event_upper_bound":detector_events,
        "retained_native_detector_event_limit":ARBOR_MAX_NATIVE_DETECTOR_EVENTS,
        "native_report_value_limit":ARBOR_MAX_NATIVE_REPORT_VALUES,"ramp_knot_limit":ARBOR_MAX_RAMP_KNOTS,
        "native_epoch_limit":ARBOR_MAX_NATIVE_EPOCHS,"policy":"Fail before clock allocation/recipe/compiler; no current, dt, sample interval or event-policy coarsening"}


def _active_native_channels(membrane: Mapping[str, Any]) -> tuple[str, ...]:
    active: list[str] = []
    for key, raw_channel in dict(membrane.get("channels") or {}).items():
        channel = dict(raw_channel or {})
        if channel.get("enabled", False) and (
            float(channel.get("soma_gbar_s_cm2", 0.0)) > 0.0
            or float(channel.get("branch_gbar_s_cm2", 0.0)) > 0.0
        ):
            active.append(str(key))
    return tuple(active)


def _validate_request_capabilities(
    engine: str,
    circuit: Mapping[str, Any],
    experiment: Mapping[str, Any],
    edge_manifest: Mapping[str, Any] | None = None,
) -> None:
    """Defend the fail-closed execution boundary inside the worker process."""

    if engine not in {"arbor", "neuron"}:
        raise ValueError(f"Unsupported generic experiment engine: {engine}")
    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    if not neuron_ids:
        raise ValueError("The generic worker requires at least one neuron")
    if len(set(neuron_ids)) != len(neuron_ids):
        raise ValueError("The generic worker received duplicate neuron IDs")
    if len(neuron_ids) > 1:
        manifest = dict(edge_manifest or {})
        if int(manifest.get("schema_version", 0)) != EDGE_MANIFEST_SCHEMA_VERSION:
            raise ValueError("The selected-subgraph edge manifest schema is missing or unsupported")
        if manifest.get("kind") != "explicit_selected_subgraph":
            raise ValueError("The multi-cell worker requires an explicit selected-subgraph manifest")
        if tuple(str(value) for value in manifest.get("neuron_ids") or ()) != neuron_ids:
            raise ValueError("The edge manifest neuron order does not match the circuit")
        selected = set(neuron_ids)
        contact_ids: set[str] = set()
        for raw in manifest.get("chemical_edges") or ():
            edge = dict(raw or {})
            contact_id = str(edge.get("contact_id") or "")
            if not contact_id or contact_id in contact_ids:
                raise ValueError("Chemical contact IDs must be present and unique")
            contact_ids.add(contact_id)
            if {str(edge.get("pre_id")), str(edge.get("post_id"))} - selected:
                raise ValueError("A chemical contact endpoint is outside the selected circuit")
            values = {
                "weight": float(edge.get("weight_uS", -1.0)),
                "delay": float(edge.get("delay_ms", -1.0)),
                "tau1": float(edge.get("tau1_ms", -1.0)),
                "tau2": float(edge.get("tau2_ms", -1.0)),
                "reversal": float(edge.get("reversal_mV", math.nan)),
            }
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"Chemical contact {contact_id} has non-finite parameters")
            if values["weight"] < 0.0 or values["delay"] <= 0.0:
                raise ValueError(f"Chemical contact {contact_id} has invalid weight or delay")
            if values["tau1"] <= 0.0 or values["tau2"] <= values["tau1"]:
                raise ValueError(f"Chemical contact {contact_id} has invalid exp2syn kinetics")
            if int(edge.get("post_source_node_id", 0)) <= 0:
                raise ValueError(f"Chemical contact {contact_id} has no postsynaptic SWC node")
        for raw in manifest.get("electrical_edges") or ():
            edge = dict(raw or {})
            neuron_a = str(edge.get("neuron_a") or "")
            neuron_b = str(edge.get("neuron_b") or "")
            if neuron_a == neuron_b or {neuron_a, neuron_b} - selected:
                raise ValueError("An electrical pair endpoint is outside the selected circuit")
            contacts = list(edge.get("contacts") or ())
            if not contacts or int(edge.get("contact_count", -1)) != len(contacts):
                raise ValueError("An electrical pair has no complete contact manifest")
            for raw_contact in contacts:
                contact = dict(raw_contact or {})
                contact_id = str(contact.get("contact_id") or "")
                if not contact_id or contact_id in contact_ids:
                    raise ValueError("Electrical contact IDs must be present and unique")
                contact_ids.add(contact_id)
                pre_id = str(contact.get("pre_id") or "")
                post_id = str(contact.get("post_id") or "")
                if pre_id == post_id or {pre_id, post_id} != {neuron_a, neuron_b}:
                    raise ValueError(
                        f"Electrical contact {contact_id} does not match its outer pair"
                    )
                if int(contact.get("pre_source_node_id", 0)) <= 0 or int(
                    contact.get("post_source_node_id", 0)
                ) <= 0:
                    raise ValueError(
                        f"Electrical contact {contact_id} has no complete SWC endpoint mapping"
                    )
                conductance = float(contact.get("effective_g_uS", math.nan))
                if not math.isfinite(conductance) or conductance < 0.0:
                    raise ValueError(
                        f"Electrical contact {contact_id} has invalid conductance"
                    )
                coordinate_sources = dict(contact.get("coordinate_sources") or {})
                for endpoint in ("pre", "post"):
                    coordinates = list(
                        contact.get(f"{endpoint}_coordinate_um") or ()
                    )
                    if len(coordinates) != 3 or not all(
                        math.isfinite(float(value)) for value in coordinates
                    ):
                        raise ValueError(
                            f"Electrical contact {contact_id} has no finite {endpoint} coordinate"
                        )
                _validate_gap_contact_placement(contact, dict(manifest.get("gap_junction_policy") or {}))
        if manifest.get("electrical_edges"):
            gap_policy = dict(manifest.get("gap_junction_policy") or {})
            if gap_policy.get("mode") not in {
                "ohmic",
                "rectifying",
                "heterotypic_rectifying",
            }:
                raise ValueError("Electrical contacts have no supported gap-junction policy")

    membrane_payloads = [dict(circuit.get("membrane") or {})]
    membrane_payloads.extend(
        dict(value or {})
        for value in dict(circuit.get("neuron_mechanism_overrides") or {}).values()
    )
    membrane_payloads.extend(
        dict(value or {})
        for neuron_map in dict(
            circuit.get("compartment_mechanism_overrides") or {}
        ).values()
        for value in dict(neuron_map or {}).values()
    )
    active_channels = {
        channel
        for membrane in membrane_payloads
        for channel in _active_native_channels(membrane)
    }
    if engine not in {"neuron", "arbor"} and (any(
        membrane.get("replace_builtin_hh", False) for membrane in membrane_payloads
    ) or active_channels):
        detail = ", ".join(sorted(active_channels)) or "replace_builtin_hh"
        raise ValueError(
            "Native membrane mechanisms are outside the classic-HH worker lane: "
            + detail
        )
    if engine in {"neuron", "arbor"}:
        for membrane in membrane_payloads:
            for key in _active_native_channels(membrane):
                _validated_native_assignment(key, dict(membrane["channels"][key]))
    hh_payloads = [dict(circuit.get("hh") or {})]
    hh_payloads.extend(
        {**hh_payloads[0], **dict(value or {})}
        for value in dict(circuit.get("neuron_overrides") or {}).values()
    )
    if any(str(values.get("active_scope") or "all") not in {"all", "soma", "soma_ais"} for values in hh_payloads):
        raise ValueError("Only whole-neuron or soma-only classic HH is supported")

    stimuli = [
        dict(item)
        for item in experiment.get("stimuli") or ()
        if item.get("enabled", True)
    ]
    if len(stimuli) != 1 or stimuli[0].get("target_region") not in {"soma", "all", "ais", "selected_compartments"} or stimuli[0].get(
        "waveform"
    ) not in {"pulse_train", "step", "ramp"}:
        raise ValueError(
            "The membrane worker requires one mapped pulse-train, step or ramp stimulus"
        )
    conditions = [
        dict(item)
        for item in experiment.get("conditions") or ()
        if item.get("enabled", True)
    ]
    if not conditions:
        raise ValueError("At least one enabled condition is required")
    for condition in conditions:
        _validate_condition_manipulations(circuit, condition)
    recording = dict(experiment.get("recording") or {})
    if recording.get("target_region") not in {"soma", "all", "ais", "selected_compartments"} or not (
        recording.get("record_voltage", False) or recording.get("detect_spikes", False)
    ):
        raise ValueError("The membrane worker requires voltage recording or spike detection in a mapped region")
    if not 1 <= int(experiment.get("workers",1)) <= 256:
        raise ValueError("Worker count must be between 1 and 256")


def _load_edge_manifest(request: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    path = Path(str(request.get("edge_manifest_path") or "")).expanduser().resolve()
    if path.parent != output_dir or path.name != "edge_manifest.json" or not path.is_file():
        raise ValueError("The run-owned edge manifest path is missing or outside the run")
    expected = str(request.get("edge_manifest_sha256") or "")
    actual = _sha256(path)
    if len(expected) != 64 or actual != expected:
        raise ValueError("The run-owned edge manifest checksum does not match the request")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The run-owned edge manifest must contain a JSON object")
    return payload


def _validate_edge_source_identity(edge_manifest: Mapping[str, Any]) -> None:
    """Reject a connected run if its validated contact source changed after preflight."""

    if edge_manifest.get("kind") == "explicit_two_cell_gap_only":
        source = dict(edge_manifest.get("source") or {})
        for path_key, digest_key, label in (
            ("path", "sha256", "gap-contact table"),
            ("manifest_path", "manifest_sha256", "gap-contact source manifest"),
        ):
            path = Path(str(source.get(path_key) or "")).expanduser().resolve()
            expected = str(source.get(digest_key) or "")
            if not path.is_file():
                raise FileNotFoundError(f"The validated {label} is missing: {path}")
            if len(expected) != 64 or _sha256(path) != expected:
                raise ValueError(f"The validated {label} changed after preflight")
        return
    if edge_manifest.get("kind") != "explicit_selected_subgraph":
        return
    for raw_source in edge_manifest.get("sources") or ():
        source = dict(raw_source or {})
        kind = str(source.get("kind") or "")
        path = Path(str(source.get("path") or "")).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"A selected edge source is missing: {path}")
        if kind == "sha256_files":
            if _sha256(path) != str(source.get("sha256") or ""):
                raise ValueError("A selected edge source changed after preflight")
            manifest_path = Path(str(source.get("manifest_path") or "")).expanduser().resolve()
            if not manifest_path.is_file() or _sha256(manifest_path) != str(
                source.get("manifest_sha256") or ""
            ):
                raise ValueError("An edge-source manifest changed after preflight")
        elif kind == "immutable_sqlite":
            stat = path.stat()
            if int(source.get("size_bytes", -1)) != stat.st_size or int(
                source.get("mtime_ns", -1)
            ) != stat.st_mtime_ns:
                raise ValueError("The indexed chemical-edge cache changed after preflight")
            connection = sqlite3.connect(
                f"{path.as_uri()}?mode=ro&immutable=1", uri=True
            )
            try:
                schema_row = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='edges'"
                ).fetchone()
                meta_rows = connection.execute("SELECT k, v FROM meta ORDER BY k").fetchall()
            finally:
                connection.close()
            identity = {
                "schema": str(schema_row[0]) if schema_row else "",
                "meta": [[str(key), str(value)] for key, value in meta_rows],
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
            actual = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if actual != str(source.get("identity_sha256") or ""):
                raise ValueError("The indexed chemical-edge cache identity changed after preflight")
        elif kind == "immutable_parquet":
            stat = path.stat()
            identity = {
                "schema_contract": str(source.get("schema_contract") or ""),
                "size_bytes": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
            actual = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if (
                identity["schema_contract"]
                != "male-cns-v0.9-synapses-parquet-v1"
                or actual != str(source.get("identity_sha256") or "")
            ):
                raise ValueError("The Male-CNS chemical source identity changed after preflight")
        else:
            raise ValueError(f"Unsupported edge-source identity kind: {kind}")

    selected_identity = hashlib.sha256(
        json.dumps(
            {
                "chemical": list(edge_manifest.get("chemical_edges") or ()),
                "electrical": list(edge_manifest.get("electrical_edges") or ()),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if selected_identity != str(edge_manifest.get("selected_contact_identity_sha256") or ""):
        raise ValueError("The selected contact set identity is invalid")


def _source_to_run_node_map(path: Path) -> dict[int, int]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {
            int(row["source_node_id"]): int(row["run_node_id"])
            for row in csv.DictReader(handle)
        }


def _crossings(
    times: Sequence[float], voltages: Sequence[float], threshold: float
) -> list[float]:
    return [
        float(times[index])
        for index in range(1, min(len(times), len(voltages)))
        if float(voltages[index - 1]) < threshold <= float(voltages[index])
    ]


def _arbor_trace(
    swc_path: Path,
    hh: Mapping[str, Any],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    *,
    prefer_rostral_soma: bool,
    membrane: Mapping[str, Any] | None = None,
    channel_catalogue_path: Path | None = None,
) -> tuple[list[float], list[float], str]:
    import arbor

    morphology = _arbor_morphology_from_swc(
        arbor,
        swc_path,
        prefer_rostral_soma=prefer_rostral_soma,
    )
    units = arbor.units
    labels = arbor.label_dict(
        {
            "all": "(all)",
            "soma": "(join (tag 1) (tag 1001))",
            "branches": "(difference (all) (join (tag 1) (tag 1001)))",
            "soma-center": "(distal (tag 1001))",
        }
    )
    decor = arbor.decor()
    decor.set_property(
        Vm=float(experiment["initial_voltage_mV"]) * units.mV,
        tempK=(float(experiment["temperature_C"]) + 273.15) * units.Kelvin,
        rL=float(hh["ra_ohm_cm"]) * units.Ohm * units.cm,
        cm=float(hh["cm_uF_cm2"]) * units.uF / units.cm2,
    )
    _decorate_arbor_channels(arbor, decor, hh, membrane or {})
    properties = _arbor_channel_properties(arbor, channel_catalogue_path)
    amplitude = float(stimulus["amplitude_nA"]) * float(
        condition.get("stimulus_scale", 1.0)
    )
    for clamp in _arbor_current_clamps(arbor, stimulus, amplitude,
            integration_dt_ms=float(experiment["integration_dt_ms"]), duration_ms=float(experiment["duration_ms"])):
        decor.place(
            '"soma-center"',
            clamp,
        )
    # Keep soma/branch density boundaries in the numerical mesh. Arbor's
    # default single CV per branch can merge distinct membrane regions.
    policy = (arbor.cv_policy_max_extent(NEURON_TARGET_SEGMENT_UM*units.um, '"soma"')
              + arbor.cv_policy_max_extent(NEURON_TARGET_SEGMENT_UM*units.um, '"branches"'))
    cell = arbor.cable_cell(morphology, decor, labels, policy)

    class Recipe(arbor.recipe):
        def __init__(self) -> None:
            super().__init__()

        def num_cells(self) -> int:
            return 1

        def cell_kind(self, _gid: int) -> Any:
            return arbor.cell_kind.cable

        def cell_description(self, _gid: int) -> Any:
            return cell

        def probes(self, _gid: int) -> list[Any]:
            return [
                arbor.cable_probe_membrane_voltage(
                    '"soma-center"', "soma_voltage"
                )
            ]

        def global_properties(self, _kind: Any) -> Any:
            return properties

    context = arbor.context(threads=int(experiment.get("workers", 1)))
    simulation = arbor.simulation(
        Recipe(),
        context=context,
        seed=int(experiment.get("random_seed", 1)),
    )
    handle = simulation.sample(
        (0, "soma_voltage"),
        arbor.regular_schedule(
            float(experiment["recording"]["sample_dt_ms"]) * units.ms
        ),
    )
    simulation.run(
        tfinal=float(experiment["duration_ms"]) * units.ms,
        dt=float(experiment["integration_dt_ms"]) * units.ms,
    )
    samples = simulation.samples(handle)
    if len(samples) != 1:
        raise RuntimeError(
            f"The soma label resolved to {len(samples)} probes; exactly one is required."
        )
    data, _metadata = samples[0]
    times = [float(value) for value in data[:, 0]]
    voltages = [float(value) for value in data[:, 1]]
    return times, voltages, str(getattr(arbor, "__version__", "unknown"))


def _arbor_gap_junction(
    arbor: Any,
    policy: Mapping[str, Any],
    conductance_uS: float,
    *,
    endpoint: str,
) -> Any:
    mode = str(policy.get("mode") or "")
    direction = str(policy.get("preferred_direction") or "a_to_b")
    forward = direction == "a_to_b"
    is_source = (endpoint == "a" and forward) or (endpoint == "b" and not forward)
    if mode == "ohmic":
        return arbor.junction("digifly_gap", {"g": float(conductance_uS)})
    if mode == "rectifying":
        if is_source:
            return arbor.junction("digifly_gap", {"g": 0.0})
        return arbor.junction("digifly_rect_gap", {"gmax": float(conductance_uS)})
    if mode == "heterotypic_rectifying":
        orientation = 1.0 if is_source else -1.0
        return arbor.junction(
            "digifly_hetero_rect_gap",
            {
                "gmax_open": float(conductance_uS),
                "gmax_closed": float(conductance_uS)
                * float(policy.get("g_closed_frac", 0.0)),
                "orientation": orientation,
                "vhalf": float(policy.get("vhalf_mV", 0.0)),
                "vslope": float(policy.get("vslope_mV", 5.0)),
                "empirical_residual_frac": float(
                    policy.get("empirical_residual_frac", 0.2)
                ),
                "tau_open_ms": float(policy.get("tau_open_ms", 6.0)),
                "tau_close_ms": float(policy.get("tau_close_ms", 2.0)),
            },
        )
    raise ValueError(f"Unsupported Digifly gap-junction mode: {mode}")


def _arbor_network_traces(
    cells: Mapping[str, Mapping[str, Any]],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    edge_manifest: Mapping[str, Any],
    catalogue_path: Path | None,
    channel_catalogue_path: Path | None = None,
    *, native_report_path: Path | None = None,
) -> tuple[dict[str, tuple[list[float], list[float]]], str]:
    import arbor

    neuron_ids = tuple(cells)
    gid_by_id = {neuron_id: gid for gid, neuron_id in enumerate(neuron_ids)}
    target_ids = {
        str(value) for value in stimulus.get("target_neuron_ids") or neuron_ids
    }
    recording = dict(experiment.get("recording") or {})
    record_ids = {
        str(value) for value in recording.get("target_neuron_ids") or neuron_ids
    }
    gap_enabled = bool(condition.get("gap_junctions_enabled", True))
    chemical_enabled = bool(condition.get("chemical_synapses_enabled", True))
    policy = dict(edge_manifest.get("gap_junction_policy") or {})
    electrical_edges = [
        dict(item) for item in edge_manifest.get("electrical_edges") or ()
    ]
    chemical_edges = [dict(item) for item in edge_manifest.get("chemical_edges") or ()]
    has_electrical = bool(electrical_edges)
    properties = _arbor_channel_properties(arbor, channel_catalogue_path)
    if has_electrical:
        if catalogue_path is None:
            raise FileNotFoundError("The app-owned Arbor gap catalogue path is missing")
        catalogue_manifest_path = catalogue_path.with_name("catalogue_manifest.json")
        if not catalogue_manifest_path.is_file():
            raise FileNotFoundError(
                f"The Arbor gap catalogue source details manifest is missing: {catalogue_manifest_path}"
            )
        catalogue_manifest = json.loads(
            catalogue_manifest_path.read_text(encoding="utf-8")
        )
        if (
            catalogue_manifest.get("catalogue") != "digifly_gap"
            or catalogue_manifest.get("arbor_version")
            != str(getattr(arbor, "__version__", "unknown"))
            or catalogue_manifest.get("catalogue_sha256") != _sha256(catalogue_path)
        ):
            raise ValueError(
                "The Arbor gap catalogue does not match its ABI source details manifest"
            )
        properties.catalogue.extend(
            arbor.load_catalogue(str(catalogue_path)), "digifly_"
        )

    built_cells: list[Any] = []
    regional_probes: dict[str, list[Any]] = {}
    contact_labels: list[dict[str, Any]] = []
    chemical_connections: list[dict[str, Any]] = []
    units = arbor.units
    contact_nodes_by_id = {neuron_id: set() for neuron_id in neuron_ids}
    for edge in electrical_edges:
        for raw_contact in edge.get("contacts") or ():
            contact = dict(raw_contact)
            contact_nodes_by_id[str(contact["pre_id"])].add(
                int(contact["pre_run_node_id"])
            )
            contact_nodes_by_id[str(contact["post_id"])].add(
                int(contact["post_run_node_id"])
            )
    for contact in chemical_edges:
        contact_nodes_by_id[str(contact["post_id"])].add(
            int(contact["post_run_node_id"])
        )
    for neuron_id in neuron_ids:
        cell_data = dict(cells[neuron_id])
        swc_path = Path(str(cell_data["swc_path"]))
        hh = dict(cell_data["hh"])
        plan = cell_data.get("regional_plan")
        if plan:
            contact_nodes_by_id[neuron_id].update(r[0] for r in _swc_rows(swc_path))
        morphology, contact_locset_by_node, label_values = (
            _arbor_network_morphology_from_swc(
                arbor,
                swc_path,
                prefer_rostral_soma=bool(cell_data["prefer_rostral_soma"]),
                contact_node_ids=contact_nodes_by_id[neuron_id],
            )
        )
        if plan:
            target = int(plan["target_node_id"])
            label_values["soma-center"] = f"(on-components 0.5 (tag {1_000_000+target}))"
            regional_probes[neuron_id] = _regional_sites(plan, recording["target_region"], cell_data) if neuron_id in record_ids else []
        labels = arbor.label_dict(label_values)
        decor = arbor.decor()
        decor.set_property(
            Vm=float(experiment["initial_voltage_mV"]) * units.mV,
            tempK=(float(experiment["temperature_C"]) + 273.15) * units.Kelvin,
            rL=float(hh["ra_ohm_cm"]) * units.Ohm * units.cm,
            cm=float(hh["cm_uF_cm2"]) * units.uF / units.cm2,
        )
        if plan:
            _require_neuron_segment_budget(0, plan["estimated_nseg"], segment_budget=NEURON_MAX_TOTAL_SEGMENTS)
            for spec in plan["sections"]:
                region = f'(tag {1_000_000+spec["node_id"]})'
                props = spec["hh"]
                decor.paint(region, rL=float(props["ra_ohm_cm"])*units.Ohm*units.cm, cm=float(props["cm_uF_cm2"])*units.uF/units.cm2)
                _decorate_arbor_channels(arbor, decor, props, spec["membrane"], edge_region=region, edge_soma=spec["soma"])
        else:
            _decorate_arbor_channels(arbor, decor, hh, dict(cell_data.get("membrane") or {}))
        if neuron_id in target_ids:
            amplitude = float(stimulus["amplitude_nA"]) * float(
                condition.get("stimulus_scale", 1.0)
            )
            sites = [f'(on-components 0.5 (tag {1_000_000+s["node_id"]}))' for s in _regional_sites(plan, stimulus["target_region"], cell_data)] if plan else ['"soma-center"']
            for site in sites:
              for clamp in _arbor_current_clamps(arbor, stimulus, amplitude/len(sites),
                      integration_dt_ms=float(experiment["integration_dt_ms"]), duration_ms=float(experiment["duration_ms"]),
                      epoch_interval_ms=_arbor_ramp_epoch_interval(edge_manifest,chemical_enabled)):
                decor.place(
                    site,
                    clamp,
                )
        if chemical_enabled and chemical_edges:
            chemical_policy = dict(
                edge_manifest.get("chemical_synapse_policy") or {}
            )
            decor.place(
                '"soma-center"',
                arbor.threshold_detector(
                    float(chemical_policy.get("spike_threshold_mV", 0.0))
                    * units.mV
                ),
                "chemical-source",
            )
            for contact in chemical_edges:
                if str(contact["post_id"]) != neuron_id:
                    continue
                source_node = int(contact["post_run_node_id"])
                if source_node not in contact_locset_by_node:
                    raise ValueError(
                        f"Chemical contact {contact['contact_id']} maps to missing run node {source_node}"
                    )
                synapse_label = str(contact["contact_id"])
                decor.place(
                    contact_locset_by_node[source_node],
                    arbor.synapse(
                        "exp2syn",
                        {
                            "tau1": float(contact["tau1_ms"]),
                            "tau2": float(contact["tau2_ms"]),
                            "e": float(contact["reversal_mV"]),
                        },
                    ),
                    synapse_label,
                )
                chemical_connections.append(
                    {
                        "pre_gid": gid_by_id[str(contact["pre_id"])],
                        "post_gid": gid_by_id[neuron_id],
                        "synapse_label": synapse_label,
                        "weight_uS": float(contact["weight_uS"]),
                        "delay_ms": float(contact["delay_ms"]),
                    }
                )
        if gap_enabled:
            for edge in electrical_edges:
                for raw_contact in edge.get("contacts") or ():
                    contact = dict(raw_contact)
                    if contact["pre_id"] == neuron_id:
                        source_node = int(contact["pre_run_node_id"])
                        contact_end = "pre"
                        endpoint = "a" if edge["neuron_a"] == neuron_id else "b"
                    elif contact["post_id"] == neuron_id:
                        source_node = int(contact["post_run_node_id"])
                        contact_end = "post"
                        endpoint = "a" if edge["neuron_a"] == neuron_id else "b"
                    else:
                        continue
                    if source_node not in contact_locset_by_node:
                        raise ValueError(
                            f"Gap contact {contact['contact_id']} maps to missing run node {source_node}"
                        )
                    label = f"{contact['contact_id']}-{endpoint}"
                    decor.place(
                        _arbor_gap_contact_locset(plan, contact_locset_by_node[source_node], source_node, contact, contact_end),
                        _arbor_gap_junction(
                            arbor,
                            policy,
                            float(contact["effective_g_uS"]),
                            endpoint=endpoint,
                        ),
                        label,
                    )
        cv_policy = (arbor.cv_policy_max_extent(NEURON_TARGET_SEGMENT_UM*units.um, '"soma"')
                     + arbor.cv_policy_max_extent(NEURON_TARGET_SEGMENT_UM*units.um, '"branches"'))
        if plan:
            bounds = []
            for spec in plan["sections"]:
                for i in range(spec["nseg"]+1):
                    bounds.append(f'(on-components {i/spec["nseg"]:.17g} (tag {1_000_000+spec["node_id"]}))')
            locs = _join_expressions(bounds)
            cv_policy = arbor.cv_policy_explicit(locs)
        built_cells.append(arbor.cable_cell(morphology, decor, labels, cv_policy))

    if gap_enabled:
        for edge in electrical_edges:
            for raw_contact in edge.get("contacts") or ():
                contact = dict(raw_contact)
                pre_id, post_id = str(contact["pre_id"]), str(contact["post_id"])
                pre_endpoint = "a" if edge["neuron_a"] == pre_id else "b"
                post_endpoint = "a" if edge["neuron_a"] == post_id else "b"
                contact_labels.append(
                    {
                        "pre_gid": gid_by_id[pre_id],
                        "post_gid": gid_by_id[post_id],
                        "pre_label": f"{contact['contact_id']}-{pre_endpoint}",
                        "post_label": f"{contact['contact_id']}-{post_endpoint}",
                    }
                )

    class Recipe(arbor.recipe):
        def __init__(self) -> None:
            super().__init__()

        def num_cells(self) -> int:
            return len(built_cells)

        def cell_kind(self, _gid: int) -> Any:
            return arbor.cell_kind.cable

        def cell_description(self, gid: int) -> Any:
            return built_cells[int(gid)]

        def connections_on(self, gid: int) -> list[Any]:
            return [
                arbor.connection(
                    arbor.cell_global_label(
                        int(item["pre_gid"]), "chemical-source"
                    ),
                    arbor.cell_local_label(str(item["synapse_label"])),
                    float(item["weight_uS"]),
                    float(item["delay_ms"]) * units.ms,
                )
                for item in chemical_connections
                if int(item["post_gid"]) == int(gid)
            ]

        def gap_junctions_on(self, gid: int) -> list[Any]:
            connections: list[Any] = []
            for item in contact_labels:
                if int(item["pre_gid"]) == int(gid):
                    connections.append(
                        arbor.gap_junction_connection(
                            (int(item["post_gid"]), str(item["post_label"])),
                            str(item["pre_label"]),
                            1.0,
                        )
                    )
                elif int(item["post_gid"]) == int(gid):
                    connections.append(
                        arbor.gap_junction_connection(
                            (int(item["pre_gid"]), str(item["pre_label"])),
                            str(item["post_label"]),
                            1.0,
                        )
                    )
            return connections

        def probes(self, gid: int) -> list[Any]:
            neuron_id = neuron_ids[int(gid)]
            if neuron_id in regional_probes:
                return [arbor.cable_probe_membrane_voltage(f'(on-components 0.5 (tag {1_000_000+s["node_id"]}))', f'swc_{s["source_node_id"]}') for s in regional_probes[neuron_id]]
            return (
                [arbor.cable_probe_membrane_voltage('"soma-center"', "soma_voltage")]
                if neuron_id in record_ids
                else []
            )

        def global_properties(self, _kind: Any) -> Any:
            return properties

    probe_count = sum(len(specs) for specs in regional_probes.values()) + sum(
        neuron_id in record_ids and neuron_id not in regional_probes for neuron_id in neuron_ids)
    # Sparse regular requests can land just BEFORE a float32 connection-delay
    # epoch boundary and return the preceding full-step state. Observe every
    # actual native step instead; do not change equations, events or delays.
    sampling_plan = _arbor_sampling_plan(experiment,edge_manifest,condition,probe_count,
        detector_count=len(neuron_ids)) if chemical_enabled and chemical_edges and probe_count else None
    if sampling_plan and native_report_path is None:
        raise ValueError("Dense Arbor chemical sampling requires a preserved native report path")
    if sampling_plan:
        sampling_plan = _persist_arbor_sampling_plan(native_report_path,sampling_plan)
    sample_schedule = arbor.explicit_schedule([t*units.ms for t in sampling_plan["probe_request_ms"]]) if sampling_plan else arbor.regular_schedule(float(recording["sample_dt_ms"])*units.ms)
    context = arbor.context(threads=int(experiment.get("workers", 1)))
    simulation = arbor.simulation(
        Recipe(),
        context=context,
        seed=int(experiment.get("random_seed", 1)),
    )
    if sampling_plan:
        simulation.record(arbor.spike_recording.local)
    handles = {
        neuron_id: simulation.sample(
            (gid_by_id[neuron_id], "soma_voltage"),
            sample_schedule,
        )
        for neuron_id in neuron_ids
        if neuron_id in record_ids and neuron_id not in regional_probes
    }
    for neuron_id, specs in regional_probes.items():
        handles.pop(neuron_id, None)
        for spec in specs:
            handles[(neuron_id, spec["source_node_id"])] = simulation.sample((gid_by_id[neuron_id], f'swc_{spec["source_node_id"]}'), sample_schedule)
    simulation.run(
        tfinal=float(experiment["duration_ms"]) * units.ms,
        dt=float(experiment["integration_dt_ms"]) * units.ms,
    )
    traces: dict[str, tuple[list[float], list[float]]] = {}
    native_data = {}
    for neuron_id, handle in handles.items():
        samples = simulation.samples(handle)
        if len(samples) != 1:
            raise RuntimeError(
                f"The soma label for neuron {neuron_id} resolved to {len(samples)} probes"
            )
        data, _metadata = samples[0]
        if sampling_plan:
            native_data[neuron_id] = data
            continue
        traces[neuron_id] = (
            [float(value) for value in data[:, 0]],
            [float(value) for value in data[:, 1]],
        )
    if sampling_plan:
        detector_events = [{"neuron_id":neuron_ids[int(event["source"]["gid"])],
                            "source_lid":int(event["source"]["index"]),"time_ms":float(event["time"])}
                           for event in simulation.spikes()]
        traces = _arbor_native_report(native_report_path,native_data,sampling_plan,native_detector_events=detector_events)
    return traces, str(getattr(arbor, "__version__", "unknown"))


def _neuron_trace(
    swc_path: Path,
    hh: Mapping[str, Any],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    *,
    prefer_rostral_soma: bool,
    membrane: Mapping[str, Any] | None = None,
) -> tuple[list[float], list[float], str]:
    os.environ.pop("DISPLAY", None)
    from neuron import h
    import neuron

    h("forall delete_section()")
    h.load_file("stdrun.hoc")
    h.load_file("import3d.hoc")

    class Cell:
        pass

    cell = Cell()
    reader = h.Import3d_SWC_read()
    reader.input(str(swc_path))
    importer = h.Import3d_GUI(reader, 0)
    importer.instantiate(cell)
    sections = list(getattr(cell, "all", ()))
    if not sections:
        sections = list(h.allsec())
    if not sections:
        raise RuntimeError(f"NEURON imported no sections from {swc_path}")
    soma_candidates = list(getattr(cell, "soma", ()))
    soma = (
        soma_candidates[0]
        if soma_candidates
        else max(sections, key=lambda section: section.diam)
    )
    target_node_id, target_point = _target_soma_node(
        _swc_rows(swc_path),
        prefer_rostral=prefer_rostral_soma,
    )
    target_section = soma
    target_position = 0.5
    nearest_distance = math.inf
    for section in sections:
        point_count = int(h.n3d(sec=section))
        if point_count <= 0:
            continue
        section_length = max(float(section.L), 1e-12)
        for point_index in range(point_count):
            coordinates = (
                float(h.x3d(point_index, sec=section)),
                float(h.y3d(point_index, sec=section)),
                float(h.z3d(point_index, sec=section)),
            )
            distance = sum(
                (coordinates[axis] - target_point[axis]) ** 2 for axis in range(3)
            )
            if distance < nearest_distance:
                nearest_distance = distance
                target_section = section
                target_position = min(
                    0.999999,
                    max(
                        0.000001,
                        float(h.arc3d(point_index, sec=section)) / section_length,
                    ),
                )
    if not math.isfinite(nearest_distance):
        raise RuntimeError(
            f"NEURON exposed no 3-D section points for target SWC node {target_node_id}"
        )
    soma_names = {section.name() for section in soma_candidates}
    soma_names.add(target_section.name())
    _apply_neuron_discretization(
        sections,
        segment_budget=NEURON_MAX_TOTAL_SEGMENTS,
    )
    for section in sections:
        _decorate_neuron_section(section, hh, membrane or {}, soma=section.name() in soma_names)

    amplitude = float(stimulus["amplitude_nA"]) * float(
        condition.get("stimulus_scale", 1.0)
    )
    clamps = _neuron_current_clamps(h, target_section(target_position), stimulus, amplitude)

    sample_dt = float(experiment["recording"]["sample_dt_ms"])
    time_vector = h.Vector()
    voltage_vector = h.Vector()
    time_vector.record(h._ref_t, sample_dt)
    voltage_vector.record(target_section(target_position)._ref_v, sample_dt)
    h.dt = float(experiment["integration_dt_ms"])
    h.steps_per_ms = 1.0 / h.dt
    h.celsius = float(experiment["temperature_C"])
    h.tstop = float(experiment["duration_ms"])
    h.finitialize(float(experiment["initial_voltage_mV"]))
    h.continuerun(h.tstop)
    # Keep references alive through continuerun.
    _ = clamps
    return (
        [float(value) for value in time_vector],
        [float(value) for value in voltage_vector],
        str(getattr(neuron, "__version__", "unknown")),
    )


def _validated_neuron_gap_manifest(
    mechanism_dir: Path,
    *,
    neuron_version: str | None = None,
    expected_manifest_sha256: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Verify the complete app-owned NMODL cache before loading executable code."""

    root = mechanism_dir.expanduser().resolve()
    manifest_path = root / "mechanism_manifest.json"
    if not root.is_dir() or not manifest_path.is_file():
        raise FileNotFoundError(
            f"The app-owned NEURON gap mechanism cache is incomplete: {root}"
        )
    if expected_manifest_sha256 is not None and (
        len(expected_manifest_sha256) != 64
        or _sha256(manifest_path) != expected_manifest_sha256
    ):
        raise ValueError(
            "The app-owned NEURON gap mechanism manifest changed after preflight"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The NEURON gap mechanism manifest must contain an object")
    if int(payload.get("schema_version", 0)) != 1 or payload.get("engine") != "neuron":
        raise ValueError("The NEURON gap mechanism manifest schema is unsupported")
    if neuron_version is not None and str(payload.get("neuron_version") or "") != str(
        neuron_version
    ):
        raise ValueError(
            "The compiled gap mechanisms belong to a different NEURON runtime"
        )

    records = dict(payload.get("mechanisms") or {})
    if set(records) != set(NEURON_GAP_SOURCE_HASHES):
        raise ValueError("The NEURON gap mechanism manifest is incomplete")
    for mechanism, expected_digest in NEURON_GAP_SOURCE_HASHES.items():
        record = dict(records.get(mechanism) or {})
        if str(record.get("source_sha256") or "") != expected_digest:
            raise ValueError(f"The frozen {mechanism} source identity is invalid")
        source_relpath = Path(str(record.get("source_relpath") or ""))
        if source_relpath.is_absolute():
            raise ValueError(f"The {mechanism} source path is outside its cache")
        source_path = (root / source_relpath).resolve()
        try:
            source_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"The {mechanism} source path escapes its cache") from exc
        if not source_path.is_file() or _sha256(source_path) != expected_digest:
            raise ValueError(f"The staged {mechanism} source does not match its hash")

    library_relpath = Path(str(payload.get("library_relpath") or ""))
    if library_relpath.is_absolute():
        raise ValueError("The compiled NEURON mechanism path must be cache-relative")
    library_path = (root / library_relpath).resolve()
    try:
        library_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("The compiled NEURON mechanism path escapes its cache") from exc
    if not library_path.is_file() or _sha256(library_path) != str(
        payload.get("library_sha256") or ""
    ):
        raise ValueError("The compiled NEURON mechanism library hash is invalid")
    return payload, library_path


def _load_neuron_gap_mechanisms(
    h: Any,
    neuron: Any,
    mechanism_dir: Path,
    *,
    expected_manifest_sha256: str,
) -> tuple[dict[str, Any], Path]:
    manifest, library_path = _validated_neuron_gap_manifest(
        mechanism_dir,
        neuron_version=str(getattr(neuron, "__version__", "unknown")),
        expected_manifest_sha256=expected_manifest_sha256,
    )
    library_key = str(library_path)
    if library_key not in _LOADED_NEURON_MECHANISM_LIBRARIES:
        collisions = [name for name in NEURON_GAP_SOURCE_HASHES if hasattr(h, name)]
        if collisions:
            raise RuntimeError(
                "The selected NEURON runtime already exposes unverified mechanisms with "
                "Digifly names: " + ", ".join(collisions)
            )
        h.nrn_load_dll(library_key)
        missing = [name for name in NEURON_GAP_SOURCE_HASHES if not hasattr(h, name)]
        if missing:
            raise RuntimeError(
                "The app-owned NEURON mechanism library did not register: "
                + ", ".join(missing)
            )
        _LOADED_NEURON_MECHANISM_LIBRARIES.add(library_key)
    return manifest, library_path


def _regional_execution(circuit: Mapping[str, Any], experiment: Mapping[str, Any]) -> bool:
    scopes = [dict(circuit.get("hh") or {}), *[dict(x) for x in dict(circuit.get("neuron_overrides") or {}).values()]]
    # Native-channel models must use the same edge-midpoint electrodes and odd
    # CV grid in every backend, even for a soma-only recording. The historical
    # branch-end electrode required an extra BioNet soma proxy and produced a
    # measurable geometry-dependent error during spikes.
    membranes = [dict(circuit.get("membrane") or {}),
                 *[dict(x) for x in dict(circuit.get("neuron_mechanism_overrides") or {}).values()]]
    return bool(circuit.get("compartment_overrides") or circuit.get("compartment_mechanism_overrides")
                or (dict(circuit.get("gap_junction_policy") or {}).get("mode", "none") != "none"
                    and dict(circuit.get("gap_junction_policy") or {}).get("placement_policy") in {"soma_pair", "ais_pair"})
                or any(_active_native_channels(x) for x in membranes)
                or any(x.get("active_scope") == "soma_ais" for x in scopes)
                or dict(experiment.get("recording") or {}).get("target_region", "soma") != "soma"
                or any(x.get("target_region", "soma") != "soma" for x in experiment.get("stimuli") or ()))


def _join_expressions(terms: Sequence[str]) -> str:
    """Balanced Arbor label expression: no per-SWC-edge recursion chain."""
    terms = list(terms)
    if not terms:
        raise ValueError("No mapped region terms")
    while len(terms) > 1:
        terms = [f"(join {terms[i]} {terms[i+1]})" if i+1 < len(terms) else terms[i] for i in range(0,len(terms),2)]
    return terms[0]


def _regional_cell_data(circuit: Mapping[str, Any], neuron_id: str, node_map: Mapping[int, int]) -> dict[str, Any]:
    result = {"neuron_id": neuron_id, "hh": _merged_hh(circuit, neuron_id),
              "membrane": dict(dict(circuit.get("neuron_mechanism_overrides") or {}).get(neuron_id, circuit.get("membrane") or {})),
              "source_by_run": {str(v): int(k) for k, v in node_map.items()}}
    for key in ("compartment_overrides", "compartment_mechanism_overrides"):
        maps = dict(circuit.get(key) or {})
        if set(maps) - set(str(x) for x in circuit["neuron_ids"]):
            raise ValueError("A compartment override targets a neuron outside the circuit")
        result[key] = {}
        for source_id, values in dict(maps.get(neuron_id) or {}).items():
            if int(source_id) not in node_map:
                raise ValueError(f"Unmapped source SWC compartment {source_id} on neuron {neuron_id}")
            result[key][str(node_map[int(source_id)])] = dict(values)
    return result


def _electrical_contact_run_nodes(edge_manifest: Mapping[str, Any], neuron_id: str,
                                  node_map: Mapping[int, int]) -> list[int]:
    """Map hash-validated source contacts, never infer nodes from coordinates."""
    nodes = set()
    for edge in edge_manifest.get("electrical_edges") or ():
        for contact in edge.get("contacts") or ():
            for end in ("pre", "post"):
                if str(contact[f"{end}_id"]) != neuron_id:
                    continue
                source_node = int(contact[f"{end}_source_node_id"])
                if source_node not in node_map:
                    raise ValueError(f"Electrical contact targets missing source SWC node {source_node}")
                nodes.add(int(node_map[source_node]))
    return sorted(nodes)


def _regional_plan(rows: Sequence[Any], cell: Mapping[str, Any]) -> dict[str, Any]:
    """An SWC compartment is its incoming edge, never an entire branch run.

    Default -> per-neuron -> per-edge settings. Mid-edge recording/injection
    sites coincide with the central CV on the shared odd-segment grid. AIS is
    the existing geometric preview proxy, explicitly NOT an anatomical claim.
    """
    target, soma_point = _target_soma_node(rows, prefer_rostral=bool(cell.get("prefer_rostral_soma", False)))
    by_id = {r[0]: r for r in rows}
    edges = [r for r in rows if r[6] != -1]
    if not edges:
        edges = list(rows)
    edge_ids = {r[0] for r in edges}
    contact_nodes = {int(n) for n in cell.get("electrical_contact_run_node_ids") or ()}
    if contact_nodes - {r[0] for r in rows if r[6] != -1}:
        raise ValueError("Regional electrical contact requires a mapped non-root incoming SWC edge")
    # A junction at an internal SWC node uses a massless native endpoint but
    # Arbor's adjacent nonempty CV. Refine BOTH incident sides without moving
    # the physical attachment; all immediate children matter at a branch.
    contact_edges = {r[0] for r in edges if r[0] in contact_nodes or r[6] in contact_nodes}
    overrides = set(cell.get("compartment_overrides") or {}) | set(cell.get("compartment_mechanism_overrides") or {})
    if {int(n) for n in overrides} - edge_ids:
        raise ValueError("A compartment override has no incoming SWC edge (root or absent node)")
    if target not in edge_ids:
        target = edges[0][0]
    axon = [r for r in edges if r[1] == 2]
    axon.sort(key=lambda r: (min(math.dist(r[2:5], soma_point), math.dist(by_id.get(r[6], r)[2:5], soma_point)), r[0]))
    ais = {r[0] for r in axon[:max(1, min(16, (len(axon)+19)//20))]}
    base_hh = dict(cell["hh"])
    if base_hh.get("active_scope") == "soma_ais" and not ais:
        raise ValueError("AIS proxy requires type-2 axonal SWC edges; none are available")
    # BioNet requires soma[0]; construct all sections before connecting them.
    edges.sort(key=lambda r: (r[0] != target, r[0]))
    sections = []
    index = {r[0]: i for i, r in enumerate(edges)}
    sites = {}
    source_by_run = dict(cell.get("source_by_run") or {})
    for row in edges:
        node_id, kind, _, _, _, _, parent_id = row
        parent = by_id.get(parent_id, row)
        length = math.dist(parent[2:5], row[2:5])
        hh = {**base_hh, **dict(dict(cell.get("compartment_overrides") or {}).get(str(node_id)) or {})}
        if set(hh) - set(base_hh):
            raise ValueError("Unknown per-compartment HH parameter")
        if any(not math.isfinite(float(v)) for k,v in hh.items() if k != "active_scope") or float(hh["cm_uF_cm2"]) <= 0 or float(hh["ra_ohm_cm"]) <= 0:
            raise ValueError("Invalid per-compartment biophysics")
        membrane = dict(dict(cell.get("compartment_mechanism_overrides") or {}).get(str(node_id), cell.get("membrane") or {}))
        soma = kind == 1 or node_id == target
        scope = hh.get("active_scope", "all")
        if scope not in {"all", "soma", "soma_ais"}:
            raise ValueError("Unknown regional active scope")
        if scope == "soma_ais" and not ais:
            raise ValueError("AIS proxy requires type-2 axonal SWC edges")
        if scope in {"soma", "soma_ais"}:
            # Decorators already implement soma-only suppression. An AIS edge
            # keeps branch densities, without being mislabeled a soma.
            hh["active_scope"] = "all" if soma or (scope == "soma_ais" and node_id in ais) else "soma"
        section_id = index[node_id]
        sites[node_id] = {"section_id": section_id, "section_pos": 1.0}
        sections.append({"section_id": section_id, "section_name": f"digifly_edge_{node_id}",
                         "section_group": "soma" if soma else "axon" if kind == 2 else "dend" if kind != 4 else "apic",
                         "path": [parent_id, node_id] if parent_id in by_id else [node_id],
                         "node_id": node_id, "source_node_id": int(source_by_run.get(str(node_id), node_id)),
                         "length_um": max(length, .001), "nseg": _neuron_nseg_for_length(max(length, .001), target_spacing_um=ELECTRICAL_CONTACT_TARGET_SEGMENT_UM if node_id in contact_edges else None),
                         "parent_section_id": index.get(parent_id), "parent_section_pos": 1.0,
                         "uses_degenerate_stub": length == 0, "is_target_soma": node_id == target,
                         "soma": soma, "ais_proxy": node_id in ais, "hh": hh, "membrane": membrane})
    for row in rows:
        if row[0] not in sites:
            child = next((r[0] for r in edges if r[6] == row[0]), target)
            sites[row[0]] = {"section_id": index[child], "section_pos": 0.0}
    plan = {"neuron_id": str(cell.get("neuron_id") or ""), "target_node_id": target, "sections": sections, "node_sites": sites,
            "estimated_nseg": sum(s["nseg"] for s in sections),
            "degenerate_stub_count": sum(s["uses_degenerate_stub"] for s in sections),
            "ais_policy": "Geometric preview proxy: nearest ceil(5%) type-2 edges, bounded 1..16; not anatomical AIS",
            "site_policy": "Midpoint of each incoming source SWC edge; shared odd CV grid",
            "precedence": "default -> per-neuron -> per-SWC-edge"}
    if contact_nodes:
        plan["electrical_mesh_policy"] = {"schema_version":1, "target_spacing_um":ELECTRICAL_CONTACT_TARGET_SEGMENT_UM,
            "baseline_spacing_um":NEURON_TARGET_SEGMENT_UM, "contact_run_node_ids":sorted(contact_nodes),
            "contact_source_node_ids":[int(source_by_run.get(str(n),n)) for n in sorted(contact_nodes)],
            "refined_run_node_ids":sorted(contact_edges), "adjacency":"incoming edge plus ALL immediate outgoing edges",
            "attachment":"Unchanged physical SWC node. Native endpoint/BioNet source pointer; Arbor adjacent nonempty junction CV. Not identical discrete attachment states."}
    return plan


def _regional_sites(plan: Mapping[str, Any], region: str, cell: Mapping[str, Any]) -> list[dict[str, Any]]:
    sections = list(plan["sections"])
    selected = set(cell.get("compartment_overrides") or {}) | set(cell.get("compartment_mechanism_overrides") or {})
    sites = [s for s in sections if region == "all" or (region == "soma" and s["is_target_soma"])
             or (region == "ais" and s["ais_proxy"]) or (region == "selected_compartments" and str(s["node_id"]) in selected)]
    if not sites:
        raise ValueError(f"Target region {region} has no mapped SWC edges")
    return sites


def _regional_report_budget(plans: Mapping[str, Any], experiment: Mapping[str, Any]) -> int:
    """Conservative bound includes BioNet's full native all-CV report.

    This is an operational memory/output limit, not a scientific criterion.
    All engines share the bound so changing engines cannot hide a large report.
    """
    _require_neuron_segment_budget(0, sum(int(p["estimated_nseg"]) for p in plans.values()), segment_budget=NEURON_MAX_TOTAL_SEGMENTS)
    recording = dict(experiment.get("recording") or {})
    targets = recording.get("target_neuron_ids") or tuple(plans)
    columns = sum(int(plans[n]["estimated_nseg"]) for n in targets)
    samples = math.ceil(float(experiment["duration_ms"])/float(experiment["integration_dt_ms"]))+1
    trials = int(experiment.get("repetitions", 1))*sum(bool(c.get("enabled", True)) for c in experiment.get("conditions") or ({},))
    values = columns*samples*trials
    if values > 25_000_000:
        raise ValueError(f"Regional native recording would require {values:,} values; the local safety limit is 25,000,000. Reduce the circuit, duration or recording targets.")
    return values


def _neuron_regional_cell(h: Any, swc_path: Path, neuron_id: str, cell: Mapping[str, Any], segment_budget: int) -> dict[str, Any]:
    rows = _swc_rows(swc_path)
    plan = _regional_plan(rows, cell)
    _require_neuron_segment_budget(0, plan["estimated_nseg"], segment_budget=segment_budget)
    by_id = {r[0]: r for r in rows}
    counts = {group: 0 for group in ("soma", "axon", "dend", "apic")}
    sections = []
    for spec in plan["sections"]:
        group = spec["section_group"]
        sections.append(h.Section(name=f"{group}[{counts[group]}]"))
        counts[group] += 1
    for section, spec in zip(sections, plan["sections"]):
        h.pt3dclear(sec=section)
        for n in spec["path"]:
            r = by_id[n]
            h.pt3dadd(*map(float, r[2:5]), 2*float(r[5]), sec=section)
        if spec["uses_degenerate_stub"]:
            r = by_id[spec["node_id"]]
            h.pt3dadd(float(r[2])+.001, float(r[3]), float(r[4]), 2*float(r[5]), sec=section)
        section.nseg = spec["nseg"]
        _decorate_neuron_section(section, spec["hh"], spec["membrane"], soma=spec["soma"])
    for section, spec in zip(sections, plan["sections"]):
        if spec["parent_section_id"] is not None:
            section.connect(sections[spec["parent_section_id"]](1), 0)
    node_sites = {n:(sections[s["section_id"]],s["section_pos"]) for n,s in plan["node_sites"].items()}
    target = next(s for s in plan["sections"] if s["is_target_soma"])
    return {"sections": tuple(sections), "node_sites": node_sites,
            "soma_site": (sections[target["section_id"]], .5), "nseg": plan["estimated_nseg"], "regional_plan": plan}


def _neuron_branch_cell(
    h: Any,
    swc_path: Path,
    neuron_id: str,
    hh: Mapping[str, Any],
    *,
    prefer_rostral_soma: bool,
    segment_budget: int,
    membrane: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build maximal branch-run sections and an O(1) SWC-node site map."""

    rows = _swc_rows(swc_path)
    target_node_id, _target_point = _target_soma_node(
        rows,
        prefer_rostral=prefer_rostral_soma,
    )
    by_id = {row[0]: row for row in rows}
    children: dict[int, list[int]] = {node_id: [] for node_id in by_id}
    for node_id, _swc_type, _x, _y, _z, _radius, parent_id in rows:
        if parent_id in children:
            children[parent_id].append(node_id)
    for values in children.values():
        values.sort()

    def is_breakpoint(node_id: int) -> bool:
        row = by_id[node_id]
        parent_id = row[6]
        child_ids = children[node_id]
        if node_id == target_node_id or parent_id == -1:
            return True
        if len(child_ids) != 1 or by_id[parent_id][1] != row[1]:
            return True
        return by_id[child_ids[0]][1] != row[1]

    breakpoints = {node_id for node_id in by_id if is_breakpoint(node_id)}
    safe_id = "".join(character if character.isalnum() else "_" for character in neuron_id)
    sections: list[Any] = []
    node_sites: dict[int, tuple[Any, float]] = {}
    soma_names: set[str] = set()
    visited_edges: set[tuple[int, int]] = set()
    total_nseg = 0

    def make_section(path: list[int]) -> None:
        nonlocal total_nseg
        if len(path) < 2:
            return
        section = h.Section(
            name=f"digifly_{safe_id}_b{len(sections)}_{path[0]}_{path[-1]}"
        )
        h.pt3dclear(sec=section)
        arcs = [0.0]
        for index, node_id in enumerate(path):
            row = by_id[node_id]
            h.pt3dadd(
                float(row[2]),
                float(row[3]),
                float(row[4]),
                2.0 * float(row[5]),
                sec=section,
            )
            if index:
                previous = by_id[path[index - 1]]
                arcs.append(
                    arcs[-1]
                    + math.dist(
                        (previous[2], previous[3], previous[4]),
                        (row[2], row[3], row[4]),
                    )
                )
        total_arc = arcs[-1]
        if total_arc <= 0.0:
            final = by_id[path[-1]]
            h.pt3dadd(
                float(final[2]) + 0.001,
                float(final[3]),
                float(final[4]),
                2.0 * float(final[5]),
                sec=section,
            )
            total_arc = 0.001
        target_nseg = _neuron_nseg_for_length(total_arc)
        _require_neuron_segment_budget(
            total_nseg,
            target_nseg,
            segment_budget=segment_budget,
        )
        section.nseg = target_nseg
        total_nseg += target_nseg
        parent_site = node_sites.get(path[0])
        if parent_site is not None:
            parent_section, parent_x = parent_site
            try:
                section.connect(parent_section(float(parent_x)), 0.0)
            except TypeError:
                section.connect(parent_section(float(parent_x)))
        sections.append(section)
        denominator = arcs[-1] if arcs[-1] > 0.0 else 1.0
        for index, node_id in enumerate(path):
            if index == 0 and node_id in node_sites:
                continue
            node_sites[node_id] = (section, float(arcs[index] / denominator))
        if any(by_id[node_id][1] == 1 for node_id in path[1:]):
            soma_names.add(section.name())

    def make_singleton(node_id: int) -> None:
        nonlocal total_nseg
        _require_neuron_segment_budget(
            total_nseg,
            1,
            segment_budget=segment_budget,
        )
        row = by_id[node_id]
        section = h.Section(name=f"digifly_{safe_id}_b{len(sections)}_{node_id}")
        h.pt3dclear(sec=section)
        diameter = 2.0 * float(row[5])
        h.pt3dadd(float(row[2]), float(row[3]), float(row[4]), diameter, sec=section)
        h.pt3dadd(
            float(row[2]) + 0.001,
            float(row[3]),
            float(row[4]),
            diameter,
            sec=section,
        )
        sections.append(section)
        total_nseg += 1
        node_sites[node_id] = (section, 0.0)
        if row[1] == 1:
            soma_names.add(section.name())

    def trace_path(start: int, child: int) -> list[int]:
        path = [start, child]
        visited_edges.add((start, child))
        current = child
        while current not in breakpoints:
            next_ids = children[current]
            if len(next_ids) != 1:
                break
            next_id = next_ids[0]
            visited_edges.add((current, next_id))
            path.append(next_id)
            current = next_id
        return path

    roots = sorted(node_id for node_id, row in by_id.items() if row[6] == -1)
    # Start only at component roots. End breakpoints are appended after their
    # incoming section exists, so every child connects to an established site.
    queue = roots
    processed: set[int] = set()
    queue_index = 0
    while queue_index < len(queue):
        start = queue[queue_index]
        queue_index += 1
        if start in processed or start not in breakpoints:
            continue
        processed.add(start)
        child_ids = children[start]
        if not child_ids:
            if start not in node_sites:
                make_singleton(start)
            continue
        for child in child_ids:
            if (start, child) in visited_edges:
                continue
            path = trace_path(start, child)
            make_section(path)
            end = path[-1]
            if end in breakpoints and end not in processed:
                queue.append(end)

    for node_id in sorted(by_id):
        if node_id not in node_sites:
            make_singleton(node_id)
    if target_node_id not in node_sites:
        raise RuntimeError(f"NEURON could not map soma target node {target_node_id}")
    soma_site = node_sites[target_node_id]
    soma_names.add(soma_site[0].name())
    for section in sections:
        _decorate_neuron_section(section, hh, membrane or {}, soma=section.name() in soma_names)
    return {
        "sections": tuple(sections),
        "node_sites": node_sites,
        "soma_site": soma_site,
        "nseg": total_nseg,
    }


def _neuron_set_gap_pointer(
    h: Any,
    process: Any,
    peer_site: tuple[Any, float],
) -> None:
    peer_section, peer_x = peer_site
    try:
        h.setpointer(peer_section(float(peer_x))._ref_v, "vgap_ptr", process)
    except Exception:
        h.setpointer(peer_section(float(peer_x))._ref_v, process, "vgap_ptr")


def _neuron_network_traces(
    cells: Mapping[str, Mapping[str, Any]],
    experiment: Mapping[str, Any],
    stimulus: Mapping[str, Any],
    condition: Mapping[str, Any],
    edge_manifest: Mapping[str, Any],
    mechanism_dir: Path | None,
    mechanism_manifest_sha256: str,
) -> tuple[dict[str, tuple[list[float], list[float]]], str]:
    """Execute one deterministic, fixed-step multi-cell NEURON network."""

    os.environ.pop("DISPLAY", None)
    from neuron import h
    import neuron

    h("forall delete_section()")
    h.load_file("stdrun.hoc")
    neuron_ids = tuple(cells)
    chemical_enabled = bool(condition.get("chemical_synapses_enabled", True))
    gap_enabled = bool(condition.get("gap_junctions_enabled", True))
    chemical_edges = [dict(item) for item in edge_manifest.get("chemical_edges") or ()]
    electrical_edges = [dict(item) for item in edge_manifest.get("electrical_edges") or ()]
    if gap_enabled and electrical_edges:
        if mechanism_dir is None:
            raise FileNotFoundError("The app-owned NEURON gap mechanism path is missing")
        _load_neuron_gap_mechanisms(
            h,
            neuron,
            mechanism_dir,
            expected_manifest_sha256=mechanism_manifest_sha256,
        )

    built: dict[str, dict[str, Any]] = {}
    total_segments = 0
    for neuron_id in neuron_ids:
        cell = dict(cells[neuron_id])
        if cell.get("regional"):
            built[neuron_id] = _neuron_regional_cell(h, Path(cell["swc_path"]), neuron_id, cell, NEURON_MAX_TOTAL_SEGMENTS-total_segments)
        else:
            built[neuron_id] = _neuron_branch_cell(
            h,
            Path(str(cell["swc_path"])),
            neuron_id,
            dict(cell["hh"]),
            prefer_rostral_soma=bool(cell["prefer_rostral_soma"]),
            segment_budget=NEURON_MAX_TOTAL_SEGMENTS - total_segments,
            membrane=dict(cell.get("membrane") or {}),
        )
        total_segments += int(built[neuron_id]["nseg"])
    h.define_shape()

    target_ids = {
        str(value) for value in stimulus.get("target_neuron_ids") or neuron_ids
    }
    recording = dict(experiment.get("recording") or {})
    record_ids = {
        str(value) for value in recording.get("target_neuron_ids") or neuron_ids
    }
    amplitude = float(stimulus["amplitude_nA"]) * float(
        condition.get("stimulus_scale", 1.0)
    )
    clamps: list[Any] = []
    for neuron_id in neuron_ids:
        if neuron_id not in target_ids:
            continue
        if cells[neuron_id].get("regional"):
            specs = _regional_sites(built[neuron_id]["regional_plan"], stimulus["target_region"], cells[neuron_id])
            sites = [(built[neuron_id]["sections"][s["section_id"]], .5) for s in specs]
        else:
            sites = [built[neuron_id]["soma_site"]]
        for section, x in sites:
            clamps.extend(_neuron_current_clamps(h, section(float(x)), stimulus, amplitude/len(sites)))

    synapses: list[Any] = []
    netcons: list[Any] = []
    if chemical_enabled:
        threshold = float(
            dict(edge_manifest.get("chemical_synapse_policy") or {}).get(
                "spike_threshold_mV", 0.0
            )
        )
        for contact in chemical_edges:
            pre_id = str(contact["pre_id"])
            post_id = str(contact["post_id"])
            source_section, source_x = built[pre_id]["soma_site"]
            post_node = int(contact["post_run_node_id"])
            post_site = built[post_id]["node_sites"].get(post_node)
            if post_site is None:
                raise ValueError(
                    f"Chemical contact {contact['contact_id']} maps to missing run node {post_node}"
                )
            post_section, post_x = post_site
            synapse = h.Exp2Syn(post_section(float(post_x)))
            synapse.e = float(contact["reversal_mV"])
            synapse.tau1 = float(contact["tau1_ms"])
            synapse.tau2 = float(contact["tau2_ms"])
            netcon = h.NetCon(
                source_section(float(source_x))._ref_v,
                synapse,
                sec=source_section,
            )
            netcon.threshold = threshold
            netcon.weight[0] = float(contact["weight_uS"])
            netcon.delay = float(contact["delay_ms"])
            synapses.append(synapse)
            netcons.append(netcon)

    gap_processes: list[Any] = []
    if gap_enabled:
        policy = dict(edge_manifest.get("gap_junction_policy") or {})
        mode = str(policy.get("mode") or "")
        preferred = str(policy.get("preferred_direction") or "a_to_b")
        for edge in electrical_edges:
            neuron_a = str(edge["neuron_a"])
            neuron_b = str(edge["neuron_b"])
            for raw_contact in edge.get("contacts") or ():
                contact = dict(raw_contact)

                def site_for(neuron_id: str) -> tuple[Any, float]:
                    if str(contact["pre_id"]) == neuron_id:
                        node_id = int(contact["pre_run_node_id"])
                        contact_end = "pre"
                    elif str(contact["post_id"]) == neuron_id:
                        node_id = int(contact["post_run_node_id"])
                        contact_end = "post"
                    else:
                        raise ValueError(
                            f"Gap contact {contact['contact_id']} is outside pair {neuron_a}, {neuron_b}"
                        )
                    if f"{contact_end}_source_edge_fraction" in contact:
                        plan = built[neuron_id].get("regional_plan")
                        if not plan:
                            raise ValueError("Synthetic electrical placement requires the shared regional mesh")
                        section_id,x = _regional_contact_site(plan,node_id,float(contact[f"{contact_end}_source_edge_fraction"]))
                        site = (built[neuron_id]["sections"][section_id],x)
                    else:
                        site = built[neuron_id]["node_sites"].get(node_id)
                    if site is None:
                        raise ValueError(
                            f"Gap contact {contact['contact_id']} maps to missing run node {node_id}"
                        )
                    return site

                site_a = site_for(neuron_a)
                site_b = site_for(neuron_b)
                conductance_ns = float(contact["effective_g_uS"]) * 1000.0
                if mode == "ohmic":
                    section_a, x_a = site_a
                    section_b, x_b = site_b
                    gap_a = h.Gap(float(x_a), sec=section_a)
                    gap_b = h.Gap(float(x_b), sec=section_b)
                    gap_a.g = conductance_ns
                    gap_b.g = conductance_ns
                    _neuron_set_gap_pointer(h, gap_a, site_b)
                    _neuron_set_gap_pointer(h, gap_b, site_a)
                    gap_processes.extend((gap_a, gap_b))
                elif mode == "rectifying":
                    source_site, target_site = (
                        (site_a, site_b) if preferred == "a_to_b" else (site_b, site_a)
                    )
                    target_section, target_x = target_site
                    gap = h.RectGap(float(target_x), sec=target_section)
                    gap.gmax = conductance_ns
                    _neuron_set_gap_pointer(h, gap, source_site)
                    gap_processes.append(gap)
                elif mode == "heterotypic_rectifying":
                    section_a, x_a = site_a
                    section_b, x_b = site_b
                    gap_a = h.HeteroRectGap(float(x_a), sec=section_a)
                    gap_b = h.HeteroRectGap(float(x_b), sec=section_b)
                    orient_a, orient_b = (
                        (1.0, -1.0) if preferred == "a_to_b" else (-1.0, 1.0)
                    )
                    for process, orientation in ((gap_a, orient_a), (gap_b, orient_b)):
                        process.gmax_open = conductance_ns
                        process.gmax_closed = conductance_ns * float(
                            policy.get("g_closed_frac", 0.0)
                        )
                        process.orientation = orientation
                        process.vhalf = float(policy.get("vhalf_mV", 0.0))
                        process.vslope = float(policy.get("vslope_mV", 5.0))
                        process.empirical_residual_frac = float(
                            policy.get("empirical_residual_frac", 0.2)
                        )
                        process.tau_open_ms = float(policy.get("tau_open_ms", 6.0))
                        process.tau_close_ms = float(policy.get("tau_close_ms", 2.0))
                    _neuron_set_gap_pointer(h, gap_a, site_b)
                    _neuron_set_gap_pointer(h, gap_b, site_a)
                    gap_processes.extend((gap_a, gap_b))
                else:
                    raise ValueError(f"Unsupported Digifly gap-junction mode: {mode}")

    sample_dt = float(recording["sample_dt_ms"])
    time_vector = h.Vector()
    time_vector.record(h._ref_t, sample_dt)
    voltage_vectors: dict[str, Any] = {}
    for neuron_id in neuron_ids:
        if neuron_id not in record_ids:
            continue
        if cells[neuron_id].get("regional"):
            specs = _regional_sites(built[neuron_id]["regional_plan"], recording["target_region"], cells[neuron_id])
            sites = [((neuron_id, s["source_node_id"]), built[neuron_id]["sections"][s["section_id"]], .5) for s in specs]
        else:
            sec, x = built[neuron_id]["soma_site"]
            sites = [(neuron_id, sec, x)]
        for key, section, x in sites:
            vector = h.Vector()
            vector.record(section(float(x))._ref_v, sample_dt)
            voltage_vectors[key] = vector
    h.dt = float(experiment["integration_dt_ms"])
    h.steps_per_ms = 1.0 / h.dt
    h.celsius = float(experiment["temperature_C"])
    h.tstop = float(experiment["duration_ms"])
    try:
        h.CVode().active(0)
    except Exception:
        pass
    h.finitialize(float(experiment["initial_voltage_mV"]))
    h.continuerun(h.tstop)
    times = [float(value) for value in time_vector]
    traces = {
        neuron_id: (times, [float(value) for value in voltage_vectors[neuron_id]])
        for neuron_id in voltage_vectors
    }
    # Keep all hoc objects and sections alive through continuerun.
    _ = (built, clamps, synapses, netcons, gap_processes)
    return traces, str(getattr(neuron, "__version__", "unknown"))


def _write_plot(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    experiment: Mapping[str, Any],
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    grouped: dict[Any, tuple[list[float], list[float]]] = {}
    for row in rows:
        key = (
            str(row["condition"]),
            int(row["repetition"]),
            str(row["neuron_id"]),
            str(row.get("compartment_id", "")),
        )
        times, voltages = grouped.setdefault(key, ([], []))
        times.append(float(row["time_ms"]))
        voltages.append(float(row["voltage_mV"]))
    figure, axis = plt.subplots(figsize=(11.0, 5.9), constrained_layout=True)
    for (condition, repetition, neuron_id, compartment_id), (times, voltages) in grouped.items():
        label = f"{condition} · {neuron_id}" + (f" · SWC {compartment_id}" if compartment_id else "")
        if int(experiment.get("repetitions", 1)) != 1:
            label += f" · rep {repetition}"
        axis.plot(times, voltages, linewidth=1.9, label=label)
    axis.set_title(
        str(experiment.get("name") or "Digifly experiment"),
        fontsize=16,
    )
    axis.set_xlabel("Time (ms)", fontsize=13)
    axis.set_ylabel("Membrane voltage (mV)", fontsize=13)
    axis.tick_params(axis="both", labelsize=11)
    axis.grid(True, alpha=0.25)
    axis.legend(loc="best", fontsize=11)
    # Preserve enough source pixels for a crisp fit-to-frame preview on Retina
    # displays while keeping the portable PNG comfortably below dataset scale.
    figure.savefig(path, dpi=260)
    plt.close(figure)


def _write_spike_plot(path: Path, rows: Sequence[Mapping[str, Any]], experiment: Mapping[str, Any]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    grouped = {}
    for row in rows:
        key = (str(row["condition"]), int(row["repetition"]), str(row["neuron_id"]), str(row.get("compartment_id", "")))
        grouped.setdefault(key, []).append(float(row["spike_time_ms"]))
    figure, axis = plt.subplots(figsize=(11.0, 5.9), constrained_layout=True)
    for index, (key, times) in enumerate(grouped.items()):
        label = f"{key[0]} · {key[2]}" + (f" · SWC {key[3]}" if key[3] else "")
        axis.scatter(times, [index]*len(times), marker="|", label=label)
    axis.set(xlabel="Time (ms)", ylabel="Recorded source", title=str(experiment["name"]), xlim=(0,float(experiment["duration_ms"])))
    axis.set_yticks([])
    if grouped:
        axis.legend(loc="best", fontsize=10)
    else:
        axis.text(.5,.5,"No threshold crossings",ha="center",va="center",transform=axis.transAxes)
    figure.savefig(path,dpi=260)
    plt.close(figure)


def _trial_input_pins(paths: Sequence[Path]) -> list[dict[str,str]]:
    return [{"path":str(path.resolve()),"sha256":_sha256(path)} for path in sorted(set(paths))]


def _verify_trial_pins(pins: Sequence[Mapping[str,Any]]) -> None:
    for pin in pins:
        if _sha256(Path(pin["path"])) != pin["sha256"]:
            raise ValueError(f"Independent trial source/input changed: {pin['path']}")


def _contact_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_independent_contact_manifest(
    source_record: Mapping[str, Any], materialized: Mapping[str, Any],
    active_ids: Sequence[str], expected_materialized_sha256: str,
) -> None:
    """Bind unchanged source contacts and their condition-scoped execution mapping.

    The selected-contact identity belongs to the original source document, not
    to a document augmented with run/section IDs or filtered after ablation.
    Only the enumerated execution fields may differ from source contacts.
    The entire mapped document is separately pinned by the child request hash.
    """
    source_path = Path(str(source_record.get("path") or "")).expanduser().resolve()
    source = _load_edge_manifest({"edge_manifest_path": str(source_path),
        "edge_manifest_sha256": source_record.get("sha256")}, source_path.parent)
    _validate_edge_source_identity(source)
    if len(expected_materialized_sha256) != 64 or _contact_manifest_sha256(materialized) != expected_materialized_sha256:
        raise ValueError("Independent trial mapped contact manifest changed")
    active = [str(value) for value in active_ids]
    if len(active) != len(set(active)) or any(value not in source.get("neuron_ids", ()) for value in active):
        raise ValueError("Independent trial active neuron identities are invalid")
    expected = _condition_edges(source, active)
    projected = json.loads(json.dumps(materialized))
    chemical_fields = {"post_run_node_id", "bionet_section_id", "bionet_section_pos"}
    electrical_fields = {f"{end}_{suffix}" for end in ("pre", "post")
        for suffix in ("run_node_id", "bionet_node_id", "bionet_section_id", "bionet_section_pos")}
    for manifest, remove in ((expected, False), (projected, True)):
        groups = [(manifest.get("chemical_edges") or (), chemical_fields),
            ([contact for edge in manifest.get("electrical_edges") or () for contact in edge.get("contacts") or ()], electrical_fields)]
        for contacts, fields in groups:
            for contact in contacts:
                if not remove and fields.intersection(contact):
                    raise ValueError("Independent trial source contacts contain execution mapping fields")
                if remove:
                    for field in fields:
                        contact.pop(field, None)
    if projected != expected:
        raise ValueError("Independent trial mapped contacts do not preserve the selected source contacts")


def _trial_environment() -> dict[str,str]:
    environment = dict(os.environ)
    for key in tuple(environment):
        if key in {"PYTHONHOME","PYTHONPATH","PYTHONEXECUTABLE","PYTHONSTARTUP","PYTHONINSPECT","PYTHONUSERBASE","PYTHONPLATLIBDIR","__PYVENV_LAUNCHER__","VIRTUAL_ENV","LD_LIBRARY_PATH","LD_PRELOAD","DISPLAY"} or key.startswith(("CONDA_","DYLD_","QT_","QML_","NEURON","NRN","CORENRN","NMODL")):
            environment.pop(key,None)
    environment.update(NEURON_MODULE_OPTIONS="-nogui",PYTHONNOUSERSITE="1",PYTHONDONTWRITEBYTECODE="1",MPLBACKEND="Agg",OMP_NUM_THREADS="1",OPENBLAS_NUM_THREADS="1",MKL_NUM_THREADS="1")
    environment["PATH"] = os.pathsep.join(dict.fromkeys([str(Path(sys.executable).absolute().parent),*environment.get("PATH","").split(os.pathsep)]))
    return environment


def _execute_neuron_trial(path: Path, expected_sha256: str) -> None:
    if _sha256(path) != expected_sha256:
        raise ValueError("Independent trial request changed before child execution")
    trial = json.loads(path.read_text(encoding="utf-8"))
    if trial.get("schema") != "digifly-independent-neuron-trial-v1":
        raise ValueError("Unknown independent trial request")
    _verify_trial_pins(trial["pins"])
    _validate_independent_contact_manifest(trial["source_edge_manifest"], trial["edge_manifest"],
        trial["active_neuron_ids"], trial["mapped_contact_manifest_sha256"])
    if set(trial["cells"]) != set(trial["active_neuron_ids"]):
        raise ValueError("Independent trial cells do not match its active neuron identities")
    _load_precompiled_native_channels(trial["native_channel_execution"])
    mechanism_text = trial.get("neuron_gap_mechanisms") or ""
    traces,version = _neuron_network_traces(trial["cells"],trial["experiment"],trial["stimulus"],trial["condition"],trial["edge_manifest"],
        Path(mechanism_text) if mechanism_text else None,trial["neuron_gap_manifest_sha256"])
    _verify_trial_pins(trial["pins"])
    _atomic_json(path.parent/"trial_result.json",{"schema_version":1,"trial_sha256":expected_sha256,"runtime_version":version,
        "traces":[{"neuron_id":key[0] if isinstance(key,tuple) else key,"compartment_id":key[1] if isinstance(key,tuple) else "",
                   "times":list(map(float,times)),"voltages":list(map(float,voltages))} for key,(times,voltages) in traces.items()]})


def _prepare_neuron_trials(request_path: Path, request: Mapping[str,Any], cells: Mapping[str,Any], edges: Mapping[str,Any],
                           native: Mapping[str,Any], conditions: Sequence[Mapping[str,Any]], stimulus: Mapping[str,Any]) -> tuple[dict[tuple[int,int],Path],dict[str,Any]]:
    experiment = request["experiment"]
    output_dir = request_path.parent
    source_edges = _load_edge_manifest(request, output_dir)
    _validate_edge_source_identity(source_edges)
    source_record = {"path": request["edge_manifest_path"], "sha256": request["edge_manifest_sha256"]}
    runnable = sum(bool(_condition_cells(cells,c)) for c in conditions)*int(experiment["repetitions"])
    samples = max(2,int(math.ceil(float(experiment["duration_ms"])/float(experiment["recording"]["sample_dt_ms"]))))
    segments = sum(sum(s["nseg"] for s in cell.get("regional_plan",{}).get("sections",())) or len(_swc_rows(Path(cell["swc_path"]))) for cell in cells.values())
    forecast = _trials.resource_forecast(int(experiment.get("workers",1)),runnable,engine="neuron",segments=segments,report_values=samples*max(1,segments))
    _atomic_json(output_dir/"worker_resource_forecast.json",forecast)
    paths = [request_path,Path(source_record["path"]),Path(__file__),Path(_trials.__file__),*map(lambda c:Path(c["swc_path"]),cells.values()),
             *[Path(record["path"]) for record in request.get("morphologies",{}).values()]]
    if native:
        paths.extend([Path(native["library"]),Path(native["builder"]["path"]),*[Path(record["path"]) for record in native["sources"].values()]])
    pins = _trial_input_pins(paths)
    tasks=[]
    results={}
    root=output_dir/"independent_trials"
    root.mkdir()
    for index,condition in enumerate(conditions):
        condition_cells=_condition_cells(cells,condition)
        if not condition_cells:
            continue
        condition_edges=_condition_edges(edges,tuple(condition_cells))
        mapped_digest = _contact_manifest_sha256(condition_edges)
        _validate_independent_contact_manifest(source_record, condition_edges, tuple(condition_cells), mapped_digest)
        for repetition in range(1,int(experiment["repetitions"])+1):
            trial_dir=root/f"condition-{index+1:03d}-repetition-{repetition:03d}"
            trial_dir.mkdir()
            path=trial_dir/"trial_request.json"
            seed=int(experiment.get("random_seed",1))+repetition-1
            _atomic_json(path,{"schema":"digifly-independent-neuron-trial-v1","pins":pins,"cells":condition_cells,
                "experiment":{**experiment,"workers":1,"random_seed":seed},"stimulus":stimulus,"condition":condition,"edge_manifest":condition_edges,
                "source_edge_manifest": source_record, "active_neuron_ids": list(condition_cells),
                "mapped_contact_manifest_sha256": mapped_digest,
                "native_channel_execution":native,"neuron_gap_mechanisms":request.get("neuron_gap_mechanisms") or "",
                "neuron_gap_manifest_sha256":request.get("neuron_gap_manifest_sha256") or ""})
            digest=_sha256(path)
            tasks.append({"label":f"{condition['name']} repetition {repetition}","seed":seed,"cwd":str(trial_dir),
                "log_path":str(trial_dir/"worker_subprocess.log"),"pins":[*pins,{"path":str(path),"sha256":digest}],
                "command":[str(Path(sys.executable).absolute()),"-B",str(Path(__file__).resolve()),"--execute-trial",str(path),"--trial-sha256",digest]})
            results[index,repetition]=path
    _trials.execute(tasks,forecast["effective_workers"],environment=_trial_environment(),receipt_path=output_dir/"independent_trial_execution.json",cancel_marker=output_dir/"cancel.requested")
    return results,forecast


def run(request_path: Path) -> Path:
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if int(request.get("schema_version", 0)) != REQUEST_SCHEMA_VERSION:
        raise ValueError("Unsupported generic experiment request schema")
    output_dir = Path(str(request["output_dir"])).expanduser().resolve()
    if request_path.resolve().parent != output_dir:
        raise ValueError("Worker request must reside inside its exact output directory")
    experiment = dict(request["experiment"])
    circuit = dict(request["circuit"])
    engine = str(request["engine"])
    edge_manifest = _load_edge_manifest(request, output_dir)
    _validate_request_capabilities(engine, circuit, experiment, edge_manifest)
    _validate_edge_source_identity(edge_manifest)
    neuron_ids = tuple(str(value) for value in circuit.get("neuron_ids") or ())
    morphology_transform = (
        "parent_before_child_reindex_segment_tree_root_stub_v1"
        if engine == "arbor"
        else "parent_before_child_reindex_neuron_branch_runs_v1"
        if engine == "neuron"
        else "parent_before_child_reindex_v1"
    )
    if _regional_execution(circuit, experiment):
        morphology_transform = "parent_before_child_reindex_source_swc_edge_odd_cv_v1"
    morphology_records: dict[str, dict[str, Any]] = {}
    simulation_cells: dict[str, dict[str, Any]] = {}
    morphology_artifacts: list[dict[str, str]] = []
    morphology_metadata: dict[str, dict[str, Any]] = {}
    resolved_edge_manifest = json.loads(json.dumps(edge_manifest))
    chemical_contacts = list(resolved_edge_manifest.get("chemical_edges") or ())
    electrical_contacts = [
        contact
        for edge in resolved_edge_manifest.get("electrical_edges") or ()
        for contact in edge.get("contacts") or ()
    ]
    morphology_root = output_dir / "morphologies"
    morphology_root.mkdir(parents=True, exist_ok=False)
    path_components = {
        neuron_id: _safe_neuron_path_component(neuron_id)
        for neuron_id in neuron_ids
    }
    if len(set(path_components.values())) != len(path_components):
        raise ValueError("Neuron IDs did not produce unique run path components")
    for neuron_id in neuron_ids:
        morphology_record = dict(
            (request.get("morphologies") or {}).get(neuron_id) or {}
        )
        swc_path = Path(str(morphology_record.get("path") or "")).expanduser().resolve()
        if not swc_path.is_file():
            raise FileNotFoundError(f"Source SWC is missing: {swc_path}")
        source_label = f"Source SWC for neuron {neuron_id}"
        expected_digest = _required_sha256(
            morphology_record.get("sha256"),
            label=source_label,
        )
        path_component = path_components[neuron_id]
        cell_dir = morphology_root / path_component
        cell_dir.mkdir(exist_ok=False)
        normalized_swc_path, node_map_path, normalized_digest = _normalize_swc(
            swc_path,
            cell_dir,
            expected_source_sha256=expected_digest,
            source_label=source_label,
            coordinate_transform=morphology_record.get("coordinate_transform"),
        )
        actual_digest = expected_digest
        node_map = _source_to_run_node_map(node_map_path)
        for contact in electrical_contacts:
            if str(contact["pre_id"]) == neuron_id:
                contact["pre_run_node_id"] = node_map[int(contact["pre_source_node_id"])]
            if str(contact["post_id"]) == neuron_id:
                contact["post_run_node_id"] = node_map[int(contact["post_source_node_id"])]
        for contact in chemical_contacts:
            if str(contact["post_id"]) == neuron_id:
                contact["post_run_node_id"] = node_map[int(contact["post_source_node_id"])]
        prefer_rostral_soma = str(
            morphology_record.get("family") or ""
        ).upper().startswith("DN")
        run_target_node_id, _target_point = _target_soma_node(
            _swc_rows(normalized_swc_path),
            prefer_rostral=prefer_rostral_soma,
        )
        source_by_run_node = {
            run_node_id: source_node_id
            for source_node_id, run_node_id in node_map.items()
        }
        source_target_node_id = source_by_run_node[run_target_node_id]
        target_policy = (
            "rostral max-Z non-root type-1 node"
            if prefer_rostral_soma
            else "widest non-root type-1 node"
        )
        root_stub = (
            _arbor_root_stub_length(_swc_rows(normalized_swc_path))
            if engine == "arbor"
            else 0.0
        )
        morphology_records[neuron_id] = morphology_record
        simulation_cells[neuron_id] = {
            "swc_path": str(normalized_swc_path),
            "hh": _merged_hh(circuit, neuron_id),
            "membrane": dict(dict(circuit.get("neuron_mechanism_overrides") or {}).get(neuron_id, circuit.get("membrane") or {})),
            "prefer_rostral_soma": prefer_rostral_soma,
        }
        if _regional_execution(circuit, experiment):
            simulation_cells[neuron_id].update(_regional_cell_data(circuit, neuron_id, node_map))
            simulation_cells[neuron_id]["electrical_contact_run_node_ids"] = _electrical_contact_run_nodes(edge_manifest, neuron_id, node_map)
            simulation_cells[neuron_id]["regional"] = True
            regional_plan = _regional_plan(_swc_rows(normalized_swc_path), simulation_cells[neuron_id])
            simulation_cells[neuron_id]["regional_plan"] = regional_plan
            _atomic_json(cell_dir / "regional_map.json", regional_plan)
        morphology_metadata[neuron_id] = {
            "run_path_component": path_component,
            "neuron_type": str(morphology_record.get("neuron_type") or "Unknown"),
            "source_swc_sha256": actual_digest,
            "simulation_swc_sha256": normalized_digest,
            "coordinate_transform": morphology_record.get("coordinate_transform"),
            "source_soma_target_node_id": source_target_node_id,
            "soma_target_policy": target_policy,
            "arbor_root_stub_length_um": root_stub,
        }
        relative_dir = Path("morphologies") / path_component
        if simulation_cells[neuron_id].get("regional"):
            morphology_artifacts.append({"kind": "document", "path": str(relative_dir / "regional_map.json"), "label": f"SWC edge/CV biophysics and recording map · {neuron_id}"})
        morphology_label = (
            "Normalized morphology"
            if len(neuron_ids) == 1
            else f"Normalized morphology · {neuron_id}"
        )
        node_map_label = (
            "Source-to-run SWC node map"
            if len(neuron_ids) == 1
            else f"Source-to-run SWC node map · {neuron_id}"
        )
        morphology_artifacts.extend(
            (
                {
                    "kind": "morphology",
                    "path": str(relative_dir / normalized_swc_path.name),
                    "label": morphology_label,
                },
                {
                    "kind": "table",
                    "path": str(relative_dir / node_map_path.name),
                    "label": node_map_label,
                },
            )
        )
    if _regional_execution(circuit, experiment):
        _regional_report_budget({n:c["regional_plan"] for n,c in simulation_cells.items()}, experiment)
    resolved_edge_path = output_dir / "resolved_edge_manifest.json"
    _atomic_json(resolved_edge_path, resolved_edge_manifest)
    if engine == "arbor":
        _atomic_json(output_dir/"arbor_execution_resource_forecast.json",
                     _arbor_execution_resource_forecast(simulation_cells,experiment,resolved_edge_manifest))

    native_evidence = _prepare_native_channels(request, output_dir) if engine == "neuron" else {}
    if engine == "arbor":
        native_evidence = _prepare_arbor_channels(request, output_dir)
    channel_catalogue_path = Path(native_evidence["library"]) if engine == "arbor" and native_evidence else None

    manifest_path = output_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "state": "running",
            "native_channel_execution": native_evidence,
            "started_at": _now(),
            "morphology_transform": morphology_transform,
            "morphologies": morphology_metadata,
            "resolved_edge_manifest_path": str(resolved_edge_path),
            "resolved_edge_manifest_sha256": _sha256(resolved_edge_path),
        }
    )
    _atomic_json(manifest_path, manifest)
    _emit(
        "start",
        f"Starting {engine.upper()} source-bound membrane experiment",
        neuron_ids=list(neuron_ids),
        chemical_contacts=len(chemical_contacts),
        electrical_contacts=len(electrical_contacts),
    )

    stimuli = [item for item in experiment.get("stimuli") or () if item.get("enabled", True)]
    conditions = [
        item for item in experiment.get("conditions") or () if item.get("enabled", True)
    ]
    stimulus = dict(stimuli[0])
    independent_results = {}
    if engine == "neuron" and int(experiment.get("workers",1)) > 1:
        independent_results,forecast = _prepare_neuron_trials(request_path,request,simulation_cells,resolved_edge_manifest,native_evidence,conditions,stimulus)
        manifest["worker_execution"] = forecast
        _atomic_json(manifest_path,manifest)
    trace_rows: list[dict[str, Any]] = []
    spike_rows: list[dict[str, Any]] = []
    runtime_version = "unknown"
    total = len(conditions) * int(experiment["repetitions"])
    completed = 0
    condition_receipts = []
    ramp_receipts = []
    arbor_native_artifacts = []
    for condition_index, condition in enumerate(conditions):
        condition_cells = _condition_cells(simulation_cells, condition)
        condition_edges = _condition_edges(resolved_edge_manifest, tuple(condition_cells))
        if stimulus.get("waveform") == "ramp":
            ramp_receipts.append(_ramp_quadrature_record(engine,experiment,stimulus,condition,condition_cells,condition_edges))
        condition_receipts.append({"condition":str(condition["name"]),
            "active_neuron_ids":list(condition_cells), "disabled_neuron_ids":list(condition.get("disabled_neuron_ids") or ()),
            "mechanism_scales":dict(condition.get("mechanism_scales") or {}),
            "ablation_policy":"Remove cells, all incident chemical/electrical contacts, clamps and recordings; no fabricated voltage",
            "scale_policy":"Multiply conductances after default/neuron/source-edge precedence; kinetics/reversals/source hashes unchanged",
            "cells": {n:{"hh":c["hh"], "membrane":c["membrane"], "regional_plan":c.get("regional_plan")} for n,c in condition_cells.items()},
            "edge_manifest":condition_edges})
        for repetition in range(1, int(experiment["repetitions"]) + 1):
            if engine=="neuron" and int(experiment.get("workers",1))>1 and (output_dir/"cancel.requested").is_file():
                raise _trials.TrialCancelled("Independent trial result aggregation cancelled by app request")
            _emit(
                "simulate",
                f"Running {condition['name']} repetition {repetition}",
                completed=completed,
                total=total,
            )
            if not condition_cells:
                traces = {}
            elif independent_results:
                trial_path = independent_results[condition_index,repetition]
                result = json.loads((trial_path.parent/"trial_result.json").read_text(encoding="utf-8"))
                if result.get("trial_sha256") != _sha256(trial_path):
                    raise ValueError("Independent trial result does not bind its exact request")
                runtime_version = result["runtime_version"]
                traces = {(row["neuron_id"],row["compartment_id"]) if row["compartment_id"] else row["neuron_id"]:
                          (row["times"],row["voltages"]) for row in result["traces"]}
            elif engine == "arbor" and (
                len(neuron_ids) > 1 or chemical_contacts or electrical_contacts or _regional_execution(circuit, experiment)
            ):
                catalogue_text = str(request.get("arbor_gap_catalogue") or "").strip()
                catalogue_path = Path(catalogue_text) if catalogue_text else None
                if electrical_contacts and (
                    catalogue_path is None or not catalogue_path.is_file()
                ):
                    raise FileNotFoundError(
                        f"The app-owned Arbor gap catalogue is missing: {catalogue_path}"
                    )
                traces, runtime_version = _arbor_network_traces(
                    condition_cells,
                    experiment,
                    stimulus,
                    condition,
                    condition_edges,
                    catalogue_path,
                    channel_catalogue_path,
                    native_report_path=output_dir/"arbor_native_reports"/f"condition-{condition_index+1}-repetition-{repetition}.csv",
                )
                native_path = Path("arbor_native_reports")/f"condition-{condition_index+1}-repetition-{repetition}.csv"
                if (output_dir/native_path).is_file():
                    arbor_native_artifacts.extend(({"kind":"table","path":str(native_path),"label":"Unmodified dense native Arbor observations"},
                        {"kind":"document","path":str(native_path.with_suffix('.plan.json')),"label":"Prospective exact-source strict-interior observation plan"},
                        {"kind":"document","path":str(native_path.with_suffix('.observations.json')),"label":"Native observations and detector events preserved before validation"},
                        {"kind":"document","path":str(native_path.with_suffix('.json')),"label":"Native-clock requested-grid selection receipt"}))
            # Cell count must not select a different morphology constructor.
            # Import3d rewrites soma geometry in the old single-cell path;
            # branch-run construction is shared with networks and BioNet.
            elif engine == "neuron":
                mechanism_text = str(
                    request.get("neuron_gap_mechanisms") or ""
                ).strip()
                mechanism_dir = Path(mechanism_text) if mechanism_text else None
                if electrical_contacts and (
                    mechanism_dir is None or not mechanism_dir.is_dir()
                ):
                    raise FileNotFoundError(
                        f"The app-owned NEURON gap mechanism cache is missing: {mechanism_dir}"
                    )
                traces, runtime_version = _neuron_network_traces(
                    condition_cells,
                    experiment,
                    stimulus,
                    condition,
                    condition_edges,
                    mechanism_dir,
                    str(request.get("neuron_gap_manifest_sha256") or ""),
                )
            else:
                neuron_id = next(iter(condition_cells))
                cell = condition_cells[neuron_id]
                simulate = _arbor_trace if engine == "arbor" else _neuron_trace
                times, voltages, runtime_version = simulate(
                    Path(str(cell["swc_path"])),
                    dict(cell["hh"]),
                    experiment,
                    stimulus,
                    condition,
                    prefer_rostral_soma=bool(cell["prefer_rostral_soma"]),
                    membrane=dict(cell.get("membrane") or {}),
                    **({"channel_catalogue_path": channel_catalogue_path} if engine == "arbor" else {}),
                )
                traces = {neuron_id: (times, voltages)}
            for neuron_id, (times, voltages) in traces.items():
                compartment_id = ""
                if isinstance(neuron_id, tuple):
                    neuron_id, compartment_id = neuron_id
                if len(times) < 2 or len(times) != len(voltages):
                    raise RuntimeError(
                        f"Simulator returned an incomplete soma-voltage trace for {neuron_id}"
                    )
                if not all(
                    math.isfinite(float(time_ms)) and math.isfinite(float(voltage_mV))
                    for time_ms, voltage_mV in zip(times, voltages)
                ):
                    raise RuntimeError(
                        f"Simulator returned non-finite soma voltage for {neuron_id}; no result was accepted"
                    )
                for time_ms, voltage_mV in zip(times, voltages):
                    trace_rows.append(
                        {
                            "condition": str(condition["name"]),
                            "repetition": repetition,
                            "neuron_id": neuron_id,
                            "compartment_id": compartment_id,
                            "time_ms": time_ms,
                            "voltage_mV": voltage_mV,
                        }
                    )
                if experiment["recording"].get("detect_spikes", True):
                    for spike_ms in _crossings(
                        times,
                        voltages,
                        float(experiment["recording"]["spike_threshold_mV"]),
                    ):
                        spike_rows.append(
                            {
                                "condition": str(condition["name"]),
                                "repetition": repetition,
                                "neuron_id": neuron_id,
                                "compartment_id": compartment_id,
                                "spike_time_ms": spike_ms,
                            }
                        )
            completed += 1

    conditions_path = output_dir / "condition_execution.json"
    _atomic_json(conditions_path, {"schema_version":1, "conditions":condition_receipts})
    if ramp_receipts:
        _atomic_json(output_dir/"stimulus_quadrature.json", {"schema":"digifly-ramp-quadrature-v1",
            "source_stimulus_unchanged":True,"output_timestamp_adjustment_ms":0.0,"conditions":ramp_receipts,
            "references":{
                "arbor_left_evaluation":"https://github.com/arbor-sim/arbor/blob/v0.12.2/arbor/backends/shared_state_base.hpp#L128",
                "arbor_step_boundaries":"https://github.com/arbor-sim/arbor/blob/v0.12.2/arbor/timestep_range.hpp#L12",
                "arbor_epochs":"https://github.com/arbor-sim/arbor/blob/v0.12.2/arbor/simulation.cpp#L275",
                "neuron_midpoint_evaluation":"https://github.com/neuronsimulator/nrn/blob/9.0.1/src/nrnoc/fadvance.cpp#L470"}})

    trace_path = output_dir / "voltage_traces.csv"
    with trace_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "condition",
                "repetition",
                "neuron_id",
                "compartment_id",
                "time_ms",
                "voltage_mV",
            ),
        )
        writer.writeheader()
        if experiment["recording"].get("record_voltage", True):
            writer.writerows(trace_rows)
    spike_path = output_dir / "spikes.csv"
    with spike_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("condition", "repetition", "neuron_id", "compartment_id", "spike_time_ms"),
        )
        writer.writeheader()
        writer.writerows(spike_rows)

    artifacts = [
        {"kind": "table", "path": trace_path.name, "label": "Mapped membrane voltage traces"},
        {"kind": "table", "path": spike_path.name, "label": "Threshold-crossing spikes"},
        {"kind": "document", "path": "experiment.json", "label": "Experiment document"},
        {"kind": "document", "path": "circuit.json", "label": "Circuit document"},
        {"kind": "document", "path": "edge_manifest.json", "label": "Source edge manifest"},
        {"kind": "document", "path": resolved_edge_path.name, "label": "Resolved edge manifest"},
        {"kind": "document", "path": request_path.name, "label": "Resolved worker request"},
    ]
    artifacts.extend(morphology_artifacts)
    if engine == "arbor":
        artifacts.extend(arbor_native_artifacts)
        artifacts.append({"kind":"document","path":"arbor_execution_resource_forecast.json","label":"Exact-envelope and native-report resource forecast"})
    if engine=="neuron" and int(experiment.get("workers",1))>1:
        artifacts.extend({"kind":"document","path":name,"label":label} for name,label in
            (("worker_resource_forecast.json","Requested/effective independent trial resources"),("independent_trial_execution.json","Independent child execution and cleanup receipt")))
    artifacts.append({"kind":"document", "path":conditions_path.name, "label":"Condition ablation and materialized conductances"})
    if ramp_receipts:
        artifacts.append({"kind":"document","path":"stimulus_quadrature.json","label":"Physical ramp and explicit solver midpoint quadrature"})
    voltage_enabled = bool(experiment["recording"].get("record_voltage", True))
    if not voltage_enabled:
        artifacts = [a for a in artifacts if a["path"] != trace_path.name]
    if experiment["recording"].get("make_plots", True):
        plot_path = output_dir / ("voltage_comparison.png" if voltage_enabled else "spike_raster.png")
        (_write_plot if voltage_enabled else _write_spike_plot)(plot_path, trace_rows if voltage_enabled else spike_rows, experiment)
        artifacts.append(
            {"kind": "image", "path": plot_path.name, "label": "Voltage comparison" if voltage_enabled else "Spike raster"}
        )
    completed_at = _now()
    neuron_types = {
        neuron_id: str(record.get("neuron_type") or "Unknown")
        for neuron_id, record in morphology_records.items()
    }
    metadata = {
        "Neuron IDs": ", ".join(neuron_ids),
        "Neuron types": ", ".join(
            f"{neuron_id}: {neuron_types[neuron_id]}" for neuron_id in neuron_ids
        ),
        "Simulator version": runtime_version,
        "Conditions": len(conditions),
        "Repetitions": int(experiment["repetitions"]),
        "Duration (ms)": float(experiment["duration_ms"]),
        "Integration step (ms)": float(experiment["integration_dt_ms"]),
        "Recorded samples": len(trace_rows) if voltage_enabled else 0,
        "Samples used for spike detection": len(trace_rows),
        "Detected spikes": len(spike_rows),
        "Morphology transform": morphology_transform,
        "Chemical contacts": len(chemical_contacts),
        "Electrical contacts": len(electrical_contacts),
        "Edge manifest SHA-256": str(request["edge_manifest_sha256"]),
    }
    if engine == "neuron":
        metadata["Requested independent trial workers"]=int(experiment.get("workers",1))
        metadata["Effective independent trial workers"]=manifest.get("worker_execution",{}).get("effective_workers",1)
        metadata.update(
            {
                "NEURON target segment length (um)": NEURON_TARGET_SEGMENT_UM,
                "NEURON total segment safety limit": NEURON_MAX_TOTAL_SEGMENTS,
            }
        )
    if electrical_contacts:
        metadata.update(
            {
                "Gap-junction mode": str(
                    resolved_edge_manifest["gap_junction_policy"]["mode"]
                ),
                "Gap conductance basis": str(
                    resolved_edge_manifest["gap_junction_policy"][
                        "conductance_basis"
                    ]
                ),
            }
        )
        if engine == "arbor":
            catalogue_path = Path(str(request["arbor_gap_catalogue"])).resolve()
            metadata["Arbor gap catalogue SHA-256"] = _sha256(catalogue_path)
        else:
            mechanism_dir = Path(str(request["neuron_gap_mechanisms"])).resolve()
            mechanism_manifest, library_path = _validated_neuron_gap_manifest(
                mechanism_dir,
                neuron_version=runtime_version if runtime_version != "unknown" else None,
                expected_manifest_sha256=str(
                    request.get("neuron_gap_manifest_sha256") or ""
                ),
            )
            metadata.update(
                {
                    "NEURON gap mechanism library SHA-256": str(
                        mechanism_manifest["library_sha256"]
                    ),
                    "NEURON gap mechanism library": str(library_path),
                }
            )
    if len(neuron_ids) == 1:
        neuron_id = neuron_ids[0]
        single = morphology_metadata[neuron_id]
        metadata.update(
            {
                "Neuron ID": neuron_id,
                "Neuron type": neuron_types[neuron_id],
                "SWC SHA-256": single["source_swc_sha256"],
                "Simulation SWC SHA-256": single["simulation_swc_sha256"],
                "Source soma target node ID": single["source_soma_target_node_id"],
                "Soma target policy": single["soma_target_policy"],
            }
        )
        if engine == "arbor":
            metadata["Arbor root stub length (um)"] = single[
                "arbor_root_stub_length_um"
            ]
    summary = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "workflow": "experiment_builder_v1",
        "engine": engine,
        "status": "complete",
        "completed_at": completed_at,
        "title": str(experiment["name"]),
        "metadata": metadata,
        "artifacts": artifacts,
    }
    summary_path = output_dir / "summary.json"
    if engine=="neuron" and int(experiment.get("workers",1))>1 and (output_dir/"cancel.requested").is_file():
        raise _trials.TrialCancelled("Independent trial result publication cancelled by app request")
    _atomic_json(summary_path, summary)
    manifest.update(
        {
            "state": "completed",
            "completed_at": completed_at,
            "summary_path": str(summary_path),
        }
    )
    _atomic_json(manifest_path, manifest)
    _emit("complete", "Experiment completed", summary_path=str(summary_path))
    return summary_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    entry = parser.add_mutually_exclusive_group(required=True)
    entry.add_argument("--request")
    entry.add_argument("--execute-trial")
    parser.add_argument("--trial-sha256",default="")
    args = parser.parse_args(argv)
    try:
        if args.execute_trial:
            _execute_neuron_trial(Path(args.execute_trial).resolve(),args.trial_sha256)
        else:
            run(Path(args.request).expanduser().resolve())
    except Exception as exc:
        request_path = Path(args.request or args.execute_trial).expanduser().resolve()
        manifest_path = request_path.parent / "run_manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                cancelled=isinstance(exc,_trials.TrialCancelled)
                manifest.update({"state": "cancelled" if cancelled else "failed", "cancelled_at" if cancelled else "failed_at": _now(), "error": str(exc)})
                _atomic_json(manifest_path, manifest)
            except Exception:
                pass
        _emit("cancelled" if isinstance(exc,_trials.TrialCancelled) else "failed", str(exc), error_type=type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
