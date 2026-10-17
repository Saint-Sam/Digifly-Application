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
  window PNGs. It never replaces the supplied original app. This is not live
  GitHub transport, Windows11, SmartScreen or scientific validation.
- The Windows workflow fails before artifact acceptance when this runner fails;
  its evidence validator rejects missing/stale archive/executable receipts.
  A release review must retain that source-bound native CI evidence as well as
  existing runtime/SWC/Parquet/notice gates. Source-only tests are insufficient.

Current state/evidence: `workstation_active_v1.json` updater lane. Existing public
Windows and Mac downloads remain unchanged until their successor passes its
applicable gates. macOS implementation is not newly qualified by Windows CI.
