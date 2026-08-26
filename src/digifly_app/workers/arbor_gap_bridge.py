"""Fail-closed bridge from the staged Arbor runner to Digifly gap models.

The Phase 2 Arbor runner is deliberately treated as input code.  This module
loads the app-owned mechanism catalogue and applies the two narrow runtime
hooks needed by that runner:

* every new ``neuron_cable_properties`` catalogue receives the custom
  mechanisms under the ``digifly_`` prefix; and
* only the runner's ``_make_junction`` function is replaced.

The runner's gap-connection construction is left alone, so its connection
weight remains exactly ``1.0``.  There is intentionally no fallback to
Arbor's built-in ``gj`` mechanism.
"""

from __future__ import annotations

from dataclasses import dataclass
import functools
import hashlib
import inspect
import math
from pathlib import Path
import threading
from types import ModuleType
from typing import Any, Callable, Mapping
import weakref


SUPPORTED_ARBOR_VERSION = "0.12.2"
CATALOGUE_PREFIX = "digifly_"
STAGING_RUNNER_MODULE = "digifly.phase2.arbor_build.runner"

_EXPECTED_CATALOGUE_PARAMETERS: dict[str, frozenset[str]] = {
    "gap": frozenset({"g"}),
    "rect_gap": frozenset({"gmax"}),
    "hetero_rect_gap": frozenset(
        {
            "gmax_open",
            "gmax_closed",
            "orientation",
            "vhalf",
            "vslope",
            "empirical_residual_frac",
            "tau_open_ms",
            "tau_close_ms",
        }
    ),
}

_MODE_ALIASES = {
    "gap": "gap",
    "ohmic": "gap",
    "rect_gap": "rect_gap",
    "rectgap": "rect_gap",
    "rectifying": "rect_gap",
    "hetero_rect_gap": "hetero_rect_gap",
    "heterorectgap": "hetero_rect_gap",
    "heterotypic_rectifying": "hetero_rect_gap",
}

_FORWARD_DIRECTIONS = frozenset({"pre_to_post", "a_to_b", "forward", "source_to_target"})
_REVERSE_DIRECTIONS = frozenset({"post_to_pre", "b_to_a", "reverse", "target_to_source"})
_SYMMETRIC_DIRECTIONS = frozenset({"symmetric", "bidirectional", "both"})


class ArborGapBridgeError(RuntimeError):
    """The custom gap bridge could not be installed or used exactly."""


@dataclass(frozen=True)
class ArborGapBridge:
    """Installed bridge identity and provenance."""

    arbor_version: str
    catalogue_path: str
    catalogue_sha256: str
    catalogue_prefix: str
    runner_module: str
    mechanisms: tuple[str, ...]

    @property
    def metadata(self) -> dict[str, Any]:
        """Return a JSON-safe description suitable for run provenance."""

        return {
            "status": "installed",
            "arbor_version": self.arbor_version,
            "catalogue_path": self.catalogue_path,
            "catalogue_sha256": self.catalogue_sha256,
            "catalogue_prefix": self.catalogue_prefix,
            "runner_module": self.runner_module,
            "mechanisms": list(self.mechanisms),
            "connection_weight": 1.0,
            "fallback_mechanism": None,
        }


@dataclass
class _InstalledState:
    bridge: ArborGapBridge
    arbor_module: ModuleType
    runner_module: ModuleType
    original_properties_factory: Callable[..., Any]
    properties_factory: Callable[..., Any]
    original_make_junction: Callable[..., Any]
    make_junction: Callable[..., Any]


_INSTALL_LOCK = threading.RLock()
_RUNNER_INSTALLS: "weakref.WeakKeyDictionary[ModuleType, _InstalledState]" = (
    weakref.WeakKeyDictionary()
)
_ARBOR_INSTALLS: "weakref.WeakKeyDictionary[ModuleType, _InstalledState]" = (
    weakref.WeakKeyDictionary()
)


def install_arbor_gap_bridge(
    *,
    arbor_module: ModuleType,
    runner_module: ModuleType,
    catalogue_path: str | Path,
) -> ArborGapBridge:
    """Load, validate, and install the app-owned Arbor gap catalogue.

    Repeating the call with the same modules and resolved catalogue path is
    idempotent.  A changed path, a modified hook, a different Arbor version,
    or an unexpected staging-runner API raises :class:`ArborGapBridgeError`.
    Installation never substitutes ``gj`` when custom loading or placement
    fails.
    """

    resolved_catalogue = Path(catalogue_path).expanduser().resolve()
    with _INSTALL_LOCK:
        existing = _RUNNER_INSTALLS.get(runner_module)
        if existing is not None:
            _assert_unchanged_install(
                existing,
                arbor_module=arbor_module,
                runner_module=runner_module,
                catalogue_path=resolved_catalogue,
            )
            return existing.bridge
        if arbor_module in _ARBOR_INSTALLS:
            raise ArborGapBridgeError(
                "This Arbor module is already owned by a different gap-bridge installation."
            )

        _validate_arbor_module(arbor_module)
        original_make_junction = _validate_runner_module(runner_module)
        original_properties_factory = getattr(arbor_module, "neuron_cable_properties", None)
        if not callable(original_properties_factory):
            raise ArborGapBridgeError("Arbor does not expose neuron_cable_properties().")
        if not resolved_catalogue.is_file():
            raise ArborGapBridgeError(
                f"The compiled Digifly Arbor catalogue does not exist: {resolved_catalogue}"
            )

        try:
            custom_catalogue = arbor_module.load_catalogue(str(resolved_catalogue))
        except Exception as exc:
            raise ArborGapBridgeError(
                f"Arbor could not load the Digifly gap catalogue: {resolved_catalogue}"
            ) from exc
        _validate_catalogue(custom_catalogue)

        properties_factory = _properties_factory_wrapper(
            original_properties_factory,
            custom_catalogue,
        )
        # Probe the exact extension operation before mutating either module.
        # The wrapper repeats this for every properties object made later.
        try:
            properties_factory()
        except Exception as exc:
            if isinstance(exc, ArborGapBridgeError):
                raise
            raise ArborGapBridgeError(
                "The Digifly catalogue could not be extended into fresh Arbor cable properties."
            ) from exc

        make_junction = _make_junction_wrapper(arbor_module)
        bridge = ArborGapBridge(
            arbor_version=str(arbor_module.__version__),
            catalogue_path=str(resolved_catalogue),
            catalogue_sha256=_sha256(resolved_catalogue),
            catalogue_prefix=CATALOGUE_PREFIX,
            runner_module=STAGING_RUNNER_MODULE,
            mechanisms=tuple(sorted(_EXPECTED_CATALOGUE_PARAMETERS)),
        )
        state = _InstalledState(
            bridge=bridge,
            arbor_module=arbor_module,
            runner_module=runner_module,
            original_properties_factory=original_properties_factory,
            properties_factory=properties_factory,
            original_make_junction=original_make_junction,
            make_junction=make_junction,
        )

        try:
            arbor_module.neuron_cable_properties = properties_factory
            runner_module._make_junction = make_junction
        except Exception as exc:
            # Restore the first assignment if the second one fails. Loading a
            # shared catalogue itself is process-global and cannot be undone,
            # but no approximate runner behavior is left installed.
            arbor_module.neuron_cable_properties = original_properties_factory
            runner_module._make_junction = original_make_junction
            raise ArborGapBridgeError("Could not install the Digifly Arbor gap hooks.") from exc

        _RUNNER_INSTALLS[runner_module] = state
        _ARBOR_INSTALLS[arbor_module] = state
        return bridge


def _assert_unchanged_install(
    state: _InstalledState,
    *,
    arbor_module: ModuleType,
    runner_module: ModuleType,
    catalogue_path: Path,
) -> None:
    if state.arbor_module is not arbor_module:
        raise ArborGapBridgeError("The staging runner is already bridged to another Arbor module.")
    if Path(state.bridge.catalogue_path) != catalogue_path:
        raise ArborGapBridgeError(
            "The staging runner is already bridged to a different compiled catalogue."
        )
    if getattr(arbor_module, "neuron_cable_properties", None) is not state.properties_factory:
        raise ArborGapBridgeError("The installed Arbor cable-properties hook was modified.")
    if getattr(runner_module, "_make_junction", None) is not state.make_junction:
        raise ArborGapBridgeError("The installed staging-runner junction hook was modified.")


def _validate_arbor_module(arbor_module: ModuleType) -> None:
    version = str(getattr(arbor_module, "__version__", "unknown"))
    if version != SUPPORTED_ARBOR_VERSION:
        raise ArborGapBridgeError(
            f"The Digifly gap catalogue requires Arbor {SUPPORTED_ARBOR_VERSION}; found {version}."
        )
    if not callable(getattr(arbor_module, "load_catalogue", None)):
        raise ArborGapBridgeError("Arbor does not expose load_catalogue().")
    if not callable(getattr(arbor_module, "junction", None)):
        raise ArborGapBridgeError("Arbor does not expose junction().")


def _validate_runner_module(runner_module: ModuleType) -> Callable[..., Any]:
    name = str(getattr(runner_module, "__name__", ""))
    if name != STAGING_RUNNER_MODULE:
        raise ArborGapBridgeError(
            f"Refusing to patch non-staging runner {name!r}; expected {STAGING_RUNNER_MODULE!r}."
        )
    make_junction = getattr(runner_module, "_make_junction", None)
    if not callable(make_junction):
        raise ArborGapBridgeError("The staging runner has no callable _make_junction hook.")
    try:
        signature = inspect.signature(make_junction)
    except (TypeError, ValueError) as exc:
        raise ArborGapBridgeError("Could not inspect staging runner _make_junction.") from exc
    parameters = tuple(signature.parameters.values())
    expected_names = ("A", "pair", "cfg", "prefix")
    if tuple(parameter.name for parameter in parameters) != expected_names:
        raise ArborGapBridgeError(
            f"Staging runner _make_junction signature changed: {signature}."
        )
    if any(
        parameter.kind is not inspect.Parameter.POSITIONAL_OR_KEYWORD
        for parameter in parameters[:3]
    ) or parameters[3].kind is not inspect.Parameter.KEYWORD_ONLY:
        raise ArborGapBridgeError(
            f"Staging runner _make_junction calling convention changed: {signature}."
        )
    if any(parameter.default is not inspect.Parameter.empty for parameter in parameters):
        raise ArborGapBridgeError(
            f"Staging runner _make_junction defaults changed: {signature}."
        )
    return make_junction


def _validate_catalogue(catalogue: Any) -> None:
    try:
        available = set(catalogue.keys())
    except Exception as exc:
        raise ArborGapBridgeError("Loaded object is not an inspectable Arbor catalogue.") from exc
    missing = sorted(set(_EXPECTED_CATALOGUE_PARAMETERS) - available)
    if missing:
        raise ArborGapBridgeError(
            "Digifly gap catalogue is missing mechanisms: " + ", ".join(missing)
        )
    for mechanism, expected_parameters in _EXPECTED_CATALOGUE_PARAMETERS.items():
        try:
            info = catalogue[mechanism]
            kind = str(info.kind).strip().lower()
            parameters = frozenset(info.parameters)
        except Exception as exc:
            raise ArborGapBridgeError(
                f"Could not inspect Digifly catalogue mechanism {mechanism!r}."
            ) from exc
        if "gap junction" not in kind:
            raise ArborGapBridgeError(
                f"Digifly mechanism {mechanism!r} has wrong Arbor kind: {info.kind!r}."
            )
        if parameters != expected_parameters:
            raise ArborGapBridgeError(
                f"Digifly mechanism {mechanism!r} parameters changed: "
                f"expected {sorted(expected_parameters)}, found {sorted(parameters)}."
            )


def _properties_factory_wrapper(
    original_factory: Callable[..., Any],
    custom_catalogue: Any,
) -> Callable[..., Any]:
    @functools.wraps(original_factory)
    def properties_factory(*args: Any, **kwargs: Any) -> Any:
        properties = original_factory(*args, **kwargs)
        catalogue = getattr(properties, "catalogue", None)
        if catalogue is None or not callable(getattr(catalogue, "extend", None)):
            raise ArborGapBridgeError(
                "Fresh Arbor neuron cable properties have no extendable catalogue."
            )
        collisions = [
            CATALOGUE_PREFIX + mechanism
            for mechanism in _EXPECTED_CATALOGUE_PARAMETERS
            if CATALOGUE_PREFIX + mechanism in catalogue
        ]
        if collisions:
            raise ArborGapBridgeError(
                "Fresh Arbor cable properties already contain Digifly mechanism names: "
                + ", ".join(sorted(collisions))
            )
        try:
            catalogue.extend(custom_catalogue, CATALOGUE_PREFIX)
        except Exception as exc:
            raise ArborGapBridgeError(
                "Could not extend fresh Arbor cable properties with the Digifly catalogue."
            ) from exc
        missing = [
            CATALOGUE_PREFIX + mechanism
            for mechanism in _EXPECTED_CATALOGUE_PARAMETERS
            if CATALOGUE_PREFIX + mechanism not in catalogue
        ]
        if missing:
            raise ArborGapBridgeError(
                "Arbor catalogue extension omitted mechanisms: " + ", ".join(sorted(missing))
            )
        return properties

    return properties_factory


def _make_junction_wrapper(arbor_module: ModuleType) -> Callable[..., Any]:
    def make_junction(
        A: ModuleType,
        pair: Mapping[str, Any],
        cfg: Mapping[str, Any],
        *,
        prefix: str,
    ) -> Any:
        if A is not arbor_module:
            raise ArborGapBridgeError(
                "The patched staging runner was called with an unvalidated Arbor module."
            )
        try:
            return _custom_junction(A, pair, cfg, prefix=prefix)
        except ArborGapBridgeError:
            raise
        except Exception as exc:
            raise ArborGapBridgeError(
                "Arbor rejected the exact Digifly gap-junction placement; no gj fallback was used."
            ) from exc

    make_junction.__name__ = "_digifly_make_junction"
    make_junction.__qualname__ = "_digifly_make_junction"
    return make_junction


def _custom_junction(
    A: ModuleType,
    pair: Mapping[str, Any],
    cfg: Mapping[str, Any],
    *,
    prefix: str,
) -> Any:
    endpoint = str(prefix).strip().lower()
    if endpoint not in {"pre", "post"}:
        raise ArborGapBridgeError(f"Unknown gap endpoint prefix {prefix!r}.")
    gap_cfg_value = cfg.get("gap")
    if not isinstance(gap_cfg_value, Mapping):
        raise ArborGapBridgeError("Arbor config must contain a gap mapping.")
    gap_cfg = gap_cfg_value
    raw_mode = pair.get("mode", gap_cfg.get("mechanism"))
    mode_key = str(raw_mode or "").strip().lower().replace("-", "_")
    mode = _MODE_ALIASES.get(mode_key)
    if mode is None:
        raise ArborGapBridgeError(
            f"Unsupported exact Digifly gap mechanism {raw_mode!r}; built-in gj is forbidden."
        )
    g_uS = _finite_float(pair.get("g_uS"), "pair.g_uS", minimum=0.0)

    raw_direction = pair.get("directionality", gap_cfg.get("directionality"))
    direction = str(raw_direction or "").strip().lower().replace("-", "_")
    if mode == "gap":
        if direction not in _SYMMETRIC_DIRECTIONS:
            raise ArborGapBridgeError(
                f"Exact Gap is symmetric, but directionality was {raw_direction!r}."
            )
        return A.junction(CATALOGUE_PREFIX + "gap", {"g": g_uS})

    if direction in _FORWARD_DIRECTIONS:
        forward = True
    elif direction in _REVERSE_DIRECTIONS:
        forward = False
    else:
        raise ArborGapBridgeError(
            f"{mode} requires pre_to_post or post_to_pre directionality; found {raw_direction!r}."
        )

    if mode == "rect_gap":
        target_endpoint = "post" if forward else "pre"
        if endpoint == target_endpoint:
            return A.junction(CATALOGUE_PREFIX + "rect_gap", {"gmax": g_uS})
        # NEURON's RectGap is one-sided. Arbor still needs a peer endpoint for
        # the gap connection, so the source uses the exact ohmic model at zero
        # conductance instead of receiving a second rectifying current.
        return A.junction(CATALOGUE_PREFIX + "gap", {"g": 0.0})

    g_closed_frac = _required_cfg_float(
        gap_cfg,
        "g_closed_frac",
        minimum=0.0,
    )
    vhalf_mV = _required_cfg_float(gap_cfg, "vhalf_mV")
    vslope_mV = _required_cfg_float(gap_cfg, "vslope_mV", exclusive_minimum=0.0)
    residual = _required_cfg_float(
        gap_cfg,
        "empirical_residual_frac",
        minimum=0.0,
        maximum=1.0,
    )
    tau_open_ms = _required_cfg_float(gap_cfg, "tau_open_ms", exclusive_minimum=0.0)
    tau_close_ms = _required_cfg_float(gap_cfg, "tau_close_ms", exclusive_minimum=0.0)
    if forward:
        orientation = 1.0 if endpoint == "pre" else -1.0
    else:
        orientation = -1.0 if endpoint == "pre" else 1.0
    return A.junction(
        CATALOGUE_PREFIX + "hetero_rect_gap",
        {
            "gmax_open": g_uS,
            "gmax_closed": g_uS * g_closed_frac,
            "orientation": orientation,
            "vhalf": vhalf_mV,
            "vslope": vslope_mV,
            "empirical_residual_frac": residual,
            "tau_open_ms": tau_open_ms,
            "tau_close_ms": tau_close_ms,
        },
    )


def _required_cfg_float(
    config: Mapping[str, Any],
    key: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    if key not in config:
        raise ArborGapBridgeError(f"Arbor gap config is missing required parameter {key!r}.")
    return _finite_float(
        config[key],
        f"gap.{key}",
        minimum=minimum,
        maximum=maximum,
        exclusive_minimum=exclusive_minimum,
    )


def _finite_float(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: float | None = None,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ArborGapBridgeError(f"{label} must be numeric; found {value!r}.") from exc
    if not math.isfinite(result):
        raise ArborGapBridgeError(f"{label} must be finite; found {value!r}.")
    if minimum is not None and result < minimum:
        raise ArborGapBridgeError(f"{label} must be at least {minimum}; found {result}.")
    if maximum is not None and result > maximum:
        raise ArborGapBridgeError(f"{label} must be at most {maximum}; found {result}.")
    if exclusive_minimum is not None and result <= exclusive_minimum:
        raise ArborGapBridgeError(
            f"{label} must be greater than {exclusive_minimum}; found {result}."
        )
    return result


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = [
    "ArborGapBridge",
    "ArborGapBridgeError",
    "CATALOGUE_PREFIX",
    "SUPPORTED_ARBOR_VERSION",
    "install_arbor_gap_bridge",
]
