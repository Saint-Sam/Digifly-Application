from __future__ import annotations

from dataclasses import asdict, dataclass, field
import csv
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Mapping

from digifly_app.core.models import (
    CheckState,
    ExecutionPlan,
    PreflightCheck,
    PreflightReport,
    ResultRecord,
)
from digifly_app.core.resources import capture_resources
from digifly_app.core.results import load_escape_siz_result
from digifly_app.core.workspace import DigiflyWorkspace
from .base import EngineAdapter


CONTACT_COUNT_POLICY = "deduplicated_visible_contact_sites_post_xyz"
GFC2_IDS = (
    13127,
    13479,
    13645,
    13846,
    14527,
    14662,
    15292,
    15505,
    15938,
    16764,
    17245,
)
CANONICAL_VISIBLE_COUNTS = {
    (10000, 10110): 146,
    (10000, 11446): 16,
    (10000, 11654): 47,
    (10002, 10068): 135,
    (10002, 10110): 4,
    (10002, 11446): 65,
    (10002, 11654): 28,
}


def latest_gfc2_stimulus() -> dict[str, dict[str, float]]:
    mapping = {str(gid): 0.9 for gid in GFC2_IDS}
    return {"gap_enabled": dict(mapping), "gap_disabled": dict(mapping)}


@dataclass
class EscapeSizConfig:
    """Versioned controls for the first Escape-SIZ recipe."""

    recipe_version: int = 1
    preset: str = "latest_gfc2_pairwise"
    python_executable: str = "/opt/anaconda3/bin/python"
    contact_site_na_multiplier: float = 2.5
    gj_model: str = "heterotypic_rectifying"
    hetero_g_closed_frac: float = 0.0
    hetero_vhalf_mV: float = 0.0
    hetero_vslope_mV: float = 5.0
    hetero_empirical_residual_frac: float = 0.20
    hetero_tau_open_ms: float = 6.0
    hetero_tau_close_ms: float = 2.0
    separate_gfs: bool = True
    gfc2_ohmic: bool = True
    frequency_hz: float = 100.0
    max_pulses: int = 10
    gap_enabled_amp_nA: float = 1.0
    gap_disabled_amp_nA: float = 0.46142578125
    stimulus_by_condition: dict[str, dict[str, float]] = field(default_factory=latest_gfc2_stimulus)
    nproc: int = 1
    force_restart_cache: bool = False
    vmin_mV: float = -80.0
    vmax_mV: float = 40.0
    include_extra_target_heatmaps: bool = False

    BUILD_TIME_FIELDS = (
        "gj_model",
        "hetero_g_closed_frac",
        "hetero_vhalf_mV",
        "hetero_vslope_mV",
        "hetero_empirical_residual_frac",
        "hetero_tau_open_ms",
        "hetero_tau_close_ms",
        "separate_gfs",
        "gfc2_ohmic",
    )
    RUNTIME_SAFE_FIELDS = (
        "contact_site_na_multiplier",
        "frequency_hz",
        "max_pulses",
        "gap_enabled_amp_nA",
        "gap_disabled_amp_nA",
        "stimulus_by_condition",
        "vmin_mV",
        "vmax_mV",
        "include_extra_target_heatmaps",
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EscapeSizConfig":
        fields = cls.__dataclass_fields__
        values = {key: value for key, value in payload.items() if key in fields}
        return cls(**values)

    @classmethod
    def canonical_dual_gf(cls) -> "EscapeSizConfig":
        return cls(
            preset="canonical_dual_gf",
            gfc2_ohmic=False,
            gap_enabled_amp_nA=1.58935546875,
            gap_disabled_amp_nA=0.46142578125,
            stimulus_by_condition={},
        )

    def errors(self) -> list[str]:
        errors: list[str] = []
        if self.recipe_version != 1:
            errors.append(f"Unsupported recipe version {self.recipe_version}.")
        if self.gj_model not in {"ohmic", "heterotypic_rectifying"}:
            errors.append("Gap-junction model must be ohmic or heterotypic_rectifying.")
        if self.contact_site_na_multiplier <= 0:
            errors.append("Contact-site sodium multiplier must be positive.")
        if self.frequency_hz <= 0:
            errors.append("Stimulus frequency must be positive.")
        if self.max_pulses < 1:
            errors.append("Maximum pulses must be at least one.")
        if not 1 <= self.nproc <= 64:
            errors.append("Worker count must be between 1 and 64.")
        if not 0 <= self.hetero_g_closed_frac <= 1:
            errors.append("Closed conductance fraction must be in [0, 1].")
        if not 0 <= self.hetero_empirical_residual_frac <= 1:
            errors.append("Empirical residual fraction must be in [0, 1].")
        if self.hetero_vslope_mV <= 0:
            errors.append("Heterotypic slope must be positive.")
        if self.hetero_tau_open_ms <= 0 or self.hetero_tau_close_ms <= 0:
            errors.append("Heterotypic time constants must be positive.")
        if self.vmin_mV >= self.vmax_mV:
            errors.append("Heatmap minimum must be below its maximum.")
        for condition, mapping in self.stimulus_by_condition.items():
            if condition not in {"gap_enabled", "gap_disabled"}:
                errors.append(f"Unknown stimulus condition {condition!r}.")
            for gid, amplitude in mapping.items():
                try:
                    int(gid)
                    numeric = float(amplitude)
                except (TypeError, ValueError):
                    errors.append(f"Invalid stimulus target {gid!r}: {amplitude!r}.")
                    continue
                if numeric < 0:
                    errors.append(f"Stimulus amplitude for {gid} cannot be negative.")
        return errors


class NeuronEscapeSizAdapter(EngineAdapter[EscapeSizConfig]):
    key = "neuron_escape_siz"
    display_name = "NEURON · Escape-SIZ"

    RUNNER_NAME = "run_baseline_with_10002_gfcs_contact_site_na_heatmaps.py"
    EDGE_NAME = "10000_10002_10068_10110_11446_11654_DLMs_GFCs_gap_edges_unique_contact_sites.csv"

    @property
    def runner_path(self) -> Path:
        return self.workspace.gfc_root / self.RUNNER_NAME

    @property
    def edge_path(self) -> Path:
        return self.workspace.gfc_root / "gap_edge_cache" / self.EDGE_NAME

    @property
    def worker_path(self) -> Path:
        return Path(__file__).resolve().parents[1] / "workers" / "escape_siz_worker.py"

    @property
    def base_case_path(self) -> Path:
        return (
            self.workspace.gfc_root
            / "phase2_build_cache_sessions"
            / "10000_10002_10068_10110_11446_11654_DLMs_GFCs"
            / "gap_enabled"
            / "case.json"
        )

    @property
    def camera_preset_path(self) -> Path:
        return (
            self.workspace.root.parent
            / "Digifly_NEW"
            / "VIP_Glia_Sim"
            / "notebooks"
            / "debug"
            / "outputs"
            / "morphology_mutation_camera_glia_selector_10000_10002_to_10068_10110_plotshape_v1_baseline.json"
        )

    def workflow_paths(
        self,
        config: EscapeSizConfig,
        *,
        output_root: str | Path | None = None,
    ) -> dict[str, Path]:
        topology_suffix = "_no_direct_gf_edges" if config.separate_gfs else ""
        gfc2_suffix = "_gfc2_ohmic" if config.gfc2_ohmic else ""
        if config.gj_model == "ohmic":
            base_name = "10000_10002_10068_10110_11446_11654_DLMs_GFCs"
            session_name = f"{base_name}{topology_suffix}{gfc2_suffix}"
            stem = f"baseline_with_10002_gfcs_contact_site_na_3p625{topology_suffix}{gfc2_suffix}"
        else:
            mechanism = (
                "_paperfit_"
                f"resid{_token(config.hetero_empirical_residual_frac)}"
                f"_tauopen{_token(config.hetero_tau_open_ms)}"
                f"_tauclose{_token(config.hetero_tau_close_ms)}"
                f"_closed{_token(config.hetero_g_closed_frac)}"
                f"_vhalf{_token(config.hetero_vhalf_mV)}"
                f"_vslope{_token(config.hetero_vslope_mV)}"
            )
            session_name = (
                "10000_10002_10068_10110_11446_11654_DLMs_GFCs_heterotypic_shakb"
                f"{mechanism}{topology_suffix}{gfc2_suffix}"
            )
            stem = (
                "baseline_with_10002_gfcs_contact_site_na_heterotypic_shakb"
                f"{mechanism}{topology_suffix}{gfc2_suffix}"
            )
        execution_root = (
            self.workspace.gfc_root
            if output_root is None
            else Path(output_root).expanduser().resolve() / "escape_siz"
        )
        session_root = execution_root / "phase2_build_cache_sessions" / session_name / "gap_enabled"
        run_root = execution_root / "runs" / stem
        return {
            "session_root": session_root,
            "status_path": session_root / "status.json",
            "run_root": run_root,
            "summary_path": run_root / f"{stem}_summary.json",
        }

    def validate(
        self,
        config: EscapeSizConfig,
        *,
        output_root: str | Path,
        allow_new_cache_build: bool,
        legacy_write_acknowledged: bool,
    ) -> PreflightReport:
        checks = list(self.workspace.base_preflight().checks)
        for message in config.errors():
            checks.append(
                PreflightCheck(
                    key="configuration",
                    title="Experiment configuration",
                    state=CheckState.FAIL,
                    detail=message,
                    blocking=True,
                )
            )
        if not config.errors():
            checks.append(
                PreflightCheck(
                    key="configuration",
                    title="Experiment configuration",
                    state=CheckState.PASS,
                    detail="All recipe values are within their supported ranges.",
                )
            )

        checks.extend(
            (
                _file_check("runner", "Escape-SIZ runner", self.runner_path, blocking=True),
                _file_check("app_worker", "App-owned Escape-SIZ worker", self.worker_path, blocking=True),
                _file_check("contact_edges", "Deduplicated contact edges", self.edge_path, blocking=True),
                _file_check(
                    "phase2_package",
                    "Phase 2 simulation package",
                    self.workspace.phase2_neuron / "digifly" / "phase2" / "__init__.py",
                    blocking=True,
                ),
            )
        )
        checks.append(self._runtime_check(config))
        checks.append(self._contact_count_check())
        checks.append(self._morphology_source_check(allow_new_cache_build=allow_new_cache_build))
        checks.append(self._camera_preset_check())

        paths = self.workflow_paths(config, output_root=output_root)
        checks.append(
            self._cache_check(
                paths["status_path"],
                requested_nproc=config.nproc,
                allow_new_cache_build=allow_new_cache_build,
            )
        )

        resource = capture_resources(output_root)
        disk_state = CheckState.PASS if resource.disk_free_gb >= 20 else CheckState.WARNING
        checks.append(
            PreflightCheck(
                key="disk",
                title="Output storage",
                state=disk_state,
                detail=(
                    f"{resource.disk_free_gb:.1f} GB free ({resource.disk_used_percent:.1f}% used). "
                    "The current legacy runner/plotter still emits and consumes full records.csv files."
                ),
                blocking=False,
                path=str(Path(output_root).expanduser()),
            )
        )
        checks.append(
            PreflightCheck(
                key="recording_estimate",
                title="Recording-size estimate",
                state=CheckState.WARNING,
                detail=(
                    "The validated 49-cell GFC2 recipe records 10,026 compartments. Its latest enabled/disabled "
                    "records.csv files were about 1.9 GB each and each condition took roughly 330 seconds. "
                    "Compact NPZ cannot become the default until the canonical plotters consume it."
                ),
                blocking=False,
            )
        )
        checks.append(
            PreflightCheck(
                key="workers",
                title="Resource-aware workers",
                state=CheckState.WARNING if config.nproc > resource.neuron_worker_default else CheckState.PASS,
                detail=(
                    f"Requested {config.nproc}; workload-specific safe default is at most "
                    f"{resource.neuron_worker_default}. {resource.note}"
                ),
                blocking=False,
            )
        )
        checks.append(
            PreflightCheck(
                key="output_boundary",
                title="App-owned output boundary",
                state=CheckState.PASS,
                detail=(
                    f"Caches, requests, simulations, status files, and plots resolve beneath "
                    f"{Path(output_root).expanduser().resolve() / 'escape_siz'}. Digifly Public is input-only."
                ),
                blocking=False,
                path=str(Path(output_root).expanduser()),
            )
        )
        return PreflightReport(tuple(checks))

    def plan(self, config: EscapeSizConfig, *, output_root: str | Path) -> ExecutionPlan:
        args: list[str] = [
            "-B",
            str(self.worker_path),
            "--digifly-public-root",
            str(self.workspace.root),
            "--output-root",
            str(Path(output_root).expanduser().resolve()),
            "--contact-site-na-multiplier",
            _number(config.contact_site_na_multiplier),
            "--gj-model",
            config.gj_model,
            "--hetero-g-closed-frac",
            _number(config.hetero_g_closed_frac),
            "--hetero-vhalf-mV",
            _number(config.hetero_vhalf_mV),
            "--hetero-vslope-mV",
            _number(config.hetero_vslope_mV),
            "--hetero-empirical-residual-frac",
            _number(config.hetero_empirical_residual_frac),
            "--hetero-tau-open-ms",
            _number(config.hetero_tau_open_ms),
            "--hetero-tau-close-ms",
            _number(config.hetero_tau_close_ms),
            "--freq-hz",
            _number(config.frequency_hz),
            "--max-pulses",
            str(config.max_pulses),
            "--gap-enabled-amp-nA",
            _number(config.gap_enabled_amp_nA),
            "--gap-disabled-amp-nA",
            _number(config.gap_disabled_amp_nA),
            "--nproc",
            str(config.nproc),
            "--vmin",
            _number(config.vmin_mV),
            "--vmax",
            _number(config.vmax_mV),
        ]
        if config.separate_gfs:
            args.append("--separate-gfs")
        if config.gfc2_ohmic:
            args.append("--gfc2-ohmic")
        if config.force_restart_cache:
            args.append("--force-restart-cache")
        if config.stimulus_by_condition:
            args.extend(
                (
                    "--stim-target-amps-json",
                    json.dumps(config.stimulus_by_condition, separators=(",", ":"), sort_keys=True),
                )
            )
        if not config.include_extra_target_heatmaps:
            args.append("--no-extra-stim-target-heatmaps")

        output = Path(output_root).expanduser().resolve()
        app_src = Path(__file__).resolve().parents[2]
        python_paths = [str(app_src), str(self.workspace.phase2_neuron), str(self.workspace.gfc_root)]
        # The validated Escape-SIZ environment uses NEURON 8.2.6 from the
        # application bundle. /opt/anaconda3 also contains NEURON 9, so the
        # module path must be explicit and its effective identity is probed.
        neuron_bundle_python = Path("/Applications/NEURON/lib/python")
        if neuron_bundle_python.is_dir():
            python_paths.append(str(neuron_bundle_python))
        inherited = os.environ.get("PYTHONPATH", "")
        if inherited:
            python_paths.extend(path for path in inherited.split(os.pathsep) if path)
        env = {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join(dict.fromkeys(python_paths)),
            "MPLCONFIGDIR": str(output / "_runtime" / "matplotlib"),
            "NEURON_MODULE_OPTIONS": "-nogui",
            "PYTHONNOUSERSITE": "1",
        }
        expected = self.workflow_paths(config, output_root=output_root)["summary_path"]
        working_directory = Path(output_root).expanduser().resolve()
        return ExecutionPlan(
            engine="neuron",
            workflow="escape_siz_gfc_contact_na_v1",
            program=str(Path(config.python_executable).expanduser()),
            arguments=tuple(args),
            working_directory=str(working_directory),
            environment=env,
            output_behavior="app_owned",
            expected_summary_path=str(expected),
            build_time_fields=EscapeSizConfig.BUILD_TIME_FIELDS,
            runtime_safe_fields=EscapeSizConfig.RUNTIME_SAFE_FIELDS,
        )

    def latest_result(self, config: EscapeSizConfig | None = None) -> ResultRecord | None:
        if config is not None:
            summary = self.workflow_paths(config)["summary_path"]
            if summary.is_file():
                return load_escape_siz_result(summary)
        summaries = list(self.workspace.gfc_root.glob("runs/*/*_summary.json"))
        if not summaries:
            return None
        latest = max(summaries, key=lambda path: path.stat().st_mtime)
        return load_escape_siz_result(latest)

    def _runtime_check(self, config: EscapeSizConfig) -> PreflightCheck:
        executable = Path(config.python_executable).expanduser()
        if not executable.is_file():
            return PreflightCheck(
                key="neuron_runtime",
                title="NEURON worker runtime",
                state=CheckState.FAIL,
                detail=f"Python executable does not exist: {executable}",
                blocking=True,
                path=str(executable),
            )
        plan = self.plan(config, output_root=self.workspace.root.parent / "Digifly App" / "workspace")
        env = os.environ.copy()
        env.update(plan.environment)
        code = (
            "import json, neuron; "
            "print(json.dumps({'version': getattr(neuron, '__version__', 'unknown'), "
            "'path': getattr(neuron, '__file__', 'unknown')}))"
        )
        try:
            completed = subprocess.run(
                [str(executable), "-c", code],
                cwd=str(self.workspace.gfc_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PreflightCheck(
                key="neuron_runtime",
                title="NEURON worker runtime",
                state=CheckState.FAIL,
                detail=f"Runtime probe failed: {exc}",
                blocking=True,
            )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return PreflightCheck(
                key="neuron_runtime",
                title="NEURON worker runtime",
                state=CheckState.FAIL,
                detail=detail or "The configured interpreter could not import NEURON.",
                blocking=True,
            )
        try:
            identity = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            identity = {"version": "unknown", "path": completed.stdout.strip()}
        return PreflightCheck(
            key="neuron_runtime",
            title="NEURON worker runtime",
            state=CheckState.PASS,
            detail=f"NEURON {identity.get('version')} from {identity.get('path')}",
            path=str(executable),
        )

    def _contact_count_check(self) -> PreflightCheck:
        if not self.edge_path.is_file():
            return PreflightCheck(
                key="contact_policy",
                title="Visible-contact policy",
                state=CheckState.FAIL,
                detail=f"Contact table is missing: {self.edge_path}",
                blocking=True,
            )
        counts: dict[tuple[int, int], int] = {}
        try:
            with self.edge_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                if not {"pre_id", "post_id"}.issubset(reader.fieldnames or []):
                    raise ValueError("pre_id/post_id columns are absent")
                for row in reader:
                    pair = (int(float(row["pre_id"])), int(float(row["post_id"])))
                    if pair in CANONICAL_VISIBLE_COUNTS:
                        counts[pair] = counts.get(pair, 0) + 1
        except (OSError, ValueError, TypeError) as exc:
            return PreflightCheck(
                key="contact_policy",
                title="Visible-contact policy",
                state=CheckState.FAIL,
                detail=f"Could not validate contact table: {exc}",
                blocking=True,
            )
        mismatches = {
            pair: (counts.get(pair, 0), expected)
            for pair, expected in CANONICAL_VISIBLE_COUNTS.items()
            if counts.get(pair, 0) != expected
        }
        if mismatches:
            detail = "; ".join(
                f"{pre}->{post}: found {actual}, expected {expected}"
                for (pre, post), (actual, expected) in sorted(mismatches.items())
            )
            return PreflightCheck(
                key="contact_policy",
                title="Visible-contact policy",
                state=CheckState.FAIL,
                detail=f"Canonical deduplicated counts do not match. {detail}",
                blocking=True,
            )
        return PreflightCheck(
            key="contact_policy",
            title="Visible-contact policy",
            state=CheckState.PASS,
            detail=(
                f"Verified {CONTACT_COUNT_POLICY} and all seven canonical GF→PSI/TTMn pair counts."
            ),
            path=str(self.edge_path),
        )

    def _morphology_source_check(self, *, allow_new_cache_build: bool) -> PreflightCheck:
        if not self.base_case_path.is_file():
            return PreflightCheck(
                key="morphology_source",
                title="49-cell morphology source",
                state=CheckState.FAIL if allow_new_cache_build else CheckState.WARNING,
                detail=f"Base cache case is missing: {self.base_case_path}",
                blocking=allow_new_cache_build,
                path=str(self.base_case_path),
            )
        try:
            case = json.loads(self.base_case_path.read_text(encoding="utf-8"))
            swc_root = Path(str(case.get("swc_dir") or "")).expanduser()
            morphology_overlay = Path(str(case.get("morph_swc_dir") or "")).expanduser()
            selection = (case.get("selection") or {}).get("neuron_ids") or []
        except (OSError, json.JSONDecodeError, TypeError, AttributeError) as exc:
            return PreflightCheck(
                key="morphology_source",
                title="49-cell morphology source",
                state=CheckState.FAIL,
                detail=f"Could not inspect the base cache case: {exc}",
                blocking=True,
                path=str(self.base_case_path),
            )
        missing = [path for path in (swc_root, morphology_overlay) if not path.is_dir()]
        if missing:
            return PreflightCheck(
                key="morphology_source",
                title="49-cell morphology source",
                state=CheckState.FAIL if allow_new_cache_build else CheckState.WARNING,
                detail=(
                    f"The {len(selection)}-cell recipe references missing morphology roots: "
                    + ", ".join(str(path) for path in missing)
                    + (". A rebuild cannot proceed." if allow_new_cache_build else ". A live ready cache may still be reusable.")
                ),
                blocking=allow_new_cache_build,
            )
        external = not _is_relative_to(swc_root.resolve(), self.workspace.root)
        return PreflightCheck(
            key="morphology_source",
            title="49-cell morphology source",
            state=CheckState.WARNING if external else CheckState.PASS,
            detail=(
                f"Resolved {len(selection)} selected cells. SWC root: {swc_root}. Overlay: {morphology_overlay}. "
                + (
                    "The complete GFC morphology pack is external to Digifly Public and must remain an explicit machine binding."
                    if external
                    else "Morphology assets are contained in the configured workspace."
                )
            ),
            blocking=False,
            path=str(swc_root),
        )

    def _camera_preset_check(self) -> PreflightCheck:
        exists = self.camera_preset_path.is_file()
        return PreflightCheck(
            key="camera_preset",
            title="Saved-view anatomy camera",
            state=CheckState.WARNING,
            detail=(
                f"External camera preset is available at {self.camera_preset_path}; it must be promoted to a versioned app asset."
                if exists
                else "The saved VIP_Glia camera preset is missing. Canonical heatmaps must refuse raw-node-order fallback."
            ),
            blocking=False,
            path=str(self.camera_preset_path),
        )

    def _cache_check(
        self,
        status_path: Path,
        *,
        requested_nproc: int,
        allow_new_cache_build: bool,
    ) -> PreflightCheck:
        if not status_path.is_file():
            return PreflightCheck(
                key="cache",
                title="Compatible NEURON cache",
                state=CheckState.WARNING if allow_new_cache_build else CheckState.FAIL,
                detail=(
                    f"No compatible cache status exists at {status_path}. A new build is permitted."
                    if allow_new_cache_build
                    else f"No compatible ready cache exists at {status_path}; cache building is locked."
                ),
                blocking=not allow_new_cache_build,
                path=str(status_path),
            )
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return PreflightCheck(
                key="cache",
                title="Compatible NEURON cache",
                state=CheckState.FAIL,
                detail=f"Cache status is unreadable: {exc}",
                blocking=True,
                path=str(status_path),
            )
        state = str(payload.get("state") or payload.get("status") or "unknown")
        cache_nproc = payload.get("nproc")
        pid = payload.get("pid")
        alive = _pid_alive(pid)
        if state != "ready":
            return PreflightCheck(
                key="cache",
                title="Compatible NEURON cache",
                state=CheckState.WARNING if allow_new_cache_build else CheckState.FAIL,
                detail=f"Cache state is {state!r}; building/recovery is {'permitted' if allow_new_cache_build else 'locked'}.",
                blocking=not allow_new_cache_build,
                path=str(status_path),
            )
        notes = [f"ready", f"nproc={cache_nproc}", f"pid={pid} ({'alive' if alive else 'not active'})"]
        check_state = CheckState.PASS
        if cache_nproc is not None and int(cache_nproc) != int(requested_nproc):
            notes.append(f"requested nproc={requested_nproc}; the cache service may restart")
            check_state = CheckState.WARNING
        elif not alive:
            notes.append("the cached build can be reused but its service may need a restart")
            check_state = CheckState.WARNING
        return PreflightCheck(
            key="cache",
            title="Compatible NEURON cache",
            state=check_state,
            detail=", ".join(notes),
            blocking=False,
            path=str(status_path),
        )


def _file_check(key: str, title: str, path: Path, *, blocking: bool) -> PreflightCheck:
    exists = path.is_file()
    return PreflightCheck(
        key=key,
        title=title,
        state=CheckState.PASS if exists else CheckState.FAIL,
        detail=f"Found {path}" if exists else f"Missing required file: {path}",
        blocking=blocking,
        path=str(path),
    )


def _token(value: float) -> str:
    return f"{float(value):g}".replace("-", "m").replace(".", "p")


def _number(value: float) -> str:
    return f"{float(value):.12g}"


def _pid_alive(value: Any) -> bool:
    try:
        pid = int(value)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
