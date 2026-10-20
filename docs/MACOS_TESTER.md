# Current Mac tester

Build20261020.3 targets Apple-silicon Macs on macOS14 or newer. Use the explicitly
versioned macOS-arm64 ZIP from the repository release page. There is no current
Intel Mac package.

Unzip and open Digifly Workstation. This alpha is ad-hoc signed, not signed with
an Apple Developer ID and not Apple-notarized. Follow your organization's security
policy; some managed Macs will not allow this tester to open. This is not a claim
of notarized production distribution.

The included isolated Arbor0.12.2 runtime is unchanged. Other selected simulator
installations, downloaded SWCs, saved projects and run data remain external.
No SWC corpus, user settings, token or scientific run is bundled. The app carries
component-specific license documents and upstream source links; Digifly's MIT
license does not replace dependency or model licenses.

This edition repairs Data Library loading of saved simulator selections and
ships an actual embedded build number. Update discovery and installation refuse
older app versions. Successful updates remove only exact unchanged updater-owned
obsolete files; unknown/edited files and failed-update recovery are retained.

Data Library lists linked local folders separately from downloaded subsets.
Save/Open library shortcut controls retain external folder locations across
restarts and updates. A shortcut stores only a local path and dataset label;
on a different device, select that user's local folder once. The page scrolls
instead of compressing tables and buttons. Mac startup disables bytecode-cache
writes so a runtime import cannot mutate the installed bundle during an update.

The final packaged app passed Data Library controls, disposable install/reopen,
older-journal cleanup and injected early-startup restoration with draft and
external-data preservation. These engineering tests use offline same-build
copies, not a full live cross-version upgrade or scientific equivalence test.

Maintainers must stage the reviewed license documents with
`DIGIFLY_MAC_NOTICE_SOURCE` before signing a public tester. The notice collector
uses exact package versions and pinned upstream sources; it does not execute
simulation models or inspect credentials. An ordinary local build without this
review is not automatically a public release.
