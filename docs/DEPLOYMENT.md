# Deployment

Digifly Workstation uses Qt for Python's supported `pyside6-deploy` path. The checked-in
`pysidedeploy.spec` keeps the simulator worker and architecture guide inside the
bundle while leaving NEURON, Arbor, BMTK, and VND as external runtime profiles.

## Supported private-test Macs

The private alpha targets macOS 14.0 or newer. Apple-silicon and Intel Macs use
separate architecture-native downloads; changing a plist label cannot make an
arm64 executable run on Intel. The release workflow builds and verifies both
`arm64` and `x86_64` archives before attaching them to the same GitHub
prerelease.

## Local macOS build

From the repository root:

```bash
./scripts/setup_dev.sh
./scripts/build_macos.sh
```

`setup_dev.sh` selects an available Python 3.11+ interpreter. Set
`DIGIFLY_BOOTSTRAP_PYTHON=/absolute/path/to/python` when a specific build
interpreter is required.

The build appears at `dist/Digifly Workstation.app`. It is ad-hoc signed so its
modified bundle metadata remains internally consistent. This is suitable for
local testing only. It does not make the app trusted on another Mac.

## Private Developer ID release

The repository is privately licensed, but that is separate from Apple's trust
system. A private build intended for another Mac should still be signed with a
Developer ID Application certificate and notarized.

One-time Apple setup:

1. Join the Apple Developer Program and create a **Developer ID Application**
   certificate in Certificates, Identifiers & Profiles. Download the `.cer`
   file and open it to install the certificate and its private key in Keychain.
2. Confirm that Keychain exposes the signing identity:

   ```bash
   security find-identity -v -p codesigning
   ```

   Copy the 40-character hash from the `Developer ID Application` row.
3. Create an app-specific password for the Apple ID, then store notarization
   credentials in Keychain once:

   ```bash
   xcrun notarytool store-credentials digifly-notary \
     --apple-id "YOUR_APPLE_ID" \
     --team-id "YOUR_TEAM_ID"
   ```

   Enter the app-specific password only at the secure prompt. Do not put it in
   this repository or in an environment variable.

After that setup, one command builds, signs, submits, staples, verifies, and
archives the private macOS release:

```bash
DIGIFLY_DEVELOPER_IDENTITY="40_CHARACTER_HASH" \
DIGIFLY_NOTARY_PROFILE="digifly-notary" \
DIGIFLY_BUILD_NUMBER="1" \
./scripts/build_macos.sh --developer-id
```

Increment `DIGIFLY_BUILD_NUMBER` for each distributed build. Release mode is
fail-closed: it never falls back to ad-hoc signing, never accepts passwords on
the command line, and does not replace the existing app until Apple accepts
the notarization. It creates
`dist/Digifly-Workstation-0.1.0-macos-<architecture>.zip` only after the ticket
has been stapled to the app, re-extracted, and re-verified. The ZIP and its
`.sha256` checksum are promoted from same-filesystem staging paths only after
those checks pass. Submission reports and standard-error diagnostics are retained under
`deployment/notarization`.

The Developer ID lane deliberately starts with no entitlements. Digifly runs
NEURON, Arbor, and BMTK as separate external processes, so the GUI currently
does not need development-only execution exceptions or relaxed library
validation. Add an entitlement only after a hardened-runtime test demonstrates
a specific need.

This machine currently has no valid code-signing identity installed, so the
first Developer ID run cannot be performed until step 1 is complete.

## Distribution hold points

Developer ID proves who signed an app; it does not grant rights to third-party
material. Before sharing even a privately licensed build, finish the
third-party notice/source review, resolve redistribution permission for the
Augustin/ModelDB mechanism derivatives, confirm ownership of the Digifly gap
sources, and preserve the provenance for the Digifly-owned paired-GF icon. The
temporary PySide icon has been removed. Qt Virtual Keyboard is not used by
Digifly and is explicitly excluded from the build and artifact audit.
Local builds use the host CPU architecture and declare macOS 14.0 as their
minimum system version. The GitHub private-release workflow produces separate
Apple-silicon (`arm64`) and Intel (`x86_64`) artifacts. Both must pass their
native runner build, bundle audit, code-signature check, archive test, and
minimum-version assertion before publication.

Generated Nuitka and Qt deployment directories are ignored by Git.
