#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: DIGIFLY_DEVELOPER_IDENTITY=<40-hex-certificate-hash> $0 /path/to/Digifly\\ Workstation.app" >&2
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

app_bundle="$1"
identity="${DIGIFLY_DEVELOPER_IDENTITY:-}"

if [[ ! -d "$app_bundle" || "${app_bundle##*.}" != "app" ]]; then
  echo "Expected an existing macOS .app bundle: $app_bundle" >&2
  exit 2
fi
if [[ ! "$identity" =~ ^[[:xdigit:]]{40}$ ]]; then
  echo "Set DIGIFLY_DEVELOPER_IDENTITY to the 40-character hash shown by:" >&2
  echo "  security find-identity -v -p codesigning" >&2
  exit 2
fi

identity_line="$(/usr/bin/security find-identity -v -p codesigning | /usr/bin/grep -i "$identity" | /usr/bin/head -n 1 || true)"
if [[ -z "$identity_line" || "$identity_line" != *"Developer ID Application:"* ]]; then
  echo "The selected identity is not an installed Developer ID Application certificate." >&2
  exit 2
fi

# The current Nuitka layout contains Mach-O leaf files but no nested signed
# containers. Fail closed if a future build adds one; it needs an explicit
# inside-out signing rule and may need its own entitlements review.
nested_container="$(/usr/bin/find "$app_bundle" -depth -type d \( \
  -name '*.app' -o -name '*.framework' -o -name '*.appex' -o \
  -name '*.xpc' -o -name '*.plugin' -o -name '*.bundle' \
\) ! -path "$app_bundle" -print -quit)"
if [[ -n "$nested_container" ]]; then
  echo "Unsupported nested code container in release bundle: $nested_container" >&2
  exit 3
fi

signed_count=0
while IFS= read -r -d '' code_file; do
  description="$(/usr/bin/file -b "$code_file")"
  if [[ "$description" != *"Mach-O"* ]]; then
    continue
  fi
  sign_args=(--force --timestamp --sign "$identity")
  if [[ "$description" == *"executable"* ]]; then
    sign_args+=(--options runtime)
  fi
  /usr/bin/codesign "${sign_args[@]}" "$code_file"
  ((signed_count += 1))
done < <(/usr/bin/find "$app_bundle" -type f -print0)

if [[ $signed_count -eq 0 ]]; then
  echo "No Mach-O code was found in $app_bundle" >&2
  exit 3
fi

# Sign the outer bundle last. --deep is intentionally reserved for verification.
/usr/bin/codesign \
  --force \
  --timestamp \
  --options runtime \
  --sign "$identity" \
  "$app_bundle"
/usr/bin/codesign --verify --deep --strict --verbose=4 "$app_bundle"

signature_details="$(/usr/bin/codesign --display --verbose=4 "$app_bundle" 2>&1)"
if [[ "$signature_details" != *"Authority=Developer ID Application:"* ]]; then
  echo "The outer app does not have a Developer ID Application authority." >&2
  exit 4
fi
if [[ "$signature_details" != *"TeamIdentifier="* || "$signature_details" == *"TeamIdentifier=not set"* ]]; then
  echo "The outer app has no Apple Developer Team identifier." >&2
  exit 4
fi
if [[ "$signature_details" != *"runtime"* ]]; then
  echo "The outer app is missing the hardened-runtime signature flag." >&2
  exit 4
fi
if [[ "$signature_details" != *"Timestamp="* ]]; then
  echo "The outer app is missing a secure signing timestamp." >&2
  exit 4
fi
if /usr/bin/codesign --display --entitlements :- "$app_bundle" 2>&1 \
  | /usr/bin/grep -q "com.apple.security.get-task-allow"; then
  echo "Release signing refuses the development-only get-task-allow entitlement." >&2
  exit 4
fi

echo "Developer ID signed and verified $app_bundle ($signed_count Mach-O files)."
