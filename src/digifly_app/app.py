from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def _qt_smoke_test() -> tuple[bool, str]:
    """Probe Qt out of process because an incompatible Qt binary can abort."""
    # Nuitka's macOS app bundle has already linked Qt during compilation and
    # does not ship a general-purpose ``python -c`` executable beside the app.
    if "__compiled__" in globals() or getattr(sys, "frozen", False) or not Path(sys.executable).is_file():
        return True, "bundled Qt runtime"
    code = "from PySide6.QtCore import qVersion; print(qVersion())"
    try:
        completed = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if completed.returncode != 0:
        return False, (completed.stderr or completed.stdout).strip()
    return True, completed.stdout.strip()


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--package-smoke-test":
        import argparse
        from digifly_app.ui.package_smoke import run_package_smoke
        parser = argparse.ArgumentParser()
        parser.add_argument("--package-smoke-test", type=Path, required=True)
        parser.add_argument("--data-reader", type=Path, required=True)
        args = parser.parse_args()
        return run_package_smoke(args.package_smoke_test, args.data_reader)
    ok, detail = _qt_smoke_test()
    if not ok:
        print(
            "Digifly Workstation could not start its Qt interface.\n"
            f"Configured Python: {sys.executable}\n"
            f"Qt smoke-test detail: {detail or 'unknown failure'}\n\n"
            "Use the app's isolated .venv or run `python -m digifly_app.cli doctor`.",
            file=sys.stderr,
        )
        return 2
    from digifly_app.ui.main_window import launch

    return launch(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
