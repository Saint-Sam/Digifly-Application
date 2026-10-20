# Linked local library visibility

The existing Data Library table enumerates managed download manifests, while
Circuit Builder separately discovers workspace SWC folders and explicit external
bindings. Registering a user-owned folder therefore never made it visible here.

This repair adds a separate linked-local-library section using those same source
providers. Existing Male CNS and full local MANC roots remain in place and are
read-only. Missing registered folders remain visible. Canonical paths deduplicate
sources, while managed acquisitions stay in the existing separate list with an
explicit dataset/subset label. No existing source is copied, rewritten or removed.

An explicit cancellable worker counts regular SWC files/bytes without blocking
the UI or following directory links/junctions. Counts are session snapshots,
not biological completeness or unique-neuron claims. Refresh itself does no
recursive tree scan. Linked rows expose folder opening and counting only;
managed deletion/registration controls cannot target them.

Acceptance requires source/negative tests, actual Mac and Windows packaged
workspace discovery/Register/count controls with isolated settings, real local
Mac library visibility and source/profile preservation, retained exact runtime
and notice documents, and both platform delivery checks. No scientific model,
download, scheduler check or new background schedule is required or authorized.

Current progress and exact artifacts are in `workstation_active_v1.json` under
`shared_fix_delivery.linked_libraries_20261020`.

October20 extension: the user requests a saved shortcut file for external SWCs.
Save library shortcut writes a small `.digifly-library.json` containing only
the local folder, label and dataset identity, and remembers a read-only profile
binding outside the app. Default storage is the profile's `config/library-links`
folder. Existing identical links/bindings are reused; different file bytes are
not overwritten. Open library shortcut reviews the location before registration
and offers local-folder selection if the original device path is unavailable.
No executable actions, credentials, downloads or SWC copies are embedded.
On a different device its user must locate their own copy once; a link cannot
make another computer's disk available. This is an app-readable shortcut, not
an OS-specific Finder alias or Windows shell shortcut/file association.

The first Windows20.1 native tests passed, but root visual inspection found
cramped/overlapping controls at1120x760. This candidate is retained, not published.
The successor uses a scrollable page, two-column action rows and noncollapsing
tables. Both actual successor packages must pass shortcut Save/Open/review,
reloaded external-profile discovery, source preservation and no-overlap checks.
Native updater install/rollback also preserves a real saved-link file and its
profile bytes. The earlier real Mac20.1 full-folder counts remain file-count
observations, not unique-neuron/completeness acceptance.
