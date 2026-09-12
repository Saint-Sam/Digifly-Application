#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/Digifly\\ Workstation.app" >&2
  exit 2
fi

app_bundle="$1"
bundle_arbor="${DIGIFLY_BUNDLE_ARBOR:-1}"
arbor_version="${DIGIFLY_ARBOR_VERSION:-0.12.2}"
python_release="20260901"
python_version="3.12.14"

if [[ "$bundle_arbor" == "0" ]]; then
  echo "Bundled Arbor disabled; preserving the external-runtime-only package rule."
  exit 0
fi
if [[ ! -d "$app_bundle/Contents/Resources" ]]; then
  echo "Invalid macOS app bundle: $app_bundle" >&2
  exit 2
fi

case "$(uname -m)" in
  arm64)
    python_arch="aarch64"
    python_sha="81a359f1cfadd4da11766534c5913791cea55f26e1bb902cacd2a531bb1e4b2b"
    arbor_requirement="arbor==$arbor_version"
    ;;
  x86_64)
    echo "Arbor $arbor_version has no official macOS x86_64 wheel; set DIGIFLY_BUNDLE_ARBOR=0 for the Intel build." >&2
    exit 2
    ;;
  *)
    echo "Bundled Arbor is supported only on macOS arm64 and x86_64." >&2
    exit 2
    ;;
esac

runtime_root="$app_bundle/Contents/Resources/runtimes/arbor"
if [[ -e "$runtime_root" ]]; then
  echo "Refusing to overwrite an existing bundled Arbor runtime: $runtime_root" >&2
  exit 2
fi
stage_dir="$(mktemp -d /private/tmp/digifly-arbor-runtime.XXXXXX)"
trap 'rm -rf -- "$stage_dir"' EXIT
archive="$stage_dir/python.tar.gz"
url="https://github.com/astral-sh/python-build-standalone/releases/download/${python_release}/cpython-${python_version}%2B${python_release}-${python_arch}-apple-darwin-install_only_stripped.tar.gz"

curl --fail --location --retry 3 --output "$archive" "$url"
echo "$python_sha  $archive" | shasum -a 256 -c -
tar -xzf "$archive" -C "$stage_dir"
runtime_python="$stage_dir/python/bin/python3"
env -u PIP_NO_INDEX "$runtime_python" -m pip install \
  --disable-pip-version-check --no-cache-dir "$arbor_requirement"
"$runtime_python" - <<'PY'
import arbor
assert arbor.__version__ == "0.12.2", arbor.__version__
tree = arbor.segment_tree()
tree.append(arbor.mnpos, arbor.mpoint(0, 0, 0, 3), arbor.mpoint(0, 0, 10, 3), 1)
assert tree.size == 1
print(f"Verified bundled Arbor {arbor.__version__}")
PY

mkdir -p "$(dirname "$runtime_root")"
mv "$stage_dir/python" "$runtime_root"
find "$runtime_root" -type d -name __pycache__ -prune -exec rm -rf {} +
mkdir -p "$runtime_root/LICENSES"
cp "$PWD/THIRD_PARTY_LICENSES/Arbor-BSD-3-Clause.txt" \
  "$runtime_root/LICENSES/Arbor-BSD-3-Clause.txt"
cat > "$runtime_root/DIGIFLY_RUNTIME.json" <<EOF
{
  "component": "Arbor",
  "version": "$arbor_version",
  "python": "$python_version",
  "architecture": "$(uname -m)",
  "policy": "temporary-bundled-runtime-v1",
  "disable_with": "DIGIFLY_BUNDLE_ARBOR=0 at build time or DIGIFLY_DISABLE_BUNDLED_ARBOR=1 at run time"
}
EOF
"$runtime_root/bin/python3" -c 'import arbor; print(arbor.__version__)'
echo "Bundled and verified Arbor $arbor_version at $runtime_root"
