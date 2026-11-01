# Mac student checks

The current Mac download is 20261031.14. The Windows download is 20261026.9;
the later Mac current-tracking and large-recording changes are still Mac-only.

The Mac control inventory has 321 sites: 61 have accepted actual-app evidence,
229 are unverified, 8 have source tests only, 3 are blocked, 2 failed, and 18 are
legacy controls no longer exposed. Dynamic instances and parameter combinations
also need their own checks.

The separate Mac review opened the existing 50-neuron project and its full
recording. Tests exercised both recorded conditions, cancellation and restart,
measured replay, Play/Pause, trace search and paging, 2D/3D views, the split view,
membrane and chemical currents, and image export. All 57,950,916 voltage values
and 144,516 trace identities and clocks matched the saved-data comparison.
The installed app, 327 run files and 50 source SWCs were unchanged.

One 0.64-second pause remains during the fresh-process read. The cancelled-load
heading can stay stale, long export legends can clip, and an axis label can
overlap a tick. See DF-0101 in the [bug and fix log](BUG_FIX_LOG.md). General
student verification is unfinished. Existing failures and source-only checks
have not been counted as actual-app passes.
