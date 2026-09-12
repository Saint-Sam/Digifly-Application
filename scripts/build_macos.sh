#!/usr/bin/env bash
set -euo pipefail

build_mode="local"
case "${1:-}" in
  "")
    ;;
  --developer-id)
    build_mode="developer-id"
    shift
    ;;
  *)
    echo "Usage: $0 [--developer-id]" >&2
    exit 2
    ;;
esac
if [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--developer-id]" >&2
  exit 2
fi
if [[ "$build_mode" == "developer-id" ]]; then
  if [[ ! "${DIGIFLY_DEVELOPER_IDENTITY:-}" =~ ^[[:xdigit:]]{40}$ ]]; then
    echo "Developer ID mode requires DIGIFLY_DEVELOPER_IDENTITY as a 40-character certificate hash." >&2
    exit 2
  fi
  if [[ -z "${DIGIFLY_NOTARY_PROFILE:-}" ]]; then
    echo "Developer ID mode requires DIGIFLY_NOTARY_PROFILE as a Keychain profile name." >&2
    exit 2
  fi
fi

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
release_bundle="$release_dir/Digifly Workstation.app"
release_archive_dir="$release_dir/previous_builds"
release_version="${DIGIFLY_RELEASE_VERSION:-0.1.0}"
minimum_macos="${DIGIFLY_MINIMUM_MACOS:-14.0}"
release_architecture="$(/usr/bin/uname -m)"
release_zip="${DIGIFLY_RELEASE_ZIP:-$release_dir/Digifly-Workstation-$release_version-macOS-$release_architecture.zip}"
build_number="${DIGIFLY_BUILD_NUMBER:-1}"
build_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
stage_root="$(mktemp -d /private/tmp/digifly-workstation-build.XXXXXX)"
stage_python="$stage_root/.venv/bin/python"
stage_spec="$stage_root/pysidedeploy.spec"
stage_deploy_dir="$stage_root/deployment"
stage_bundle="$stage_root/Digifly Workstation.app"
stage_log="$stage_root/build.log"
candidate_root="$release_dir/.digifly-candidate.$$"
candidate_bundle="$candidate_root/Digifly Workstation.app"
backup_bundle="$release_archive_dir/Digifly Workstation.pre-$build_stamp-$$.app"
build_succeeded=0
promotion_in_progress=0
had_previous_release=0

archive_failed_build() {
  local failure_dir="$source_deploy_dir/failed_builds/$build_stamp-$$"

  mkdir -p "$failure_dir"
  if [[ -d "$stage_deploy_dir" ]]; then
    rsync -a \
      --exclude '/.nuitka-cache/' \
      --exclude '/.ccache/' \
      --exclude '/.pip-cache/' \
      "$stage_deploy_dir/" "$failure_dir/deployment/"
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

  if [[ $status -ne 0 && $promotion_in_progress -eq 1 && $had_previous_release -eq 1 ]]; then
    local interrupted_release="$source_deploy_dir/failed_bundles/Digifly Workstation.interrupted-$build_stamp-$$.app"
    mkdir -p "$source_deploy_dir/failed_bundles"
    if [[ -e "$release_bundle" ]]; then
      mv "$release_bundle" "$interrupted_release" || true
    fi
    if [[ -e "$backup_bundle" && ! -e "$release_bundle" ]]; then
      mv "$backup_bundle" "$release_bundle" || true
      echo "Restored the previous release after interrupted promotion." >&2
    fi
  fi
  if [[ $status -ne 0 && -d "$candidate_root" ]]; then
    local failed_candidate="$source_deploy_dir/failed_bundles/Digifly Workstation.candidate-$build_stamp-$$.app"
    mkdir -p "$source_deploy_dir/failed_bundles"
    if [[ -d "$candidate_bundle" ]]; then
      mv "$candidate_bundle" "$failed_candidate" || true
      echo "Preserved failed release candidate at $failed_candidate" >&2
    fi
    rmdir "$candidate_root" 2>/dev/null || true
  fi
  if [[ $status -ne 0 && $build_succeeded -eq 0 ]]; then
    archive_failed_build || true
  fi
  case "$stage_root" in
    /private/tmp/digifly-workstation-build.*)
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
if [[ ! "$build_number" =~ ^[0-9]+([.][0-9]+){0,2}$ ]]; then
  echo "DIGIFLY_BUILD_NUMBER must be one to three dot-separated non-negative integers." >&2
  exit 2
fi
if [[ ! "$release_version" =~ ^[0-9]+([.][0-9]+){1,2}$ ]]; then
  echo "DIGIFLY_RELEASE_VERSION must be a two- or three-part numeric app version." >&2
  exit 2
fi
if [[ ! "$minimum_macos" =~ ^[0-9]+([.][0-9]+){1,2}$ ]]; then
  echo "DIGIFLY_MINIMUM_MACOS must be a numeric macOS version such as 14.0." >&2
  exit 2
fi
if [[ -e "$candidate_root" ]]; then
  echo "Refusing to overwrite stale release candidate at $candidate_root" >&2
  exit 2
fi
if [[ "$build_mode" == "developer-id" && -e "$release_zip" ]]; then
  echo "Refusing to overwrite existing Developer ID release archive at $release_zip" >&2
  exit 2
fi
if [[ "$build_mode" == "developer-id" && -e "$release_zip.sha256" ]]; then
  echo "Refusing to overwrite existing Developer ID release checksum at $release_zip.sha256" >&2
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
  /bin/cp -cR "$source_deploy_dir/.nuitka-cache" "$stage_deploy_dir/.nuitka-cache"
fi
if [[ -d "$source_deploy_dir/.pip-cache" ]]; then
  /bin/cp -cR "$source_deploy_dir/.pip-cache" "$stage_deploy_dir/.pip-cache"
fi

python_minor="$($stage_python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
deploy_script="$stage_root/.venv/lib/python$python_minor/site-packages/PySide6/scripts/deploy.py"
app_icon="$stage_root/src/digifly_app/assets/digifly_icon.icns"
if [[ ! -f "$deploy_script" ]]; then
  echo "The PySide deployment tools are incomplete in $project_dir/.venv" >&2
  exit 2
fi
if [[ ! -f "$app_icon" ]]; then
  echo "The Digifly application icon is missing at $app_icon" >&2
  exit 2
fi

export VIRTUAL_ENV="$stage_root/.venv"
# Use Apple's install_name_tool while retaining Anaconda's other dependency
# scanners. Anaconda's install_name_tool emits a "replacing existing
# signature" diagnostic that Nuitka 2.7 incorrectly treats as fatal.
ln -s /usr/bin/install_name_tool "$stage_root/tool-shims/install_name_tool"
export PATH="$stage_root/tool-shims:$stage_root/.venv/bin:$host_path"
export PYTHONPATH="$stage_root/src"
export NUITKA_CACHE_DIR="$stage_deploy_dir/.nuitka-cache"
export PIP_CACHE_DIR="$stage_deploy_dir/.pip-cache"
export TMPDIR="$stage_root/tmp"
export NUITKA_ASSUME_YES_FOR_DOWNLOADS="yes"
export PIP_NO_INDEX="1"
export MACOSX_DEPLOYMENT_TARGET="$minimum_macos"

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

/usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName Digifly Workstation" "$stage_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleName Digifly Workstation" "$stage_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier org.digifly.workstation" "$stage_bundle/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $release_version" "$stage_bundle/Contents/Info.plist"
if /usr/libexec/PlistBuddy -c "Print :LSMinimumSystemVersion" "$stage_bundle/Contents/Info.plist" >/dev/null 2>&1; then
  /usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $minimum_macos" "$stage_bundle/Contents/Info.plist"
else
  /usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string $minimum_macos" "$stage_bundle/Contents/Info.plist"
fi
if /usr/libexec/PlistBuddy -c "Print :CFBundleVersion" "$stage_bundle/Contents/Info.plist" >/dev/null 2>&1; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $build_number" "$stage_bundle/Contents/Info.plist"
else
  /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $build_number" "$stage_bundle/Contents/Info.plist"
fi
codesign --force --deep --sign - "$stage_bundle"
codesign --verify --deep --strict "$stage_bundle"

# Copy and verify the candidate before moving the currently installed release.
mkdir -p "$candidate_root"
if [[ "$build_mode" == "developer-id" ]]; then
  # Preserve framework and bundle metadata, then sign the final copy inside-out.
  /usr/bin/ditto "$stage_bundle" "$candidate_bundle"
  "$project_dir/scripts/sign_macos_app.sh" "$candidate_bundle"
else
  rsync -a "$stage_bundle/" "$candidate_bundle/"
  # Ad-hoc signatures on some nested bundle resources are stored in extended
  # attributes that macOS's bundled rsync does not preserve with -a. Sign again
  # on the release filesystem, then verify that exact candidate.
  codesign --force --deep --sign - "$candidate_bundle"
fi
codesign --verify --deep --strict "$candidate_bundle"
if [[ ! -x "$candidate_bundle/Contents/MacOS/main" ]]; then
  echo "Release candidate lost its executable permission during copying." >&2
  exit 4
fi
if ! /usr/bin/file "$candidate_bundle/Contents/MacOS/main" | /usr/bin/grep -q " $release_architecture"; then
  echo "Release executable does not match host architecture $release_architecture." >&2
  exit 4
fi
# Enforce the same dataset, generated-state, oversized-file, and developer-path
# boundary used for wheel/sdist releases before the previous app is moved.
"$stage_python" -m digifly_app.packaging.audit "$candidate_bundle"
if [[ "$build_mode" == "developer-id" ]]; then
  DIGIFLY_NOTARY_DIAGNOSTICS_DIR="$source_deploy_dir/notarization" \
    "$project_dir/scripts/notarize_macos_app.sh" "$candidate_bundle" "$release_zip"
fi

if [[ -e "$release_bundle" ]]; then
  had_previous_release=1
  promotion_in_progress=1
  mv "$release_bundle" "$backup_bundle"
  echo "Preserved previous release at $backup_bundle"
fi
mv "$candidate_bundle" "$release_bundle"
rmdir "$candidate_root"
verify_release_bundle() {
  codesign --verify --deep --strict "$release_bundle" || return 1
  if [[ "$build_mode" == "developer-id" ]]; then
    /usr/bin/xcrun stapler validate "$release_bundle" || return 1
    /usr/sbin/spctl --assess --type execute --verbose=4 "$release_bundle" || return 1
  fi
}
if ! verify_release_bundle; then
  failed_release="$source_deploy_dir/failed_bundles/Digifly Workstation.$build_stamp-$$.app"
  mkdir -p "$source_deploy_dir/failed_bundles"
  mv "$release_bundle" "$failed_release"
  if [[ -e "$backup_bundle" ]]; then
    mv "$backup_bundle" "$release_bundle"
  fi
  promotion_in_progress=0
  echo "Release verification failed; restored the previous bundle." >&2
  exit 5
fi
promotion_in_progress=0

# Save updated local caches only after a successful, verified build.
if [[ -d "$stage_deploy_dir/.nuitka-cache" ]]; then
  rsync -a "$stage_deploy_dir/.nuitka-cache/" "$source_deploy_dir/.nuitka-cache/"
fi
if [[ -d "$stage_deploy_dir/.pip-cache" ]]; then
  rsync -a "$stage_deploy_dir/.pip-cache/" "$source_deploy_dir/.pip-cache/"
fi

build_succeeded=1
echo "Built and verified $release_bundle"
if [[ "$build_mode" == "developer-id" ]]; then
  echo "Developer ID release archive: $release_zip"
else
  "$project_dir/scripts/archive_macos_app.sh" "$release_bundle" "$release_zip"
  echo "Ad-hoc private-testing archive: $release_zip"
fi
