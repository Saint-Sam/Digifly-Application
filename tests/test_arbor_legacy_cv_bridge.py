from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from digifly_app.workers.arbor_legacy_cv_bridge import (
    ArborLegacyCVBridgeError,
    BRIDGE_REVISION,
    CV_POLICY_TOKEN,
    EXPECTED_APP_RECIPE,
    LOCSET_JOIN_STRATEGY,
    STAGING_RUNNER_MODULE,
    _balanced_join_depth,
    _join_locset,
    build_legacy_cell_layout,
    install_arbor_legacy_cv_bridge,
)


@dataclass(frozen=True)
class Node:
    node_id: int
    parent_id: int
    x: float
    y: float = 0.0
    z: float = 0.0


@dataclass(frozen=True)
class Layout:
    section_paths: tuple[tuple[int, ...], ...]
    section_nseg: tuple[int, ...]
    node_to_cv: dict[int, tuple[int, int]]


class FakePolicy:
    def __init__(self, locset: str) -> None:
        self.locset = str(locset)


class FakeDescription:
    def __init__(self, cv_count: int) -> None:
        self.cv_count = int(cv_count)
        self.policy: FakePolicy | None = None

    def discretization(self, policy: FakePolicy) -> None:
        self.policy = policy


class FakeArbor(ModuleType):
    def __init__(self, *, version: str = "0.12.2") -> None:
        super().__init__("arbor")
        self.__version__ = version
        self.explicit_calls: list[str] = []
        self.cv_data_calls = 0

        def cv_policy_explicit(locset: str) -> FakePolicy:
            self.explicit_calls.append(str(locset))
            return FakePolicy(str(locset))

        def cv_data(description: FakeDescription) -> Any:
            self.cv_data_calls += 1
            return SimpleNamespace(num_cv=int(description.cv_count))

        self.cv_policy_explicit = cv_policy_explicit
        self.cv_data = cv_data


class FakeIndex:
    def __init__(
        self,
        node_to_segment: dict[int, int],
        segment_locations: dict[int, tuple[int, float, float]],
    ) -> None:
        self.node_to_segment = dict(node_to_segment)
        self.segment_locations = dict(segment_locations)
        self.validated: list[str] = []

    def validate_locset(self, locset: str) -> list[object]:
        self.validated.append(str(locset))
        return [object()]

    def locset_for_node(self, node_id: int) -> str:
        segment_id = int(self.node_to_segment[int(node_id)])
        branch, _prox, distal = self.segment_locations[segment_id]
        return f"(location {int(branch)} {float(distal):g})"


def _runner(
    *,
    cv_count: int = 2,
    cross_branch: bool = False,
    root_stub: bool = False,
) -> ModuleType:
    runner = ModuleType(STAGING_RUNNER_MODULE)
    nodes = [Node(1, -1, 0.0), Node(2, 1, 10.0), Node(3, 2, 20.0)]
    layout = Layout(
        section_paths=((1, 2, 3),) if cross_branch else ((1, 2), (2, 3)),
        section_nseg=(1,) if cross_branch else (1, 1),
        node_to_cv={1: (0, 0), 2: (0, 0), 3: ((0 if cross_branch else 1), 0)},
    )

    def legacy_neuron_cv_layout(nodes, *, nseg_um=40.0):
        assert float(nseg_um) == 40.0
        return layout

    def soma_node(nodes):
        return nodes[1]

    class _DigiflyRecipe:
        def __new__(cls, A, *args, **kwargs):
            config = args[0]

            class Recipe:
                def __init__(self) -> None:
                    self.cfg = config
                    self.cells = {0: SimpleNamespace(nodes=nodes, neuron_id=10000)}
                    self.indices = {
                        0: FakeIndex(
                            {1: 0, 2: 1, 3: 2},
                            {
                                0: (0, 0.0, 0.0),
                                1: (0, (0.1 if root_stub else 0.0), 0.5),
                                2: ((1 if cross_branch else 0), 0.5, 1.0),
                            },
                        )
                    }
                    self.locsets = {0: {"soma": "(location 0 0.25)", "siz": "(location 0 1)"}}
                    self.probe_specs = {
                        0: [
                            {
                                "tag": "soma_v",
                                "locset": "(location 0 0.25)",
                                "source": "record.voltage_locations.soma",
                            },
                            {
                                "tag": "node3_v",
                                "locset": "(location 0 1)",
                                "source": "record.node_voltage_probes",
                            },
                        ]
                    }
                    self.diagnostics: dict[str, Any] = {}

                def cell_description(self, gid):
                    return FakeDescription(cv_count)

            return Recipe()

    runner.legacy_neuron_cv_layout = legacy_neuron_cv_layout
    runner.soma_node = soma_node
    runner._DigiflyRecipe = _DigiflyRecipe
    return runner


def _config(**updates: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "metadata": {"app_recipe": EXPECTED_APP_RECIPE},
        "arbor": {
            "cv_policy": CV_POLICY_TOKEN,
            "legacy_section_nseg_um": 40.0,
        },
        "swc_section_nseg_um": 40.0,
    }
    config.update(updates)
    return config


def test_bridge_installs_explicit_policy_and_native_legacy_soma_site() -> None:
    arbor = FakeArbor()
    runner = _runner()
    first = install_arbor_legacy_cv_bridge(arbor_module=arbor, runner_module=runner)
    second = install_arbor_legacy_cv_bridge(arbor_module=arbor, runner_module=runner)
    assert second is first
    assert first.metadata["topology_equivalence_claim"] is False
    assert first.metadata["zero_area_fork_cv_caveat"] is True
    assert first.metadata["staged_root_stub_caveat"] is True
    assert first.metadata["bridge_revision"] == BRIDGE_REVISION
    assert first.metadata["locset_join_strategy"] == LOCSET_JOIN_STRATEGY

    recipe = runner._DigiflyRecipe(arbor, _config())
    # Node 2 owns the incoming section (1, 2), where native SWCCell records it
    # at x=1.0. On the staged Arbor branch that is location 0.5, not midpoint
    # location 0.25 and not an invented grouped-section x=0.5 rule.
    assert recipe.locsets[0]["soma"] == "(location 0 0.5)"
    assert recipe.probe_specs[0][0]["locset"] == "(location 0 0.5)"
    assert recipe.probe_specs[0][1]["locset"] == "(location 0 1)"
    layout = recipe._digifly_legacy_cell_layouts[0]
    assert layout.soma_section_x == 1.0
    assert layout.legacy_membrane_cv_count == 2
    assert layout.legacy_fork_node_count == 0
    assert layout.legacy_root_stub_cv_count == 0
    assert layout.expected_candidate_arbor_cv_count == 2
    assert layout.locset_join_depth == 2
    assert "(location 0 0)" in layout.boundary_locset
    assert "(location 0 0.5)" in layout.boundary_locset
    assert "(location 0 1)" in layout.boundary_locset

    description = recipe.cell_description(0)
    assert description.policy is layout.policy
    assert arbor.cv_data_calls == 1
    # The deterministic candidate count is validated once even when Arbor
    # requests the same cell description again.
    recipe.cell_description(0)
    assert arbor.cv_data_calls == 1
    diagnostics = recipe.diagnostics["legacy_neuron_cv_bridge"]
    assert diagnostics["topology_equivalence_claim"] is False
    assert diagnostics["bridge_revision"] == BRIDGE_REVISION
    assert diagnostics["locset_join_strategy"] == LOCSET_JOIN_STRATEGY
    assert diagnostics["cells"]["0"]["locset_join_depth"] == 2
    assert diagnostics["cells"]["0"]["actual_arbor_total_cv_count"] == 2


def test_explicit_boundary_join_is_binary_and_logarithmically_balanced() -> None:
    locations = tuple((0, index / 4.0) for index in range(5))
    expression = _join_locset(locations)

    assert expression.startswith("(join (join")
    assert expression.count("(join ") == len(locations) - 1
    assert all(f"(location 0 {index / 4.0:.17g})" in expression for index in range(5))
    assert _balanced_join_depth(len(locations)) == 3
    assert _balanced_join_depth(1) == 0
    with pytest.raises(ArborLegacyCVBridgeError, match="empty"):
        _balanced_join_depth(0)


def test_root_soma_uses_native_section_x_zero_not_midpoint() -> None:
    arbor = FakeArbor()
    runner = _runner()
    nodes = [Node(1, -1, 0.0), Node(2, 1, 10.0)]

    def root_soma(_nodes):
        return nodes[0]

    def one_section(_nodes, *, nseg_um=40.0):
        return Layout(((1, 2),), (1,), {1: (0, 0), 2: (0, 0)})

    runner.soma_node = root_soma
    runner.legacy_neuron_cv_layout = one_section
    cell = SimpleNamespace(nodes=nodes, neuron_id=1)
    index = FakeIndex({1: 0, 2: 1}, {0: (0, 0.0, 0.0), 1: (0, 0.0, 1.0)})
    layout = build_legacy_cell_layout(
        arbor_module=arbor,
        runner_module=runner,
        cell=cell,
        index=index,
    )
    assert layout.soma_section_x == 0.0
    assert layout.soma_locset == "(location 0 0)"


def test_candidate_count_includes_normalized_arbor_root_stub() -> None:
    arbor = FakeArbor()
    runner = _runner(cv_count=3, root_stub=True)
    install_arbor_legacy_cv_bridge(arbor_module=arbor, runner_module=runner)
    recipe = runner._DigiflyRecipe(arbor, _config())
    layout = recipe._digifly_legacy_cell_layouts[0]
    assert layout.legacy_membrane_cv_count == 2
    assert layout.legacy_fork_node_count == 0
    assert layout.legacy_root_stub_cv_count == 1
    assert layout.expected_candidate_arbor_cv_count == 3
    recipe.cell_description(0)


def test_bridge_fails_closed_on_cross_branch_or_candidate_count_mismatch() -> None:
    arbor = FakeArbor()
    runner = _runner(cross_branch=True)
    install_arbor_legacy_cv_bridge(arbor_module=arbor, runner_module=runner)
    with pytest.raises(ArborLegacyCVBridgeError, match="spans Arbor branches"):
        runner._DigiflyRecipe(arbor, _config())

    arbor = FakeArbor()
    runner = _runner(cv_count=3)
    install_arbor_legacy_cv_bridge(arbor_module=arbor, runner_module=runner)
    recipe = runner._DigiflyRecipe(arbor, _config())
    with pytest.raises(ArborLegacyCVBridgeError, match="candidate mismatch"):
        recipe.cell_description(0)


def test_bridge_rejects_version_signature_and_opt_in_drift() -> None:
    with pytest.raises(ArborLegacyCVBridgeError, match="requires Arbor 0.12.2"):
        install_arbor_legacy_cv_bridge(
            arbor_module=FakeArbor(version="0.13.0"),
            runner_module=_runner(),
        )

    bad_runner = _runner()

    def changed_recipe(A, config):
        return None

    bad_runner._DigiflyRecipe = changed_recipe
    with pytest.raises(ArborLegacyCVBridgeError, match="signature changed"):
        install_arbor_legacy_cv_bridge(
            arbor_module=FakeArbor(),
            runner_module=bad_runner,
        )

    arbor = FakeArbor()
    runner = _runner()
    install_arbor_legacy_cv_bridge(arbor_module=arbor, runner_module=runner)
    bad_config = _config()
    bad_config["arbor"] = {"cv_policy": "every_segment"}
    with pytest.raises(ArborLegacyCVBridgeError, match="opt in"):
        runner._DigiflyRecipe(arbor, bad_config)
