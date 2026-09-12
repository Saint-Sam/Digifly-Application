#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 /path/to/Application.app /path/to/release.zip" >&2
  exit 2
fi

app_bundle="$1"
release_zip="$2"
release_dir="$(cd "$(dirname "$release_zip")" && pwd)"
release_name="$(basename "$release_zip")"
candidate_zip="$release_dir/.$release_name.candidate.$$"
candidate_checksum="$candidate_zip.sha256"

cleanup() {
  rm -f -- "$candidate_zip" "$candidate_checksum"
}
trap cleanup EXIT

if [[ ! -d "$app_bundle" || ! -x "$app_bundle/Contents/MacOS/main" ]]; then
  echo "A complete macOS application bundle is required: $app_bundle" >&2
  exit 2
fi

/usr/bin/codesign --verify --deep --strict "$app_bundle"
/usr/bin/ditto -c -k --sequesterRsrc --keepParent "$app_bundle" "$candidate_zip"
/usr/bin/unzip -tq "$candidate_zip"
(
  cd "$release_dir"
  /usr/bin/shasum -a 256 "$(basename "$candidate_zip")" > "$candidate_checksum"
)
/bin/mv -f "$candidate_zip" "$release_zip"
/usr/bin/sed "s/$(basename "$candidate_zip")/$release_name/" "$candidate_checksum" > "$release_zip.sha256"
rm -f -- "$candidate_checksum"
trap - EXIT

echo "Archived and verified $release_zip"
