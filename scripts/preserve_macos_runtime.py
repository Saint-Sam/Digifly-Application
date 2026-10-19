"""Copy an already checked private runtime without rebuilding its files."""
import argparse
from pathlib import Path
import subprocess
import json

from digifly_app.core.update_installer import tree_inventory


def preserve(source: Path, bundle: Path):
    relative = Path("Contents/Resources/runtimes/arbor")
    if source.resolve() == bundle.resolve() or (bundle / relative).exists():
        raise ValueError("Runtime destination must be new.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(source)], check=True)
    original = tree_inventory(source / relative)
    if not original:
        raise ValueError("Empty source runtime.")
    subprocess.run(["/usr/bin/ditto", str(source / relative), str(bundle / relative)], check=True)
    if tree_inventory(bundle / relative) != original:
        raise ValueError("Private runtime copy changed.")
    print(json.dumps({"ok": True, "copied_runtime_files": len(original), "source": str(source)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    preserve(args.source, args.bundle)
