"""Read-only BioNet evidence to a new app Results view; never launches science.

This module imports only the standard library until conversion is requested.
The packaged UI can delegate HDF5 conversion to its selected BioNet Python.
Checks establish data/load integrity, not renewed scientific run acceptance.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import re
import subprocess
import uuid

WORKFLOW = "pdcm_bionet_results_view_v1"
POPULATIONS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
FULL_COUNTS = (20683, 5834, 21915, 5479, 4850, 1065, 14395, 2948)
POLICY = "released_method_fixed_unique_integer_variance_v3"
MECHANISM_SHA256 = "d25f643ff90083706aadf4f2f992183d2d267289b9e38e542fca7439b74c1ebc"
GENERATOR_SHA256 = "d634ac906a95ee33074f01b9976e10603832bf4a186c40c768288c35239bda73"
SCALABLE_POLICY = "released_method_fixed_unique_integer_variance_scalable_v4"
SCALABLE_GENERATOR_SHA256 = "ef0f3b61cfcb353236a44e7f2b250ca2dae19a8ae1cc1e54a08251f6bf5135bc"
FIVEPCT_WORKFLOW = "pdcm_bionet_5pct_v1_app"
MAX_CHUNK = 65536
MAX_EVENTS = 5_000_000
CONVERSION_TIMEOUT_SECONDS = 120
PAPER_SAMPLE_SCOPE = "Saved deterministic capped sample; spikes/settled_spikes retain all owned cells"


def _json(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _owned(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Artifact must have an owned relative path")
    path = root / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Missing or outside artifact: {relative}")
    return path


def _bound(root, relative, inventory):
    path = _owned(root, relative)
    record = inventory.get(str(relative))
    digest = record.get("sha256") if isinstance(record, dict) else record
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or _hash(path) != digest:
        raise ValueError(f"Artifact hash differs or is absent: {relative}")
    if isinstance(record, dict) and (type(record.get("bytes")) is not int or path.stat().st_size != record["bytes"]):
        raise ValueError(f"Artifact size differs: {relative}")
    return path


def _integer(value, label):
    if type(value) is not int or value < 0:
        raise ValueError(f"Invalid {label}")
    return value


def _analysis_for(run):
    name = run.name
    if name == "simulation":
        # App-owned runs keep raw output and analysis as siblings. Infer only
        # this exact layout, and bind it to the selected raw run before handing
        # anything to the converter. All existing source/numeric gates remain.
        candidate = run.parent / "analysis"
        if candidate.is_symlink() or not candidate.is_dir():
            raise ValueError("Select the existing paper-window analysis folder explicitly")
        manifest = _json(_owned(candidate, "analysis_manifest.json"))
        if manifest.get("source_artifact_manifest_sha256") != _hash(_owned(run, "artifact_manifest.json")):
            raise ValueError("Sibling analysis belongs to another run")
        return candidate
    candidate = run.with_name(name[:-4] + "-analysis-v1") if name.endswith("-run") else run.with_name(name.replace("-run-", "-analysis-", 1))
    if candidate == run or not candidate.is_dir():
        raise ValueError("Select the existing paper-window analysis folder explicitly")
    return candidate


def _fivepct_app_binding(run, analysis, manifest_path, analysis_manifest_path, condition, audit):
    """Require the distinct completed app contract before exposing a 5% view.

    Import only the trusted worker installed beside this converter (or in the
    source package's workers folder). Never import a saved run's worker or
    scientific scripts. Its validation checks frozen source/request identity.
    """
    filename = "pdcm_bionet_5pct_app_worker.py"
    installed = Path(__file__).parent / filename
    if not installed.is_file():
        installed = Path(__file__).parent.parent / "workers" / filename
    if installed.is_symlink() or not installed.is_file():
        raise ValueError("Installed source-bound 5% app contract is absent")
    loader = importlib.util.spec_from_file_location("digifly_saved_results_5pct_contract", installed)
    if loader is None or loader.loader is None:
        raise ValueError("Installed 5% app contract cannot be read")
    contract = importlib.util.module_from_spec(loader)
    loader.loader.exec_module(contract)
    root = run.parent
    if run.name != "simulation" or analysis != root / "analysis" or contract.WORKFLOW != FIVEPCT_WORKFLOW:
        raise ValueError("5% Results require the owned completed 5% app execution layout")
    request = contract.validate_request(root)
    bounds = contract.BOUNDS
    if _hash(_owned(root, filename)) != _hash(installed):
        raise ValueError("Saved 5% worker differs from the installed app contract")
    installed_controller = installed.parent.parent / "core/pdcm_bionet_5pct_app.py"
    if (installed_controller.is_symlink() or not installed_controller.is_file()
            or _hash(_owned(root, contract.CONTROLLER_FILENAME)) != _hash(installed_controller)):
        raise ValueError("Saved 5% controller differs from the installed app source")
    for key in ("scale", "duration_ms", "drive", "dt_ms", "network_seed", "stimulus_seed"):
        if condition.get(key) != request["spec"][key]:
            raise ValueError("5% Results condition differs from its app request")
    names = ("request.json", "bundle_manifest.json", filename, contract.CONTROLLER_FILENAME, "resource_contract.json",
             "run_manifest.json", "execution_claim.json", "worker_claim.json", "runtime.json",
             "worker_result.json", "execution.json", "summary.json", "worker.log", "resource_samples.jsonl",
             "execution_adapter.json", "input_resource_guard.json", "structure/manifest.json")
    paths = {name: _owned(root, name) for name in names}
    hashes = {name: _hash(path) for name, path in paths.items()}
    state, claim, worker_claim, runtime, result, execution, summary = (
        _json(paths[name]) for name in ("run_manifest.json", "execution_claim.json", "worker_claim.json",
                                      "runtime.json", "worker_result.json", "execution.json", "summary.json"))
    resource_hash = hashes["resource_contract.json"]
    contract.validate_structure(condition, request["spec"])
    if hashes["structure/manifest.json"] != _hash(run / "input/manifest.json"):
        raise ValueError("5% saved run input differs from its built app structure")
    guard = _json(paths["input_resource_guard.json"])
    if (guard.get("workflow") != FIVEPCT_WORKFLOW or guard.get("bounds") != bounds
            or guard.get("external_events") != condition["external_events"]
            or guard.get("external_cells") != contract.MODEL_CONTRACT["cortical_cells"]
            or type(guard.get("maximum_events_in_one_cell")) is not int
            or not 0 < guard["maximum_events_in_one_cell"] <= bounds["external_events_per_cell"]
            or guard.get("batch_values_unchanged") is not True
            or guard.get("generator_restored_before_source_replay") is not True):
        raise ValueError("5% complete input resource evidence differs")
    adapter = _json(paths["execution_adapter.json"])
    if (adapter.get("schema") != "digifly-pdcm-bionet-5pct-v4-execution-adapter-v1"
            or adapter.get("workflow") != FIVEPCT_WORKFLOW
            or adapter.get("policy") != contract.MODEL_CONTRACT["execution_adapter_policy"]
            or adapter.get("runner_source_sha256") != contract.SOURCE_PINS["scripts/run_pdcm_bionet_scalable_v4.py"]
            or adapter.get("original_approved_condition") != [.02, 60100., "balanced_poisson"]
            or adapter.get("effective_approved_condition") != [.05, 60100., "balanced_poisson"]
            or adapter.get("restored_approved_condition") != [.02, 60100., "balanced_poisson"]
            or adapter.get("spec") != request["spec"] or adapter.get("runner_returned") is not True
            or adapter.get("scientific_acceptance") is not False):
        raise ValueError("5% executed condition adapter evidence differs")
    if (state.get("workflow") != FIVEPCT_WORKFLOW or state.get("state") != "completed"
            or state.get("scientific_acceptance") is not False
            or state.get("request_sha256") != hashes["request.json"]
            or state.get("bundle_manifest_sha256") != hashes["bundle_manifest.json"]
            or state.get("resource_contract_sha256") != resource_hash
            or state.get("summary_sha256") != hashes["summary.json"]):
        raise ValueError("5% app completion or request/resource binding is absent")
    if (claim.get("schema") != contract.CLAIM_SCHEMA or claim.get("workflow") != FIVEPCT_WORKFLOW
            or claim.get("bounds") != bounds or claim.get("resource_contract") != contract.RESOURCE_CONTRACT
            or claim.get("resource_contract_sha256") != resource_hash
            or claim.get("request_sha256") != hashes["request.json"]
            or claim.get("bundle_manifest_sha256") != hashes["bundle_manifest.json"]
            or claim.get("worker_sha256") != hashes[filename]
            or claim.get("controller_source_sha256") != hashes[contract.CONTROLLER_FILENAME]
            or claim.get("scientific_acceptance") is not False
            or worker_claim.get("workflow") != FIVEPCT_WORKFLOW
            or worker_claim.get("execution_claim_sha256") != hashes["execution_claim.json"]
            or runtime.get("python_sha256") != claim.get("python_sha256")
            or not re.fullmatch(r"[0-9a-f]{64}", str(claim.get("python_sha256", "")))
            or runtime.get("versions") != {"bmtk": "1.2.0", "neuron": "9.0.1", "numpy": "1.26.4", "h5py": "3.11.0"}):
        raise ValueError("5% app launch, worker or runtime identity differs")
    if (result.get("workflow") != FIVEPCT_WORKFLOW or result.get("spec") != request["spec"]
            or result.get("state") != "completed_technical_unreviewed"
            or result.get("model_contract") != contract.MODEL_CONTRACT
            or result.get("source_sha256") != contract.SOURCE_PINS
            or result.get("resource_contract") != contract.RESOURCE_CONTRACT
            or result.get("resource_contract_sha256") != resource_hash
            or result.get("execution_claim_sha256") != hashes["execution_claim.json"]
            or result.get("worker_claim_sha256") != hashes["worker_claim.json"]
            or result.get("input_resource_guard_sha256") != hashes["input_resource_guard.json"]
            or result.get("execution_adapter_sha256") != hashes["execution_adapter.json"]
            or result.get("worker_sha256") != hashes[filename]
            or result.get("controller_source_sha256") != hashes[contract.CONTROLLER_FILENAME]
            or result.get("runtime_sha256") != hashes["runtime.json"]
            or result.get("artifact_manifest_sha256") != _hash(manifest_path)
            or result.get("analysis_manifest_sha256") != _hash(analysis_manifest_path)
            or result.get("audit") != audit or result.get("technical_acceptance") is not True
            or result.get("scientific_acceptance") is not False
            or audit.get("cortical_cells") != contract.MODEL_CONTRACT["cortical_cells"]
            or audit.get("recurrent_contacts_censused") != contract.MODEL_CONTRACT["recurrent_contacts"]):
        raise ValueError("5% saved output differs from its completed app result")
    if (execution.get("schema") != contract.EXECUTION_SCHEMA or execution.get("workflow") != FIVEPCT_WORKFLOW
            or execution.get("command") != claim.get("command")
            or execution.get("bounds") != bounds or execution.get("resource_contract") != contract.RESOURCE_CONTRACT
            or execution.get("resource_contract_sha256") != resource_hash
            or execution.get("claim_sha256") != hashes["execution_claim.json"]
            or execution.get("worker_log_sha256") != hashes["worker.log"]
            or execution.get("resource_samples_sha256") != hashes["resource_samples.jsonl"]
            or execution.get("exit_code") != 0 or execution.get("failure") is not None
            or execution.get("scientific_acceptance") is not False):
        raise ValueError("5% Results require a successful bound app execution")
    walltime = execution.get("walltime_s")
    if type(walltime) not in (int, float) or not math.isfinite(walltime) or not 0 < walltime <= bounds["timeout_seconds"]:
        raise ValueError("5% app execution time measurement is invalid")
    for key, minimum, maximum in (
        ("sampled_process_tree_peak_rss_bytes", 1, bounds["rss_bytes"]),
        ("sampled_retained_output_peak_bytes", 1, bounds["output_bytes"]),
        ("sample_count", 1, math.ceil(bounds["timeout_seconds"] / bounds["sample_interval_seconds"]) + 1),
        ("sampled_minimum_available_memory_bytes", bounds["memory_reserve_bytes"], None),
        ("sampled_minimum_free_disk_bytes", bounds["disk_reserve_bytes"] + bounds["durable_copy_bytes"], None),
    ):
        value = execution.get(key)
        if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
            raise ValueError("5% app resource measurement is missing or outside its contract")
    if summary.get("state") != "completed" or summary.get("execution_sha256") != hashes["execution.json"]:
        raise ValueError("5% app summary lacks completed execution binding")
    for key, value in result.items():
        if key != "state" and summary.get(key) != value:
            raise ValueError("5% app summary differs from its worker result")
    # A complete durable copy retains historical absolute launch paths. Bind
    # their common identity while consuming the selected owned sibling files.
    original_root = Path(str(summary.get("run_root", "")))
    if (not original_root.is_absolute() or result.get("raw_run_root") != str(original_root / "simulation")
            or result.get("analysis_root") != str(original_root / "analysis")
            or summary.get("summary_path") != str(original_root / "summary.json")):
        raise ValueError("5% app result paths differ from their owned launch identity")
    return {str(paths[name]): digest for name, digest in hashes.items()}


def _saved_sources(run, analysis):
    """Bind only the consumed files; do not import/reexecute old audit scripts."""
    manifest_path = _owned(run, "artifact_manifest.json")
    files = _json(manifest_path).get("files")
    if not isinstance(files, dict):
        raise ValueError("Run artifact inventory is absent")
    required = ["provenance.json", "technical_result.json", "config.json", "input/manifest.json",
                "input/network/cortex_nodes.h5", "native_output/spikes.h5", "mechanisms/DigiflyPDCMEvent.mod"]
    for relative in required:
        _bound(run, relative, files)
    p, t, m = (_json(run / name) for name in required[:2] + ["input/manifest.json"])
    if t.get("state") != "completed_technical_unreviewed" or t.get("scientific_acceptance") is not False:
        raise ValueError("Saved technical state must not imply scientific acceptance")
    if any(t.get(key) != value for key, value in p.items()):
        raise ValueError("Technical result/provenance identity differs")
    scalable = m.get("construction_policy") == SCALABLE_POLICY
    if m.get("construction_policy") not in (POLICY, SCALABLE_POLICY) or m.get("independent_inputs") is not True:
        raise ValueError("Not the approved independent released-method construction")
    fivepct = scalable and m.get("scale") == .05
    expected_scale = .05 if fivepct else (.02 if scalable else .01)
    allowed_drives = ("balanced_poisson",) if scalable else ("balanced_poisson", "balanced_dc")
    if m.get("drive") not in allowed_drives or m.get("duration_ms") != 60100 or m.get("scale") != expected_scale:
        raise ValueError("Results import supports source-bound full60.1s1% Poisson/DC or 2%v4 Poisson only, plus distinct 5% app Poisson, not pilots")
    expected = [math.floor(expected_scale * n) for n in FULL_COUNTS]
    if m.get("population_order") != list(POPULATIONS) or m.get("scaled_counts") != expected or any(type(n) is not int for n in m["scaled_counts"]):
        raise ValueError("Population census differs")
    for key in ("network_seed", "stimulus_seed"):
        _integer(m.get(key), key)
        _integer(p.get(key), "provenance " + key)
        if p.get(key) != m[key]:
            raise ValueError("Seed/provenance identity differs")
    if p.get("duration_ms") != 60100 or p.get("input_manifest_sha256") != _hash(run / "input/manifest.json"):
        raise ValueError("Condition/input provenance differs")
    if scalable and any(p.get(key) != m[key] for key in ("scale", "drive")):
        raise ValueError("Scalable execution identity differs")
    if p.get("mechanism_source_sha256") != MECHANISM_SHA256 or _hash(run / "mechanisms/DigiflyPDCMEvent.mod") != MECHANISM_SHA256:
        raise ValueError("Independent mechanism source differs")
    input_files = m.get("files")
    if not isinstance(input_files, dict):
        raise ValueError("Independent input inventory is absent")
    generator_name = "build_pdcm_bionet_scalable_v4.py" if scalable else "build_pdcm_bionet_primary_v3.py"
    generator = _bound(run / "input", "provenance/" + generator_name, input_files)
    if _hash(generator) != (SCALABLE_GENERATOR_SHA256 if scalable else GENERATOR_SHA256):
        raise ValueError("Approved independent generator differs")
    required.append("input/provenance/" + generator_name)
    _bound(run, required[-1], files)
    binaries = [key for key in files if re.fullmatch(r"mechanisms/[^/]+/libnrnmech\.(dylib|so|dll)", key)]
    if len(binaries) != 1 or _hash(_bound(run, binaries[0], files)) != p.get("mechanism_compiled_sha256"):
        raise ValueError("Compiled mechanism binding differs")
    config = _json(run / "config.json")
    if config.get("target_simulator") != "BioNet" or config.get("run", {}).get("tstop") != 60100:
        raise ValueError("Saved simulator/duration differs")
    analysis_name = "manifest.json" if (analysis / "manifest.json").is_file() else "analysis_manifest.json"
    am = _json(_owned(analysis, analysis_name))
    source_hash = am.get("source_run_manifest_sha256", am.get("source_artifact_manifest_sha256"))
    if source_hash != _hash(manifest_path):
        raise ValueError("Analysis belongs to another run")
    af = am.get("files", am.get("files_sha256"))
    if not isinstance(af, dict):
        raise ValueError("Analysis inventory is absent")
    for relative in af:
        _bound(analysis, relative, af)
    for relative in ("audit.json", "paper_metrics.csv", "sampling_manifest.json"):
        if relative not in af:
            raise ValueError(f"Missing paper-window artifact: {relative}")
    audit = _json(analysis / "audit.json")
    if audit.get("technical_acceptance") is not True or audit.get("scientific_acceptance") is not False:
        raise ValueError("Prior technical audit is missing or overclaims acceptance")
    for key in (("network_seed", "stimulus_seed", "duration_ms", "scale", "drive") if scalable else ("network_seed", "stimulus_seed", "duration_ms")):
        if audit.get(key) != m[key]:
            raise ValueError("Analysis condition identity differs")
    expected_windows = {"settled": [100,60100], "correlation": [100,5100]}
    if scalable:
        expected_windows.update(synchrony_figures4_7=[100,5100], synchrony_figure8=[100,60100])
    if audit.get("analysis_windows_ms") != expected_windows:
        raise ValueError("Not the declared paper windows")
    source_files = {str(run / relative): _hash(run / relative) for relative in required + binaries}
    source_files[str(manifest_path)] = _hash(manifest_path)
    source_files[str(analysis / analysis_name)] = _hash(analysis / analysis_name)
    source_files.update({str(analysis / relative): _hash(analysis / relative) for relative in af})
    if fivepct:
        source_files.update(_fivepct_app_binding(run, analysis, manifest_path, analysis / analysis_name, m, audit))
    return m, p, audit, source_files


def _saved_sample_ids(analysis, counts, seed, *, scalable=False):
    """Validate the stored deterministic selection without running analysis."""
    sample = _json(analysis / "sampling_manifest.json")
    if (type(sample.get("schema_version")) is not int or sample["schema_version"] != 1
            or sample.get("method") != "uniform without replacement per population"
            or sample.get("maximum_cells_per_population") != 1000
            or type(sample.get("seed")) is not int or sample["seed"] != seed
            or not isinstance(sample.get("cell_ids"), dict)
            or set(sample["cell_ids"]) != set(POPULATIONS)):
        raise ValueError("Saved paper sampling policy or seed differs")
    caps = sample.get("sample_caps")
    if scalable or caps is not None:
        if not isinstance(caps, dict) or set(caps) != set(POPULATIONS):
            raise ValueError("Saved paper sample caps differ")
    begin = 0
    for index, (pop, count) in enumerate(zip(POPULATIONS, counts)):
        selected = min(1000, count)
        ids = sample["cell_ids"][pop]
        if (not isinstance(ids, list) or len(ids) != selected or any(type(gid) is not int for gid in ids)
                or ids != sorted(set(ids)) or any(not begin <= gid < begin + count for gid in ids)):
            raise ValueError("Saved sampled IDs must be unique owned population IDs of the exact count")
        expected = sorted(random.Random(seed + index).sample(range(begin, begin + count), selected))
        if ids != expected:
            raise ValueError("Saved sampled IDs differ from the source-bound deterministic selection")
        if caps is not None and (type(caps[pop]) is not int or caps[pop] != selected):
            raise ValueError("Saved paper sample cap count differs")
        begin += count
    return sample["cell_ids"]


def _paper_table(analysis, counts, spike_counts, *, scalable=False, silent_counts=None):
    """Check paper rows against their saved sample, never whole-owned counts."""
    with (analysis / "paper_metrics.csv").open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)):
            raise ValueError("Paper table columns are duplicated")
        rows = list(reader)
    if [row.get("population") for row in rows] != list(POPULATIONS):
        raise ValueError("Paper table population rows differ")
    for index, row in enumerate(rows):
        if None in row or any(value is None for value in row.values()):
            raise ValueError("Malformed paper table row")
        expected_rate = spike_counts[index] / (counts[index] * 60)
        rate_matches = (math.isclose(float(row.get("mean_rate_hz_60s", "nan")), expected_rate, rel_tol=1e-12, abs_tol=1e-12)
                        if scalable else row.get("mean_rate_hz_60s") == format(expected_rate, ".8g"))
        if row.get("sampled_cells") != str(counts[index]) or not rate_matches:
            raise ValueError("Paper-window rate/cell table differs from saved spikes")
        try:
            defined = int(row["correlation_defined_cells"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Correlation cell census missing or invalid") from exc
        if not 0 <= defined <= counts[index] or (spike_counts[index] == 0 and defined != 0):
            raise ValueError("Correlation cell census differs")
        if scalable:
            keys = ("sampled_silent_cells_60s", "sampled_cv_defined_cells_60s",
                    "sampled_cv_undefined_cells_60s", "correlation_undefined_cells_5s")
            values = {}
            for key in keys:
                text = row.get(key)
                try:
                    value = int(text)
                except (TypeError, ValueError) as exc:
                    raise ValueError("Sampled metric cell census missing or invalid") from exc
                if str(value) != text or not 0 <= value <= counts[index]:
                    raise ValueError("Sampled metric cell census differs")
                values[key] = value
            valid_cv = values["sampled_cv_defined_cells_60s"]
            silent = values["sampled_silent_cells_60s"]
            if (silent_counts is None or silent != silent_counts[index]
                    or valid_cv + values["sampled_cv_undefined_cells_60s"] != counts[index]
                    or valid_cv > counts[index] - silent
                    or defined > counts[index] - silent
                    or (valid_cv == 0) != (row.get("mean_cv_isi_60s") == "")
                    or values["correlation_undefined_cells_5s"] != counts[index] - defined):
                raise ValueError("Sampled silent/CV/correlation denominator differs")
        for key in ("mean_cv_isi_60s", "synchrony_3ms_5s", "synchrony_3ms_60s", "mean_signed_cross_correlation_25ms_5s", "mean_absolute_cross_correlation_25ms_5s"):
            text = row.get(key)
            if text is None:
                raise ValueError("Paper table metric column missing")
            if not text:
                continue
            number = float(text)
            if not math.isfinite(number) or ("signed_cross" in key and not -1 <= number <= 1) or ("signed_cross" not in key and number < 0):
                raise ValueError("Invalid paper metric")
            if spike_counts[index] == 0:
                raise ValueError("Silent population secondary metrics must remain undefined")
    return rows


def _convert(run, output_root, name, analysis):
    try:
        import h5py
        import numpy as np
    except ImportError as exc:
        raise ValueError("Use the selected BioNet Python with h5py for saved-results conversion") from exc
    m, provenance, audit, sources = _saved_sources(run, analysis)
    if output_root.is_relative_to(run) or output_root.is_relative_to(analysis):
        raise ValueError("Results cache must be outside original evidence folders")
    counts = m["scaled_counts"]
    n = sum(counts)
    with h5py.File(run / "input/network/cortex_nodes.h5", "r") as handle:
        group = handle["nodes/cortex"]
        if group["node_id"].shape != (n,) or group["node_type_id"].shape != (n,):
            raise ValueError("Outside source-bound condition cell census")
        ids, types = group["node_id"][:], group["node_type_id"][:]
    if ids.dtype.kind not in "iu" or types.dtype.kind not in "iu" or not np.array_equal(ids, np.arange(n)) or not np.array_equal(types, np.repeat(np.arange(100,108), counts)):
        raise ValueError("Actual SONATA node IDs/types disagree with population census")
    scalable = m["construction_policy"] == SCALABLE_POLICY
    sampled_ids = _saved_sample_ids(analysis, counts, m["stimulus_seed"], scalable=scalable)
    sampled_counts = np.array([len(sampled_ids[pop]) for pop in POPULATIONS], dtype=np.int64)
    sampled_mask = np.zeros(n, dtype=bool)
    sampled_mask[[gid for pop in POPULATIONS for gid in sampled_ids[pop]]] = True
    slug = re.sub(r"[^a-z0-9]+", "-", (name or run.name).casefold()).strip("-")[:60] or "bionet"
    view = output_root / (slug + "-view-" + uuid.uuid4().hex)
    view.mkdir(parents=True, exist_ok=False)
    try:
        with (view / "cells.csv").open("x", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream); writer.writerow(("gid", "population"))
            writer.writerows((int(gid), POPULATIONS[int(kind)-100]) for gid, kind in zip(ids,types))
        population_ends = np.cumsum(counts)
        settled_counts = np.zeros(8, dtype=np.int64)
        sampled_settled_counts = np.zeros(8, dtype=np.int64)
        settled_counts_by_cell = np.zeros(n, dtype=np.int64)
        total, last = 0, -math.inf
        with h5py.File(run / "native_output/spikes.h5", "r") as handle, (view / "spikes.csv").open("x", newline="", encoding="utf-8") as stream:
            group = handle["spikes/cortex"]
            gids, times = group["node_ids"], group["timestamps"]
            if gids.ndim != 1 or times.ndim != 1 or gids.shape != times.shape or gids.dtype.kind not in "iu" or times.dtype.kind not in "fiu" or len(gids) > MAX_EVENTS:
                raise ValueError("Invalid cortical spike arrays")
            writer = csv.writer(stream); writer.writerow(("time_ms", "gid", "population"))
            for start in range(0, len(gids), MAX_CHUNK):
                gi, ti = gids[start:start+MAX_CHUNK], times[start:start+MAX_CHUNK]
                if not np.all(np.isfinite(ti)) or np.any(ti < 0) or np.any(ti > 60100) or np.any(gi < 0) or np.any(gi >= n) or np.any(np.diff(ti) < 0) or (len(ti) and ti[0] < last):
                    raise ValueError("Invalid, out-of-range or unsorted cortical spike data")
                pi = np.searchsorted(population_ends, gi, side="right")
                selected = (ti >= 100) & (ti < 60100)
                settled_counts += np.bincount(pi[selected], minlength=8)
                sampled_settled = selected & sampled_mask[gi]
                sampled_settled_counts += np.bincount(pi[sampled_settled], minlength=8)
                settled_counts_by_cell += np.bincount(gi[selected].astype(np.int64), minlength=n)
                writer.writerows((repr(float(t)), int(g), POPULATIONS[int(pop)]) for t,g,pop in zip(ti,gi,pi))
                total += len(gi)
                if len(ti): last = float(ti[-1])
        if audit.get("cortical_spikes") != total:
            raise ValueError("Saved spike census differs from prior technical audit")
        sampled_silent_counts = [int(np.count_nonzero(settled_counts_by_cell[sampled_ids[pop]] == 0)) for pop in POPULATIONS]
        rows = _paper_table(analysis, sampled_counts, sampled_settled_counts,
                            scalable=scalable, silent_counts=sampled_silent_counts)
        with (view / "paper_metrics.csv").open("x", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
        # Close a source-changing race before exposing a complete summary.
        for path,digest in sources.items():
            if _hash(path) != digest: raise ValueError("Original evidence changed during conversion")
        evidence = {"source_run":str(run), "source_analysis":str(analysis), "files_sha256":sources,
                    "provenance":provenance, "prior_technical_audit":audit,
                    "paper_sample_binding":{"sampling_manifest_sha256":_hash(analysis / "sampling_manifest.json"),
                        "sampled_cells":dict(zip(POPULATIONS, map(int, sampled_counts))),
                        "sampled_settled_spikes":dict(zip(POPULATIONS, map(int, sampled_settled_counts))),
                        "whole_settled_spikes":dict(zip(POPULATIONS, map(int, settled_counts)))},
                    "interpretation":"Load-integrity checks only; prior technical review retained, scientific acceptance not granted."}
        (view / "source_index.json").write_text(json.dumps(evidence, indent=2)+"\n", encoding="utf-8")
        artifacts = [{"kind":"table","path":path,"label":label} for path,label in (("spikes.csv","Cortical spikes — all events"),("cells.csv","Cortical population IDs"),("paper_metrics.csv","Paper-window population metrics"))]
        artifacts.append({"kind":"json","path":"source_index.json","label":"Original evidence and technical provenance"})
        payload = {"schema_version":1,"workflow":WORKFLOW,"engine":"BioNet","status":"complete",
                   "completed_at":datetime.now(timezone.utc).isoformat(),"title":name or f"PDCM BioNet · {m['drive']} · own network {m['network_seed']}",
                   "technical_acceptance":True,"scientific_acceptance":False,"artifacts":artifacts,
                   "metadata":{"Engine":"BioNet","duration_ms":60100,"scale":m["scale"],"drive":m["drive"],"network_seed":m["network_seed"],"stimulus_seed":m["stimulus_seed"],
                               "Technical status":"Prior saved technical acceptance; view integrity checked","Scientific status":"Not established — one own-network result, shared NEURON",
                               "source_run":str(run),"source_analysis":str(analysis),"settled_window_ms":[100,60100],"spikes":total,"settled_spikes":int(settled_counts.sum()),
                               "paper_sampled_cells":int(sampled_counts.sum()),"paper_sampled_settled_spikes":int(sampled_settled_counts.sum()),
                               "Paper metric scope":PAPER_SAMPLE_SCOPE, "paper_population_table":rows}}
        payload["view_files_sha256"] = {a["path"]:_hash(view / a["path"]) for a in artifacts}
        summary = view / "summary.json"
        summary.write_text(json.dumps(payload, indent=2)+"\n", encoding="utf-8")
        return summary
    except Exception as exc:
        (view / "IMPORT_FAILED.json").write_text(json.dumps({"error":str(exc),"original_run_untouched":True})+"\n", encoding="utf-8")
        raise


def create_bionet_results_view(run_root, output_root, name=None, *, analysis_root=None, python_executable=None):
    """Return a new summary Path; external conversion runs no scientific code.

    Call from an app worker thread. Pass the selected BioNet Python in packaged
    builds; bundle this file as workers/pdcm_campaign_results.py for that route.
    Without it, HDF5 imports remain lazy and occur only during conversion.
    """
    run = Path(run_root).expanduser().resolve(strict=True)
    output = Path(output_root).expanduser().resolve()
    analysis = Path(analysis_root).expanduser().resolve(strict=True) if analysis_root else _analysis_for(run)
    if python_executable is None:
        return _convert(run, output, name, analysis)
    from .paths import worker_path
    from .process_environment import external_runtime_launcher, external_runtime_path, sanitized_external_environment
    worker = worker_path("pdcm_campaign_results.py")
    if not worker.is_file(): worker = Path(__file__)
    if not worker.is_file(): raise ValueError("Packaged saved-results converter is missing")
    invocation = str(external_runtime_launcher(python_executable))
    command = [invocation,"-B",str(worker),"--run",str(run),"--output-root",str(output),"--analysis",str(analysis)]
    if name is not None: command += ["--name",name]
    environment = sanitized_external_environment({"PATH":external_runtime_path(python_executable)})
    try:
        process = subprocess.run(command, text=True, capture_output=True, check=False, env=environment, timeout=CONVERSION_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Saved-results conversion exceeded its120s read-only conversion bound; no simulation was run") from exc
    if process.returncode: raise ValueError("BioNet saved-results conversion failed: " + process.stderr[-2000:])
    result = _json_text(process.stdout)
    summary = Path(result["summary_path"]).resolve(strict=True)
    if not summary.is_relative_to(output): raise ValueError("Converter returned an outside cache path")
    load_pdcm_campaign_result(summary)
    return summary


def _json_text(text):
    value = json.loads(text)
    if not isinstance(value,dict): raise ValueError("Invalid converter response")
    return value


def load_pdcm_campaign_result(path):
    """Load the sidecar into ResultRecord without importing HDF5 or simulators."""
    from .models import Artifact, CheckState, PreflightCheck, ResultRecord
    summary = Path(path).expanduser().resolve(strict=True)
    root = summary.parent
    payload = _json(summary)
    if payload.get("workflow") != WORKFLOW or payload.get("schema_version") != 1 or payload.get("scientific_acceptance") is not False or payload.get("technical_acceptance") is not True:
        raise ValueError("Unsupported saved BioNet Results view")
    inventory = payload.get("view_files_sha256")
    if not isinstance(inventory,dict): raise ValueError("View artifact inventory is absent")
    artifacts = []
    for item in payload.get("artifacts",[]):
        path = _bound(root,item["path"],inventory)
        artifacts.append(Artifact(item["kind"],str(path),item["label"],True))
    if set(inventory) != {a["path"] for a in payload["artifacts"]} or not {"spikes.csv","cells.csv","paper_metrics.csv","source_index.json"}.issubset(inventory):
        raise ValueError("Incomplete view artifacts")
    index = _json(root / "source_index.json")
    for path,digest in index["files_sha256"].items():
        if _hash(Path(path)) != digest: raise ValueError("Referenced original evidence changed")
    metadata = payload.get("metadata")
    if not isinstance(metadata,dict) or metadata.get("Engine") != "BioNet" or payload.get("engine") != "BioNet" or payload.get("status") != "complete":
        raise ValueError("Results view identity differs")
    for key in ("network_seed","stimulus_seed","duration_ms"):
        if metadata.get(key) != index["provenance"].get(key):
            raise ValueError("Displayed condition differs from source provenance")
    input_manifest = _json(Path(index["source_run"]) / "input/manifest.json")
    for key in ("network_seed","stimulus_seed","duration_ms","scale","drive"):
        if metadata.get(key) != input_manifest.get(key):
            raise ValueError("Displayed condition differs from original input")
    if metadata.get("source_run") != index.get("source_run") or metadata.get("source_analysis") != index.get("source_analysis"):
        raise ValueError("Displayed evidence route differs")
    # Older accepted all-cell views have no sample binding. New views bind
    # both scopes explicitly so their displayed census cannot be relabelled.
    if input_manifest.get("scale") == .05 and "paper_sample_binding" not in index:
        raise ValueError("5% Results require their distinct sampled and whole-population census binding")
    if "paper_sample_binding" in index:
        sample = index["paper_sample_binding"]
        if not isinstance(sample, dict):
            raise ValueError("Results sample binding is invalid")
        for key in ("sampled_cells", "sampled_settled_spikes", "whole_settled_spikes"):
            if not isinstance(sample.get(key), dict) or set(sample[key]) != set(POPULATIONS):
                raise ValueError("Results sample population census differs")
            for value in sample[key].values():
                _integer(value, "Results sample census")
        expected = dict(zip(POPULATIONS, (min(1000, count) for count in input_manifest["scaled_counts"])))
        sample_path = str(Path(index["source_analysis"]) / "sampling_manifest.json")
        if (sample["sampled_cells"] != expected
                or sample.get("sampling_manifest_sha256") != index["files_sha256"].get(sample_path)
                or any(sample["sampled_settled_spikes"][pop] > sample["whole_settled_spikes"][pop] for pop in POPULATIONS)
                or metadata.get("paper_sampled_cells") != sum(sample["sampled_cells"].values())
                or metadata.get("paper_sampled_settled_spikes") != sum(sample["sampled_settled_spikes"].values())
                or metadata.get("settled_spikes") != sum(sample["whole_settled_spikes"].values())
                or metadata.get("spikes") != index["prior_technical_audit"].get("cortical_spikes")
                or metadata.get("Paper metric scope") != PAPER_SAMPLE_SCOPE):
            raise ValueError("Displayed sampled and whole-population census differs")
    with (root / "paper_metrics.csv").open(newline="",encoding="utf-8") as stream:
        if metadata.get("paper_population_table") != list(csv.DictReader(stream)):
            raise ValueError("Displayed paper metrics differ from saved table")
    checks = (PreflightCheck("saved_view_integrity","Saved output integrity",CheckState.PASS,"Selected original evidence and view artifact hashes match. Opening this view did not rerun the simulation."),
              PreflightCheck("replication_limits","Scientific replication",CheckState.WARNING,"Prior technical acceptance only; independent-network cohort and paper claims remain separate."))
    return ResultRecord(str(summary),payload["status"],payload["completed_at"],payload["title"],payload["metadata"],tuple(artifacts),checks)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description="Convert saved BioNet evidence only; no simulations")
    parser.add_argument("--run",required=True);parser.add_argument("--output-root",required=True);parser.add_argument("--analysis",required=True);parser.add_argument("--name")
    args=parser.parse_args()
    result=_convert(Path(args.run).resolve(),Path(args.output_root).resolve(),args.name,Path(args.analysis).resolve())
    print(json.dumps({"summary_path":str(result)}))
