# Windows updater completion gate

## October18 cleanup override — current repair

The user now requires obsolete update files to be removed automatically after
a verified successful restart. This supersedes the older retain-every-copy
policy below. The current app and active draft, saved projects, SWCs, datasets,
installed simulators, run data and credentials remain protected. No broad
folder/age-based sweep is permitted. Failed or live updates retain recovery.

After the new MainWindow restores its draft and the installer exits, perform
one bounded background cleanup pass over successful journals for this exact
installation. Remove only unchanged recorded app backups/helper copies,
verified updater-cache ZIP/checksum files and extraction leftovers. Keep small
records and the currently open recovery draft; remove superseded unchanged
auto-generated recovery snapshots, never edited drafts. Unexpected files,
symlinks, Windows junctions, changed copies or uncertain ownership defer that
item safely. Older journals without ZIP paths require checksum and complete
archive-member matching against their committed app inventory; unknown ZIPs
are not cleanup targets. No attempt is made to find/delete unrelated old apps
or manually saved projects in Desktop/Downloads.

Acceptance adds native successful cleanup and a legacy-journal upgrade case
on both actual platforms, plus early-startup rollback with no cleanup. The
paired validator requires this new evidence rather than accepting historical
retention-only receipts. Unit negative cases cover unchanged/edited drafts,
changed trees, live processes, wrong roots, locks, links and a native Windows
junction. A small deletion record reports removed paths/logical bytes and any
items kept; it is not a promise that disk free space increases by that amount.

### October18 measured paired delivery

Completed: local Mac20261018.5 (runtime-preserved archive) and published
Windows0.1.0-alpha.20261018.4, sourcec3cb3ed / native CI39175391897. Both actual
packages pass normal post-restart cleanup, legacy-journal archive identification
and cleanup, and failed-startup restoration with no cleanup. Active drafts and
external SWC/runtime/run/project sentinels remain unchanged. Windows additionally
passes its real junction refusal, existing shared-library/Parquet controls and
private official NEURON Install/Use/Run/Results engineering test. Windows source
regression:189cases,186pass,3platform skips; local wider suite:189cases,185pass,
4platform skips. These are engineering checks, not scientific replication.

Final Mac archive:88,217,264bytes, SHA256
`e205d8bbdaaf3c50a8eb6e1dd89094eddbc502b60985d47229c250e9643d39a9`.
All3085packaged files/140source inputs match. The full-package gate caught
regenerated runtime bytecode/support paths before delivery; the entire accepted
2897-file runtime was copied unchanged, the unchanged unsigned compiled body was
checked, the app re-signed, and all native checks repeated on the final archive.
The old generated runtime/archive remain separate. Actual current-app promotion
preserves the previous app; original user window93345 on.17.4 was not closed or
modified. Save/quit/reopen is needed to use the new current Mac app.

Final Windows archive:225,443,905bytes, SHA256
`9d03f32e85db2c9bdd7440f0e5c055d95a44b2dc9eae0ff799c7903fbfe9df8f`.
All415outer files,3425private runtime files and25reused full notice files checked.
Public postflight preserves all32previous asset IDs/digests, renaming only the
stable Windows pair, leaves public Mac assets unchanged, verifies the public
checksum and proves new-build discovery/no same-build offer. The checks use
offline disposable same-build copies with legacy journal compatibility, not a
live cross-version public install, Windows11 or notarized Mac qualification.

Exact acceptance: `outputs/workstation/paired-updater-cleanup-acceptance-20261018-v1.json`.
Its valid guard passes; a version with unsuccessful cleanup is rejected with
`Missing native post-update cleanup evidence.` Scientific work and schedules
were unchanged. Separately, twelve duplicate expanded app trees from finished
earlier updater tests were removed only after matching every file to retained
checked apps/ZIPs and checking no process used them. All test records/images,
release apps/archives, user data and simulator installations remain available;
see `outputs/workstation/obsolete-updater-test-copy-cleanup-20261018-v1.json`.

October17 paired-platform continuation: the human requires the same fixes in
the Mac app. Its first candidate.20261017.3 passed the compiled neuron-list UI
but failed actual disposable updater startup acknowledgement. Runtime context
and the visible error prove `sys.executable` names nonexistent
`Contents/MacOS/python`, not the actual `main`. The old app and backup were
retained; the helper correctly refused to move/kill the still-live unacknowledged
GUI. The owned test error/window was closed manually, preserving evidence.
This candidate is not the delivered successor.

The repair uses dyld's `_NSGetExecutablePath` with its required-size negotiation,
then resolves the returned path. [Apple's API contract](https://github.com/apple-oss-distributions/dyld/blob/main/include/mach-o/dyld.h)
describes this size/path behavior. Windows keeps its existing loaded-module API.
Both new candidates must pass native install/reopen and startup-failure rollback,
not only the source test or a successful package build. The paired handoff and
`scripts/check_paired_app_fix.py` retain and recheck both artifacts/evidence.
This source change does not establish live cross-version public upgrades or
Developer ID/notarization; those remain distinct qualification.

Measured successor: Mac.20261017.4 and Windows.20261017.3 now both pass actual
packaged original-app refusal, disposable install/MainWindow+draft reopening,
injected early-startup failure/exact rollback/reopening, full app-tree and
directory-identity transitions, retained backup/failed candidate and unchanged
external sentinels/draft. Root viewed both native install/rollback PNGs on both
platforms. Their shared source and exact executable/archive identities are
bound by `outputs/workstation/paired-neuprint-updater-acceptance-20261017-v1.json`.
The current Mac promotion checks that pair before any mutation and retains the
entire previous app. This is an offline same-build engineering test, not a
measured public cross-version install or hung-child auto-recovery claim.

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
