#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
deploy_python="$project_dir/.venv/bin/python"
deploy_tool="$project_dir/.venv/bin/pyside6-deploy"
generated_bundle="$project_dir/deployment/main.app"
generated_spec="$project_dir/deployment/pysidedeploy.generated.spec"
release_dir="$project_dir/dist"
release_bundle="$release_dir/Digifly App.app"
deploy_tmp="$project_dir/deployment/.tmp"
export NUITKA_CACHE_DIR="$project_dir/deployment/.nuitka-cache"
export CCACHE_DIR="$project_dir/deployment/.ccache"
export PIP_CACHE_DIR="$project_dir/deployment/.pip-cache"
export TMPDIR="$deploy_tmp"
export NUITKA_ASSUME_YES_FOR_DOWNLOADS="yes"

if [[ ! -x "$deploy_python" || ! -x "$deploy_tool" ]]; then
  echo "Run ./scripts/setup_dev.sh before building the app bundle." >&2
  exit 2
fi
if [[ -e "$release_bundle" ]]; then
  echo "Refusing to overwrite $release_bundle; move the existing bundle first." >&2
  exit 2
fi

cd "$project_dir"
mkdir -p "$deploy_tmp"
cp "$project_dir/pysidedeploy.spec" "$generated_spec"
"$deploy_tool" -c "$generated_spec" --keep-deployment-files -f main.py
if [[ ! -f "$generated_bundle/Contents/Info.plist" || ! -x "$generated_bundle/Contents/MacOS/main" ]]; then
  echo "Deployment did not produce a complete bundle at $generated_bundle" >&2
  exit 3
fi

mkdir -p "$release_dir"
mv "$generated_bundle" "$release_bundle"
/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Digifly App" "$release_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName Digifly App" "$release_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier org.digifly.app" "$release_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 0.1.0" "$release_bundle/Contents/Info.plist"
codesign --force --deep --sign - "$release_bundle"
codesign --verify --deep --strict "$release_bundle"
echo "Built $release_bundle"
