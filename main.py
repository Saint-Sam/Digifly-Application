"""Deployment entry point for Digifly App."""

from __future__ import annotations

from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from digifly_app.app import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
