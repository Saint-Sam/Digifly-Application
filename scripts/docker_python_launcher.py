"""Python-compatible launcher for Digifly's containerized simulator runtime."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


DEFAULT_IMAGE = "ghcr.io/saint-sam/digifly-simulators:0.1.0-alpha.1"


def _docker() -> str:
    executable = shutil.which("docker")
    if not executable:
        raise RuntimeError(
            "Docker Desktop is not available. Install and start Docker Desktop, then retry."
        )
    check = subprocess.run(
        [executable, "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if check.returncode:
        raise RuntimeError(
            "Docker Desktop is installed but its engine is not running. Start Docker Desktop and retry."
        )
    return executable


def _is_absolute_path(value: str) -> bool:
    return Path(value).is_absolute() or (
        len(value) > 2 and value[1] == ":" and value[2] in "\\/"
    )


def _rewrite_payload(value: Any, run_dir: Path, mounts: dict[Path, str]) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_payload(item, run_dir, mounts) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_payload(item, run_dir, mounts) for item in value]
    if not isinstance(value, str) or not _is_absolute_path(value):
        return value
    path = Path(value).expanduser().absolute()
    try:
        relative = path.relative_to(run_dir)
    except ValueError:
        relative = None
    if relative is not None:
        return "/digifly/run" if not relative.parts else f"/digifly/run/{relative.as_posix()}"
    if not path.exists():
        return value
    source = path if path.is_dir() else path.parent
    target = mounts.setdefault(source, f"/digifly/inputs/{len(mounts):04d}")
    return target if path.is_dir() else f"{target}/{path.name}"


def _worker_run(arguments: list[str]) -> int:
    request_index = arguments.index("--request") + 1
    request_path = Path(arguments[request_index]).expanduser().absolute()
    run_dir = request_path.parent
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    mounts: dict[Path, str] = {}
    rewritten = _rewrite_payload(payload, run_dir, mounts)
    container_request = run_dir / "worker_request.container.json"
    container_request.write_text(json.dumps(rewritten, indent=2), encoding="utf-8")
    worker_name = Path(arguments[1]).name
    image = os.environ.get("DIGIFLY_SIMULATOR_IMAGE", DEFAULT_IMAGE).strip() or DEFAULT_IMAGE
    name_hash = hashlib.sha256(str(run_dir).encode()).hexdigest()[:12]
    command = [
        _docker(), "run", "--rm", "--name", f"digifly-{name_hash}",
        "--network", "none", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
        "-v", f"{run_dir}:/digifly/run:rw",
    ]
    for source, target in mounts.items():
        command.extend(("-v", f"{source}:{target}:ro"))
    command.extend(
        (
            image, "python", "-B", f"/opt/digifly/src/digifly_app/workers/{worker_name}",
            "--request", "/digifly/run/worker_request.container.json",
        )
    )
    return subprocess.run(command, check=False).returncode


def main() -> int:
    arguments = sys.argv[1:]
    try:
        docker = _docker()
        image = os.environ.get("DIGIFLY_SIMULATOR_IMAGE", DEFAULT_IMAGE).strip() or DEFAULT_IMAGE
        if "--request" in arguments and len(arguments) >= 3:
            return _worker_run(arguments)
        return subprocess.run([docker, "run", "--rm", "--network", "none", image, "python", *arguments], check=False).returncode
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(f"Digifly container runtime: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
