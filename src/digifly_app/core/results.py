from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .models import Artifact, CheckState, PreflightCheck, ResultRecord, deep_find_values


EXPECTED_CONTACT_POLICY = "deduplicated_visible_contact_sites_post_xyz"
EXPECTED_ORDERING = "saved_gf_camera_vertical_then_tree_distance"


def load_escape_siz_result(path: str | Path) -> ResultRecord:
    summary_path = Path(path).expanduser().resolve()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Escape-SIZ summary must contain a JSON object.")
    artifacts = _collect_artifacts(payload)
    checks = _contract_checks(payload, artifacts)
    metadata = {
        "gap-junction model": payload.get("gj_model", "unknown"),
        "contact-site Na": payload.get("contact_site_sodium_multiplier", "unknown"),
        "contact policy": payload.get("contact_count_policy", "missing"),
        "separate GFs": payload.get("separate_gfs", "unknown"),
        "GFC2 pairwise ohmic": payload.get("gfc2_ohmic", "unknown"),
        "cache": payload.get("cache_session_root", "unknown"),
        "stimulus targets": _stimulus_summary(payload),
        "heatmap targets": _heatmap_targets(payload),
    }
    return ResultRecord(
        summary_path=str(summary_path),
        status=str(payload.get("status") or "unknown"),
        completed_at=str(payload.get("completed_at") or "unknown"),
        title="Escape-SIZ · GFC contact-site sodium comparison",
        metadata=metadata,
        artifacts=tuple(artifacts),
        checks=tuple(checks),
    )


def _collect_artifacts(payload: Mapping[str, Any]) -> list[Artifact]:
    candidates: list[tuple[str, str, str]] = []
    plots = payload.get("plots")
    if isinstance(plots, Mapping):
        for key, value in plots.items():
            if isinstance(value, str):
                kind = _kind_for_path(value)
                if kind:
                    candidates.append((kind, value, _label_for_key(str(key))))
            elif key == "stim_target_heatmaps" and isinstance(value, Mapping):
                for target, target_plots in value.items():
                    if isinstance(target_plots, Mapping):
                        for nested_key, nested_value in target_plots.items():
                            if isinstance(nested_value, str) and _kind_for_path(nested_value):
                                candidates.append(
                                    (_kind_for_path(nested_value) or "file", nested_value, f"Target {target} · {nested_key}")
                                )
    for key in ("summary_json", "edge_path", "chemical_edges_path", "case_json"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            candidates.append((_kind_for_path(value) or "file", value, _label_for_key(key)))
    seen: set[str] = set()
    artifacts: list[Artifact] = []
    for kind, raw_path, label in candidates:
        resolved = str(Path(raw_path).expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        artifacts.append(
            Artifact(kind=kind, path=resolved, label=label, exists=Path(resolved).exists())
        )
    return artifacts


def _contract_checks(payload: Mapping[str, Any], artifacts: list[Artifact]) -> list[PreflightCheck]:
    checks: list[PreflightCheck] = []
    policy = payload.get("contact_count_policy")
    checks.append(
        PreflightCheck(
            key="result_contact_policy",
            title="Contact-count policy",
            state=CheckState.PASS if policy == EXPECTED_CONTACT_POLICY else CheckState.FAIL,
            detail=(
                f"Recorded {policy}."
                if policy == EXPECTED_CONTACT_POLICY
                else f"Expected {EXPECTED_CONTACT_POLICY}; found {policy!r}."
            ),
            blocking=True,
        )
    )
    counts = payload.get("visible_contact_counts")
    checks.append(
        PreflightCheck(
            key="result_contact_counts",
            title="Per-pair visible contact counts",
            state=CheckState.PASS if isinstance(counts, list) and bool(counts) else CheckState.FAIL,
            detail=(
                f"Summary contains {len(counts)} pair-count rows."
                if isinstance(counts, list) and counts
                else "No visible-contact count rows were recorded."
            ),
            blocking=True,
        )
    )
    ordering_values = [str(item) for item in deep_find_values(payload, "ordering_basis")]
    ordering_ok = EXPECTED_ORDERING in ordering_values
    checks.append(
        PreflightCheck(
            key="result_ordering",
            title="Saved-view anatomy ordering",
            state=CheckState.PASS if ordering_ok else CheckState.WARNING,
            detail=(
                f"Verified {EXPECTED_ORDERING}."
                if ordering_ok
                else "The top-level summary does not expose the canonical ordering basis; inspect the compartment summary before interpretation."
            ),
            blocking=False,
        )
    )
    image = next((artifact for artifact in artifacts if artifact.kind == "image"), None)
    pdf = next((artifact for artifact in artifacts if artifact.kind == "pdf"), None)
    plots_ok = bool(image and image.exists and pdf and pdf.exists)
    checks.append(
        PreflightCheck(
            key="result_plots",
            title="Canonical PNG and PDF",
            state=CheckState.PASS if plots_ok else CheckState.WARNING,
            detail=(
                "Both PNG and PDF plot artifacts exist."
                if plots_ok
                else "A PNG or PDF plot artifact is missing."
            ),
            blocking=False,
        )
    )
    targets = _heatmap_target_values(payload)
    canonical = len(targets) == 2 and set(targets) == {10000, 10002}
    checks.append(
        PreflightCheck(
            key="result_heatmap_targets",
            title="Heatmap target semantics",
            state=CheckState.PASS if canonical else CheckState.WARNING,
            detail=(
                "The primary panels are the canonical GF10002/GF10000 comparison."
                if canonical
                else f"Primary heatmap targets are {targets or 'not recorded'}, not both GFs. Treat this as an arbitrary-target diagnostic even if a legacy filename says 'bothGFs'."
            ),
            blocking=False,
        )
    )
    return checks


def _stimulus_summary(payload: Mapping[str, Any]) -> str:
    maps = payload.get("stim_target_maps")
    if not isinstance(maps, Mapping):
        return "not recorded"
    parts = []
    for condition in ("gap_enabled", "gap_disabled"):
        mapping = maps.get(condition)
        if isinstance(mapping, Mapping):
            parts.append(f"{condition}: {len(mapping)} targets")
    return ", ".join(parts) if parts else "not recorded"


def _heatmap_target_values(payload: Mapping[str, Any]) -> list[int]:
    plots = payload.get("plots")
    raw = plots.get("heatmap_neuron_ids") if isinstance(plots, Mapping) else None
    if not isinstance(raw, list):
        return []
    values: list[int] = []
    for item in raw:
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return values


def _heatmap_targets(payload: Mapping[str, Any]) -> str:
    values = _heatmap_target_values(payload)
    return ", ".join(str(value) for value in values) if values else "not recorded"


def _kind_for_path(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".pdf": "pdf",
        ".json": "json",
        ".csv": "table",
        ".npz": "array",
        ".h5": "sonata",
        ".hdf5": "sonata",
        ".swc": "morphology",
    }.get(suffix)


def _label_for_key(key: str) -> str:
    return key.replace("_", " ").strip().title()
