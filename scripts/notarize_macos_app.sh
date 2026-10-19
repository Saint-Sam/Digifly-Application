#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: DIGIFLY_NOTARY_PROFILE=<keychain-profile> $0 /path/to/Digifly\\ Workstation.app /path/to/release.zip" >&2
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

app_bundle="$1"
release_zip="$2"
release_zip_name="$(basename "$release_zip")"
release_zip_parent_input="$(dirname "$release_zip")"
profile="${DIGIFLY_NOTARY_PROFILE:-}"
diagnostics_dir="${DIGIFLY_NOTARY_DIAGNOSTICS_DIR:-$(cd "$(dirname "$0")/.." && pwd)/deployment/notarization}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
temp_root="$(mktemp -d /private/tmp/digifly-notary.XXXXXX)"
archive_stage_root=""
submitted_zip="$temp_root/Digifly-Workstation-submission.zip"
submit_json="$diagnostics_dir/$stamp-submission.json"
submit_stderr="$diagnostics_dir/$stamp-stderr.log"
notary_log="$diagnostics_dir/$stamp-log.json"

cleanup_temp() {
  case "$temp_root" in
    /private/tmp/digifly-notary.*)
      rm -rf -- "$temp_root"
      ;;
    *)
      echo "Refusing to clean unexpected notarization path: $temp_root" >&2
      ;;
  esac
  if [[ -n "$archive_stage_root" ]]; then
    case "$archive_stage_root" in
      "$release_zip_parent"/.digifly-release.*)
        rm -rf -- "$archive_stage_root"
        ;;
      *)
        echo "Refusing to clean unexpected release staging path: $archive_stage_root" >&2
        ;;
    esac
  fi
}
trap cleanup_temp EXIT

if [[ ! -d "$app_bundle" || "$(basename "$app_bundle")" != "Digifly Workstation.app" ]]; then
  echo "Notarization requires a bundle named exactly 'Digifly Workstation.app'." >&2
  exit 2
fi
if [[ -z "$profile" ]]; then
  echo "Set DIGIFLY_NOTARY_PROFILE to a notarytool credential profile stored in Keychain." >&2
  exit 2
fi
if [[ "${release_zip##*.}" != "zip" ]]; then
  echo "The final release artifact must use a .zip filename." >&2
  exit 2
fi
/bin/mkdir -p "$diagnostics_dir" "$release_zip_parent_input"
release_zip_parent="$(cd "$release_zip_parent_input" && pwd)"
release_zip="$release_zip_parent/$release_zip_name"
checksum_file="$release_zip.sha256"
if [[ -e "$release_zip" || -e "$checksum_file" ]]; then
  echo "Refusing to overwrite existing release archive: $release_zip" >&2
  exit 2
fi

/usr/bin/codesign --verify --deep --strict --verbose=4 "$app_bundle"
signature_details="$(/usr/bin/codesign --display --verbose=4 "$app_bundle" 2>&1)"
if [[ "$signature_details" != *"Authority=Developer ID Application:"* || "$signature_details" != *"runtime"* ]]; then
  echo "Notarization requires a Developer ID Application signature with hardened runtime." >&2
  exit 3
fi

/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$app_bundle" "$submitted_zip"

set +e
/usr/bin/xcrun notarytool submit "$submitted_zip" \
  --keychain-profile "$profile" \
  --wait \
  --output-format json >"$submit_json" 2>"$submit_stderr"
submit_exit=$?
set -e

submission_id="$(/usr/bin/plutil -extract id raw -o - "$submit_json" 2>/dev/null || true)"
status="$(/usr/bin/plutil -extract status raw -o - "$submit_json" 2>/dev/null || true)"
if [[ -n "$submission_id" ]]; then
  /usr/bin/xcrun notarytool log --keychain-profile "$profile" \
    "$submission_id" "$notary_log" || true
fi
if [[ $submit_exit -ne 0 || "$status" != "Accepted" ]]; then
  failed_zip="$diagnostics_dir/$stamp-submitted.zip"
  /bin/cp "$submitted_zip" "$failed_zip"
  echo "Apple notarization was not accepted (status: ${status:-unavailable})." >&2
  echo "Preserved the exact submission at $failed_zip" >&2
  echo "Submission report: $submit_json" >&2
  exit 4
fi

/usr/bin/xcrun stapler staple "$app_bundle"
/usr/bin/xcrun stapler validate "$app_bundle"
/usr/bin/codesign --verify --deep --strict --verbose=4 "$app_bundle"
/usr/sbin/spctl --assess --type execute --verbose=4 "$app_bundle"

# Stapling changes the app, so create the distributable archive only afterward.
archive_stage_root="$(mktemp -d "$release_zip_parent/.digifly-release.XXXXXX")"
candidate_release_zip="$archive_stage_root/$release_zip_name"
candidate_checksum="$archive_stage_root/$release_zip_name.sha256"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$app_bundle" "$candidate_release_zip"
/usr/bin/unzip -tq "$candidate_release_zip"
extract_root="$temp_root/extracted"
/bin/mkdir -p "$extract_root"
/usr/bin/ditto -x -k "$candidate_release_zip" "$extract_root"
extracted_app="$extract_root/Digifly Workstation.app"
/usr/bin/codesign --verify --deep --strict --verbose=4 "$extracted_app"
/usr/bin/xcrun stapler validate "$extracted_app"
/usr/sbin/spctl --assess --type execute --verbose=4 "$extracted_app"
checksum_value="$(/usr/bin/shasum -a 256 "$candidate_release_zip" | /usr/bin/awk '{print $1}')"
printf '%s  %s\n' "$checksum_value" "$release_zip_name" >"$candidate_checksum"
/bin/mv "$candidate_release_zip" "$release_zip"
/bin/mv "$candidate_checksum" "$checksum_file"
/bin/rmdir "$archive_stage_root"
archive_stage_root=""
echo "Notarized, stapled, and archived $release_zip"
echo "Release checksum: $checksum_file"
echo "Submission report: $submit_json"
if [[ -f "$notary_log" ]]; then
  echo "Notarization log: $notary_log"
fi
