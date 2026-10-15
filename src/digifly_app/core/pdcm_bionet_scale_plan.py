"""Read-only, allocation-free PDCM scale planning; not execution qualification.

These constants/formulas are the separately authored primary-v3 construction.
No author builder, generated graph, simulator or native mechanism is imported.
Larger scales require a separate scalable workflow; this module cannot open it.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Mapping

GiB = 1024**3
POPS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
FULL = (20683, 5834, 21915, 5479, 4850, 1065, 14395, 2948)
INPUTS = (1600, 1500, 2100, 1900, 2000, 1900, 2900, 2100)
P = ((.1009,.1689,.0437,.0818,.0323,0,.0076,0),
     (.1346,.1371,.0316,.0515,.0755,0,.0042,0),
     (.0077,.0059,.0497,.135,.0067,.0003,.0453,0),
     (.0691,.0029,.0794,.1597,.0033,0,.1057,0),
     (.1004,.0622,.0505,.0057,.0831,.3726,.0204,0),
     (.0548,.0269,.0257,.0022,.06,.3158,.0086,0),
     (.0156,.0066,.0211,.0166,.0572,.0197,.0396,.2252),
     (.0364,.001,.0034,.0005,.0277,.008,.0658,.1443))
BUILDER_SHA256 = "d634ac906a95ee33074f01b9976e10603832bf4a186c40c768288c35239bda73"


def _positive(value, label: str, *, integer: bool = False) -> float:
    if (type(value) not in ((int,) if integer else (int,float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{label} must be a positive finite {'integer' if integer else 'number'}")
    return value


def validate_planning_condition(spec: Mapping) -> None:
    scale = _positive(spec.get("scale"),"scale")
    duration = _positive(spec.get("duration_ms"),"duration_ms")
    # Only the existing baseline and human-directed first paper extensions.
    valid = ((scale in (.01,.02,.05) and duration == 60100.
              and spec.get("drive") == "balanced_poisson")
             or (scale == .01 and duration == 60100. and spec.get("drive") == "balanced_dc")
             or (scale == .1 and duration == 4000. and spec.get("drive") == "balanced_poisson"))
    if not valid:
        raise ValueError("Planning is limited to 1% full BP/DC, 2%/5% full BP, or the dedicated 10%/4s BP Figure9 condition; it does not authorize execution")


def structure_forecast(spec: Mapping) -> dict:
    """Exact deterministic floor/round/cap counts, not a sampled network."""
    validate_planning_condition(spec)
    scale,duration = float(spec["scale"]),float(spec["duration_ms"])
    counts = [math.floor(scale*count) for count in FULL]
    rules = []
    for target in range(8):
        row = []
        for source in range(8):
            contacts = math.log1p(-P[target][source])/math.log1p(-1./(FULL[target]*FULL[source])) if P[target][source] else 0.
            divergence = max(0,min(round(scale*contacts/FULL[source]),counts[target]-1))
            row.append(divergence*counts[source])
        rules.append(row)
    external_mean = sum(count*inputs*scale*8.*duration/1000.
                        for count,inputs in zip(counts,INPUTS)) if spec["drive"] == "balanced_poisson" else 0.
    clock_ticks = round(duration/.025)
    return {"schema":"digifly-pdcm-bionet-structure-forecast-v1",
        "scale":scale,"duration_ms":duration,"drive":spec["drive"],
        "population_order":list(POPS),"full_counts":list(FULL),"scaled_counts":counts,
        "cortical_nodes":sum(counts),"recurrent_contacts":sum(map(sum,rules)),
        "recurrent_rule_contacts_target_row_source_column":rules,
        "external_source_nodes":sum(counts) if external_mean else 0,
        "external_event_count_mean":external_mean,
        "shared_clock_source_nodes":1,"shared_clock_source_events":clock_ticks,
        "clock_contacts":sum(counts),"delivered_clock_event_count":sum(counts)*clock_ticks,
        "builder_source_sha256":BUILDER_SHA256,
        "forecast_only":True,"scientific_acceptance":False,
        "count_note":"Deterministic source-defined graph counts; Poisson count is an expectation, never an exact realization or hard upper bound",
        "scaling_note":"Cells and source-stream count ~s; recurrent contacts ~s² after truncation/rounding/caps; per-source Poisson rate ~s, so aggregate external events ~s²×duration; shared clock-file events ~duration, delivered clock updates ~N×duration"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_accepted_app_reference(run_root: Path, review_path: Path) -> dict:
    """Read five known metadata files only; do not re-audit accepted raw/runtime data."""
    root = Path(run_root)
    paths = {"review":Path(review_path),"execution":root/"execution.json", "request":root/"request.json",
             "artifacts":root/"simulation/artifact_manifest.json","structure":root/"simulation/input/manifest.json"}
    values = {key:json.loads(path.read_text()) for key,path in paths.items()}
    review,execution,request,artifacts,structure = (values[key] for key in ("review","execution","request","artifacts","structure"))
    spec = request["spec"]
    if (review.get("technical_review_passed") is not True or review.get("scientific_acceptance") is not False
            or request.get("workflow") != "pdcm_bionet_primary_v3_app"
            or execution.get("workflow") != request["workflow"]
            or execution.get("exit_code") != 0 or execution.get("failure") is not None
            or execution.get("scientific_acceptance") is not False
            or spec.get("scale") != .01 or spec.get("duration_ms") != 60100.
            or spec.get("drive") != "balanced_poisson"
            or review.get("root") != str(root.absolute())):
        raise ValueError("Resource reference must be a technically reviewed, unchanged full 1% Poisson app observation")
    for key in ("scale","duration_ms","drive","network_seed","stimulus_seed"):
        if review.get(key) != spec[key] or structure.get(key) != spec[key]:
            raise ValueError("Resource reference condition identities differ")
    if (review.get("walltime_s") != execution.get("walltime_s")
            or review.get("sampled_process_tree_peak_rss_bytes") != execution.get("sampled_process_tree_peak_rss_bytes")
            or review.get("runtime_versions",{}).get("bmtk") != "1.2.0"
            or review.get("runtime_versions",{}).get("neuron") != "9.0.1"):
        raise ValueError("Resource reference measured/runtime evidence differs from its review")
    if (review["evidence_sha256"]["execution.json"] != _sha(paths["execution"])
            or review["evidence_sha256"]["request.json"] != _sha(paths["request"])
            or review["evidence_sha256"]["simulation/artifact_manifest.json"] != _sha(paths["artifacts"])
            or artifacts["files"]["input/manifest.json"] != _sha(paths["structure"])
            or review.get("source_sha256") != request.get("source_sha256")
            or request["source_sha256"].get("scripts/build_pdcm_bionet_primary_v3.py") != BUILDER_SHA256):
        raise ValueError("Resource reference metadata/source binding differs")
    reference = {"schema":"digifly-pdcm-bionet-measured-resource-reference-v1",
        "spec":dict(spec),"structure":structure,
        "walltime_s":execution["walltime_s"],
        "sampled_process_tree_peak_rss_bytes":execution["sampled_process_tree_peak_rss_bytes"],
        "retained_bytes":review["retained_bytes"],
        "runtime_versions":review["runtime_versions"],
        "observed_execution_bounds":execution["bounds"],
        "source_sha256":request["source_sha256"],
        "metadata_sha256":{key:{"path":str(path),"sha256":_sha(path)} for key,path in paths.items()},
        "measurement_note":"One source-bound seed/runtime observation; 0.25s sampled process-tree RSS is not a kernel high-water mark or universal scale law. No accepted raw/compiled/runtime files were re-audited."}
    validate_measured_reference(reference)
    return reference


def validate_measured_reference(reference: Mapping) -> None:
    spec,structure = reference["spec"],reference["structure"]
    expected = structure_forecast(spec)
    if (spec["scale"] != .01 or spec["duration_ms"] != 60100. or spec["drive"] != "balanced_poisson"
            or structure.get("construction_policy") != "released_method_fixed_unique_integer_variance_v3"
            or reference["source_sha256"].get("scripts/build_pdcm_bionet_primary_v3.py") != BUILDER_SHA256
            or structure.get("scaled_counts") != expected["scaled_counts"]
            or structure.get("recurrent_contacts") != expected["recurrent_contacts"]
            or structure.get("clock_events") != expected["shared_clock_source_events"]):
        raise ValueError("Measured reference is not the source-defined full 1% primary method")
    for key in ("walltime_s","sampled_process_tree_peak_rss_bytes","retained_bytes"):
        _positive(reference[key],key,integer=key != "walltime_s")
    bounds = reference.get("observed_execution_bounds")
    if bounds is not None:
        for field,measurement in (("timeout_seconds","walltime_s"),("rss_bytes","sampled_process_tree_peak_rss_bytes"),
                                  ("output_bytes","retained_bytes")):
            _positive(bounds[field],field,integer=True)
            if reference[measurement] > bounds[field]:
                raise ValueError("Measured resource reference exceeds its declared execution bound")
    _positive(structure["external_events"],"external_events",integer=True)
    files = structure["files"]
    for value in files.values():
        _positive(value["bytes"],"declared file bytes",integer=True)
    if not {"clock_spikes.h5","external_spikes.h5","network/cortex_cortex_edges.h5"}.issubset(files):
        raise ValueError("Measured source storage components are missing")
    if reference["retained_bytes"] < 2*sum(value["bytes"] for value in files.values()):
        raise ValueError("Measured retained storage cannot fit the declared source and immutable input copies")


def scale_resource_plan(spec: Mapping, reference: Mapping) -> dict:
    """Component-separated planning scenarios; NEVER a larger-scale admission."""
    target = structure_forecast(spec)
    if spec["drive"] != "balanced_poisson":
        raise ValueError("This measured Poisson reference cannot forecast the legacy DC path's additional retained Poisson-reference construction; provide a separately qualified DC planner")
    validate_measured_reference(reference)
    baseline = structure_forecast(reference["spec"])
    ratios = {"cells":target["cortical_nodes"]/baseline["cortical_nodes"],
        "recurrent_graph":target["recurrent_contacts"]/baseline["recurrent_contacts"],
        "external_events_mean":target["external_event_count_mean"]/baseline["external_event_count_mean"],
        "shared_clock_file":target["shared_clock_source_events"]/baseline["shared_clock_source_events"],
        "delivered_clock_updates":target["delivered_clock_event_count"]/baseline["delivered_clock_event_count"]}
    components = {}
    for name,value in reference["structure"]["files"].items():
        ratio = ratios["external_events_mean"] if name == "external_spikes.h5" else (
            ratios["shared_clock_file"] if name == "clock_spikes.h5" else (
            ratios["recurrent_graph"] if name == "network/cortex_cortex_edges.h5" else (
            ratios["cells"] if name.startswith("network/") and name.endswith(".h5") else 1.)))
        components[name] = math.ceil(value["bytes"]*ratio)
    structure_bytes = sum(components.values())
    # The runner retains an immutable input copy, not just the source structure.
    residual = max(0,reference["retained_bytes"]-2*sum(v["bytes"] for v in reference["structure"]["files"].values()))
    retained = 2*structure_bytes+math.ceil(residual*max(1.,ratios["cells"],ratios["recurrent_graph"]))
    rss = reference["sampled_process_tree_peak_rss_bytes"]
    work_ratios = {"delivered_clock_updates":ratios["delivered_clock_updates"],
        "external_events_mean":ratios["external_events_mean"],
        "graph_event_proxy":ratios["recurrent_graph"]*ratios["shared_clock_file"]}
    return {"schema":"digifly-pdcm-bionet-scale-resource-plan-v1","condition":dict(spec),
        "structure_forecast":target,"component_ratios":ratios,
        "source_structure_storage_projection_bytes":structure_bytes,
        "source_storage_components_projection_bytes":components,
        "retained_source_and_immutable_input_copy_projection_bytes":retained,
        "rss_scenarios_bytes":{key:math.ceil(rss*ratios[key]) for key in ("cells","recurrent_graph","external_events_mean","shared_clock_file")},
        "walltime_scenarios_seconds":{key:reference["walltime_s"]*ratio for key,ratio in work_ratios.items()},
        "reference_measurement":{key:reference[key] for key in ("walltime_s","sampled_process_tree_peak_rss_bytes","retained_bytes")},
        "reference_metadata_sha256":reference.get("metadata_sha256",{}),
        "larger_scale_qualified":False,"execution_admitted":False,"scientific_acceptance":False,
        "limits":"Storage is an expectation-based component projection, not a Poisson bound; RSS/walltime scenarios scale an undecomposed single observation, not additive estimates, guarantees, or reasons to raise a watchdog. Different phases/runtime behavior and event/spike outcomes can dominate. Figure9 4s cannot export 60s paper metrics.",
        "required_next_source":"Separate scalable primary-v4 builder/runner/analyzer and source/resource admission; preserve primary-v3 and accepted runs. No new pilot follows from this planner."}


def check_live_resources(resources: Mapping, bounds: Mapping) -> dict:
    """Actual fail-closed admission for the existing, unchanged v3 envelope."""
    for key in ("available_memory_bytes","free_disk_bytes"):
        if type(resources.get(key)) is not int or resources[key] < 0:
            raise ValueError(f"Measured {key} must be a nonnegative integer byte count")
    for field in ("memory_reserve_bytes","disk_reserve_bytes"):
        _positive(bounds[field],field,integer=True)
    if resources["available_memory_bytes"] < bounds["memory_reserve_bytes"]:
        raise ValueError("This full-duration app run requires at least 8 GiB of available memory")
    if resources["free_disk_bytes"] < bounds["disk_reserve_bytes"]:
        raise ValueError("This full-duration app run requires at least 10 GiB of free disk")
    return {"schema":"digifly-pdcm-bionet-live-resource-admission-v1",
        "measured_resources":dict(resources),"bounds":dict(bounds),"available_resource_gate_passed":True,
        "scope":"Existing fixed primary-v3 execution only; this live snapshot does not qualify a new scale or guarantee future memory/disk availability"}
