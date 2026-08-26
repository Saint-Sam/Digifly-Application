from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from typing import Any, Mapping

from digifly_app.core.models import (
    Artifact,
    CheckState,
    ExecutionPlan,
    PreflightCheck,
    PreflightReport,
    ResultRecord,
)
from digifly_app.core.process_environment import (
    external_runtime_path,
    sanitized_external_environment,
)
from digifly_app.core.workspace import DigiflyWorkspace
from digifly_app.core.paths import RESOURCE_ROOT_ENV, resource_path, resource_root, worker_path
from .base import EngineAdapter


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
EXPECTED_SELECTED_IDS = (
    10000,
    10002,
    10014,
    10068,
    10074,
    10088,
    10110,
    10228,
    10361,
    10589,
    10592,
    10892,
    11446,
    11654,
    12191,
    13127,
    13479,
    13645,
    13658,
    13846,
    14527,
    14662,
    15292,
    15505,
    15938,
    16764,
    17245,
    17383,
    17458,
    18309,
    21601,
    23606,
    24198,
    24412,
    24436,
    25080,
    25215,
    25629,
    26376,
    27502,
    31823,
    40029,
    41708,
    42164,
    43758,
    101102,
    101549,
    163891,
    169914,
)

EXPECTED_ARBOR_VERSION = "0.12.2"
CURRENT_CV_POLICY = "every_segment"
EXPECTED_CHEMICAL_ROWS = 2430
EXPECTED_DIRECT_GF_ROWS = 99
EXPECTED_FILTERED_CHEMICAL_ROWS = 2331
EXPECTED_FILTERED_CHEMICAL_PAIRS = 69
EXPECTED_CHEMICAL_SHA256 = "90016e471b1608d2516ecbe84df6a78e4463766fe2125dddeb6a86970e0924f7"
EXPECTED_GAP_ROWS = 959
EXPECTED_GAP_PAIRS = 58
EXPECTED_GAP_SOURCE_SHA256 = "df64a7822294119c6e58b385c34f6b59354805b7ad8bf5f6054b29bd8c74235f"
EXPECTED_GAP_ARBOR_SHA256 = "bc116eaf2d55250838e93d7e1a88eacce660274df5de7cf90af6849a4d48fc56"
EXPECTED_CONTACT_NODE_COUNTS = {
    10000: (115, 112),
    10002: (144, 138),
}
GAP_CATALOGUE_NAME = "digifly_gap"
GAP_CATALOGUE_FILE = "digifly_gap-catalogue.so"
GAP_CATALOGUE_CACHE_VERSION = "v1"
EXPECTED_GAP_MECHANISMS = {
    "gap": {"g"},
    "rect_gap": {"gmax"},
    "hetero_rect_gap": {
        "gmax_open",
        "gmax_closed",
        "orientation",
        "vhalf",
        "vslope",
        "empirical_residual_frac",
        "tau_open_ms",
        "tau_close_ms",
    },
}


@dataclass
class ArborAblationComparisonConfig:
    """Locked Arbor rendering of the active Escape-SIZ Ablation notebook.

    The app-owned Arbor catalogue preserves the notebook's HeteroRectGap
    equations and parameters. Cross-backend equivalence remains an empirical
    result rather than an assumption because Arbor uses ``cnexp`` where the
    NEURON mechanism requests ``derivimplicit``.
    """

    recipe_version: int = 1
    preset: str = "ablation_notebook_arbor_comparison"
    python_executable: str = "/opt/anaconda3/bin/python"
    contact_site_na_multiplier: float = 2.5
    # Retained as a locked compatibility/provenance field for old saved app
    # projects. It is not used as HeteroRectGap's closed conductance; the exact
    # port uses requested_hetero_g_closed_frac=0 and the separate residual floor.
    static_reverse_fraction: float = 0.20
    requested_hetero_g_closed_frac: float = 0.0
    requested_hetero_vhalf_mV: float = 0.0
    requested_hetero_vslope_mV: float = 5.0
    requested_empirical_residual_frac: float = 0.20
    requested_tau_open_ms: float = 6.0
    requested_tau_close_ms: float = 2.0
    frequency_hz: float = 100.0
    max_pulses: int = 10
    stimulus_amp_nA: float = 0.9
    stimulus_duration_ms: float = 0.4
    dt_ms: float = 0.01
    sample_dt_ms: float = 0.05
    threads: int = 4
    cv_policy: str = CURRENT_CV_POLICY
    cv_max_extent_um: float = 20.0
    vmin_mV: float = -80.0
    vmax_mV: float = 40.0
    make_plots: bool = True

    BUILD_TIME_FIELDS = (
        "contact_site_na_multiplier",
        "static_reverse_fraction",
        "requested_hetero_g_closed_frac",
        "requested_hetero_vhalf_mV",
        "requested_hetero_vslope_mV",
        "requested_empirical_residual_frac",
        "requested_tau_open_ms",
        "requested_tau_close_ms",
        "cv_policy",
        "cv_max_extent_um",
    )
    RUNTIME_SAFE_FIELDS = (
        "frequency_hz",
        "max_pulses",
        "stimulus_amp_nA",
        "stimulus_duration_ms",
        "dt_ms",
        "sample_dt_ms",
        "threads",
        "vmin_mV",
        "vmax_mV",
        "make_plots",
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ArborAblationComparisonConfig":
        fields = cls.__dataclass_fields__
        return cls(**{key: value for key, value in payload.items() if key in fields})

    def errors(self) -> list[str]:
        errors: list[str] = []
        if self.recipe_version != 1:
            errors.append(f"Unsupported Arbor comparison recipe version {self.recipe_version}.")
        if self.preset != "ablation_notebook_arbor_comparison":
            errors.append("This adapter only accepts the Ablation-notebook Arbor comparison preset.")
        exact_values = (
            ("contact-site Na multiplier", self.contact_site_na_multiplier, 2.5),
            ("static reverse gap fraction", self.static_reverse_fraction, 0.20),
            ("requested closed fraction", self.requested_hetero_g_closed_frac, 0.0),
            ("requested vhalf", self.requested_hetero_vhalf_mV, 0.0),
            ("requested vslope", self.requested_hetero_vslope_mV, 5.0),
            ("requested empirical residual", self.requested_empirical_residual_frac, 0.20),
            ("requested tau-open", self.requested_tau_open_ms, 6.0),
            ("requested tau-close", self.requested_tau_close_ms, 2.0),
            ("frequency", self.frequency_hz, 100.0),
            ("stimulus amplitude", self.stimulus_amp_nA, 0.9),
            ("stimulus duration", self.stimulus_duration_ms, 0.4),
            ("integration dt", self.dt_ms, 0.01),
            ("sampling dt", self.sample_dt_ms, 0.05),
        )
        for label, actual, expected in exact_values:
            if abs(float(actual) - float(expected)) > 1e-12:
                errors.append(
                    f"The active-notebook comparison pins {label} to {expected:g}; found {actual:g}."
                )
        if self.max_pulses != 10:
            errors.append("The active-notebook comparison pins the pulse count to 10.")
        if self.cv_policy != CURRENT_CV_POLICY:
            errors.append(
                "The production Arbor comparison uses the validated every-segment CV policy."
            )
        if not 1 <= int(self.threads) <= 64:
            errors.append("Arbor thread count must be between 1 and 64.")
        if float(self.cv_max_extent_um) <= 0:
            errors.append("CV maximum extent must be positive.")
        if float(self.vmin_mV) >= float(self.vmax_mV):
            errors.append("Plot minimum must be below its maximum.")
        return errors


class ArborEscapeSizAdapter(EngineAdapter[ArborAblationComparisonConfig]):
    key = "arbor_escape_siz"
    display_name = "Arbor · Escape-SIZ Ablation comparison"

    HELPER_NAME = "giant_fiber_ablation_arbor.py"
    EQUIVALENCE_AUDITOR_NAME = "giant_fiber_neuron_arbor_equivalence.py"

    @property
    def project_root(self) -> Path:
        return (
            self.workspace.phase2_arbor
            / "Projects"
            / "Escape-SIZ"
            / "Giant Fiber Ablation Comparisons"
        )

    @property
    def helper_path(self) -> Path:
        return self.project_root / self.HELPER_NAME

    @property
    def equivalence_auditor_path(self) -> Path:
        return self.project_root / self.EQUIVALENCE_AUDITOR_NAME

    @property
    def input_root(self) -> Path:
        return (
            self.workspace.phase2_arbor
            / "Projects"
            / "Escape-SIZ"
            / "arbor_inputs"
            / "giant_fiber_ablation"
        )

    @property
    def manifest_path(self) -> Path:
        return self.input_root / "manifest.json"

    @property
    def chemical_edges_path(self) -> Path:
        return self.input_root / "chemical_edges.csv"

    @property
    def gap_contacts_path(self) -> Path:
        return self.input_root / "gap_contacts.csv"

    @property
    def gap_contacts_arbor_path(self) -> Path:
        return self.input_root / "gap_contacts_arbor.csv"

    @property
    def contact_site_nodes_path(self) -> Path:
        return self.input_root / "contact_site_nodes.json"

    @property
    def worker_path(self) -> Path:
        return worker_path("arbor_escape_siz_worker.py")

    @property
    def gap_bridge_path(self) -> Path:
        return worker_path("arbor_gap_bridge.py")

    @property
    def mechanism_source_root(self) -> Path:
        return resource_path("mechanisms", "arbor_gap_junctions")

    def gap_catalogue_path(self, output_root: str | Path) -> Path:
        override = os.environ.get("DIGIFLY_ARBOR_GAP_CATALOGUE", "").strip()
        if override:
            return Path(override).expanduser().resolve()
        runtime = Path(output_root).expanduser().resolve() / "_runtime" / "arbor_catalogues"
        cache_key = (
            f"arbor-{EXPECTED_ARBOR_VERSION}-{platform.system().lower()}-"
            f"{platform.machine().lower()}-digifly-gap-{GAP_CATALOGUE_CACHE_VERSION}"
        )
        return runtime / cache_key / GAP_CATALOGUE_FILE

    def workflow_paths(
        self,
        config: ArborAblationComparisonConfig | None = None,
        *,
        output_root: str | Path,
    ) -> dict[str, Path]:
        root = (
            Path(output_root).expanduser().resolve()
            / "escape_siz"
            / "arbor"
            / "ablation_notebook_exact_gap_comparison"
        )
        return {
            "comparison_root": root,
            "summary_path": root / "comparison_summary.json",
            "filtered_edges_path": root / "_inputs" / "chemical_edges_no_direct_gf.csv",
            "gap_enabled_run": root / "gap_enabled",
            "gap_disabled_run": root / "gap_disabled",
            "plans_root": root / "_plans",
            "provenance_path": root / "worker_provenance.json",
        }

    def validate(
        self,
        config: ArborAblationComparisonConfig,
        *,
        output_root: str | Path,
        allow_new_cache_build: bool,
        legacy_write_acknowledged: bool,
    ) -> PreflightReport:
        del allow_new_cache_build, legacy_write_acknowledged
        checks = list(self.workspace.base_preflight().checks)
        output = Path(output_root).expanduser().resolve()
        try:
            output.relative_to(self.workspace.root)
        except ValueError:
            output_inside_public = False
        else:
            output_inside_public = True
        errors = config.errors()
        checks.extend(
            PreflightCheck(
                key="arbor_configuration",
                title="Arbor Ablation comparison configuration",
                state=CheckState.FAIL,
                detail=message,
                blocking=True,
            )
            for message in errors
        )
        if not errors:
            checks.append(
                PreflightCheck(
                    key="arbor_configuration",
                    title="Arbor Ablation comparison configuration",
                    state=CheckState.PASS,
                    detail=(
                        "Pinned to the active notebook: 49 cells, 11 GFC2 stimuli at 0.9 nA, "
                        "100 Hz × 10 pulses, 2.5× GF contact-site Na, and direct GF chemical edges removed."
                    ),
                )
            )
        checks.extend(
            (
                _file_check("arbor_helper", "Validated Arbor Escape-SIZ helper", self.helper_path),
                _file_check("arbor_worker", "App-owned Arbor worker", self.worker_path),
                _file_check("arbor_gap_bridge", "App-owned Arbor junction bridge", self.gap_bridge_path),
                _file_check(
                    "arbor_gap_sources",
                    "App-owned Arbor gap mechanism sources",
                    self.mechanism_source_root / "source_manifest.json",
                ),
                _file_check("arbor_manifest", "Self-contained Arbor input manifest", self.manifest_path),
                _file_check(
                    "arbor_equivalence_auditor",
                    "NEURON–Arbor equivalence auditor",
                    self.equivalence_auditor_path,
                ),
                self._input_contract_check(),
                self._runtime_check(config),
                self._gap_catalogue_check(config, output_root=output),
                self._existing_result_policy_check(config, output_root=output),
                PreflightCheck(
                    key="arbor_gap_capability",
                    title="Gap-junction equation port",
                    state=CheckState.PASS,
                    detail=(
                        "The app-owned hetero_rect_gap junction represents the notebook's closed=0, residual=0.20, "
                        "vhalf=0 mV, vslope=5 mV, tau-open=6 ms, tau-close=2 ms, and +1/-1 endpoint orientation. "
                        "Arbor's cnexp state update differs from NEURON derivimplicit, so trace equivalence is audited."
                    ),
                    blocking=True,
                ),
                PreflightCheck(
                    key="arbor_cv_policy_safety",
                    title="Arbor CV policy safety",
                    state=CheckState.PASS,
                    detail=(
                        "Production runs use Arbor's validated every_segment policy. The explicit "
                        "legacy-section compatibility prototype is quarantined to bounded, "
                        "single-thread diagnostics after a four-thread native Arbor run exited "
                        "with SIGBUS; it is not installed by this adapter."
                    ),
                    blocking=True,
                ),
                PreflightCheck(
                    key="arbor_cache_policy",
                    title="Arbor cache boundary",
                    state=CheckState.PASS,
                    detail=(
                        "Arbor consumes the staged 49-SWC bundle directly. Only the ABI-specific app-owned "
                        "mechanism catalogue is reused from the output runtime cache."
                    ),
                ),
                PreflightCheck(
                    key="arbor_output_boundary",
                    title="App-owned output boundary",
                    state=CheckState.FAIL if output_inside_public else CheckState.PASS,
                    detail=(
                        f"Output root {output} is inside Digifly Public; choose an app-owned directory."
                        if output_inside_public
                        else (
                            f"Filtered edges, plans, simulations, metrics, and plots resolve beneath "
                            f"{self.workflow_paths(config, output_root=output)['comparison_root']}. "
                            "Digifly Public remains input-only."
                        )
                    ),
                    blocking=True,
                    path=str(output),
                ),
            )
        )
        return PreflightReport(tuple(checks))

    def plan(
        self,
        config: ArborAblationComparisonConfig,
        *,
        output_root: str | Path,
    ) -> ExecutionPlan:
        output = Path(output_root).expanduser().resolve()
        args = [
            "-B",
            str(self.worker_path),
            "--digifly-public-root",
            str(self.workspace.root),
            "--output-root",
            str(output),
            "--gap-catalogue",
            str(self.gap_catalogue_path(output)),
            "--contact-site-na-multiplier",
            _number(config.contact_site_na_multiplier),
            "--static-reverse-fraction",
            _number(config.static_reverse_fraction),
            "--requested-hetero-g-closed-frac",
            _number(config.requested_hetero_g_closed_frac),
            "--requested-vhalf-mV",
            _number(config.requested_hetero_vhalf_mV),
            "--requested-vslope-mV",
            _number(config.requested_hetero_vslope_mV),
            "--requested-empirical-residual-frac",
            _number(config.requested_empirical_residual_frac),
            "--requested-tau-open-ms",
            _number(config.requested_tau_open_ms),
            "--requested-tau-close-ms",
            _number(config.requested_tau_close_ms),
            "--freq-hz",
            _number(config.frequency_hz),
            "--max-pulses",
            str(config.max_pulses),
            "--stim-amp-nA",
            _number(config.stimulus_amp_nA),
            "--stim-dur-ms",
            _number(config.stimulus_duration_ms),
            "--dt-ms",
            _number(config.dt_ms),
            "--sample-dt-ms",
            _number(config.sample_dt_ms),
            "--threads",
            str(config.threads),
            "--cv-policy",
            config.cv_policy,
            "--cv-max-extent-um",
            _number(config.cv_max_extent_um),
            "--vmin",
            _number(config.vmin_mV),
            "--vmax",
            _number(config.vmax_mV),
        ]
        if not config.make_plots:
            args.append("--no-plots")
        python_paths = [str(self.workspace.phase2_arbor), str(self.project_root)]
        comparison_root = self.workflow_paths(config, output_root=output)["comparison_root"]
        env = {
            "PATH": external_runtime_path(config.python_executable),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": os.pathsep.join(dict.fromkeys(python_paths)),
            "MPLCONFIGDIR": str(output / "_runtime" / "arbor_matplotlib"),
            "DIGIFLY_PHASE2_ARBOR_OUTPUT_ROOT": str(output / "escape_siz" / "arbor"),
            "DIGIFLY_GIANT_FIBER_ARBOR_OUTPUT_ROOT": str(comparison_root),
            RESOURCE_ROOT_ENV: str(resource_root()),
        }
        return ExecutionPlan(
            engine="arbor",
            workflow="escape_siz_ablation_notebook_arbor_exact_gap_v2",
            program=str(Path(config.python_executable).expanduser()),
            arguments=tuple(args),
            working_directory=str(output),
            environment=env,
            output_behavior="app_owned",
            expected_summary_path=str(
                self.workflow_paths(config, output_root=output)["summary_path"]
            ),
            build_time_fields=ArborAblationComparisonConfig.BUILD_TIME_FIELDS,
            runtime_safe_fields=ArborAblationComparisonConfig.RUNTIME_SAFE_FIELDS,
        )

    def latest_result(
        self,
        config: ArborAblationComparisonConfig | None = None,
        *,
        output_root: str | Path | None = None,
    ) -> ResultRecord | None:
        if output_root is None:
            return None
        summary = self.workflow_paths(config, output_root=output_root)["summary_path"]
        if not summary.is_file():
            return None
        return load_arbor_ablation_result(summary)

    def _existing_result_policy_check(
        self,
        config: ArborAblationComparisonConfig,
        *,
        output_root: str | Path,
    ) -> PreflightCheck:
        summary = self.workflow_paths(config, output_root=output_root)["summary_path"]
        if not summary.is_file():
            return PreflightCheck(
                key="arbor_existing_result_cv_policy",
                title="Existing Arbor result policy",
                state=CheckState.INFO,
                detail=f"No existing comparison summary; the next run will use {CURRENT_CV_POLICY}.",
                path=str(summary),
            )
        try:
            payload = json.loads(summary.read_text(encoding="utf-8"))
            artifact_policy, source = _artifact_cv_policy(summary, payload)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return PreflightCheck(
                key="arbor_existing_result_cv_policy",
                title="Existing Arbor result policy",
                state=CheckState.WARNING,
                detail=f"Could not identify the CV policy of the existing summary: {exc}",
                path=str(summary),
            )
        matches = artifact_policy == CURRENT_CV_POLICY
        return PreflightCheck(
            key="arbor_existing_result_cv_policy",
            title="Existing Arbor result policy",
            state=CheckState.PASS if matches else CheckState.WARNING,
            detail=(
                f"Existing artifacts use {artifact_policy} ({source}), matching the current policy."
                if matches
                else (
                    f"Existing artifacts use {artifact_policy} ({source}); the current app uses "
                    f"{CURRENT_CV_POLICY}. Other-policy traces and audits are retained for "
                    "provenance but are not evidence for the production policy."
                )
            ),
            path=str(summary),
        )

    def _runtime_check(self, config: ArborAblationComparisonConfig) -> PreflightCheck:
        executable = Path(config.python_executable).expanduser()
        if not executable.is_file():
            return PreflightCheck(
                key="arbor_runtime",
                title="Arbor worker stack",
                state=CheckState.FAIL,
                detail=f"Python executable does not exist: {executable}",
                blocking=True,
                path=str(executable),
            )
        code = (
            "import json, arbor, numpy, pandas; "
            "import digifly.phase2.api; "
            "cfg=arbor.config(); "
            "print(json.dumps({'version': getattr(arbor, '__version__', cfg.get('version')), "
            "'path': getattr(arbor, '__file__', 'unknown'), 'numpy': numpy.__version__, "
            "'pandas': pandas.__version__, 'mpi': cfg.get('mpi'), 'gpu': cfg.get('gpu'), "
            "'vectorize': cfg.get('vectorize')}))"
        )
        try:
            with tempfile.TemporaryDirectory(prefix="digifly_arbor_probe_") as temporary:
                root = Path(temporary)
                environment = sanitized_external_environment(
                    {
                        "PATH": external_runtime_path(config.python_executable),
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONNOUSERSITE": "1",
                        "PYTHONPATH": os.pathsep.join((str(self.workspace.phase2_arbor), str(self.project_root))),
                        "MPLCONFIGDIR": str(root / "matplotlib"),
                    }
                )
                (root / "matplotlib").mkdir(parents=True, exist_ok=True)
                completed = subprocess.run(
                    [str(executable), "-B", "-c", code],
                    cwd=str(root),
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PreflightCheck(
                key="arbor_runtime",
                title="Arbor worker stack",
                state=CheckState.FAIL,
                detail=f"Runtime probe failed: {exc}",
                blocking=True,
            )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return PreflightCheck(
                key="arbor_runtime",
                title="Arbor worker stack",
                state=CheckState.FAIL,
                detail=detail[-4000:] or "The configured interpreter could not import Arbor.",
                blocking=True,
                path=str(executable),
            )
        try:
            identity = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            identity = {"version": "unknown", "path": completed.stdout.strip()}
        if str(identity.get("version")) != EXPECTED_ARBOR_VERSION:
            return PreflightCheck(
                key="arbor_runtime",
                title="Arbor worker stack",
                state=CheckState.FAIL,
                detail=(
                    f"The validated comparison uses Arbor {EXPECTED_ARBOR_VERSION}; found "
                    f"{identity.get('version')} at {identity.get('path')}."
                ),
                blocking=True,
                path=str(executable),
            )
        return PreflightCheck(
            key="arbor_runtime",
            title="Arbor worker stack",
            state=CheckState.PASS,
            detail=(
                f"Arbor {identity.get('version')} at {identity.get('path')}; NumPy {identity.get('numpy')}; "
                f"pandas {identity.get('pandas')}; vectorized CPU={bool(identity.get('vectorize'))}; "
                f"MPI={bool(identity.get('mpi'))}; GPU={identity.get('gpu') or 'none'}."
            ),
            path=str(executable),
        )

    def _gap_catalogue_check(
        self,
        config: ArborAblationComparisonConfig,
        *,
        output_root: str | Path,
    ) -> PreflightCheck:
        """Load and inspect the exact junction catalogue in the worker runtime."""

        executable = Path(config.python_executable).expanduser()
        catalogue = self.gap_catalogue_path(output_root)
        manifest_path = catalogue.with_name("catalogue_manifest.json")
        if not catalogue.is_file():
            return PreflightCheck(
                key="arbor_gap_catalogue",
                title="App-owned Arbor gap catalogue",
                state=CheckState.FAIL,
                detail=(
                    f"The compiler/ABI-specific catalogue is missing: {catalogue}. "
                    "Build the app-owned mechanisms with a compiler-matched Arbor 0.12.2 runtime first."
                ),
                blocking=True,
                path=str(catalogue),
            )
        if not manifest_path.is_file():
            return PreflightCheck(
                key="arbor_gap_catalogue",
                title="App-owned Arbor gap catalogue",
                state=CheckState.FAIL,
                detail=f"Catalogue provenance manifest is missing: {manifest_path}",
                blocking=True,
                path=str(catalogue),
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            digest = _sha256(catalogue)
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            return PreflightCheck(
                key="arbor_gap_catalogue",
                title="App-owned Arbor gap catalogue",
                state=CheckState.FAIL,
                detail=f"Could not validate the catalogue manifest: {exc}",
                blocking=True,
                path=str(catalogue),
            )
        if (
            manifest.get("catalogue") != GAP_CATALOGUE_NAME
            or manifest.get("arbor_version") != EXPECTED_ARBOR_VERSION
            or manifest.get("catalogue_sha256") != digest
        ):
            return PreflightCheck(
                key="arbor_gap_catalogue",
                title="App-owned Arbor gap catalogue",
                state=CheckState.FAIL,
                detail=(
                    "Catalogue provenance does not match the artifact or Arbor version: "
                    f"manifest={manifest_path}, sha256={digest}."
                ),
                blocking=True,
                path=str(catalogue),
            )
        code = (
            "import arbor,json,sys; "
            "c=arbor.load_catalogue(sys.argv[1]); "
            "p=arbor.neuron_cable_properties(); p.catalogue.extend(c,'digifly_'); "
            "print(json.dumps({'version':getattr(arbor,'__version__','unknown'),"
            "'mechanisms':{str(n):{'kind':str(c[n].kind),'parameters':sorted(str(x) for x in c[n].parameters)} for n in c},"
            "'prefixed':sorted(str(n) for n in p.catalogue if str(n).startswith('digifly_'))}))"
        )
        environment = sanitized_external_environment(
            {
                "PATH": external_runtime_path(config.python_executable),
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1",
            }
        )
        try:
            completed = subprocess.run(
                [str(executable), "-B", "-c", code, str(catalogue)],
                env=environment,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PreflightCheck(
                key="arbor_gap_catalogue",
                title="App-owned Arbor gap catalogue",
                state=CheckState.FAIL,
                detail=f"Catalogue load probe failed: {exc}",
                blocking=True,
                path=str(catalogue),
            )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            return PreflightCheck(
                key="arbor_gap_catalogue",
                title="App-owned Arbor gap catalogue",
                state=CheckState.FAIL,
                detail=detail[-4000:] or "Arbor could not load the custom gap catalogue.",
                blocking=True,
                path=str(catalogue),
            )
        try:
            identity = json.loads(completed.stdout.strip().splitlines()[-1])
            mechanisms = dict(identity.get("mechanisms") or {})
            valid = str(identity.get("version")) == EXPECTED_ARBOR_VERSION
            for name, expected_parameters in EXPECTED_GAP_MECHANISMS.items():
                record = dict(mechanisms.get(name) or {})
                valid = valid and "gap junction" in str(record.get("kind", "")).lower()
                valid = valid and set(record.get("parameters") or []) == expected_parameters
            valid = valid and {
                f"digifly_{name}" for name in EXPECTED_GAP_MECHANISMS
            }.issubset(set(identity.get("prefixed") or []))
        except (json.JSONDecodeError, IndexError, TypeError, ValueError):
            valid = False
            identity = {}
        if not valid:
            return PreflightCheck(
                key="arbor_gap_catalogue",
                title="App-owned Arbor gap catalogue",
                state=CheckState.FAIL,
                detail=f"Catalogue mechanism contract failed: {identity}",
                blocking=True,
                path=str(catalogue),
            )
        return PreflightCheck(
            key="arbor_gap_catalogue",
            title="App-owned Arbor gap catalogue",
            state=CheckState.PASS,
            detail=(
                f"Loaded {GAP_CATALOGUE_NAME} ({digest[:12]}…) with Gap, RectGap, and "
                "HeteroRectGap junctions under the digifly_ prefix."
            ),
            blocking=True,
            path=str(catalogue),
        )

    def _input_contract_check(self) -> PreflightCheck:
        required = (
            self.manifest_path,
            self.chemical_edges_path,
            self.gap_contacts_path,
            self.gap_contacts_arbor_path,
            self.contact_site_nodes_path,
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            return PreflightCheck(
                key="arbor_input_contract",
                title="Exact Ablation input bundle",
                state=CheckState.FAIL,
                detail="Missing input files: " + ", ".join(missing),
                blocking=True,
                path=str(self.input_root),
            )
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            selected = tuple(int(value) for value in manifest.get("selected_neuron_ids") or [])
            if selected != EXPECTED_SELECTED_IDS or not bool(manifest.get("self_contained")):
                raise ValueError("manifest is not the exact self-contained 49-cell selection")
            for neuron_id in selected:
                swc = self.input_root / "swcs" / f"{neuron_id}_axodendro_with_synapses.swc"
                if not swc.is_file() or swc.is_symlink():
                    raise ValueError(f"missing or linked selected SWC: {swc.name}")
            chemical_rows, direct_rows, filtered_pairs = _chemical_contract(self.chemical_edges_path)
            if (
                chemical_rows != EXPECTED_CHEMICAL_ROWS
                or direct_rows != EXPECTED_DIRECT_GF_ROWS
                or filtered_pairs != EXPECTED_FILTERED_CHEMICAL_PAIRS
            ):
                raise ValueError(
                    f"chemical rows/direct rows/filtered pairs were {chemical_rows}/{direct_rows}/{filtered_pairs}"
                )
            chemical_hash = _sha256(self.chemical_edges_path)
            if chemical_hash != EXPECTED_CHEMICAL_SHA256:
                raise ValueError(f"chemical-edge SHA-256 changed: {chemical_hash}")
            gap_rows, gap_pairs = _edge_counts(self.gap_contacts_path)
            gap_hash = _sha256(self.gap_contacts_path)
            if (gap_rows, gap_pairs, gap_hash) != (
                EXPECTED_GAP_ROWS,
                EXPECTED_GAP_PAIRS,
                EXPECTED_GAP_SOURCE_SHA256,
            ):
                raise ValueError(
                    f"gap rows/pairs/hash were {gap_rows}/{gap_pairs}/{gap_hash}"
                )
            arbor_gap_rows, arbor_gap_pairs = _edge_counts(self.gap_contacts_arbor_path)
            arbor_gap_hash = _sha256(self.gap_contacts_arbor_path)
            if (arbor_gap_rows, arbor_gap_pairs, arbor_gap_hash) != (
                EXPECTED_GAP_ROWS,
                EXPECTED_GAP_PAIRS,
                EXPECTED_GAP_ARBOR_SHA256,
            ):
                raise ValueError(
                    "derived Arbor gap rows/pairs/hash were "
                    f"{arbor_gap_rows}/{arbor_gap_pairs}/{arbor_gap_hash}"
                )
            nodes = json.loads(self.contact_site_nodes_path.read_text(encoding="utf-8"))
            raw_nodes = dict(nodes.get("nodes_by_driver") or {})
            node_counts: dict[int, tuple[int, int]] = {}
            for driver_id in EXPECTED_CONTACT_NODE_COUNTS:
                values = [
                    int(node_id)
                    for group in dict(raw_nodes.get(str(driver_id)) or {}).values()
                    for node_id in list(group)
                ]
                node_counts[driver_id] = (len(values), len(set(values)))
            if node_counts != EXPECTED_CONTACT_NODE_COUNTS:
                raise ValueError(f"contact-site node counts changed: {node_counts}")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return PreflightCheck(
                key="arbor_input_contract",
                title="Exact Ablation input bundle",
                state=CheckState.FAIL,
                detail=f"Input contract failed: {exc}",
                blocking=True,
                path=str(self.input_root),
            )
        return PreflightCheck(
            key="arbor_input_contract",
            title="Exact Ablation input bundle",
            state=CheckState.PASS,
            detail=(
                f"Verified 49 self-contained SWCs, {chemical_rows} source chemical rows, removal of "
                f"{direct_rows} direct GF rows to {EXPECTED_FILTERED_CHEMICAL_ROWS} app-owned rows across "
                f"{filtered_pairs} pairs, and {gap_rows} contact rows across {gap_pairs} pairs."
            ),
            path=str(self.input_root),
        )


def _artifact_cv_policy(
    summary_path: Path,
    payload: Mapping[str, Any],
) -> tuple[str, str]:
    bridge = dict(payload.get("legacy_neuron_cv_bridge") or {})
    if bridge.get("cv_policy"):
        return str(bridge["cv_policy"]), "summary bridge provenance"
    root = summary_path.parent.resolve()
    candidates: list[str] = []
    candidates.extend(str(value) for value in dict(payload.get("planned_configs") or {}).values())
    for value in dict(payload.get("run_artifacts") or {}).values():
        if isinstance(value, Mapping) and value.get("config_json"):
            candidates.append(str(value["config_json"]))
    policies: set[str] = set()
    for raw_path in candidates:
        candidate = Path(raw_path).expanduser().resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            config = json.loads(candidate.read_text(encoding="utf-8"))
            policy = str(dict(config.get("arbor") or {}).get("cv_policy") or "").strip()
        except (OSError, TypeError, json.JSONDecodeError):
            continue
        if policy:
            policies.add(policy)
    if len(policies) == 1:
        return next(iter(policies)), "saved run/config artifact"
    if policies:
        return "mixed:" + ",".join(sorted(policies)), "inconsistent saved configs"
    return "unknown", "no saved CV-policy provenance"


def _audit_cv_policy(
    comparison_root: Path,
    audit_payload: Mapping[str, Any],
) -> str:
    policies: set[str] = set()
    root = comparison_root.resolve()
    for raw_run in dict(audit_payload.get("arbor_runs") or {}).values():
        config_path = Path(str(raw_run)).expanduser().resolve() / "config.json"
        try:
            config_path.relative_to(root)
        except ValueError:
            continue
        if not config_path.is_file():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            policy = str(dict(config.get("arbor") or {}).get("cv_policy") or "").strip()
        except (OSError, TypeError, json.JSONDecodeError):
            continue
        if policy:
            policies.add(policy)
    return next(iter(policies)) if len(policies) == 1 else "unknown"


def load_arbor_ablation_result(path: str | Path) -> ResultRecord:
    summary_path = Path(path).expanduser().resolve()
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Arbor comparison summary must contain a JSON object.")
    artifacts = _result_artifacts(summary_path, payload)
    gap = dict(payload.get("gap_model_comparison") or {})
    inputs = dict(payload.get("input_validation") or {})
    stimuli = dict(payload.get("stimulus") or {})
    artifact_policy, artifact_policy_source = _artifact_cv_policy(summary_path, payload)
    policy_matches = artifact_policy == CURRENT_CV_POLICY
    checks = [
        PreflightCheck(
            key="arbor_result_capability_label",
            title="Comparison implementation label",
            state=(
                CheckState.PASS
                if payload.get("comparison_class") == "app_owned_equation_port"
                and payload.get("equivalence_claim") is False
                else CheckState.FAIL
            ),
            detail=(
                "Result uses the app-owned equation port and leaves cross-backend equivalence to the auditor."
                if payload.get("equivalence_claim") is False
                else "Result is missing the required pending-equivalence declaration."
            ),
            blocking=True,
        ),
        PreflightCheck(
            key="arbor_result_cv_policy",
            title="Arbor discretization compatibility policy",
            state=(
                CheckState.PASS
                if policy_matches
                else (CheckState.WARNING if artifact_policy != "unknown" else CheckState.INFO)
            ),
            detail=(
                f"Artifacts use {artifact_policy} ({artifact_policy_source}), matching the current app."
                if policy_matches
                else (
                    f"Artifacts use {artifact_policy} ({artifact_policy_source}); current policy is "
                    f"{CURRENT_CV_POLICY}. Other-policy traces/audits are not evidence for the "
                    "current production policy."
                )
            ),
            blocking=False,
        ),
        PreflightCheck(
            key="arbor_result_edges",
            title="Notebook chemical-edge topology",
            state=(
                CheckState.PASS
                if int(inputs.get("filtered_chemical_rows") or -1) == EXPECTED_FILTERED_CHEMICAL_ROWS
                and type(inputs.get("direct_gf_rows_retained")) is int
                and inputs.get("direct_gf_rows_retained") == 0
                else CheckState.FAIL
            ),
            detail=(
                f"Recorded {inputs.get('filtered_chemical_rows')} filtered rows and "
                f"{inputs.get('direct_gf_rows_retained')} retained direct GF rows."
            ),
            blocking=True,
        ),
        PreflightCheck(
            key="arbor_result_stimulus",
            title="Notebook GFC2 stimulus",
            state=(
                CheckState.PASS
                if tuple(int(value) for value in stimuli.get("target_ids") or []) == GFC2_IDS
                and float(stimuli.get("amplitude_nA") or -1) == 0.9
                else CheckState.FAIL
            ),
            detail=(
                f"Recorded {len(stimuli.get('target_ids') or [])} targets at "
                f"{stimuli.get('amplitude_nA')} nA."
            ),
            blocking=True,
        ),
        PreflightCheck(
            key="arbor_result_gap_equation_port",
            title="Arbor HeteroRectGap equation port",
            state=(
                CheckState.PASS
                if gap.get("effective_mechanism") == "digifly_hetero_rect_gap"
                and float(gap.get("g_closed_frac", -1)) == 0.0
                and float(gap.get("empirical_residual_frac", -1)) == 0.20
                and float(gap.get("vhalf_mV", -1)) == 0.0
                and float(gap.get("vslope_mV", -1)) == 5.0
                and float(gap.get("tau_open_ms", -1)) == 6.0
                and float(gap.get("tau_close_ms", -1)) == 2.0
                and dict(gap.get("endpoint_orientations") or {})
                == {"pre": 1.0, "post": -1.0}
                and not list(gap.get("unrepresented_parameters") or [])
                and isinstance(gap.get("catalogue_sha256"), str)
                and len(str(gap.get("catalogue_sha256"))) == 64
                else CheckState.FAIL
            ),
            detail=(
                "App-owned hetero_rect_gap records closed=0, residual=0.20, vhalf=0 mV, vslope=5 mV, "
                "tau-open=6 ms, tau-close=2 ms, and +1/-1 endpoint orientation."
            ),
            blocking=True,
        ),
    ]
    metadata: dict[str, Any] = {
        "comparison class": payload.get("comparison_class", "missing"),
        "equivalence claim": payload.get("equivalence_claim", "missing"),
        "gap mechanism": gap.get("effective_mechanism", "missing"),
        "gap catalogue": gap.get("catalogue_name", "missing"),
        "gap catalogue sha256": gap.get("catalogue_sha256", "missing"),
        "gap solver": "Arbor cnexp · NEURON derivimplicit",
        "unrepresented kinetics": ", ".join(
            str(value) for value in gap.get("unrepresented_parameters") or []
        ) or "none",
        "chemical edges": inputs.get("filtered_chemical_rows", "unknown"),
        "stimulus targets": len(stimuli.get("target_ids") or []),
        "contact-site Na": payload.get("contact_site_na_multiplier", "unknown"),
        "equivalence audit": payload.get("equivalence_audit_status", "not recorded"),
        "artifact CV policy": artifact_policy,
        "current CV policy": CURRENT_CV_POLICY,
        "CV policy provenance": artifact_policy_source,
        "topology equivalence claim": False,
    }
    audit = _latest_equivalence_report(summary_path.parent)
    if audit is not None:
        audit_path, audit_payload = audit
        verdict = str(audit_payload.get("verdict") or "UNKNOWN")
        audit_policy = _audit_cv_policy(summary_path.parent, audit_payload)
        stale_prior_policy = (
            artifact_policy != "unknown"
            and artifact_policy != CURRENT_CV_POLICY
        ) or (
            audit_policy != "unknown"
            and audit_policy != CURRENT_CV_POLICY
        )
        metadata.update(
            {
                "equivalence audit": (
                    f"{verdict} (prior {audit_policy} policy)"
                    if stale_prior_policy
                    else verdict
                ),
                "audit CV policy": audit_policy,
                "audit generated": audit_payload.get("generated_at_utc", "not recorded"),
                "summary audit marker": payload.get("equivalence_audit_status", "not recorded"),
                "equation-level gap exact": audit_payload.get(
                    "equation_level_gap_mechanism_exact", "not recorded"
                ),
            }
        )
        if stale_prior_policy:
            checks.append(
                PreflightCheck(
                    key="arbor_equivalence_policy_stale",
                    title="Prior-policy equivalence audit",
                    state=CheckState.WARNING,
                    detail=(
                        f"This audit used {audit_policy}; the current app uses {CURRENT_CV_POLICY}. "
                        "It is retained for provenance but is not evidence for the production policy."
                    ),
                    blocking=False,
                    path=str(audit_path),
                )
            )
        else:
            checks.extend(_equivalence_checks(audit_path, audit_payload))
        existing = {artifact.path for artifact in artifacts}
        for artifact in _equivalence_artifacts(summary_path.parent, audit_path, audit_payload):
            if artifact.path not in existing:
                artifacts.append(artifact)
                existing.add(artifact.path)
    return ResultRecord(
        summary_path=str(summary_path),
        status=str(payload.get("status") or "unknown"),
        completed_at=str(payload.get("completed_at") or payload.get("created_at") or "unknown"),
        title="Escape-SIZ · Ablation notebook Arbor comparison",
        metadata=metadata,
        artifacts=tuple(artifacts),
        checks=tuple(checks),
    )


_EQUIVALENCE_GATE_TITLES = {
    "all_key_soma_covered": "All key somata covered",
    "implementation": "Implementation contract",
    "stimulus_source_soma_fraction": "Stimulus-source baseline readiness",
    "postsynaptic_soma_fraction": "Postsynaptic soma traces",
    "driver_compartment_fraction": "Driver compartment traces",
    "key_trace_shape": "Key trace shape",
    "key_pulse_hits": "Key pulse responses",
    "key_spikes": "Key spike responses",
    "gap_effect_direction": "Gap-effect direction",
}


def _latest_equivalence_report(comparison_root: Path) -> tuple[Path, Mapping[str, Any]] | None:
    """Read the newest valid auditor report beneath an app-owned comparison root."""

    root = comparison_root.resolve()
    candidates: list[tuple[float, str, Path, Mapping[str, Any]]] = []
    for candidate in root.rglob("equivalence_report.json"):
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
            if not resolved.is_file():
                continue
            report = json.loads(resolved.read_text(encoding="utf-8"))
            if (
                not isinstance(report, Mapping)
                or not isinstance(report.get("verdict"), str)
                or not isinstance(report.get("gates"), Mapping)
            ):
                continue
            generated = str(report.get("generated_at_utc") or "")
            try:
                generated_at = datetime.fromisoformat(
                    generated.replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, OverflowError):
                generated_at = resolved.stat().st_mtime
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        candidates.append((generated_at, str(resolved), resolved, report))
    if not candidates:
        return None
    _, _, report_path, report = max(candidates, key=lambda item: (item[0], item[1]))
    return report_path, report


def _equivalence_checks(report_path: Path, report: Mapping[str, Any]) -> list[PreflightCheck]:
    verdict = str(report.get("verdict") or "UNKNOWN")
    passed = report.get("result_passed", report.get("passed")) is True
    generated = str(report.get("generated_at_utc") or "time not recorded")
    checks = [
        PreflightCheck(
            key="arbor_equivalence_verdict",
            title="NEURON–Arbor equivalence verdict",
            state=CheckState.PASS if passed else CheckState.FAIL,
            detail=(
                f"Latest read-only auditor verdict: {verdict} ({generated}). "
                "The native Arbor comparison summary was not modified."
            ),
            blocking=True,
            path=str(report_path),
        )
    ]
    gates = dict(report.get("gates") or {})
    ordered_keys = [key for key in _EQUIVALENCE_GATE_TITLES if key in gates]
    ordered_keys.extend(sorted(str(key) for key in gates if str(key) not in ordered_keys))
    for key in ordered_keys:
        gate_passed = gates.get(key) is True
        checks.append(
            PreflightCheck(
                key=f"arbor_equivalence_gate_{key}",
                title=_EQUIVALENCE_GATE_TITLES.get(key, key.replace("_", " ").title()),
                state=CheckState.PASS if gate_passed else CheckState.FAIL,
                detail=_equivalence_gate_detail(key, gate_passed, report),
                blocking=True,
            )
        )
    return checks


def _equivalence_gate_detail(key: str, passed: bool, report: Mapping[str, Any]) -> str:
    state = "PASS" if passed else "FAIL"
    acceptance = dict(report.get("acceptance") or {})
    trace = dict(report.get("trace_summary") or {})
    coverage = dict(report.get("coverage") or {})
    implementation = dict(report.get("implementation_summary") or {})
    readiness = dict(report.get("baseline_readiness") or {})
    worst_source = dict(readiness.get("worst_rmse_evidence") or {})
    details: dict[str, str] = {
        "all_key_soma_covered": (
            f"{coverage.get('comparable_soma_count', 'unknown')} comparable somata; "
            f"key coverage complete={coverage.get('key_soma_complete', 'unknown')}."
        ),
        "implementation": (
            f"{implementation.get('required_failure_count', 'unknown')} required failures across "
            f"{implementation.get('required_check_count', 'unknown')} checks."
        ),
        "stimulus_source_soma_fraction": (
            f"Gap-off source-soma pass fraction {_metric(readiness.get('pass_fraction'))}; required "
            f"{_metric(readiness.get('required_pass_fraction'))}. Comparable "
            f"{readiness.get('comparable_seed_soma_count', 'unknown')}/"
            f"{readiness.get('required_seed_count', 'unknown')}; worst RMSE "
            f"{_metric(worst_source.get('rmse_mV'))} mV at neuron "
            f"{worst_source.get('neuron_id', 'unknown')}."
        ),
        "postsynaptic_soma_fraction": (
            f"Pass fraction {_metric(trace.get('postsynaptic_soma_pass_fraction'))}; required "
            f"{_metric(acceptance.get('postsynaptic_soma_pass_fraction_min'))}."
        ),
        "driver_compartment_fraction": (
            f"Pass fraction {_metric(trace.get('driver_compartment_pass_fraction'))}; required "
            f"{_metric(acceptance.get('driver_compartment_pass_fraction_min'))}."
        ),
        "key_trace_shape": (
            f"Key pass fraction {_metric(trace.get('key_pass_fraction'))}; maximum RMSE "
            f"{_metric(trace.get('maximum_key_rmse_mV'))} mV; minimum Pearson r "
            f"{_metric(trace.get('minimum_key_pearson_r'))}."
        ),
        "key_pulse_hits": (
            f"Pass fraction {_metric(report.get('pulse_hit_pass_fraction'))}; absolute tolerance "
            f"{_metric(acceptance.get('pulse_hit_abs_tolerance'))}."
        ),
        "key_spikes": (
            f"Pass fraction {_metric(report.get('spike_pass_fraction'))}; absolute tolerance "
            f"{_metric(acceptance.get('spike_count_abs_tolerance'))}."
        ),
        "gap_effect_direction": (
            f"Pass fraction {_metric(report.get('gap_direction_pass_fraction'))}; all reported metrics "
            f"{_metric(report.get('gap_direction_all_reported_metrics_pass_fraction'))}."
        ),
    }
    return f"Auditor gate {state}. {details.get(key, 'See the equivalence report for evidence.')}"


def _metric(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return "not recorded" if value is None else str(value).lower()
    if isinstance(value, (int, float)):
        return f"{float(value):.4g}"
    return str(value)


def _equivalence_artifacts(
    comparison_root: Path,
    report_path: Path,
    report: Mapping[str, Any],
) -> list[Artifact]:
    """Expose auditor outputs without allowing report paths to escape the comparison root."""

    root = comparison_root.resolve()
    candidates: list[tuple[str, str]] = [("Equivalence report", str(report_path))]
    for key, value in dict(report.get("artifacts") or {}).items():
        if isinstance(value, str) and value:
            candidates.append(
                (f"Equivalence audit · {str(key).replace('_', ' ').title()}", value)
            )
    artifacts: list[Artifact] = []
    seen: set[str] = set()
    for label, raw_path in candidates:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = report_path.parent / path
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        artifacts.append(
            Artifact(
                kind=_kind_for_path(key),
                path=key,
                label=label,
                exists=resolved.is_file(),
            )
        )
    return artifacts


def _result_artifacts(summary_path: Path, payload: Mapping[str, Any]) -> list[Artifact]:
    candidates: list[tuple[str, str, str]] = [
        ("json", str(summary_path), "Comparison summary"),
    ]
    for key in ("filtered_chemical_edges", "worker_provenance", "equivalence_auditor"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            candidates.append((_kind_for_path(value), value, key.replace("_", " ").title()))
    for group_key in ("plots", "response_metrics", "planned_configs", "run_artifacts"):
        group = payload.get(group_key)
        if isinstance(group, Mapping):
            for key, value in group.items():
                if isinstance(value, str) and Path(value).suffix:
                    candidates.append((_kind_for_path(value), value, str(key).replace("_", " ").title()))
                elif isinstance(value, Mapping):
                    for nested_key, nested_value in value.items():
                        if isinstance(nested_value, str) and Path(nested_value).suffix:
                            candidates.append(
                                (
                                    _kind_for_path(nested_value),
                                    nested_value,
                                    f"{key} · {str(nested_key).replace('_', ' ').title()}",
                                )
                            )
    seen: set[str] = set()
    artifacts: list[Artifact] = []
    for kind, raw_path, label in candidates:
        resolved = str(Path(raw_path).expanduser().resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        artifacts.append(
            Artifact(kind=kind, path=resolved, label=label, exists=Path(resolved).exists())
        )
    return artifacts


def _chemical_contract(path: Path) -> tuple[int, int, int]:
    total = 0
    direct = 0
    retained_pairs: set[tuple[int, int]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not {"pre_id", "post_id"}.issubset(reader.fieldnames or []):
            raise ValueError("chemical edges lack pre_id/post_id")
        for row in reader:
            pair = (int(float(row["pre_id"])), int(float(row["post_id"])))
            total += 1
            if pair in {(10000, 10002), (10002, 10000)}:
                direct += 1
            else:
                retained_pairs.add(pair)
    return total, direct, len(retained_pairs)


def _edge_counts(path: Path) -> tuple[int, int]:
    rows = 0
    pairs: set[tuple[int, int]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if not {"pre_id", "post_id"}.issubset(reader.fieldnames or []):
            raise ValueError(f"{path.name} lacks pre_id/post_id")
        for row in reader:
            rows += 1
            pairs.add((int(float(row["pre_id"])), int(float(row["post_id"]))))
    return rows, len(pairs)


def _file_check(key: str, title: str, path: Path) -> PreflightCheck:
    exists = path.is_file()
    return PreflightCheck(
        key=key,
        title=title,
        state=CheckState.PASS if exists else CheckState.FAIL,
        detail=f"Found {path}" if exists else f"Missing required file: {path}",
        blocking=True,
        path=str(path),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: float) -> str:
    return f"{float(value):.15g}"


def _kind_for_path(path: str) -> str:
    return {
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".pdf": "pdf",
        ".json": "json",
        ".csv": "table",
        ".npz": "array",
    }.get(Path(path).suffix.lower(), "file")
