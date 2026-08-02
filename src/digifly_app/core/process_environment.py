from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping


# External scientific runtimes must not inherit the embedded GUI interpreter's
# module, loader, virtual-environment, or Qt plugin paths.  In a standalone
# Nuitka bundle those paths point at binaries built for the app executable, not
# for an external Python such as /opt/anaconda3/bin/python.
EXTERNAL_PYTHON_ENV_REMOVE = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "PYTHONUSERBASE",
    "PYTHONPLATLIBDIR",
    "__PYVENV_LAUNCHER__",
    "VIRTUAL_ENV",
    "_PYTHON_SYSCONFIGDATA_NAME",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
    "QML_IMPORT_PATH",
    "QML2_IMPORT_PATH",
)


def sanitized_external_environment(
    overrides: Mapping[str, str],
    *,
    inherited: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an environment safe for an external interpreter or simulator."""
    environment = dict(os.environ if inherited is None else inherited)
    for key in tuple(environment):
        if (
            key in EXTERNAL_PYTHON_ENV_REMOVE
            or key.startswith("DYLD_")
            or key.startswith("CONDA_")
        ):
            environment.pop(key, None)
    if path_value := environment.get("PATH"):
        environment["PATH"] = os.pathsep.join(
            entry
            for entry in path_value.split(os.pathsep)
            if entry and ".app/Contents/MacOS" not in entry
        )
    environment.update({str(key): str(value) for key, value in overrides.items()})
    return environment


def external_runtime_path(
    python_executable: str | Path,
    *,
    inherited: str | None = None,
) -> str:
    """Build a deterministic PATH while retaining non-bundle user tools."""
    entries = [
        str(Path(python_executable).expanduser().resolve().parent),
        "/Applications/NEURON/bin",
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    inherited_path = inherited if inherited is not None else os.environ.get("PATH", "")
    for entry in inherited_path.split(os.pathsep):
        if entry and ".app/Contents/MacOS" not in entry:
            entries.append(entry)
    return os.pathsep.join(dict.fromkeys(entries))
