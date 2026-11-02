# Student checks

The latest source includes the first-experiment guide for Male CNS v1.0 DNp01,
TTMn, PSI and DLMn. The Mac walkthrough downloaded the missing cells through the
app, saved separate repair copies, loaded all 16 neurons and completed one
NEURON experiment. Replay and paired traces opened from the saved recording.
The short pulse produced small voltage changes and no spikes.

There are 325 sites in the Mac control inventory: 61 have accepted actual-app
evidence, 12 have source tests only, 229 are unverified, 3 are blocked, 2 failed,
and 18 are legacy controls no longer exposed. The inventory also tracks dynamic
controls and parameter combinations. Each bug entry records its own build and
verification history.

The 16-neuron result check covered 30,346,246 voltage values, 188,486 trace
identities across two conditions and 840 located chemical contacts. A separate
50-neuron recording check covered 57,950,916 values and 144,516 trace identities.
Original SWCs and completed recordings were preserved.

Windows native checks cover the compiled package, included NEURON setup, a tiny
engineering experiment, library controls, the wizard, saved Results and updater
installation and recovery. Windows 11 and clean-Mac runtime setup remain open.
Public release notes identify the delivered builds. See the [bug and fix
log](BUG_FIX_LOG.md) for unresolved loading and export behavior.
