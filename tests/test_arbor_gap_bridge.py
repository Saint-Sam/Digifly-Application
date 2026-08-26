from __future__ import annotations

from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from digifly_app.workers.arbor_gap_bridge import (
    ArborGapBridgeError,
    STAGING_RUNNER_MODULE,
    install_arbor_gap_bridge,
)


EXPECTED_PARAMETERS = {
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


class FakeCatalogue(dict[str, Any]):
    def extend(self, other: "FakeCatalogue", prefix: str) -> None:
        for name, info in other.items():
            self[prefix + name] = info


class FakeArbor(ModuleType):
    def __init__(self, catalogue: FakeCatalogue, *, version: str = "0.12.2") -> None:
        super().__init__("arbor")
        self.__version__ = version
        self.loaded_paths: list[str] = []
        self.junction_calls: list[tuple[str, dict[str, float]]] = []
        self.properties_created: list[Any] = []
        self._catalogue_to_load = catalogue

        def load_catalogue(path: str) -> FakeCatalogue:
            self.loaded_paths.append(path)
            return self._catalogue_to_load

        def neuron_cable_properties() -> Any:
            properties = SimpleNamespace(
                catalogue=FakeCatalogue(
                    {
                        "gj": SimpleNamespace(
                            kind="gap junction mechanism kind",
                            parameters={"g": object()},
                        )
                    }
                )
            )
            self.properties_created.append(properties)
            return properties

        def junction(name: str, parameters: dict[str, float]) -> Any:
            copied = dict(parameters)
            self.junction_calls.append((name, copied))
            return name, copied

        self.load_catalogue = load_catalogue
        self.neuron_cable_properties = neuron_cable_properties
        self.junction = junction


def _custom_catalogue() -> FakeCatalogue:
    return FakeCatalogue(
        {
            name: SimpleNamespace(
                kind="gap junction mechanism kind",
                parameters={parameter: object() for parameter in parameters},
            )
            for name, parameters in EXPECTED_PARAMETERS.items()
        }
    )


def _runner() -> ModuleType:
    runner = ModuleType(STAGING_RUNNER_MODULE)

    def _make_junction(A, pair, cfg, *, prefix):
        raise AssertionError("The staging approximation must be replaced")

    runner._make_junction = _make_junction
    return runner


def _catalogue_file(tmp_path: Path) -> Path:
    path = tmp_path / "digifly_gap-catalogue.dylib"
    path.write_bytes(b"test catalogue")
    return path


def _hetero_config(**updates: Any) -> dict[str, Any]:
    gap = {
        "mechanism": "hetero_rect_gap",
        "directionality": "pre_to_post",
        "g_closed_frac": 0.25,
        "vhalf_mV": -2.0,
        "vslope_mV": 5.0,
        "empirical_residual_frac": 0.2,
        "tau_open_ms": 6.0,
        "tau_close_ms": 2.0,
    }
    gap.update(updates)
    return {"gap": gap}


def test_install_loads_once_extends_every_fresh_properties_and_is_idempotent(
    tmp_path: Path,
) -> None:
    arbor = FakeArbor(_custom_catalogue())
    runner = _runner()
    catalogue_path = _catalogue_file(tmp_path)

    first = install_arbor_gap_bridge(
        arbor_module=arbor,
        runner_module=runner,
        catalogue_path=catalogue_path,
    )
    second = install_arbor_gap_bridge(
        arbor_module=arbor,
        runner_module=runner,
        catalogue_path=catalogue_path,
    )

    assert second is first
    assert arbor.loaded_paths == [str(catalogue_path.resolve())]
    assert first.metadata["catalogue_prefix"] == "digifly_"
    assert first.metadata["connection_weight"] == 1.0
    assert first.metadata["fallback_mechanism"] is None
    # One properties object is the pre-install probe; these two prove that the
    # extension is applied afresh rather than only to that probe.
    properties_a = arbor.neuron_cable_properties()
    properties_b = arbor.neuron_cable_properties()
    for properties in (properties_a, properties_b):
        assert {
            "digifly_gap",
            "digifly_rect_gap",
            "digifly_hetero_rect_gap",
        }.issubset(properties.catalogue)


def test_bridge_maps_exact_ohmic_and_one_sided_rectgap_endpoints(tmp_path: Path) -> None:
    arbor = FakeArbor(_custom_catalogue())
    runner = _runner()
    install_arbor_gap_bridge(
        arbor_module=arbor,
        runner_module=runner,
        catalogue_path=_catalogue_file(tmp_path),
    )

    pair = {"mode": "gap", "directionality": "symmetric", "g_uS": 0.00125}
    assert runner._make_junction(arbor, pair, {"gap": {}}, prefix="pre") == (
        "digifly_gap",
        {"g": 0.00125},
    )
    assert runner._make_junction(arbor, pair, {"gap": {}}, prefix="post") == (
        "digifly_gap",
        {"g": 0.00125},
    )

    rect_pair = {
        "mode": "rect_gap",
        "directionality": "pre_to_post",
        "g_uS": 0.003,
    }
    assert runner._make_junction(arbor, rect_pair, {"gap": {}}, prefix="pre") == (
        "digifly_gap",
        {"g": 0.0},
    )
    assert runner._make_junction(arbor, rect_pair, {"gap": {}}, prefix="post") == (
        "digifly_rect_gap",
        {"gmax": 0.003},
    )
    rect_pair["directionality"] = "post_to_pre"
    assert runner._make_junction(arbor, rect_pair, {"gap": {}}, prefix="pre")[0] == (
        "digifly_rect_gap"
    )
    assert runner._make_junction(arbor, rect_pair, {"gap": {}}, prefix="post") == (
        "digifly_gap",
        {"g": 0.0},
    )


def test_bridge_maps_heterorectgap_parameters_and_orientations_from_gap_config(
    tmp_path: Path,
) -> None:
    arbor = FakeArbor(_custom_catalogue())
    runner = _runner()
    install_arbor_gap_bridge(
        arbor_module=arbor,
        runner_module=runner,
        catalogue_path=_catalogue_file(tmp_path),
    )
    pair = {
        "mode": "hetero_rect_gap",
        "directionality": "pre_to_post",
        "g_uS": 0.004,
        # This old approximation field must not be confused with the actual
        # closed-conductance fraction in cfg['gap'].
        "closed_fraction": 0.9,
    }
    config = _hetero_config()

    pre = runner._make_junction(arbor, pair, config, prefix="pre")
    post = runner._make_junction(arbor, pair, config, prefix="post")
    expected = {
        "gmax_open": 0.004,
        "gmax_closed": 0.001,
        "vhalf": -2.0,
        "vslope": 5.0,
        "empirical_residual_frac": 0.2,
        "tau_open_ms": 6.0,
        "tau_close_ms": 2.0,
    }
    assert pre == ("digifly_hetero_rect_gap", {**expected, "orientation": 1.0})
    assert post == ("digifly_hetero_rect_gap", {**expected, "orientation": -1.0})

    pair["directionality"] = "post_to_pre"
    assert runner._make_junction(arbor, pair, config, prefix="pre")[1]["orientation"] == -1.0
    assert runner._make_junction(arbor, pair, config, prefix="post")[1]["orientation"] == 1.0

    # The NEURON wrapper clips only the lower bound. Preserve generic model
    # behavior above one even though the locked notebook recipe uses zero.
    above_open = _hetero_config(g_closed_frac=1.25)
    junction = runner._make_junction(arbor, pair, above_open, prefix="pre")
    assert junction[1]["gmax_closed"] == pytest.approx(0.005)


def test_bridge_fails_closed_without_gj_fallback(tmp_path: Path) -> None:
    arbor = FakeArbor(_custom_catalogue())
    runner = _runner()
    install_arbor_gap_bridge(
        arbor_module=arbor,
        runner_module=runner,
        catalogue_path=_catalogue_file(tmp_path),
    )

    with pytest.raises(ArborGapBridgeError, match="built-in gj is forbidden"):
        runner._make_junction(
            arbor,
            {"mode": "gj", "directionality": "symmetric", "g_uS": 0.001},
            {"gap": {}},
            prefix="pre",
        )
    with pytest.raises(ArborGapBridgeError, match="g_closed_frac"):
        runner._make_junction(
            arbor,
            {
                "mode": "hetero_rect_gap",
                "directionality": "pre_to_post",
                "g_uS": 0.001,
            },
            {"gap": {"vhalf_mV": 0.0}},
            prefix="pre",
        )
    assert all(name != "gj" for name, _ in arbor.junction_calls)

    def reject_custom(name: str, parameters: dict[str, float]) -> Any:
        arbor.junction_calls.append((name, dict(parameters)))
        if name == "gj":
            return "forbidden fallback"
        raise RuntimeError("custom placement rejected")

    arbor.junction = reject_custom
    with pytest.raises(ArborGapBridgeError, match="no gj fallback was used"):
        runner._make_junction(
            arbor,
            {"mode": "gap", "directionality": "symmetric", "g_uS": 0.001},
            {"gap": {}},
            prefix="pre",
        )
    assert arbor.junction_calls[-1][0] == "digifly_gap"
    assert all(name != "gj" for name, _ in arbor.junction_calls)


def test_install_rejects_version_signature_and_catalogue_drift(tmp_path: Path) -> None:
    path = _catalogue_file(tmp_path)
    with pytest.raises(ArborGapBridgeError, match="requires Arbor 0.12.2"):
        install_arbor_gap_bridge(
            arbor_module=FakeArbor(_custom_catalogue(), version="0.13.0"),
            runner_module=_runner(),
            catalogue_path=path,
        )

    bad_runner = ModuleType(STAGING_RUNNER_MODULE)

    def changed_make_junction(A, pair, cfg, prefix):
        return None

    bad_runner._make_junction = changed_make_junction
    with pytest.raises(ArborGapBridgeError, match="calling convention changed"):
        install_arbor_gap_bridge(
            arbor_module=FakeArbor(_custom_catalogue()),
            runner_module=bad_runner,
            catalogue_path=path,
        )

    incomplete = _custom_catalogue()
    del incomplete["hetero_rect_gap"]
    with pytest.raises(ArborGapBridgeError, match="missing mechanisms: hetero_rect_gap"):
        install_arbor_gap_bridge(
            arbor_module=FakeArbor(incomplete),
            runner_module=_runner(),
            catalogue_path=path,
        )
