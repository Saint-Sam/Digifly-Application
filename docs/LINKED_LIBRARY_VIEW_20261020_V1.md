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
