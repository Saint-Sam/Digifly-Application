from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MECHANISM_ROOT = REPO_ROOT / "mechanisms" / "arbor_gap_junctions"
EXPECTED_NEURON_HASHES = {
    "gap": "e3ab9d0a37811314d3baa8461050e6d65fbb9b8e163ff6618a6d9f2f2549c1f2",
    "rect_gap": "d9fa308ff0433ad0eb424017fff4384d5c06915ca9a2cbe25485ad176db3a8d6",
    "hetero_rect_gap": "22b505af799076bb8079f204d222adfa72432f8609bd89fd050f7ffb325c9c2f",
}
EXPECTED_ARBOR_HASHES = {
    "gap": "2b88d95a628abd21be48d550a7f387a0cefe1b65a4c09e43d19d06b11701aec2",
    "rect_gap": "e2164233d1230be646e6e66a5307290083d2a3bc4fb23ec9062213bc0cd9558d",
    "hetero_rect_gap": "b4ded50b979589f218ad9a7366b994be3ed33bb5473eff5f908b1f5749a7ba3d",
}


def _modcc_path() -> Path | None:
    candidates = (
        os.environ.get("ARBOR_MODCC"),
        shutil.which("modcc"),
        "/opt/anaconda3/lib/python3.12/site-packages/arbor/bin/modcc",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def _arbor_include_dir(modcc: Path) -> Path | None:
    prefix = modcc.parents[1]
    candidates = [
        prefix / "include",
        *(prefix / "lib").glob("python*/site-packages/arbor/include"),
    ]
    for candidate in candidates:
        if (candidate / "arbor" / "mechanism_abi.h").is_file():
            return candidate
    return None


@pytest.fixture(scope="module")
def generated_mechanisms(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    modcc = _modcc_path()
    if modcc is None:
        pytest.skip("Arbor modcc is not installed; source-contract tests still cover the ports.")

    generated = tmp_path_factory.mktemp("arbor-gap-modcc") / "generated"
    generated.mkdir()
    command = [
        str(modcc),
        "-t",
        "cpu",
        "-N",
        "arb::digifly_gap_catalogue",
        "-o",
        str(generated),
        *(str(MECHANISM_ROOT / f"{name}.mod") for name in EXPECTED_NEURON_HASHES),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "Warnings:" not in completed.stdout + completed.stderr
    return modcc, generated


def test_manifest_freezes_neuron_sources_and_standalone_unit_contract() -> None:
    manifest = json.loads((MECHANISM_ROOT / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["catalogue_name"] == "digifly_gap"
    assert manifest["port_contract"] == {
        "arbor_conductance_unit": "uS",
        "arbor_current_unit": "nA",
        "neuron_conductance_unit": "nS",
        "neuron_to_arbor_conductance_scale": 0.001,
        "peer_voltage": "v_peer",
        "source_kind": "JUNCTION_PROCESS",
    }
    assert {
        name: record["neuron_source_sha256"]
        for name, record in manifest["mechanisms"].items()
    } == EXPECTED_NEURON_HASHES
    assert {
        name: record["arbor_source_sha256"]
        for name, record in manifest["mechanisms"].items()
    } == EXPECTED_ARBOR_HASHES
    assert "read-only input" in manifest["source_tree_policy"]


@pytest.mark.parametrize("name", EXPECTED_NEURON_HASHES)
def test_each_port_is_an_arbor_junction_without_neuron_pointer_plumbing(name: str) -> None:
    source = (MECHANISM_ROOT / f"{name}.mod").read_text(encoding="utf-8")
    assert f"JUNCTION_PROCESS {name}" in source
    assert "NONSPECIFIC_CURRENT i" in source
    assert "v_peer" in source
    assert "(uS) = (microsiemens)" in source
    assert "POINTER" not in source
    assert "vgap_ptr" not in source
    assert "use_transfer" not in source


def test_heterotypic_source_preserves_floor_and_asymmetric_gate_ode() -> None:
    source = (MECHANISM_ROOT / "hetero_rect_gap.mod").read_text(encoding="utf-8")
    for fragment in (
        "empirical_residual_frac = 0.20",
        "tau_open_ms = 6 (ms)",
        "tau_close_ms = 2 (ms)",
        "STATE {\n    gate_state\n}",
        "SOLVE gate_dynamics METHOD cnexp",
        "if (gate_inf > gate_state)",
        "gate_state' = (gate_inf - gate_state)/tau_gate",
        "g_floor < gmax_open*empirical_residual_frac",
    ):
        assert fragment in source


def test_modcc_generates_gap_junction_metadata_with_expected_parameters(
    generated_mechanisms: tuple[Path, Path],
) -> None:
    _, generated = generated_mechanisms
    expected_parameters = {
        "gap": ("g",),
        "rect_gap": ("gmax",),
        "hetero_rect_gap": (
            "gmax_open",
            "gmax_closed",
            "orientation",
            "vhalf",
            "vslope",
            "empirical_residual_frac",
            "tau_open_ms",
            "tau_close_ms",
        ),
    }
    for name, parameters in expected_parameters.items():
        metadata = (generated / f"{name}.hpp").read_text(encoding="utf-8")
        assert f'result.name="{name}"' in metadata
        assert "result.kind=arb_mechanism_kind_gap_junction" in metadata
        for parameter in parameters:
            assert f'{{ "{parameter}",' in metadata
    hetero_metadata = (generated / "hetero_rect_gap.hpp").read_text(encoding="utf-8")
    assert '{ "gate_state", "", NAN' in hetero_metadata
    assert '{ "gmax_open", "uS", 0' in hetero_metadata
    assert '{ "tau_open_ms", "ms", 6' in hetero_metadata
    assert '{ "tau_close_ms", "ms", 2' in hetero_metadata


def test_generated_kernels_pass_two_endpoint_behavior_smoke(
    generated_mechanisms: tuple[Path, Path], tmp_path: Path
) -> None:
    modcc, generated = generated_mechanisms
    compiler = shutil.which("clang++") or shutil.which("c++")
    if compiler is None:
        pytest.skip("A C++ compiler is required for the generated-kernel behavior smoke.")
    include_dir = _arbor_include_dir(modcc)
    if include_dir is None:
        pytest.skip("Arbor headers are not installed beside modcc.")

    binary = tmp_path / "arbor-gap-kernel-smoke"
    command = [
        compiler,
        "-std=c++17",
        "-O0",
        "-I",
        str(include_dir),
        str(REPO_ROOT / "tests" / "arbor_gap_kernel_smoke.cpp"),
        *(str(generated / f"{name}_cpu.cpp") for name in EXPECTED_NEURON_HASHES),
        "-o",
        str(binary),
    ]
    compiled = subprocess.run(command, check=False, capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    executed = subprocess.run([str(binary)], check=False, capture_output=True, text=True)
    assert executed.returncode == 0, executed.stdout + executed.stderr
    assert "passed two-endpoint behavior checks" in executed.stdout


def test_source_files_are_self_contained_and_have_stable_hashes() -> None:
    source_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(MECHANISM_ROOT.glob("*.mod"))
    }
    assert source_hashes == {
        f"{name}.mod": digest for name, digest in sorted(EXPECTED_ARBOR_HASHES.items())
    }


def test_recorded_build_gate_requires_a_loadable_verified_catalogue() -> None:
    status = json.loads((MECHANISM_ROOT / "build_status.json").read_text(encoding="utf-8"))
    assert status["runtime"]["arbor_version"] == "0.12.2"
    assert status["checks"]["modcc_cpu_source_generation"] == "pass"
    assert status["checks"]["generated_cpu_kernel_behavior"] == "pass"
    assert status["checks"]["shared_catalogue_link"] == "pass"
    assert status["checks"]["load_catalogue"] == "pass"
    assert status["checks"]["actual_two_cell_hetero_rect_gap"] == "pass"
    assert status["resolved_blocker"]["code"] == "apple_llvm_bitcode_version_mismatch"
    assert len(status["build"]["catalogue_sha256"]) == 64
