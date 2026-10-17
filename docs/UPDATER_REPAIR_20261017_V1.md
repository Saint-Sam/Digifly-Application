# Windows updater completion gate

This is the updater lane of `../codex.md`, not a new workflow authority.
The October17 human reports a verified download followed by a disabled-install
message. Inspection confirms the shipped handler had no installer helper;
historical download/ZIP checks did not qualify installation. The old app cannot
bootstrap this repair itself: one manual download/extraction is necessary.

The normal user path must select the newest explicit platform/build version,
verify the checksum, request install/restart consent, save a separate draft,
stage outside the installed app, and copy the current app to an external helper
location. This copy avoids holding Windows executable/DLL files in the directory
being replaced, without requiring a separate Python installation or elevation.
The helper waits on the original process, rechecks both complete app trees,
retains the previous app under an exact transaction-specific rollback name,
replaces only that app directory and starts its real MainWindow/recovered draft.
An executable-bound startup acknowledgement commits the transaction. No user
workspace, SWC collection, credential, installed simulator, registry or PATH
is an installation target. Active work must finish/safely stop first.

If the candidate exits before acknowledgement, restore the exact old directory,
retain the failed candidate and reopen the draft in the restored app. A live
unacknowledged candidate is never killed or moved: retain its backup, report
recovery required and do not retry automatically. Unknown files added inside a
Windows app directory block automatic replacement; keep user data outside it.
Archives, previous app copies and failure evidence are retained, not deleted.
Disk, unsafe paths/links, changed staged files and duplicate locks must refuse
before replacing the live app. No signature/authenticity claim follows from
checksums alone; download origin remains the exact GitHub release over HTTPS.

Acceptance on the actual Windows deliverable:

- `tests/test_updates.py`, `test_update_installer.py` and `test_updater_gate.py`
  exercise version ordering, checksum/path/tamper guards, duplicate locks,
  startup acknowledgement, failed startup rollback and live-child safety.
- `scripts/check_packaged_updater.py` drives the actual packaged Check for
  updates controls and workers against an offline verified-archive fixture.
  Both disposable install/reopen and startup-failure/restore/reopen must pass,
  with exact tree hashes, external-state sentinels, unchanged draft and visible
  window PNGs. The actual EXE must refuse the automatic engineering route on
  an unmarked original app, before Qt or staging writes; only the runner's
  explicitly marked disposable copies can use it. It never replaces the
  supplied original app. This is not live
  GitHub transport, Windows11, SmartScreen or scientific validation.
- The Windows workflow fails before artifact acceptance when this runner fails;
  its evidence validator rejects missing/stale archive/executable receipts.
  A release review must retain that source-bound native CI evidence as well as
  existing runtime/SWC/Parquet/notice gates. Source-only tests are insufficient.

Current state/evidence: `workstation_active_v1.json` updater lane. Existing public
Windows and Mac downloads remain unchanged until their successor passes its
applicable gates. macOS implementation is not newly qualified by Windows CI.

Windows process identity/wait APIs follow Microsoft's primary documentation:
[GetModuleFileNameW](https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-getmodulefilenamew),
[OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess)
and [WaitForSingleObject](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject).
Use the loaded Windows module path (canonicalized for short names), not a
compiler's possibly retained `sys.executable` name, to identify the installed
app and its acknowledged executable hash. Exact native runtime context is
retained in the disposable packaged checks; no credential contents are logged.

## October17 measured delivery

Windows `0.1.0-alpha.20261017.1`, source
`a15bc3338e69c98412c91333f4fbb7e22e118fbd`, passed native CI39044154231:
145 source cases and one non-Windows negative skip, actual compiled EXE runtime
setup/Run/Results, SWC intake, full supplied Parquet and the three updater gates
(unmarked original refusal, disposable install/reopen, failed-startup rollback/reopen).
The actual directory identities, full app trees, draft and external sentinels
were checked. Root viewed both reopened MainWindow PNGs, including their distinct
installed/restored status messages; CI-size label clipping remains visible.

Native runtime context confirms `sys.executable` was the app folder's
`python.exe`, while the actual loaded process module is `Digifly Workstation.exe`.
The Windows API identity repair is measured, not just a source-level inference.
The native updater uses an offline verified-archive fixture and same-build
disposable copies, not a full live cross-version public archive upgrade.

Final415outer/3425runtime files pass full CRC/path/hash/source/notice checks.
All387original payload files survive the notice-only overlay; installed runtime
files and25 full reused notices match the preceding published edition. The
published ZIP is225331078bytes, SHA256
`6ef9124779f8bf51836b8890d35ec238616065b028f4ef5f0cee70bcb339a768`.
Independent public postflight preserves all16prior asset IDs/bytes/digests, only
renames the old stable Windows pair to rollback, and leaves Mac assets intact.
Real public discovery chooses the new build for.16 and offers no update for.17;
public checksum transport passes. Existing download-only apps need the one-time
manual Extract All/bootstrap. Windows11/SmartScreen/macOS-helper/full scientific
qualification and live-hang automatic recovery are not claimed.

Evidence: `outputs/workstation/windows-updater-delivery-review-20261017-v1.json`,
`windows-updater-full-audit-20261017-v1.json`,
`windows-updater-publication-postflight-20261017-v1.json` and native receipts below
`windows-updater-ci-20261017-v4/`, all relative to `outputs/workstation/`.
