#!/usr/bin/env bash
set -euo pipefail

# PySide 6.10/Nuitka 2.7 can lose quoting while handing paths to the macOS C
# backend. Build from a real directory whose path contains no whitespace. A
# symlink is insufficient because PySide resolves the project path before it
# invokes Nuitka.
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
host_path="$PATH"
source_python="$project_dir/.venv/bin/python"
source_spec="$project_dir/pysidedeploy.spec"
source_deploy_dir="$project_dir/deployment"
release_dir="$project_dir/dist"
release_bundle="$release_dir/Digifly App.app"
release_archive_dir="$release_dir/previous_builds"
build_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage_root="$(mktemp -d /private/tmp/digifly-app-build.XXXXXX)"
stage_python="$stage_root/.venv/bin/python"
stage_spec="$stage_root/pysidedeploy.spec"
stage_deploy_dir="$stage_root/deployment"
stage_bundle="$stage_root/Digifly App.app"
stage_log="$stage_root/build.log"
candidate_bundle="$release_dir/.Digifly App.candidate.$$.app"
backup_bundle="$release_archive_dir/Digifly App.pre-$build_stamp-$$.app"
build_succeeded=0

archive_failed_build() {
  local failure_dir="$source_deploy_dir/failed_builds/$build_stamp-$$"

  mkdir -p "$failure_dir"
  if [[ -d "$stage_deploy_dir" ]]; then
    rsync -a "$stage_deploy_dir/" "$failure_dir/deployment/"
  fi
  if [[ -f "$stage_log" ]]; then
    cp "$stage_log" "$failure_dir/build.log"
  fi
  if [[ -f "$stage_spec" ]]; then
    cp "$stage_spec" "$failure_dir/pysidedeploy.generated.spec"
  fi
  echo "Preserved failed build diagnostics at $failure_dir" >&2
}

cleanup_stage() {
  local status=$?

  if [[ $status -ne 0 && $build_succeeded -eq 0 ]]; then
    archive_failed_build || true
  fi
  case "$stage_root" in
    /private/tmp/digifly-app-build.*)
      rm -rf -- "$stage_root"
      ;;
    *)
      echo "Refusing to clean unexpected staging path: $stage_root" >&2
      ;;
  esac
  return "$status"
}
trap cleanup_stage EXIT

if [[ ! -x "$source_python" ]]; then
  echo "Run ./scripts/setup_dev.sh before building the app bundle." >&2
  exit 2
fi
if [[ ! -f "$source_spec" ]]; then
  echo "Missing deployment specification at $source_spec" >&2
  exit 2
fi
if [[ -e "$candidate_bundle" ]]; then
  echo "Refusing to overwrite stale release candidate at $candidate_bundle" >&2
  exit 2
fi

mkdir -p "$stage_deploy_dir" "$stage_root/tmp"
mkdir -p "$stage_root/tool-shims"
mkdir -p "$release_dir" "$release_archive_dir"

# Copy project inputs, but never modify or consume an earlier app bundle.
rsync -a \
  --exclude '/.git/' \
  --exclude '/.pytest_cache/' \
  --exclude '/.venv/' \
  --exclude '/deployment/' \
  --exclude '/dist/' \
  --exclude '__pycache__/' \
  "$project_dir/" "$stage_root/"
# Clone the environment as well as the source. A symlink here is not enough:
# Nuitka resolves PySide's Qt libraries to their physical location before its
# dependency scan. APFS clone-on-write keeps this fast and space-efficient.
/bin/cp -cR "$project_dir/.venv" "$stage_root/.venv"

# Keep every path passed to PySide, Nuitka, SCons, and ccache whitespace-free.
# The cache copy makes the build offline-capable while reusing prior downloads.
if [[ -d "$source_deploy_dir/.nuitka-cache" ]]; then
  rsync -a "$source_deploy_dir/.nuitka-cache/" "$stage_deploy_dir/.nuitka-cache/"
fi
if [[ -d "$source_deploy_dir/.ccache" ]]; then
  rsync -a "$source_deploy_dir/.ccache/" "$stage_deploy_dir/.ccache/"
fi
if [[ -d "$source_deploy_dir/.pip-cache" ]]; then
  rsync -a "$source_deploy_dir/.pip-cache/" "$stage_deploy_dir/.pip-cache/"
fi

python_minor="$($stage_python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
deploy_script="$stage_root/.venv/lib/python$python_minor/site-packages/PySide6/scripts/deploy.py"
default_icon="$stage_root/.venv/lib/python$python_minor/site-packages/PySide6/scripts/deploy_lib/pyside_icon.icns"
if [[ ! -f "$deploy_script" || ! -f "$default_icon" ]]; then
  echo "The PySide deployment tools are incomplete in $project_dir/.venv" >&2
  exit 2
fi
cp "$default_icon" "$stage_root/pyside_icon.icns"

export VIRTUAL_ENV="$stage_root/.venv"
# Use Apple's install_name_tool while retaining Anaconda's other dependency
# scanners. Anaconda's install_name_tool emits a "replacing existing
# signature" diagnostic that Nuitka 2.7 incorrectly treats as fatal.
ln -s /usr/bin/install_name_tool "$stage_root/tool-shims/install_name_tool"
export PATH="$stage_root/tool-shims:$stage_root/.venv/bin:$host_path"
export PYTHONPATH="$stage_root/src"
export NUITKA_CACHE_DIR="$stage_deploy_dir/.nuitka-cache"
export CCACHE_DIR="$stage_deploy_dir/.ccache"
export PIP_CACHE_DIR="$stage_deploy_dir/.pip-cache"
export TMPDIR="$stage_root/tmp"
export NUITKA_ASSUME_YES_FOR_DOWNLOADS="yes"
export PIP_NO_INDEX="1"

cd "$stage_root"
"$stage_python" "$deploy_script" \
  -c "$stage_spec" \
  --keep-deployment-files \
  -f \
  main.py 2>&1 | tee "$stage_log"

# PySide 6.10 catches deployment exceptions and can return status 0 even when
# Nuitka printed FATAL. Never promote that partial output to a release bundle.
if /usr/bin/grep -Eq '(^FATAL:|\[DEPLOY\] Exception occurred:)' "$stage_log"; then
  echo "PySide/Nuitka reported a fatal deployment error; rejecting the bundle." >&2
  exit 3
fi
if [[ ! -f "$stage_bundle/Contents/Info.plist" || ! -x "$stage_bundle/Contents/MacOS/main" ]]; then
  echo "Deployment did not produce a complete executable bundle at $stage_bundle" >&2
  exit 3
fi

/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Digifly App" "$stage_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName Digifly App" "$stage_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier org.digifly.app" "$stage_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString 0.1.0" "$stage_bundle/Contents/Info.plist"
codesign --force --deep --sign - "$stage_bundle"
codesign --verify --deep --strict "$stage_bundle"

# Copy and verify the candidate before moving the currently installed release.
rsync -a "$stage_bundle/" "$candidate_bundle/"
# Ad-hoc signatures on some nested bundle resources are stored in extended
# attributes that macOS's bundled rsync does not preserve with -a. Sign again
# on the release filesystem, then verify that exact candidate.
codesign --force --deep --sign - "$candidate_bundle"
codesign --verify --deep --strict "$candidate_bundle"
if [[ ! -x "$candidate_bundle/Contents/MacOS/main" ]]; then
  echo "Release candidate lost its executable permission during copying." >&2
  exit 4
fi

if [[ -e "$release_bundle" ]]; then
  mv "$release_bundle" "$backup_bundle"
  echo "Preserved previous release at $backup_bundle"
fi
mv "$candidate_bundle" "$release_bundle"
if ! codesign --verify --deep --strict "$release_bundle"; then
  failed_release="$source_deploy_dir/failed_bundles/Digifly App.$build_stamp-$$.app"
  mkdir -p "$source_deploy_dir/failed_bundles"
  mv "$release_bundle" "$failed_release"
  if [[ -e "$backup_bundle" ]]; then
    mv "$backup_bundle" "$release_bundle"
  fi
  echo "Release verification failed; restored the previous bundle." >&2
  exit 5
fi

# Save updated local caches only after a successful, verified build.
rsync -a "$stage_deploy_dir/.nuitka-cache/" "$source_deploy_dir/.nuitka-cache/"
rsync -a "$stage_deploy_dir/.ccache/" "$source_deploy_dir/.ccache/"
if [[ -d "$stage_deploy_dir/.pip-cache" ]]; then
  rsync -a "$stage_deploy_dir/.pip-cache/" "$source_deploy_dir/.pip-cache/"
fi

build_succeeded=1
echo "Built and verified $release_bundle"
