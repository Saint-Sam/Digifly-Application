"""Fail-closed legacy NEURON discretization bridge for the Arbor runner.

The staged Phase 2 Arbor runner is input-only.  It already reconstructs the
legacy ``SWCCell`` branch sections for channel-painting diagnostics, but its
cell descriptions still use an Arbor-native CV policy and its named ``soma``
location is the widest type-1 SWC sample.  The native NEURON workflow instead
uses odd ``nseg`` values on grouped branch sections and stimulates/records at
the selected soma node's coordinate within its owning grouped section.

This module applies one narrow runtime hook to the staged runner's
``_DigiflyRecipe`` factory.  For the explicitly opted-in Escape-SIZ recipe it:

* translates every legacy section boundary (and every internal ``nseg``
  boundary) to an Arbor branch location and installs ``cv_policy_explicit``;
* replaces only the named ``soma`` locset with the native legacy
  ``SWCCell.soma_site()`` coordinate; and
* updates named soma probe specifications before Arbor constructs the cell.

The segment tree, morphology, mechanisms, synapse/gap sites, and all explicit
node/XYZ probes are left untouched.  There is no fallback to an Arbor-native
CV policy if translation or installation fails.  This is a deterministic
legacy-section-boundary compatibility candidate, not a topology-equivalence
claim: Arbor retains the staged tree's tiny root stub and represents explicit
fork boundaries with zero-area fork CVs.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
import inspect
import math
from types import ModuleType
from typing import Any, Callable, Mapping, Sequence
import threading
import weakref


SUPPORTED_ARBOR_VERSION = "0.12.2"
STAGING_RUNNER_MODULE = "digifly.phase2.arbor_build.runner"
EXPECTED_APP_RECIPE = "ablation_notebook_arbor_exact_gap_v2"
CV_POLICY_TOKEN = "legacy_neuron_section_explicit"
LEGACY_NSEG_UM = 40.0
BRIDGE_REVISION = "balanced_locset_v2"
LOCSET_JOIN_STRATEGY = "balanced_binary"


class ArborLegacyCVBridgeError(RuntimeError):
    """The legacy-boundary compatibility bridge could not be applied safely."""


@dataclass(frozen=True)
class ArborLegacyCVBridge:
    """Installed bridge identity and JSON-safe provenance."""

    arbor_version: str
    runner_module: str
    app_recipe: str
    cv_policy: str
    legacy_section_nseg_um: float

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "status": "installed",
            "arbor_version": self.arbor_version,
            "runner_module": self.runner_module,
            "app_recipe": self.app_recipe,
            "cv_policy": self.cv_policy,
            "bridge_revision": BRIDGE_REVISION,
            "locset_join_strategy": LOCSET_JOIN_STRATEGY,
            "legacy_section_nseg_um": self.legacy_section_nseg_um,
            "soma_location": "native_legacy_swc_cell_soma_site",
            "morphology_source": "staged_segment_tree_unchanged",
            "compatibility_class": "legacy_section_boundary_candidate",
            "topology_equivalence_claim": False,
            "zero_area_fork_cv_caveat": True,
            "staged_root_stub_caveat": True,
            "fallback_cv_policy": None,
        }


@dataclass(frozen=True)
class LegacyCellLayout:
    """Translated policy and named-soma location for one staged cell."""

    policy: Any
    boundary_locset: str
    soma_locset: str
    legacy_section_count: int
    legacy_membrane_cv_count: int
    explicit_boundary_count: int
    locset_join_depth: int
    legacy_fork_node_count: int
    legacy_root_stub_cv_count: int
    expected_candidate_arbor_cv_count: int
    soma_section_id: int
    soma_section_nseg: int
    soma_section_x: float


@dataclass
class _InstalledState:
    bridge: ArborLegacyCVBridge
    arbor_module: ModuleType
    runner_module: ModuleType
    original_recipe_factory: Callable[..., Any]
    recipe_factory: Callable[..., Any]


_INSTALL_LOCK = threading.RLock()
_RUNNER_INSTALLS: "weakref.WeakKeyDictionary[ModuleType, _InstalledState]" = (
    weakref.WeakKeyDictionary()
)


def install_arbor_legacy_cv_bridge(
    *,
    arbor_module: ModuleType,
    runner_module: ModuleType,
) -> ArborLegacyCVBridge:
    """Install the opt-in legacy section/CV and soma-placement bridge.

    Repeated installation on the same modules is idempotent.  Version drift,
    staged-runner API drift, a modified installed hook, or an unsupported
    morphology/layout raises :class:`ArborLegacyCVBridgeError`.
    """

    with _INSTALL_LOCK:
        existing = _RUNNER_INSTALLS.get(runner_module)
        if existing is not None:
            if existing.arbor_module is not arbor_module:
                raise ArborLegacyCVBridgeError(
                    "The staging runner is already bridged to another Arbor module."
                )
            if getattr(runner_module, "_DigiflyRecipe", None) is not existing.recipe_factory:
                raise ArborLegacyCVBridgeError(
                    "The installed staging-runner legacy-CV hook was modified."
                )
            return existing.bridge

        _validate_arbor_module(arbor_module)
        original_recipe_factory = _validate_runner_module(runner_module)
        recipe_factory = _recipe_factory_wrapper(
            arbor_module,
            runner_module,
            original_recipe_factory,
        )
        bridge = ArborLegacyCVBridge(
            arbor_version=str(arbor_module.__version__),
            runner_module=STAGING_RUNNER_MODULE,
            app_recipe=EXPECTED_APP_RECIPE,
            cv_policy=CV_POLICY_TOKEN,
            legacy_section_nseg_um=LEGACY_NSEG_UM,
        )
        state = _InstalledState(
            bridge=bridge,
            arbor_module=arbor_module,
            runner_module=runner_module,
            original_recipe_factory=original_recipe_factory,
            recipe_factory=recipe_factory,
        )
        try:
            runner_module._DigiflyRecipe = recipe_factory
        except Exception as exc:
            raise ArborLegacyCVBridgeError(
                "Could not install the staging-runner legacy-CV recipe hook."
            ) from exc
        _RUNNER_INSTALLS[runner_module] = state
        return bridge


def build_legacy_cell_layout(
    *,
    arbor_module: ModuleType,
    runner_module: ModuleType,
    cell: Any,
    index: Any,
    nseg_um: float = LEGACY_NSEG_UM,
) -> LegacyCellLayout:
    """Translate one staged cell's reconstructed NEURON layout to Arbor."""

    if not math.isfinite(float(nseg_um)) or float(nseg_um) != LEGACY_NSEG_UM:
        raise ArborLegacyCVBridgeError(
            f"The validated legacy section target is exactly {LEGACY_NSEG_UM:g} um; "
            f"found {nseg_um!r}."
        )
    nodes = list(getattr(cell, "nodes", ()) or ())
    if not nodes:
        raise ArborLegacyCVBridgeError("The staged cell has no SWC nodes.")
    try:
        layout = runner_module.legacy_neuron_cv_layout(nodes, nseg_um=float(nseg_um))
        selected_soma = runner_module.soma_node(nodes)
    except Exception as exc:
        raise ArborLegacyCVBridgeError(
            "The staged runner could not reconstruct the legacy NEURON section layout."
        ) from exc

    paths = tuple(tuple(int(value) for value in path) for path in layout.section_paths)
    nsegs = tuple(int(value) for value in layout.section_nseg)
    if not paths or len(paths) != len(nsegs):
        raise ArborLegacyCVBridgeError(
            "The reconstructed legacy section paths and nseg values are inconsistent."
        )

    boundary_locations: list[tuple[int, float]] = []
    for section_id, (path, nseg) in enumerate(zip(paths, nsegs)):
        if len(path) < 2:
            raise ArborLegacyCVBridgeError(
                f"Legacy section {section_id} is a singleton; its synthetic NEURON geometry "
                "is not present in the staged Arbor morphology."
            )
        if nseg < 1 or nseg % 2 != 1:
            raise ArborLegacyCVBridgeError(
                f"Legacy section {section_id} has invalid odd nseg={nseg}."
            )
        for boundary_index in range(nseg + 1):
            boundary_locations.append(
                _section_fraction_location(
                    nodes=nodes,
                    index=index,
                    path=path,
                    fraction=float(boundary_index) / float(nseg),
                )
            )

    canonical_boundaries = _canonical_locations(boundary_locations)
    boundary_locset = _join_locset(canonical_boundaries)
    try:
        policy = arbor_module.cv_policy_explicit(boundary_locset)
    except Exception as exc:
        raise ArborLegacyCVBridgeError(
            "Arbor rejected the translated legacy NEURON CV boundary locset."
        ) from exc

    soma_id = int(selected_soma.node_id)
    try:
        soma_section_id = int(layout.node_to_cv[soma_id][0])
        soma_path = paths[soma_section_id]
        soma_section_nseg = nsegs[soma_section_id]
    except Exception as exc:
        raise ArborLegacyCVBridgeError(
            f"The selected soma node {soma_id} is not owned by a legacy section."
        ) from exc
    soma_section_x = _section_node_fraction(
        nodes=nodes,
        path=soma_path,
        node_id=soma_id,
    )
    # The staged MorphologyIndex is the authoritative node-coordinate mapping
    # used by the already validated Arbor runner.  Reconstructing the same
    # coordinate through the owning legacy section is unsafe for morphologies
    # with degenerate/root-edge remapping: it can produce a branch id that is
    # outside the normalized Arbor morphology (observed for 27502, 43758, and
    # 101549).  Native SWCCell.soma_site() and this node locset describe the
    # same SWC coordinate; retain the legacy section x value only as provenance.
    node_locset = getattr(index, "locset_for_node", None)
    if not callable(node_locset):
        raise ArborLegacyCVBridgeError(
            "The staged morphology index cannot resolve the native soma node locset."
        )
    try:
        soma_locset = str(node_locset(soma_id))
    except Exception as exc:
        raise ArborLegacyCVBridgeError(
            f"Could not map selected soma node {soma_id} through the staged morphology index."
        ) from exc

    node_ids = {int(node.node_id) for node in nodes}
    child_counts: dict[int, int] = {}
    for node in nodes:
        parent_id = int(node.parent_id)
        if parent_id in node_ids:
            child_counts[parent_id] = int(child_counts.get(parent_id, 0)) + 1
    legacy_fork_node_count = sum(1 for count in child_counts.values() if count > 1)
    root_ids = {
        int(node.node_id)
        for node in nodes
        if int(node.parent_id) == -1 or int(node.parent_id) not in node_ids
    }
    root_stub_locations = {
        _section_fraction_location(
            nodes=nodes,
            index=index,
            path=path,
            fraction=0.0,
        )
        for path in paths
        if path and int(path[0]) in root_ids
    }
    # Arbor's normalized segment tree can retain a tiny cable before the first
    # native grouped section. cv_policy_explicit adds the morphology boundary,
    # so each such uncovered root cable is one additional candidate CV.
    legacy_root_stub_cv_count = len(
        {
            (int(branch), round(float(position), 12))
            for branch, position in root_stub_locations
            if float(position) > 1e-12
        }
    )

    _validate_locset(index, boundary_locset, "legacy CV boundary")
    _validate_locset(index, soma_locset, "native legacy soma site")
    return LegacyCellLayout(
        policy=policy,
        boundary_locset=boundary_locset,
        soma_locset=soma_locset,
        legacy_section_count=len(paths),
        legacy_membrane_cv_count=sum(nsegs),
        explicit_boundary_count=len(canonical_boundaries),
        locset_join_depth=_balanced_join_depth(len(canonical_boundaries)),
        legacy_fork_node_count=legacy_fork_node_count,
        legacy_root_stub_cv_count=legacy_root_stub_cv_count,
        expected_candidate_arbor_cv_count=(
            sum(nsegs) + legacy_fork_node_count + legacy_root_stub_cv_count
        ),
        soma_section_id=soma_section_id,
        soma_section_nseg=soma_section_nseg,
        soma_section_x=soma_section_x,
    )


def _validate_arbor_module(arbor_module: ModuleType) -> None:
    version = str(getattr(arbor_module, "__version__", "unknown"))
    if version != SUPPORTED_ARBOR_VERSION:
        raise ArborLegacyCVBridgeError(
            f"The legacy-CV bridge requires Arbor {SUPPORTED_ARBOR_VERSION}; found {version}."
        )
    if not callable(getattr(arbor_module, "cv_policy_explicit", None)):
        raise ArborLegacyCVBridgeError("Arbor does not expose cv_policy_explicit().")
    if not callable(getattr(arbor_module, "cv_data", None)):
        raise ArborLegacyCVBridgeError("Arbor does not expose cv_data().")


def _validate_runner_module(runner_module: ModuleType) -> Callable[..., Any]:
    name = str(getattr(runner_module, "__name__", ""))
    if name != STAGING_RUNNER_MODULE:
        raise ArborLegacyCVBridgeError(
            f"Refusing to patch non-staging runner {name!r}; expected {STAGING_RUNNER_MODULE!r}."
        )
    required_signatures = {
        "_DigiflyRecipe": ("A", "args", "kwargs"),
        "legacy_neuron_cv_layout": ("nodes", "nseg_um"),
        "soma_node": ("nodes",),
    }
    for name, parameter_names in required_signatures.items():
        target = getattr(runner_module, name, None)
        if not callable(target):
            raise ArborLegacyCVBridgeError(
                f"The staging runner has no callable {name} hook."
            )
        try:
            signature = inspect.signature(target)
        except (TypeError, ValueError) as exc:
            raise ArborLegacyCVBridgeError(
                f"Could not inspect staging runner {name}."
            ) from exc
        found_names = tuple(signature.parameters)
        if found_names != parameter_names:
            raise ArborLegacyCVBridgeError(
                f"Staging runner {name} signature changed: {signature}."
            )
    return runner_module._DigiflyRecipe


def _recipe_factory_wrapper(
    arbor_module: ModuleType,
    runner_module: ModuleType,
    original_factory: Callable[..., Any],
) -> Callable[..., Any]:
    @functools.wraps(original_factory)
    def recipe_factory(A: ModuleType, *args: Any, **kwargs: Any) -> Any:
        if A is not arbor_module:
            raise ArborLegacyCVBridgeError(
                "The staging runner invoked the legacy-CV bridge with another Arbor module."
            )
        recipe = original_factory(A, *args, **kwargs)
        _configure_recipe(
            arbor_module=arbor_module,
            runner_module=runner_module,
            recipe=recipe,
        )
        return recipe

    return recipe_factory


def _configure_recipe(
    *,
    arbor_module: ModuleType,
    runner_module: ModuleType,
    recipe: Any,
) -> None:
    cfg = getattr(recipe, "cfg", None)
    if not isinstance(cfg, Mapping):
        raise ArborLegacyCVBridgeError("The staged recipe has no mapping-valued cfg.")
    metadata = dict(cfg.get("metadata") or {})
    arbor_cfg = dict(cfg.get("arbor") or {})
    if str(metadata.get("app_recipe") or "") != EXPECTED_APP_RECIPE:
        raise ArborLegacyCVBridgeError(
            "The legacy-CV bridge is restricted to the exact Escape-SIZ app recipe."
        )
    if str(arbor_cfg.get("cv_policy") or "") != CV_POLICY_TOKEN:
        raise ArborLegacyCVBridgeError(
            f"The exact Escape-SIZ recipe must opt in with arbor.cv_policy={CV_POLICY_TOKEN!r}."
        )
    nseg_um = cfg.get("swc_section_nseg_um", arbor_cfg.get("legacy_section_nseg_um"))
    if nseg_um is None or float(nseg_um) != LEGACY_NSEG_UM:
        raise ArborLegacyCVBridgeError(
            f"The exact Escape-SIZ recipe requires swc_section_nseg_um={LEGACY_NSEG_UM:g}."
        )

    cells = getattr(recipe, "cells", None)
    indices = getattr(recipe, "indices", None)
    locsets = getattr(recipe, "locsets", None)
    probe_specs = getattr(recipe, "probe_specs", None)
    if not all(isinstance(value, Mapping) for value in (cells, indices, locsets, probe_specs)):
        raise ArborLegacyCVBridgeError(
            "The staged recipe cell/index/locset/probe interface changed."
        )

    translated: dict[int, LegacyCellLayout] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for raw_gid, cell in cells.items():
        gid = int(raw_gid)
        if gid not in indices or gid not in locsets or gid not in probe_specs:
            raise ArborLegacyCVBridgeError(
                f"The staged recipe omitted index/locset/probe data for gid={gid}."
            )
        layout = build_legacy_cell_layout(
            arbor_module=arbor_module,
            runner_module=runner_module,
            cell=cell,
            index=indices[gid],
            nseg_um=float(nseg_um),
        )
        translated[gid] = layout
        locsets[gid]["soma"] = layout.soma_locset
        soma_probe_count = 0
        for spec in probe_specs[gid]:
            source = str(spec.get("source") or "")
            if source == "record.voltage_locations.soma":
                spec["locset"] = layout.soma_locset
                soma_probe_count += 1
        diagnostics[str(gid)] = {
            "neuron_id": int(getattr(cell, "neuron_id")),
            "cv_policy": CV_POLICY_TOKEN,
            "legacy_section_nseg_um": float(nseg_um),
            "legacy_section_count": layout.legacy_section_count,
            "legacy_membrane_cv_count": layout.legacy_membrane_cv_count,
            "explicit_boundary_count": layout.explicit_boundary_count,
            "locset_join_strategy": LOCSET_JOIN_STRATEGY,
            "locset_join_depth": layout.locset_join_depth,
            "legacy_fork_node_count": layout.legacy_fork_node_count,
            "legacy_root_stub_cv_count": layout.legacy_root_stub_cv_count,
            "expected_candidate_arbor_cv_count": layout.expected_candidate_arbor_cv_count,
            "soma_section_id": layout.soma_section_id,
            "soma_section_nseg": layout.soma_section_nseg,
            "soma_section_x": layout.soma_section_x,
            "soma_locset": layout.soma_locset,
            "named_soma_probe_count": soma_probe_count,
        }

    recipe._digifly_legacy_cell_layouts = translated
    recipe._digifly_legacy_validated_gids = set()
    recipe_class = type(recipe)
    original_cell_description = getattr(recipe_class, "cell_description", None)
    if not callable(original_cell_description):
        raise ArborLegacyCVBridgeError(
            "The staged recipe class has no callable cell_description."
        )
    try:
        signature = inspect.signature(original_cell_description)
    except (TypeError, ValueError) as exc:
        raise ArborLegacyCVBridgeError(
            "Could not inspect staged recipe cell_description."
        ) from exc
    if tuple(signature.parameters) != ("self", "gid"):
        raise ArborLegacyCVBridgeError(
            f"Staged recipe cell_description signature changed: {signature}."
        )

    @functools.wraps(original_cell_description)
    def cell_description(self: Any, gid: int) -> Any:
        description = original_cell_description(self, gid)
        try:
            layout = self._digifly_legacy_cell_layouts[int(gid)]
            setter = getattr(description, "discretization")
            setter(layout.policy)
            if int(gid) not in self._digifly_legacy_validated_gids:
                cv_data = arbor_module.cv_data(description)
                actual_cv_count = int(cv_data.num_cv)
                if actual_cv_count != int(layout.expected_candidate_arbor_cv_count):
                    raise ArborLegacyCVBridgeError(
                        f"Legacy-boundary candidate mismatch for gid={int(gid)}: "
                        f"expected {layout.expected_candidate_arbor_cv_count} Arbor CVs "
                        f"({layout.legacy_membrane_cv_count} membrane plus "
                        f"{layout.legacy_fork_node_count} zero-area fork CVs plus "
                        f"{layout.legacy_root_stub_cv_count} staged root-stub CVs), "
                        f"found {actual_cv_count}."
                    )
                self._digifly_legacy_validated_gids.add(int(gid))
                self.diagnostics["legacy_neuron_cv_bridge"]["cells"][str(int(gid))][
                    "actual_arbor_total_cv_count"
                ] = actual_cv_count
        except Exception as exc:
            if isinstance(exc, ArborLegacyCVBridgeError):
                raise
            raise ArborLegacyCVBridgeError(
                f"Could not install the explicit legacy CV policy for gid={int(gid)}; "
                "no Arbor-native fallback was used."
            ) from exc
        return description

    recipe_class.cell_description = cell_description
    recipe._digifly_original_cell_description = original_cell_description
    recipe_diagnostics = getattr(recipe, "diagnostics", None)
    if not isinstance(recipe_diagnostics, dict):
        raise ArborLegacyCVBridgeError("The staged recipe diagnostics interface changed.")
    recipe_diagnostics["legacy_neuron_cv_bridge"] = {
        "status": "installed",
        "cv_policy": CV_POLICY_TOKEN,
        "bridge_revision": BRIDGE_REVISION,
        "locset_join_strategy": LOCSET_JOIN_STRATEGY,
        "legacy_section_nseg_um": float(nseg_um),
        "soma_location": "native_legacy_swc_cell_soma_site",
        "compatibility_class": "legacy_section_boundary_candidate",
        "topology_equivalence_claim": False,
        "zero_area_fork_cv_caveat": True,
        "staged_root_stub_caveat": True,
        "fallback_cv_policy": None,
        "cells": diagnostics,
    }


def _section_fraction_location(
    *,
    nodes: Sequence[Any],
    index: Any,
    path: Sequence[int],
    fraction: float,
) -> tuple[int, float]:
    if not math.isfinite(float(fraction)) or not 0.0 <= float(fraction) <= 1.0:
        raise ArborLegacyCVBridgeError(f"Invalid legacy section fraction {fraction!r}.")
    by_id = {int(node.node_id): node for node in nodes}
    lengths: list[float] = []
    segments: list[tuple[int, float, float]] = []
    for left_id, right_id in zip(path[:-1], path[1:]):
        if int(left_id) not in by_id or int(right_id) not in by_id:
            raise ArborLegacyCVBridgeError(
                f"Legacy section path references missing edge {left_id}->{right_id}."
            )
        try:
            segment_id = int(index.node_to_segment[int(right_id)])
            branch, prox, distal = index.segment_locations[segment_id]
        except Exception as exc:
            raise ArborLegacyCVBridgeError(
                f"Could not map legacy section edge {left_id}->{right_id} to Arbor."
            ) from exc
        branch = int(branch)
        prox = float(prox)
        distal = float(distal)
        if not (
            math.isfinite(prox)
            and math.isfinite(distal)
            and -1e-12 <= prox <= distal + 1e-12
            and distal <= 1.0 + 1e-12
        ):
            raise ArborLegacyCVBridgeError(
                f"Invalid Arbor segment interval for edge {left_id}->{right_id}: "
                f"branch={branch}, prox={prox}, distal={distal}."
            )
        left = by_id[int(left_id)]
        right = by_id[int(right_id)]
        length = math.dist(
            (float(left.x), float(left.y), float(left.z)),
            (float(right.x), float(right.y), float(right.z)),
        )
        lengths.append(float(length))
        segments.append((branch, prox, distal))

    if not segments or sum(lengths) <= 1e-12:
        raise ArborLegacyCVBridgeError(
            f"Legacy section {tuple(path)} has no representable cable length."
        )
    branches = {segment[0] for segment in segments}
    if len(branches) != 1:
        raise ArborLegacyCVBridgeError(
            f"Legacy section {tuple(path)} spans Arbor branches {sorted(branches)}."
        )
    for previous, current in zip(segments[:-1], segments[1:]):
        if abs(float(previous[2]) - float(current[1])) > 1e-9:
            raise ArborLegacyCVBridgeError(
                f"Legacy section {tuple(path)} is not contiguous on Arbor branch {previous[0]}."
            )

    total = float(sum(lengths))
    target = float(fraction) * total
    traversed = 0.0
    for edge_index, (length, segment) in enumerate(zip(lengths, segments)):
        if target <= traversed + length + 1e-12 or edge_index == len(segments) - 1:
            alpha = 0.0 if length <= 1e-12 else (target - traversed) / length
            alpha = min(1.0, max(0.0, float(alpha)))
            branch, prox, distal = segment
            return int(branch), float(prox + alpha * (distal - prox))
        traversed += length
    raise ArborLegacyCVBridgeError(
        f"Could not translate legacy section fraction {fraction} on {tuple(path)}."
    )


def _section_node_fraction(
    *,
    nodes: Sequence[Any],
    path: Sequence[int],
    node_id: int,
) -> float:
    """Return the native ``SWCCell._xloc_for_node`` value on one section."""

    normalized_path = tuple(int(value) for value in path)
    try:
        path_index = normalized_path.index(int(node_id))
    except ValueError as exc:
        raise ArborLegacyCVBridgeError(
            f"Legacy soma section {normalized_path} does not contain soma node {int(node_id)}."
        ) from exc
    by_id = {int(node.node_id): node for node in nodes}
    arcs = [0.0]
    for left_id, right_id in zip(normalized_path[:-1], normalized_path[1:]):
        left = by_id[int(left_id)]
        right = by_id[int(right_id)]
        arcs.append(
            arcs[-1]
            + math.dist(
                (float(left.x), float(left.y), float(left.z)),
                (float(right.x), float(right.y), float(right.z)),
            )
        )
    if arcs[-1] <= 1e-12:
        raise ArborLegacyCVBridgeError(
            f"Legacy soma section {normalized_path} has no representable cable length."
        )
    return float(arcs[path_index] / arcs[-1])


def _canonical_locations(locations: Sequence[tuple[int, float]]) -> list[tuple[int, float]]:
    unique: dict[tuple[int, float], tuple[int, float]] = {}
    for branch, position in locations:
        position = min(1.0, max(0.0, float(position)))
        if abs(position) <= 1e-12:
            position = 0.0
        elif abs(1.0 - position) <= 1e-12:
            position = 1.0
        # Shared boundaries are obtained through independent cumulative sums.
        # Canonicalize more tightly than the staged runner's own 9-digit
        # locsets so floating-point ghosts cannot become tiny extra CVs.
        position = round(position, 12)
        key = (int(branch), position)
        unique[key] = key
    return sorted(unique.values(), key=lambda value: (value[0], value[1]))


def _location_expression(branch: int, position: float) -> str:
    return f"(location {int(branch)} {float(position):.17g})"


def _join_locset(locations: Sequence[tuple[int, float]]) -> str:
    expressions = [_location_expression(branch, position) for branch, position in locations]
    if not expressions:
        raise ArborLegacyCVBridgeError("The translated legacy CV boundary locset is empty.")
    # Arbor 0.12.2 recursively lowers an n-ary join into a deep binary locset
    # expression. Thousands of boundaries can therefore exhaust the native
    # stack while a recipe is cloned/lowered. Pair adjacent leaves in layers
    # instead: the resolved location multiset is unchanged, while expression
    # depth is bounded by ceil(log2(N)).
    while len(expressions) > 1:
        next_layer: list[str] = []
        for index in range(0, len(expressions), 2):
            left = expressions[index]
            if index + 1 == len(expressions):
                next_layer.append(left)
            else:
                next_layer.append(f"(join {left} {expressions[index + 1]})")
        expressions = next_layer
    return expressions[0]


def _balanced_join_depth(location_count: int) -> int:
    if int(location_count) < 1:
        raise ArborLegacyCVBridgeError("The translated legacy CV boundary locset is empty.")
    return int(math.ceil(math.log2(int(location_count)))) if int(location_count) > 1 else 0


def _validate_locset(index: Any, locset: str, label: str) -> None:
    validator = getattr(index, "validate_locset", None)
    if not callable(validator):
        raise ArborLegacyCVBridgeError(
            f"The staged morphology index cannot validate the {label} locset."
        )
    try:
        locations = list(validator(str(locset)))
    except Exception as exc:
        raise ArborLegacyCVBridgeError(
            f"The translated {label} locset is invalid on the staged morphology."
        ) from exc
    if not locations:
        raise ArborLegacyCVBridgeError(
            f"The translated {label} locset resolved to no Arbor locations."
        )
