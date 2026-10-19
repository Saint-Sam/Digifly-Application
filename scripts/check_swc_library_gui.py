"""Run the engineering GUI fixture and prove native user preferences unchanged."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from PySide6.QtCore import QSettings


def fingerprint():
    settings = QSettings("digifly", "Digifly Workstation")
    # Compare this app's values, not macOS-global fallback preferences such as
    # spell-check analytics that can change independently while a test runs.
    settings.setFallbacksEnabled(False)
    settings.sync()
    values = {key: settings.value(key) for key in settings.allKeys()}
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path)
    parser.add_argument("--source", action="store_true")
    parser.add_argument("--archive", type=Path, help="Bind a packaged check to its delivery ZIP as well")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("GUI evidence destination must be new")
    project = Path(__file__).resolve().parents[1]
    command = [sys.executable, str(project / "main.py")] if args.source else [str(args.executable.resolve())]
    command += ["--package-library-test", str(output)]
    executable_sha = hashlib.sha256(args.executable.read_bytes()).hexdigest() if args.executable else None
    archive_sha = hashlib.sha256(args.archive.read_bytes()).hexdigest() if args.archive else None
    before = fingerprint()
    environment = dict(os.environ, QT_QPA_PLATFORM="cocoa" if sys.platform == "darwin" else "windows")
    result = subprocess.run(command, cwd=project, env=environment, capture_output=True, text=True, timeout=120)
    after = fingerprint()
    evidence = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    isolated_settings = list((output / "settings").glob("*.ini"))
    report = {"ok": result.returncode == 0 and evidence["ok"] and before == after and bool(isolated_settings),
              "schema": "digifly-native-library-runner-v1", "execution_mode": "source" if args.source else "packaged",
              "executable_sha256": executable_sha, "archive_sha256": archive_sha,
              "native_preferences_before_sha256": before, "native_preferences_after_sha256": after,
              "native_preferences_unchanged": before == after, "explicit_settings_files": [str(p) for p in isolated_settings],
              "exit_code": result.returncode, "engineering_only": True}
    if args.executable and hashlib.sha256(args.executable.read_bytes()).hexdigest() != executable_sha:
        report["ok"] = False
    if args.archive and hashlib.sha256(args.archive.read_bytes()).hexdigest() != archive_sha:
        report["ok"] = False
    (output / "preference-postflight.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output / "gui-stdout.txt").write_text(result.stdout, encoding="utf-8")
    (output / "gui-stderr.txt").write_text(result.stderr, encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
