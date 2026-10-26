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
    sys.dont_write_bytecode = True
    if len(sys.argv) == 3 and sys.argv[1] == '--connectivity-task':
        import json
        try:
            from digifly_app.core.connectivity_conversion import run_task
            return run_task(json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')),
                            Path(sys.argv[2]).with_suffix('.result.json'))
        except Exception as exc:
            response = Path(sys.argv[2]).with_suffix('.result.json')
            if not response.exists():
                with response.open('x', encoding='utf-8') as stream:
                    json.dump({'error': str(exc)}, stream)
            print(str(exc), file=sys.stderr)
            return 1
    # An installed Mac bundle is immutable. This also reaches external runtime
    # probes, so a lazy import cannot change the tree while an update is staged.
    if sys.platform == 'darwin':
        sys.dont_write_bytecode = True
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    if len(sys.argv) == 3 and sys.argv[1] == "--finish-update":
        from digifly_app.core.update_installer import read_request
        request = read_request(Path(sys.argv[2]).resolve())
        if request.get("engineering_test") == "exit-before-startup":
            # Meaningful rollback injection in disposable app checks. Normal
            # transactions do not contain this explicitly test-only field.
            return 73
    if len(sys.argv) == 3 and sys.argv[1] == "--install-update":
        # Runs from a complete external copy of the app: no installed DLL,
        # Qt event loop or separately installed Python is held open here.
        from digifly_app.core.update_installer import install_transaction
        return install_transaction(Path(sys.argv[2]).resolve())
    if len(sys.argv) > 1 and sys.argv[1] == "--package-update-test":
        import argparse
        from digifly_app.core.update_installer import require_disposable_test
        require_disposable_test()  # before Qt, automatic consent or any writes
        from digifly_app.ui.package_update_smoke import run_update_smoke
        parser = argparse.ArgumentParser()
        parser.add_argument("--package-update-test", type=Path, required=True)
        parser.add_argument("--update-archive", type=Path, required=True)
        parser.add_argument("--fail-new-startup", action="store_true")
        parser.add_argument("--legacy-update-request", action="store_true")
        args = parser.parse_args()
        return run_update_smoke(args.package_update_test, args.update_archive, fail_startup=args.fail_new_startup,
                                legacy_request=args.legacy_update_request)
    if len(sys.argv) > 1 and sys.argv[1] == "--library-ui-review":
        import argparse
        from digifly_app.ui.package_library_smoke import run_library_review
        parser = argparse.ArgumentParser()
        parser.add_argument("--library-ui-review", type=Path, required=True)
        parser.add_argument("--workspace", type=Path, required=True)
        parser.add_argument("--existing-profile", type=Path)
        args = parser.parse_args()
        return run_library_review(args.library_ui_review, args.workspace,args.existing_profile)
    if len(sys.argv) > 1 and sys.argv[1] == "--package-library-test":
        import argparse
        from digifly_app.ui.package_library_smoke import run_library_smoke
        parser = argparse.ArgumentParser()
        parser.add_argument("--package-library-test", type=Path, required=True)
        args = parser.parse_args()
        result=run_library_smoke(args.package_library_test)
        if result:
            return result
        import json
        import traceback
        from digifly_app.ui.fragment_review_check import check_fragment_record_roundoff
        receipt_path=args.package_library_test/'receipt.json'
        receipt=json.loads(receipt_path.read_text())
        try:
            check_fragment_record_roundoff(args.package_library_test)
            receipt['fragment_derived_roundoff_guard']=True
        except Exception:
            receipt['ok']=False
            receipt['error']=traceback.format_exc()
            result=1
        receipt_path.write_text(json.dumps(receipt,indent=2)+'\n')
        return result
    if len(sys.argv) > 1 and sys.argv[1] == "--package-neuron-test":
        import argparse
        from digifly_app.ui.package_neuron_smoke import run_neuron_package_smoke
        parser = argparse.ArgumentParser()
        parser.add_argument("--package-neuron-test", type=Path, required=True)
        args = parser.parse_args()
        return run_neuron_package_smoke(args.package_neuron_test)
    if len(sys.argv) > 1 and sys.argv[1] == "--package-smoke-test":
        import argparse
        from digifly_app.ui.package_smoke import run_package_smoke
        parser = argparse.ArgumentParser()
        parser.add_argument("--package-smoke-test", type=Path, required=True)
        parser.add_argument("--data-reader", type=Path, required=True)
        parser.add_argument("--included-parquet", action="store_true")
        args = parser.parse_args()
        return run_package_smoke(args.package_smoke_test, args.data_reader, included=args.included_parquet)
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
