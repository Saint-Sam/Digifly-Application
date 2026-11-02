# Bug and fix log

This is the running record of reported problems, repairs and remaining work in
Digifly Workstation. New entries are added as debugging happens, not only at
release time. Historical entries below were reconstructed from the dated repair
records; they are not a claim that every past issue has been recovered.

Status matters: a source test is not an installed-app test. “Checked package”
means the stated build passed its recorded checks, not that every feature,
computer or scientific model is verified. Dates are the dates of investigation
or delivery, not necessarily when the bug was introduced. Release notes can
summarize this log without replacing it.

## In progress — Mac student-readiness review, October 28, 2026

Windows work is deferred for this review. The starting package is 20261026.9.
The starting repairs below were installed in Mac build 20261028.4. The latest
checked local build is 20261030.4; later entries distinguish its measured scope.
These changes are not publicly released. The student-readiness review remains incomplete, and
each entry distinguishes tested outcomes from remaining checks.

### DF-0001 — Doctor incorrectly requires an old Arbor source folder

- **Problem:** Doctor warns about a missing legacy staging tree even when the
  app-owned Arbor execution files are available.
- **Repair:** Check the actual worker, channel translator and gap-mechanism
  resources. Keep optional legacy presets separate from execution readiness.
- **Verification:** Reproduced in the packaged Mac app. The regression failed
  before the repair; source tests now cover both available and missing files.
- **Status:** Source and native source-availability checks pass; actual isolated
  candidate20261028.2 Doctor now reports Arbor PASS. Installed in Mac20261028.4. This
  does not certify every Arbor model or a user's compiler installation.

### DF-0002 — A timed-out runtime check displays embedded program text

- **Problem:** A BioNet import timeout fills the error with the internal probe
  program instead of giving a useful explanation.
- **Repair:** Report the timeout and that the runtime remains unverified. Do
  not report it as uninstalled or silently extend the timeout.
- **Verification:** The real Doctor timed out at 20 seconds. A separate
  import-only diagnostic then succeeded, and a later real Doctor check passed
  BioNet without changing its installation. The concise-error regression passes.
- **Status:** Source/native and actual20261028.2 timeout-message checks pass;
  the actual BioNet20second timeout still occurs. The original cause is not
  fully resolved. An import-only trace later succeeds in16.37seconds, with a
  12second stack in a SciPy native extension; a following case takes1.28seconds.
  Ordered cold/warm cases do not establish an output-capture defect. DF-0050
  covers asynchronous checking, not a change to model integration or watchdogs.

### DF-0003 — Review mode opens a page without selecting its sidebar button

- **Problem:** The manual-review launcher shows Data Library while Workspace
  remains selected, making the first Workspace click ineffective.
- **Repair:** Select the page through the normal sidebar routing.
- **Verification:** Actual review-mode reproduction and a passing source test
  using MainWindow. Not reproduced as a normal-launch navigation fault.
- **Status:** Actual isolated20261028.2 held review selects Data Library correctly;
  the first Workspace click works. Native/source checks and actual observation
  recorded. Installed in Mac20261028.4; no public release.

### DF-0004 — New/Open can replace the editor during an active experiment

- **Problem:** Project replacement did not share the active-work guard used
  for shutdown and updates.
- **Repair:** Refuse New/Open while an experiment, download or update task is
  active, with instructions to Stop or Cancel first. Preserve the current editor.
- **Verification:** Failing-before and passing-after source tests retain the
  sentinel project path and experiment name. Native engineering check added.
- **Status:** Source/native guards pass and are installed in Mac20261028.4.
  Actual4 New is refused during a real background readiness check; that check
  continues afterward. A real active-simulation menu interaction is still separate.

### DF-0005 — Editable NetPyNE runs are missing from lifecycle guards

- **Problem:** The editable NetPyNE worker was absent from the common busy
  check and shutdown guard.
- **Repair:** Include that worker in update/project-replacement checks and
  refuse shutdown while it runs.
- **Verification:** Source tests exercise the actual guards with an inert busy
  worker, including a rejected close event. No scientific job was started.
- **Status:** Source/native inert-worker checks pass; installed in Mac20261028.4.
  Actual editable-model run interaction remains a separate check.

### DF-0006 — A saved profile can hide newly selected local SWC roots

- **Problem:** Canonical connectome choices ignored roots discovered in a newly
  selected legacy workspace whenever any saved profile existed.
- **Repair:** Merge discovered roots with saved sources, deduplicating exact
  dataset/root/server identities and respecting explicit server bindings.
- **Verification:** The stronger UI restoration test exposed the missing root.
  Source tests now require both restored local roots to be reachable through
  their canonical connectome choices; profile and source bytes stay unchanged.
- **Status:** Source repair checked and installed in Mac20261028.4; actual
  newly selected-workspace root restoration remains pending.

### DF-0007 — Results accepts unrelated JSON as a legacy simulation

- **Problem:** Opening a control inventory, project or empty JSON object shows
  an “unknown” legacy result instead of rejecting the wrong file.
- **Repair:** Dispatch recognized result formats explicitly; require the legacy
  summary's identifying fields before invoking its reader.
- **Verification:** Reproduced through the packaged Open summary dialog. Four
  wrong-file source cases failed before the repair. Recognized legacy summaries
  must still retain their failed scientific-evidence checks.
- **Status:** Source/native checks pass. Actual20261028.2 Open summary refuses the
  unrelated inventory and leaves Results at No result loaded, with playback and
  export still disabled. Existing legacy failure checks remain intact. Installed
  in Mac20261028.4, whose native refusal and software-reader checks also pass.

### DF-0008 — Idle New/Open/Quit can discard unsaved edits

- **Problem:** Source review found no complete dirty-state Save/Discard/Cancel
  protection for idle New/Open/Quit.
- **Repair:** Compare saved project content with a clean baseline, with
  Save/Discard/Cancel before replacement or Quit. Cancelled/failed saves cancel
  the action. Treat invalid edited controls as unsaved. Save all selected runtime
  paths, preserve explicitly unconfigured paths instead of inserting an old
  interpreter default, and write the project atomically. The editable NetPyNE model has a separate
  save decision; it is not included in a Circuit/Experiment project.
- **Verification:** Five regressions fail before the repair. The first affected
  eleven-case after-suite and expanded 42-case suite pass. A further nine-case
  project/log suite passes, including empty-runtime and atomic-save failures.
  Wider tests cover Open cancellation, separate
  model saving and update recovery changing during preparation. Actual destructive
  replacement is never tested on a user's working draft.
- **Status:** Native20261028.3 document-guard fixtures and actual modern/legacy/
  rollback update cases pass. Actual isolated28.3 New/Cancel, Save As/Cancel,
  New→Save→Cancel, explicit discard, Save As/reopen and Quit/Cancel preserve the
  expected disposable draft and runtime choices. The separate model editor also
  passes actual example/Cancel, Save/Cancel, Save/reopen and Quit/Cancel checks.
  Its app-exported settings exactly match the strict model's derived settings;
  no simulation is claimed. Installed in Mac20261028.4.
  Unapplied channel
  editor values and other dynamic controls remain separate review cases; no
  every-setting protection claim follows from the serialized-document guard.

The [Mac student-readiness record](STUDENT_READINESS_MAC_20261028.md) tracks the
current package, control coverage, failing tests and next actual-app checks.
The initial inventory contains 301 UI construction sites; it does not mean
301 features have passed, and shared controls have additional dynamic instances.

### DF-0050 — Doctor performs slow runtime imports on the UI thread

- **Date found:** October 28, 2026.
- **Problem:** The Doctor handler calls the runtime probes synchronously after
  one event-processing call. During slow imports, other UI actions can wait
  behind those checks, including navigation to a running experiment's Stop control.
- **Evidence:** Direct handler/probe data-flow inspection. A Doctor attempt also
  coincided with control-service errors, but this does not establish that it
  caused the separate attachment failures or that a Stop request was lost.
- **Repair:** Run one shared Doctor/Engines check off the UI thread, reject stale
  results after choices change, and reuse a timestamped report only for matching
  choices. Repeated clicks do not spawn another check. Update/close/project guards
  include the check. The full BioNet import gets an explicit finite 60-second limit;
  simulation limits and capability checks are unchanged.
- **Verification:** The original test records a blocked GUI timer and a probe on
  the UI thread. The first 21-case after-suite and expanded 18-case suite pass. The
  expanded suite checks stale results, exceptions, navigation/cache, busy guards
  and finite budgets. Two initial budget fixtures had incorrect constructor
  arguments, were corrected and remain in the failed test record; they were not
  runtime failures. Native20261028.3 inert-thread/timer/stale-result checks pass.
- **Status:** Actual20261028.3 Doctor shows Checking, permits navigation to Results
  and back while still Checking, then reports full BioNet import PASS. Engines
  reuses its timestamped report. A New attempt reached an already-finished check,
  so that earlier attempt is not counted as an active-work refusal. Actual4 New
  is refused while the check runs; the report then completes normally. Installed
  in Mac20261028.4; scientific-run interaction remains separate. DF-0002's clearer timeout text alone
  did not fix this, and the earlier cold-import cause is not fully established.
- **Record:** [Doctor handler](../src/digifly_app/ui/main_window.py).

### DF-0051 — Compact library menus rely on a test-only focus step

- **Date found:** October 28, 2026.
- **Problem:** Add data did not expose a menu or import picker in the current
  manual-control session. Its existing native fixture explicitly raised and
  activated the window before clicking; the actual button did not do that.
- **Repair:** Put that focus step in the production menu button, and remove the
  fixture's workaround while retaining its visible-menu/action assertions.
- **Verification:** An ordered-call regression fails before the change. This
  tests the new focus behavior, not the cause of the earlier failed observation.
  Native and actual manual menu checks on the new package are still required.
- **Status:** Native20261028.3 menus/routes pass without the old test-only focus
  step. The user confirms Add data opens with a normal click in the test app.
  This confirms menu display, not every manual menu action. Automation still
  cannot observe it; that control-tool limitation's cause remains unresolved.
  No download or Run was repeated for an unseen menu. Installed in Mac20261028.4;
  its native menu routes also pass, without claiming every human menu action.

### DF-0052 — Results “Reveal” opens the file instead of its folder

- **Date found:** October 28, 2026, actual candidate20261028.2 review.
- **Problem:** The Reveal button sends an existing artifact file to its default
  application. Its label suggests locating that file, not opening it.
- **Evidence:** A labeled software-only summary loads correctly, but Reveal does
  not expose its containing folder in the observed Finder window. The actual
  `_reveal` handler explicitly opens the file itself. A separate file viewer was
  not observed; unrelated Finder windows were preserved, not closed as test output.
- **Repair:** Open the containing folder for files, retain directory targets,
  label the button Open folder, and report unavailable folders/desktop-launch
  failures. Do not launch the artifact itself.
- **Verification/status:** The actual containing-folder regression fails before
  repair. Initial20case and expanded35case affected suites pass afterward, with
  the existing-directory and failure paths covered. Candidate20261028.4 passes
  its native folder-route/error checks and package-integrity checks. Actual4
  opens the containing test folder, and closing that owned window preserves
  Downloads. Installed in Mac20261028.4. Found after28.3 freeze, so that older
  candidate does not contain this repair.
- **Scope:** The summary was a clearly labeled file-format fixture, not neurons,
  scientific measurements or a simulator run. Artifact existence is not scientific
  acceptance, and the UI's artifact count was not treated as such.

### DF-0053 — Doctor confuses an Arbor package path with a runnable import

- **Date found:** October 28, 2026, source readiness review.
- **Problem:** The Arbor runtime check asks whether Python can find a package,
  but does not import it. A broken native installation can therefore receive
  PASS. A known working app runtime does not validate this failure path.
- **Evidence:** The handler uses `find_spec` only. An owned fake Arbor package
  raises on import but remains discoverable; the regression checks the actual
  child-process probe, with no real Arbor simulation or runtime modification.
- **Repair:** Require a bounded actual import, with bytecode writes disabled and
  a clean Arbor environment matching its execution boundary. Distinguish a wholly
  absent module from an installed package with a missing dependency. Keep timeout
  errors concise; source availability and Run preflight remain separate.
- **Verification/status:** The owned broken-import test fails before repair.
  The affected20case and expanded35case suites pass afterward, including a real
  child import, no new bytecode, dependency failure and timeout/error cases.
  Candidate20261028.4 passes the native failure guard and package-integrity
  checks. Actual4 selected-runtime Doctor reports Arbor PASS through the new
  import route. Installed in Mac20261028.4. Found after28.3 freeze; that older
  package does not contain this repair.
  No scientific acceptance follows from an import.

### DF-0054 — Student inventory omits shared control subclasses

- **Date found:** October 28, 2026, during actual Help-button checks.
- **Problem:** The static inventory recognizes direct Qt constructors but omits
  calls to shared HelpButton and scroll-safe numeric subclasses. A matching source
  hash alone therefore cannot establish that all relevant sites are accounted for.
- **Repair:** Follow control inheritance and explicit import aliases without
  importing app code. Include the derived constructor call sites, while keeping
  factories and dynamic instances subject to manual enumeration.
- **Verification:** Two regressions fail before the change. The affected
  inventory/harness/log suite passes 27 cases afterward. The real older inventory
  is refused for missing sites even though UI source hashes still match. The new
  306-site inventory passes the in-progress check and correctly refuses completion
  while its outcomes remain unverified. An initial expanded test command named a
  nonexistent test file; it collected no tests and is not counted as a pass.
- **Status:** Developer-checker repair, not a simulator or app behavior change.
  Actual Help opening/Close was separately checked for 36 labeled instances.
  This does not establish every setting interaction or student readiness.
- **Record:** Student-readiness checker (local test record).

## Historical repairs

### DF-0009 — Male CNS source selection exposes only small subsets

- **Dates:** October 1–2, 2026.
- **Problem:** Old neuPrint subsets and incomplete local indexing were mistaken
  for the full Male CNS library; a successful giant-fiber lookup hid missing TTMn.
- **Repair:** Recover the actual legacy dataset location, index the full source
  and register exact dataset/release identity. Retrieve missing neurons through
  the packaged app with the normal workspace-token feature.
- **Verification/delivery:** Real TTMn and non-GF controls rendered in checked
  Mac packages. The later source inventory matched the authoritative 165,114
  Traced release IDs. “Traced” coverage is not every annotation status or a
  guarantee that each skeleton is simulation-ready.
- **Record:** Local source recovery (local test record).

### DF-0010 — Mixed Male CNS coordinate units separate connected anatomy

- **Date:** October 2, 2026; checked Mac build 20261002.5.
- **Problem:** Different export conventions made DNp01 and TTMn appear far
  apart. Moving the camera or shifting individual neurons would hide the cause.
- **Repair:** Apply the documented, source-bound unit conversion once during
  preview/staging; keep raw SWCs, topology and original coordinates untouched.
  Unknown conventions refuse rather than being guessed.
- **Verification:** The actual 10-neuron view, preview/staging geometry and
  custom-save roundtrip passed. No per-neuron translation or invented bridge.
- **Record:** Coordinate repair (local test record).

### DF-0011 — Circuit settings are visible but unavailable to execution

- **Dates:** October 2–5, 2026.
- **Problem:** Channel, regional recording/stimulation and electrical settings
  could be drafted but rejected or lost at an execution boundary.
- **Repair:** Carry explicit channel identity and regional overrides through
  validated NEURON, Arbor and BioNet mappings. Add measured compartment playback
  and explicit geometric AIS-proxy behavior; refuse unmapped requests.
- **Verification/delivery:** Checked packaged classic/regional runs and saved
  overrides in all three engines, followed by bounded electrical controls.
  Engineering voltage limits passed for the stated fixtures, not arbitrary
  biological circuits, arbitrary custom NMODL or portable electrical MPI.
- **Record:** [Channel execution history](CHANNEL_EXECUTION_REPAIR.md).

### DF-0012 — Fresh Arbor mechanism compilation fails at the LLVM boundary

- **Date:** October 3, 2026; checked Mac build 20261002.17.
- **Problem:** An LLVM17 static wheel and LLVM16 local linker failed together;
  an older cached catalogue had concealed fresh-build failure.
- **Repair:** Use the source-locked standalone CPU mechanism ABI builder and a
  new cache version, without changing equations or linking a second solver.
- **Verification:** Fresh compile/load and actual packaged electrical control
  passed. The failed candidate and old cache evidence were retained.
- **Record:** Electrical delivery (local test record).

### DF-0013 — Fine-step Arbor sampling repeats a native state

- **Date:** October 5, 2026; checked Mac build 20261005.7.
- **Problem:** A floating-point boundary placed a requested sample before the
  intended native step, repeating the preceding state and failing clock checks.
- **Repair:** Request observations strictly inside the intended step; retain
  native integration, values, events, thresholds and complete-clock validation.
- **Verification:** Actual saved/reopened three-engine chemical control passed
  the unchanged engineering gates at the declared finer timestep. Earlier
  coarser-step numerical failures remain failures, not retroactive passes.
- **Record:** [Sampling and delivery](CHANNEL_EXECUTION_REPAIR.md#delivered-7-packaged-app-qualification-october5).

### DF-0014 — Saved BioNet results compare different cell populations

- **Date:** October 8, 2026; checked Mac Results repair 20261008.2.
- **Problem:** Conversion rejects an otherwise saved run because sampled-cell
  paper rates are compared with the all-owned-cell spike census.
- **Repair:** Verify sampling identities, window and denominator separately
  from the complete owned-cell census.
- **Verification:** Actual saved-run conversion, raster filtering, export and
  reopen passed without rerunning the model. The original scientific inputs
  and execution remained frozen; publication-level replication is unfinished.
- **Record:** BioNet saved-results repair (local test record).

### DF-0015 — Combined neuron names behave differently from individual searches

- **Dates:** October 16–18, 2026.
- **Problem:** Numeric-ID defaults and literal regex interpretation made a
  DNp01/TTMn/PSI list fail despite successful individual searches.
- **Repair:** Default to name/ID list mode: comma, semicolon and newline union;
  exact numeric IDs; explicit advanced regex modes. Show returned/missing names
  and truncation, and freeze query inputs while work is active.
- **Verification/delivery:** Shared source and compiled Mac/Windows controls
  passed. Live authenticated Windows retrieval remained a separate limitation.
- **Record:** Combined selection repair (local test record).

### DF-0016 — Downloads create separate search-named SWC collections

- **Date:** October 18, 2026; checked Mac 20261018.4 / Windows 20261018.3.
- **Problem:** Each search produced a new primary folder rather than extending
  one library for its exact server/connectome/release.
- **Repair:** Save each neuron under
  `export_swc/{AN,DN,IN,MN,SN,UNKNOWN}/type/body-ID/body-ID.swc`.
  Scan existing files, default to Keep, and request explicit per-neuron Replace
  approval before downloading replacement bytes. Retain the previous copy.
- **Verification:** Compiled Keep, Cancel, Replace, local intake and combined
  loading checks passed. Changed files, duplicates and unsafe paths refuse.
- **Record:** Single-tree storage (local test record).

### DF-0017 — Replacement moves a classified neuron into UNKNOWN

- **Date:** October 18, 2026.
- **Problem:** Less complete incoming metadata reclassified an existing
  MN/TTMn location during replacement.
- **Repair:** Keep the exact reviewed destination instead of reclassifying it.
- **Verification/delivery:** Source and final compiled single-tree replacement
  controls passed for the builds recorded in DF-0016.
- **Record:** Classified-folder repair (local test record).

### DF-0018 — Valid Windows inventory paths fail validation

- **Date:** October 18, 2026.
- **Problem:** Native path parsing misread forward-slash inventory paths,
  rejecting valid Windows files before package acceptance.
- **Repair:** Use platform-independent inventory path grammar; retain unsafe
  path refusals.
- **Verification/delivery:** The original failed Windows check was preserved;
  the repaired compiled Windows package passed valid and unsafe-path cases.
- **Record:** Portable path repair (local test record).

### DF-0019 — Windows updater stops after downloading

- **Date:** October 17, 2026.
- **Problem:** The updater verifies the download but displays an installer-helper
  placeholder instead of replacing and relaunching the app.
- **Repair:** Implement platform installation/relaunch with startup verification
  and rollback, preserving external data and the user's draft.
- **Verification/delivery:** Actual packaged normal-install and failed-startup
  rollback controls passed. Old download-only builds need one manual bootstrap.
- **Record:** [Updater repair](UPDATER_REPAIR_20261017_V1.md).

### DF-0020 — Missing build identity offers an older Mac update

- **Date:** October 19, 2026.
- **Problem:** The Mac package omitted its build-identity file; upload-time
  fallback offered an older app, which then rejected a saved runtime resource.
- **Repair:** Embed/read the actual build identity, use the supported older
  Mac metadata fallback, and refuse unknown, equal or older replacement builds.
  Preserve the saved simulator profile rather than deleting its bindings.
- **Verification:** Actual historical-catalog and old-archive refusals, saved
  simulator kinds, Data Library controls and installed executable were checked.
- **Record:** [Mac downgrade repair](UPDATER_REPAIR_20261017_V1.md#october19-mac-downgrade-and-release-folder-repair).

### DF-0021 — Successful updates leave obsolete generated copies behind

- **Dates:** October 18–19, 2026.
- **Problem:** Updates retained generated backups/cache files; initial cleanup
  did not cover unrelated old release copies in the development output folder.
- **Repair:** After a verified restart, retire only unchanged files owned by
  successful update journals. Separately verify redundant development copies
  against retained recovery archives before removing them.
- **Verification:** Normal/legacy cleanup, changed-file protection and failed
  startup recovery passed. SWCs, runtimes, run data and user-saved files remain
  protected; this is not a broad age-based disk sweep.
- **Record:** [Cleanup boundaries](UPDATER_REPAIR_20261017_V1.md).

### DF-0022 — Full local SWC libraries are absent from Data Library

- **Date:** October 20, 2026.
- **Problem:** Small downloaded subsets appear, but external full-library roots
  do not appear as linked libraries.
- **Repair:** Show read-only linked roots separately, with explicit counting and
  folder actions. Add persistent library shortcuts without copying source SWCs.
- **Verification/delivery:** Actual saved-profile Mac interaction and restored
  project checks are recorded; counts are snapshots, not completeness claims.
- **Record:** [Linked library history](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0023 — Data from other releases leaks into the selected connectome page

- **Date:** October 20, 2026.
- **Problem:** A MANC pair table and unrelated linked libraries appear on Male
  CNS pages and under releases to which they do not belong.
- **Repair:** Scope tables, links and managed data to the exact selection. The
  supplied MANC table was explicitly assigned to v1.2.1 by the operator.
- **Verification/delivery:** Saved-profile release filtering and actual Mac
  navigation checked. Unknown metadata is not guessed from a filename.
- **Record:** [Exact-release filtering](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0024 — Library and Circuit Builder choices are cluttered and inconsistent

- **Dates:** October 20–21, 2026; requested interface changes.
- **Problem:** Flat subset lists, oversized actions and disconnected source
  selectors obscure which connectome/release a user is working in.
- **Change:** Inline connectome/release navigation, compact common actions,
  family-folder counts/drilldown, and canonical Circuit Builder source choices
  mirroring Data Library. Keep underlying source identity explicit.
- **Verification/delivery:** Recorded actual Mac navigation, saved/reopened
  selection and packaged library controls. A layout change is not a simulator test.
- **Record:** [Library and builder history](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0025 — Simulator chooser opens under the connectome controls

- **Date:** October 21, 2026.
- **Problem:** The simulator choice panel expands in the wrong part of Circuit
  Builder, separating it from its label.
- **Repair:** Anchor each inline chooser beneath its own field.
- **Verification/delivery:** Packaged placement and navigation checks recorded
  alongside the common Mac/Windows library repair.
- **Record:** [Inline chooser repair](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0026 — Open connectivity folder fails before a file exists

- **Date:** October 22, 2026.
- **Problem:** The folder action has no usable target for a fresh connectivity
  library, even though it is offered in the dialog.
- **Repair:** Resolve the selected library's real connectivity location and
  handle the not-yet-created folder explicitly.
- **Verification/delivery:** Recorded packaged folder-action checks in the
  shared connectivity repair, with all dialog controls reviewed separately.
- **Record:** [Connectivity folder repair](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0027 — Cancel/restart leaves partial connectivity downloads

- **Date:** October 22, 2026.
- **Problem:** Cancelled downloads leave incomplete files; restarting can
  create redundant candidates or download before asking about an existing file.
- **Repair:** Check existing data before transfer, request replacement approval,
  and remove owned partials on cancellation without removing the accepted file.
- **Verification/delivery:** Source and native cancellation/replacement checks
  recorded. Changed/unowned files are not cleanup targets.
- **Record:** [Cancellation repair](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0028 — Male CNS orientation and TTMn labels disagree across pages

- **Date:** October 23, 2026.
- **Problem:** Male CNS views are upside down; TTMn can appear as UNKNOWN or
  retain conflicting saved names/families between library and builder paths.
- **Repair:** Apply one display convention and exact dataset-scoped annotation
  merging. Keep type TTMn and motor family MN where authoritative metadata
  supports them; do not equate IDs across specimens or change raw anatomy.
- **Verification/delivery:** Native orientation/TTMn/source identity and saved
  label checks passed in the final shared 20261023.6 packages.
- **Record:** [Orientation and label repairs](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0029 — Later connectivity downloads do not extend the earlier graph

- **Date:** October 23, 2026; checked Mac/Windows 20261023.6.
- **Problem:** Separate selected-neuron acquisitions leave separate graphs and
  omit cross-edges between earlier and newly downloaded neurons.
- **Repair:** Grow one transactional graph per exact server/connectome/release;
  query both directions across the growing selection, keep unique directed
  pairs, and replace repeated observations rather than summing them twice.
  Both builders resolve the current graph; saved circuits retain their revision.
- **Verification:** Successive acquisitions, cross-edges, deduplication,
  cancellation, isolation and actual builder/save-reopen checks passed.
  Pair counts alone still do not define synapse locations or physical strength.
- **Record:** [Cumulative connectivity](LINKED_LIBRARY_VIEW_20261020_V1.md).

### DF-0030 — Updater still expects removed checksum download files

- **Date:** October 23, 2026; checked Mac/Windows 20261023.6.
- **Problem:** After checksum sidecars were removed from public downloads, the
  actual updater still tried to reopen one during installation preparation.
- **Repair:** Carry the verified release-asset digest through preparation and
  verify again before installation; retain older-format compatibility.
- **Verification:** Actual modern, legacy and failed-startup rollback paths
  passed. Failed 23.5 evidence remains; old updater bootstrap limits are explicit.
- **Record:** [Digest-only updater repair](UPDATER_REPAIR_20261017_V1.md).

### DF-0031 — Fresh neuPrint circuits cannot run connected experiments

- **Dates:** October 25–26, 2026.
- **Problem:** Newly downloaded DNp01–TTMn–PSI cells reach an unsupported
  connected-execution path. Aggregate pair counts do not supply contact sites.
- **Repair:** Implement explicit editable strength, timing and placement models;
  retrieve and bind actual contact locations through the app. Mark gap pairs
  placed at chemical sites as a model assumption, not observed electrical anatomy.
- **Verification/delivery:** Actual fresh MANC v1.2.1 and Male CNS v1.0 app
  acquisitions and bounded three-engine connected runs are recorded. Original
  downloads/contact coordinates remained unchanged. Not whole-paper replication.
- **Record:** Fresh-circuit tests (local test record).

### DF-0032 — Inactive settings and comparison names alter experiment behavior

- **Date:** October 26, 2026.
- **Problem:** Malformed inactive comparison JSON blocks Control; explicit empty
  conditions/stimuli reset on reopen; a comparison named Control is misclassified;
  inactive fields remain editable; rounded-zero pulse frequency causes failure.
- **Repair:** Validate active intent, preserve explicit empty settings and stable
  condition identity, gate inactive editors and reject invalid pulse timing.
- **Verification:** Source/widget/native roundtrips and selected actual runs
  passed. The interaction matrix's 907 pairs are configuration checks, not
  907 simulations or every possible parameter value.
- **Record:** Experiment settings tests (local test record).

### DF-0033 — All-compartment recording is too large or fails at playback

- **Date:** October 26, 2026.
- **Problem:** Verbose voltage output consumes space; readiness accepts a request
  that exceeds the Results playback budget; BioNet repeatedly loads full reports.
- **Repair:** Losslessly compress voltage/map output, read BioNet reports in
  batches and enforce the playback budget before Run. No hidden downsampling or
  reduced precision. Repair related Results labels, scopes and timeline controls.
- **Verification/delivery:** Actual all-six-cell, all-source-edge recording for
  both datasets and all three engines passed; canonical values were checked
  against native output. Oversized requests refuse early. Large arbitrary
  circuits and every recording interaction are not certified by those controls.
- **Record:** Recording and playback tests (local test record).

### DF-0034 — Added filler cable can impede giant-fiber propagation

- **Dates:** October 26–27, 2026; checked local 20261026.9 packages.
- **Problem:** The voltage wave weakens around a healed gap; the existing
  endpoint-derived filler geometry can impose a large electrical bottleneck.
- **Change:** Test a separate opt-in axon-aware bridge copy using adjacent intact
  axon diameters. Preserve every original point, radius, real edge and contact
  location; retain the original policy and both model copies.
- **Verification:** Bounded MANC and Male CNS three-engine checks and original
  input checks passed. The MANC main-shaft control changed from no 0 mV crossings
  to crossings along all declared tested sites. This is a modeling alternative,
  not proof of reconstructed anatomy or every protocol's normal propagation.
- **Record:** Bridge comparison (local test record).

### DF-0035 — Derived bridge reports fail on last-bit platform rounding

- **Dates:** October 26–27, 2026; checked local Mac/Windows 20261026.9.
- **Problem:** Mathematically equivalent derived volume/resistance summaries can
  differ in their final floating-point bit, causing a false refusal.
- **Repair:** Allow relative 1e-12 only for the declared derived report fields.
  Geometry, material, policy, source identity and altered-input refusals stay
  strict; solver geometry and equations are unchanged.
- **Verification:** Native positive and meaningful altered-geometry refusal
  controls passed. The checked packages remained on the approved test branch;
  they were not a new public release.
- **Record:** Roundoff and package checks (local test record).

### DF-0037 — Newly acquired MANC files lose their raw-unit identity

- **Dates:** October 25–26, 2026.
- **Problem:** A fresh MANC acquisition is treated as micrometres although its
  raw coordinates use 8 nm units.
- **Repair:** Bind raw units to the actual Downloaded/Replaced acquisition,
  normalize once for execution, and retain original bytes. A kept or locally
  imported file is not assumed to use raw server units.
- **Verification/delivery:** The corrected fresh-circuit unit contracts and
  bounded app runs were checked in the subsequent three-engine review.
- **Record:** Fresh acquisition unit repair (local test record).

### DF-0038 — Root siblings are electrically disconnected in native models

- **Dates:** October 25–26, 2026.
- **Problem:** NEURON/BioNet create separate section-tree roots for outgoing
  cables at a shared SWC root. One MANC GF differs from Arbor by about 8.8 mV;
  NEURON/BioNet agreement alone hides the shared construction error.
- **Repair:** Join siblings at the exact proximal point of a real anchor edge,
  including filler-first cases. Add no cable or membrane and move no SWC points.
- **Verification:** Real-cell construction now has one native section-tree root.
  The voltage discrepancy shrank but still failed; DF-0039 was a separate cause,
  not an excuse to accept the remaining difference.
- **Record:** Root-junction diagnosis (local test record).

### DF-0039 — “Classic HH” uses different rate evaluation across engines

- **Dates:** October 25–26, 2026.
- **Problem:** Arbor evaluates HH rates directly, while the selected native
  NEURON default interpolates its rate table. Timestep refinement alone leaves
  about 0.65 mV difference in the fresh-circuit control.
- **Repair:** Translate the declared native table, its clamping and temperature
  policy into Arbor. Do not fit traces or disable the native NEURON table.
- **Verification/delivery:** Initial gate values, altered-policy refusal and the
  later actual fresh-circuit triplets passed their stated engineering limits.
  This does not establish universal event-time or biological equivalence.
- **Record:** HH table diagnosis (local test record).

### DF-0040 — Accessible text edits fail to refresh experiment previews

- **Date:** October 26, 2026.
- **Problem:** A changed text field can leave the document preview or target
  highlighting stale, depending on how the edit was entered.
- **Repair:** Observe text changes consistently while preserving guarded bulk
  restoration, rather than relying only on user-edit signals.
- **Verification/delivery:** Two failing-before text regressions, expanded
  source checks and packaged external-edit controls passed. The earlier six
  executed protocols were independently checked and were not changed by this UI fix.
- **Record:** Settings and preview repair (local test record).

### DF-0041 — Results retains help from the previous playback mode

- **Date:** October 26, 2026.
- **Problem:** Loading another voltage-only result or leaving measured playback
  retains the old accessible explanation.
- **Repair:** Reset help together with the visible mode explanation.
- **Verification/delivery:** The before-regression failed; source and final
  native playback-help checks passed. This was not a change to saved voltages.
- **Record:** Results help repair (local test record).

### DF-0042 — Window-control service reports no window while Digifly works

- **Dates:** October 25–28, 2026.
- **Problem:** Automated attachment, screenshots or pointer actions sometimes
  stall or return `noWindowsAvailable` even though the user can use the app.
- **Mitigation:** Reuse the existing handle, inspect accessible state and try
  one combined state/screenshot observation. Do not repeat an uncertain Run,
  download or Save. Normal quit and a separately verified handoff worked once.
- **Verification:** Actual recovery allowed continued checks. The permanent
  cause is unverified; no macOS permission reset or universal service fix is claimed.
- **October 28 recurrence:** A later review startup reported a visible isolated
  window, but the observed successor used default settings instead. The original
  review process was no longer present. A read-only Doctor click then returned
  a window error; the single combined recovery observation timed out. No Run
  was sent, the saved machine profile stayed unchanged, and no crash cause or
  successful isolated handoff is claimed.
- **Status:** Open external-control/workflow issue, not established as an app crash.
- **Later October28:** Actual folder dialogs, Finder opening and library handoff
  work, but Add data does not expose its menu through this control session.
  Pointer, keyboard and window-activation attempts show no menu or import picker.
  The prior native fixture explicitly activates its window before asserting menu
  visibility. Its passing result does not qualify this failed manual observation;
  focus/menu behavior versus observation failure remains unresolved. No uncertain
  download, Run or menu-item operation was repeated.
- **Record:** Observed control limitations (local test record).

### DF-0043 — Eight MPI ranks cannot bind to four allocated physical cores

- **Date:** October 5, 2026.
- **Problem:** The actual 180 GiB allocation exposes eight logical CPUs on four
  physical cores; explicit core binding fails before simulation. This is not
  evidence that the requested memory itself caused the failure.
- **Repair:** Map/bind to distinct allocated hardware threads, retain no-
  oversubscription and verify every rank's actual job/cgroup CPU mask.
- **Verification:** Guarded actual-node engineering and the one authorized
  production submission are recorded. Scientific acceptance remained a separate
  terminal-validation step; this log does not authorize another submission.
- **Record:** MPI affinity repair (local test record).

### DF-0044 — Earlier 80% production run loses an MPI peer mid-simulation

- **Date:** October 3, 2026.
- **Problem:** Peer resets precede a timeout around 31.5 simulated seconds.
  Available evidence does not identify the initiating cause or establish OOM.
- **Mitigation:** Preserve the failed run; add bounded transport/lifecycle and
  resource evidence around the unchanged worker for the explicitly authorized
  later attempt. Keep watchdog behavior and scientific equations unchanged.
- **Verification/limit:** A tiny diagnostic is not reproduction of large-scale
  stress. The original failure remains unexplained, not retroactively fixed by
  a later successful condition or the unrelated affinity repair.
- **Record:** Guarded retry diagnosis (local test record).

### DF-0045 — MPI observer races an atomic temporary-file update

- **Dates:** October 2–3, 2026.
- **Problem:** Observation of a transient file can fail independently of the
  scientific worker's numerical progress.
- **Repair:** Correct the atomic temporary-file observation race in the observer;
  preserve the pinned model and worker.
- **Verification:** The actual bounded eight-rank diagnostic passed independent
  terminal/copy/shard checks. It did not resolve DF-0044 or certify paper outcomes.
- **Record:** Tiny MPI diagnostic (local test record).

### DF-0046 — Scheduler requeue default conflicts with one-shot execution

- **Date:** October 3, 2026.
- **Problem:** The completed diagnostic's executed script inherited requeue
  permission, although its actual restart count was zero.
- **Repair:** Future staging emits explicit no-requeue and tests require it.
- **Verification/limit:** The attempted change arrived after completion and
  failed; the historical script was not rewritten. No restart occurred, and
  the later source guard is not claimed to have changed the old allocation.
- **Record:** Diagnostic limits (local test record).

### DF-0047 — Main README feature restrictions lag behind checked repairs

- **Date found:** October 28, 2026.
- **Problem:** The README still describes a soma-only BioNet subset and rejects
  electrical/regional mappings that later bounded app checks support. It also
  points to an older release page and leaves the supplied MANC table's release
  described as unknown after its explicit v1.2.1 assignment.
- **Repair:** Update the repository introduction, BioNet feature descriptions,
  release-page link, Intel qualification and MANC v1.2.1 assignment against the
  dated records. Preserve the failed historical 49-cell comparison and limits
  on arbitrary models, electrical MPI, Windows and scientific acceptance.
- **Verification:** Direct comparison with the channel, library, recording and
  release repair records. The new log link does not make the older README claims current.
- **Status:** Repository documentation corrected and checked; not uploaded.
  The already-staged 20261028.1 engineering candidate retains its pre-edit README.
  This documentation change is not claimed as part of its compiled inputs.

## Build and test incidents

### DF-0048 — Student native check fails without recording its probe result

- **Date:** October 28, 2026; candidate 20261028.1, not installed.
- **Problem:** The native library check retains 125 true flags but fails at the
  new timeout-message assertion. Its fixture uses `sys.executable` and records
  no returned error, making the compiled failure opaque. Source tests passed;
  the native check did not. The exact original returned error remains unknown.
- **Repair:** Use an owned executable placeholder for the mocked timeout, record
  its actual error/call count, and require exactly one mocked subprocess call.
  No real interpreter is launched and no timeout assertion is removed.
- **Status:** Source and native20261028.2 correction pass, including one mocked
  timeout call and its recorded error. The unavailable-process-executable source
  boundary passes. Failed28.1 remains failed; its original returned error is unknown.

### DF-0049 — Candidate assembly omits the retained notices/runtime inputs

- **Date:** October 28, 2026; candidate 20261028.1, not installed or uploaded.
- **Problem:** The build invocation omitted the existing notice/runtime reuse
  inputs. It freshly assembled the private runtime and left out the full checked
  notice bundle. Matching version strings are not byte-identity evidence.
- **Repair:** Pass the verified existing app as the runtime source and the
  matching retained notice-only input to the next build. Preserve the first
  candidate and archive instead of relabeling them as qualified.
- **Verification:** Direct package inspection found the missing notice files.
  The retained notice input matches the installed app's manifest. Runtime tree
  comparison found differences, including caches; no numerical drift is inferred.
- **Status:** Corrected28.2 independently verifies all2897runtime entries and315
  notice files, source inputs, signature and archive. Native library/safety and
  modern/legacy/rollback update cases pass. First candidate retained; no user
  installation, scientific run or public upload changed.

### DF-0036 — Mac build invocation uses a release tag as the app version

- **Date:** October 28, 2026.
- **Problem:** The student-review build command supplied an alpha release tag
  where the Mac builder requires a two- or three-part numeric app version.
- **Correction:** Use numeric app version 0.1.0, distinct build 20261028.1 and
  the versioned archive name. This was a command error, not an app runtime bug.
- **Verification:** The builder refused before compilation or package replacement
  and retained its diagnostic. Corrected build qualification remains pending.
- **Status:** Invocation corrected; no failed candidate was installed.

### DF-0055 — Unapplied Circuit Builder settings disappear without warning

- **Date:** October 28, 2026; actual Mac20261028.4.
- **Problem:** After reopening a six-cell project, changing Replace built-in HH
  Na/K without Apply and choosing New silently clears the circuit and editor.
  The document dirty check compares applied settings only. Save can likewise
  mark the document clean without storing the unapplied editor values.
- **Repair boundary:** Keep Apply explicit and scoped. Never silently apply a
  regional draft to cell-set defaults. Protect replacement, selection and source
  transitions; refuse Save, update recovery and Run until unapplied settings are
  applied or explicitly discarded. Cancel must retain controls and selection.
- **Verification:** Actual packaged negative and focused regression cases are
  retained in the student-readiness records. Actual separate Save As also says
  Saved while omitting the checked setting; reopening loses it without warning.
  The6before cases fail; expanded13selection and22final source/native-helper cases
  pass, plus the overlapping80case broad suite. Native28.7passes the guard and
  zero-unhandled-callback gate. Actual28.7New/QuitCancel retain the checked edit;
  Save/readiness/Run refuse omission; LoadCancel preserves six cells. Explicit
  defaults Apply then real SaveAs/Open retains the checked value and all six
  neurons. Original files remain intact.
- **Status:** Installed in checked Mac28.7; Windows deferred. Not full student
  sign-off, biological acceptance or a public release.

### DF-0056 — Student inventory counts an inactive historical editor as exposed

- **Date:** October 28, 2026.
- **Problem:** The conservative static inventory includes18construction sites in
  the retained ExperimentPage class, but production MainWindow constructs
  ExperimentBuilderPage and no production reference constructs the old class.
  Those inactive controls are not available for a student to test.
- **Repair:** Keep the sites in the inventory with an explicit inactive-legacy
  status and reason. The existing validator independently checks the canonical
  class/scope and refuses the exclusion if any production name, attribute or
  literal-string reference reactivates it. Arbitrary active controls and spoofed
  saved scopes cannot use this exclusion. This is deliberately not a general
  Python/plugin reachability proof.
- **Verification:** A normal inactive case, actual active-control refusal,
  outside-UI reactivation refusal and spoofed-scope refusal pass. The original
  checker refused the new status; that failure is retained. The31case affected
  harness/log suite passes. Actual inventoryv8accounts for18inactive sites and
  still refuses completion with288unverified exposed sites. No additional
  feature, run or dialog is marked passed.
- **Status:** Developer-only accounting repair; no app code or frozen Mac build
  changed. Not publicly released and not student-task completion.

### DF-0057 — Tall morphology panes clip the default circuit framing

- **Date:** October 28, 2026; actual Mac20261028.4 Results export.
- **Problem:** A saved measured two-neuron run exports a tall morphology image
  with the circuit clipped at its sides after ordinary split-view/mode changes.
  The projection fixes vertical extent but shrinks horizontal extent in portrait
  panes, even though Fit All uses one radius-based zoom for both axes.
- **Repair:** Fit the projection to the shorter pane dimension, preserving
  camera orientation, stored zoom, source points/radii and contact locations.
  Picking and export use the same projection. Add a native narrow-pane math gate.
- **Verification:** Four portrait cases fail before repair; square/wide controls
  pass. The actual clipped PNG is retained. Corrected source8case and wider71case
  suites pass. Native28.7narrow-pane gate passes. The same retained measured run
  now exports3840×5380with every visible cable endpoint inside background padding;
  the actual PNG was visually inspected. Numerical recordings were not changed.
- **Status:** Installed in checked Mac28.7; no simulator run, new numerical or
  scientific acceptance, Windows check or public release.

### DF-0058 — Choice lists and menu-created actions were missing from inventory

- **Date:** October 28, 2026; discovered in the actual playback chooser.
- **Problem:** The shared choice dialog renders its list and keyboard selection,
  but QListWidget sites and menu/addAction/addMenu creation sites were not in the
  conservative static accounting. This included the editable-model population
  recording list. An incomplete inventory cannot support full-control signoff.
- **Repair:** Include QListWidget and QMenu plus explicit menu-action/menu
  factories. Duplicate references remain conservative sites to account for;
  dynamic entries and native child controls still require outcome review.
- **Verification:** The original omission regression fails. The32case affected
  harness/log suite passes, old actual inventoryv9is refused for missing controls,
  andv10accounts for316sites (18inactive legacy,298unfinished exposed). The
  actual saved-condition chooser was changed using keyboard Down/OK, Cancel
  retained that condition, and Up/OK restored Control. The external tool's
  AX tree omits the list; screenshots and the final selection show it worked.
- **Status:** Developer-only accounting repair, not a new runtime/UI fix or
  feature-completion claim. No frozen app stage changed. Not publicly released.

### DF-0059 — Runtime Browse resolves a virtual environment to base Python

- **Date:** October 28, 2026; actual Mac20261028.4 positive Browse check.
- **Problem:** Choosing a virtual environment's bin/python through the native
  file picker returns the base interpreter instead, changing the runtime field.
  The exact environment was restored by text entry before any Run; no simulator
  installation or machine-profile binding was changed.
- **Repair:** All four runtime Browse buttons request Qt's DontResolveSymlinks
  option. Qt's Cocoa implementation maps this to the native panel's alias
  resolution property. Selected paths are kept without resolving them again.
  Primary documentation/source references are in the student-readiness guide.
- **Verification:** The modeled native-option regression fails before repair;
  a refined four-button routing check also fails before. An earlier version
  placed an assertion inside a Qt slot and falsely passed after an error printed
  to stderr; that flawed test is retained and corrected, not counted as evidence.
  The affected35-case source suite passes; actual final native picker
  preservation is still required. Candidate28.6predates this fix;28.7is building.
- **Status:** Installed in checked Mac28.7. Real native NEURON, Arbor and BioNet
  positive picker cases preserve the venv launcher, with prior choices restored.
  Fourth NetPyNE positive remains control-tool limited: AX and screenshot disagree
  about Open, a grounded click does not dismiss it, Cancel safely retains blank.
  All four native injected-route checks pass; they do not replace that missing
  manual outcome. No Windows or public-release claim.

### DF-0060 — Uncaught Qt callbacks can leave a successful test receipt

- **Date:** October 28, 2026; found while testing DF59.
- **Problem:** Python exceptions inside a clicked Qt slot can be sent to the
  exception hook rather than raised to the caller. The real package-check entry
  returned0and wrote an all-pass receipt in a bounded injected-error regression.
- **Repair:** A scoped callback-error collector now surrounds the package library
  checks and is also an automatic source-test fixture. Any uncaught callback
  error fails the owning source test or the native receipt/exit code. The normal
  app's error handling and simulation equations are unchanged.
- **Verification:** The actual entrypoint regression fails before repair. A clean
  signal, an intentionally failed signal, hook restoration and the connected
  entrypoint pass in the31-case affected suite. A broader strict121-case suite
  also passes without callback errors. The next compiled native helper includes the
  meaningful failed-signal self-check; its outer gate requires zero unhandled
  callbacks. This does not certify external processes or native crash absence.
- **Status:** Source tests and actual compiled28.7entry pass:139trueflags,
  deliberate self-check catches its expected error, outer uncaught list empty.
  Included in installed Mac28.7; not full student acceptance or a public release.
  No production experiment was launched.

### DF-0061 — Document attachment can stall the control service

- **Date:** October28–29,2026; actual Mac review.
- **Problem:** Attaching the control tool to an already-running document app
  took43,409seconds despite its30second request. It eventually showed an older
  software-only test document, not the architecture guide being checked. The
  Digifly process remained alive; its existing handle immediately responded.
- **Repair:** No app or service root-cause repair is established. Stop new
  document-app attachments; retain existing handles, verify owned processes
  independently and continue package checks. Do not repeat an uncertain Open.
- **Verification:** Held Digifly process survived and its same-handle read
  returned in0.1203seconds. Architecture-guide external-viewer outcome remains
  unverified. The compiled candidate's139 native flags pass with zero uncaught
  callbacks; that does not repair the external control service. A separately
  grounded observation-free menu keyboard sequence also opens no picker. A
  bounded12-Tab traversal reports no focused element. Those failed alternatives
  are not further reasons to loop or to declare the human-operated menu broken.
- **Status:** Tool limitation, unresolved. Not a Digifly crash or feature pass.

  November 1: a new PowerPoint attachment for the beginner guide took 10,594
  seconds despite a 30-second request. It eventually returned PowerPoint's
  file-opening window. The Digifly owner stayed alive and passed independent
  identity verification afterward. Keep the returned handle. This observation
  does not establish the cause or repair the control service.

### DF-0062 — Test-copy preparation continued before copying finished

- **Date:** October29,2026; packaging workflow, not a user app failure.
- **Problem:** An asynchronous clone returned a live session. A subsequent
  identity-edit command ran before Info.plist arrived and failed. No copy was
  launched or installed in that state; source and retained previous app stayed
  intact.
- **Repair:** Wait for the exact copy session to finish before identity/signing
  steps. Recheck the completed source plist, apply the test-only identity, wait
  for signing, then compare unsigned executable and every other resource.
- **Verification:** Original clone finished0and its completed plist exactly
  matched the candidate before the successful identity edit. Independent
  same-code/resource proof then passes:3431other entries and unsigned executable
  match. Only after that proof was the separate7review launched.
- **Status:** Failed preparation retained; no user data, runtime or source
  change. This workflow correction is not student-feature acceptance.

### DF-0063 — Re-import status calls the whole library newly imported files

- **Date:** October29,2026; actual Mac28.7 local SWC intake.
- **Problem:** Re-importing one unchanged TTMn correctly leaves six unique
  neurons and the existing contact table untouched, but reports “Imported6files”.
  The completion slot describes cumulative library totals as this operation's
  imported count. This is misleading even though deduplication works.
- **Repair:** Dataset-library completion now explicitly says how many saved
  neurons the library contains. Generic data imports retain their own file count.
  No intake, archive-retention, annotation or connectivity policy changes.
- **Verification:** Both duplicate and new-neuron message regressions fail
  before repair; the generic-import control passes. The affected88-case suite
  passes afterward. A further26-case suite includes a real tiny-file intake
  worker/slot gate with saved totals1,1,2 and callback-error checks. Actual28.7
  retains six canonical SWCs, all22protected source/contact files unchanged,
  and exact six-cell/228-site downstream binding. The misleading message is
  retained as the before observation.
- **Status:** Source-tested and compiled in checked Mac29.3; native141flags,
  source/archive/runtime/notices and modern/legacy/rollback updater checks pass.
  Actual corrected-message check and installation remain required.
  Installed28.7does not yet contain this wording repair.
  Full student verification remains incomplete; not publicly released.

### DF-0064 — Wrong notice folder is detected only after compiling

- **Date:** October29,2026; developer packaging workflow, not a user app crash.
- **Problem:** The29.1build compiled, then failed because the selected notice
  input was an app bundle rather than the reviewed notice-only folder. Nothing
  was installed or promoted. The failed log and raw compiled diagnostics remain;
  their missing original source freeze prevents treating them as a qualified
  final package.
- **Repair:** The existing notice checker has a read-only validation mode. The
  real Mac build runner now invokes it before the first project copy/compile,
  then verifies again at final staging. It checks schema, paths, complete file
  membership and every listed file's size/content. It does not certify licensing
  conclusions or application correctness.
- **Verification:** Three new regressions fail before repair; five earlier
  controls pass. Afterward35affected notice/source-freeze/intake/harness/log
  cases pass. The actual shell runner is exercised with an inert first-copy
  sentinel: wrong input is refused before copying, valid input reaches the
  sentinel without compiling. The real315-file notice-only folder verifies and
  its manifest exactly matches checked Mac28.7.
- **Status:** Local workflow repair exercised by completed29.3build with the
  reviewed315-file notice input. Independent package and native/updater checks
  pass; failed29.1is retained/unqualified and installed28.7is unchanged.
  No public release or full student sign-off.

### DF-0065 — View and model navigation indicators change without their pages

- **Date:** October29,2026; actual Mac28.7 review and source regressions.
- **Problem:** Accessibility activation selects Full skeletons, but Circuit
  Builder keeps rendering/exporting only soma points. The model rail similarly
  marks Populations while leaving Cell rules visible, with both buttons checked.
  An ordinary repeat click can also clear the active model-page indicator.
- **Repair:** Circuit Builder/Results view selectors and model-section selectors
  now respond to checked-state changes. The model rail uses one exclusive group.
  Rendering, guidance and the selected page therefore share the same state;
  original geometry, model parameters and recorded signals are unchanged.
- **Verification:** Four view state/accessibility regressions fail before repair;
  two ordinary-click controls pass. Three model-navigation cases also fail before.
  The affected83-case view/Results/pending-edit suite passes. The first integrated
  safety run finds two fixture-only attribute-name mistakes (22other cases pass);
  those are retained and corrected. Its successor24-case suite passes, including
  the real safety helper. Actual failed PNG and section-state observations remain.
- **Status:** Source-tested; source-mode and compiled29.3 Cocoa gates pass
  141trueflags with zero uncaught callbacks and unchanged native preferences.
  Checked source/archive/runtime/notices and updater install/legacy/rollback
  pass. Actual29.3review verifies Full-to-six-cables PNG and reverse Soma-to-six-
  markers PNG, both visually inspected; all six model sections match one checked
  indicator and repeat selection retains it. See
  `outputs/workstation/student-view-nav-actual-20261029-v29-3.json`.
  Not in installed28.7yet.
  The newly started29.2build
  was deliberately stopped (130) to include this repair; its owned group is
  independently absent. Actual Results-mode checks and installation remain
  required. No public release or full student sign-off.

### DF-0066 — Morphology playback leaves the expansion button in the wrong state

- **Date:** October29,2026; actual Mac29.3 Results interaction.
- **Problem:** After Expand traces, selecting Morphology voltage restores the
  circuit/trace split but leaves the expansion button checked. Its label says
  Expand traces while its active state still says expanded, so the next click
  can do nothing instead of expanding. The measured data and plot modes load.
- **Cause:** The shared split-restoration method changes visibility and labels
  without synchronizing the checkable button. The morphology-view caller invokes
  that method directly rather than toggling the button.
- **Repair:** Synchronize the button without recursive callbacks,
  preserving any caller-owned signal blocking. Keep numerical data, geometry,
  playback position and the explicit view choice unchanged.
- **Verification:** Actual2D/3D rendering and the failing morphology/expansion
  interaction are retained in
  `outputs/workstation/student-results-view-actual-20261029-v29-3.json`.
  Three new regressions fail before repair while the ordinary click control
  passes. The affected23-case suite passes afterward, including forced restore,
  the real morphology caller, existing signal blocking and the next ordinary
  expansion click. Source-mode Cocoa passes142trueflags/zero uncaught callbacks/
  unchanged preferences. The native helper now checks the forced-restore path.
- **Status:** Reproduced in the isolated29.3review; source,29.4native/package/
  updater and actual interaction checks now pass. Expand then Morphology restores
  the split and clears the checked state; the next click immediately expands,
  and Restore works. Actual screenshots were inspected. Installation remains
  held by DF68; normal installed28.7is unchanged. No public release/student sign-off.

### DF-0067 — An unmatched browser filter says the saved library is empty

- **Date:** October29,2026; actual Mac29.3 Data Library family browser.
- **Problem:** DN shows its two saved DNp01 neurons. A filter that matches no
  type then says “No saved neurons here yet”, incorrectly implying that the
  library has no neurons. Clearing the filter restores DNp01 without a download.
- **Cause:** Filtered-empty rows and an actually empty family share one label.
- **Repair:** Distinguish “No types match this filter” and “No neuron IDs match
  this filter” from the unfiltered empty-library message. Counts, files, source
  selection and query behavior do not change.
- **Verification:** Both type- and ID-filter regressions fail before repair.
  They also check truly empty AN, disabled pagination, unchanged saved data and
  clearing the filter. The affected43-case suite passes afterward, with no
  errors or skips. Actual before observation is retained in
  `outputs/workstation/student-family-browser-actual-20261029-v29-3.json`.
- **Status:** Source,29.4native/package/updater and actual checks pass. Both
  nonmatching type/ID messages are correct; clearing restores both saved DNs.
  Truly empty AN retains its empty-library message and disabled paging.
  Installation is held by DF68; not in installed28.7. No public release/student sign-off.

### DF-0068 — Mac accessibility crashes while opening a model after an inline edit

- **Date:** October29,2026; isolated Mac29.3 student review.
- **Problem:** After table add/remove checks and an inline invalid diameter edit,
  a second edit attempt reports an accessibility error. Opening the saved-model
  picker then terminates the app with SIGSEGV. The control tool subsequently
  observes a reopened empty test window without the review arguments; that
  window is closed normally and is not accepted as continued test state.
  The user later confirms clicking the crash dialog's Reopen button; reopening
  must not be attributed solely to the control tool.
- **Diagnosis:** The saved macOS crash report identifies the Cocoa accessibility
  plugin during the native file-picker modal loop. Local binary metadata and
  disassembly resolve the failing method to `accessibilitySelectedChildren`:
  the selected child is non-null, but its virtual-method pointer is invalid
  during the validity check. This is consistent with a stale selected-item
  interface; what invalidated it is not yet proved. The matching
  Qt6.10.3 implementation was inspected; source and investigation details are
  in the [student guide](STUDENT_READINESS_MAC_20261028.md#df68-mac-selected-item-crash).
  No matching upstream bug or fixed Qt version is established.
- **Verification:** Owned review session exits139 and its PID is absent.
  `outputs/workstation/student-inline-edit-crash-20261029-v1.ips` retains the
  crash; actual model baseline/restored/variant documents remain saved separately.
  The normal runtime profile still matches its pre-review digest. No simulation
  or download was active. Earlier positive model-open checks remain historical,
  not proof that this interaction passes.
- **Repair in progress:** Clear only transient table/list highlights before
  model file pickers and the unsaved-model question. Eight source regressions
  fail before this guard; they preserve rows, edits and recording checkmarks.
  A native fixture checks the same precondition with inert canceled pickers;
  it does not reproduce the Cocoa crash or certify its repair. The final33-case
  affected suite and source-mode Cocoa143flags pass, zero callbacks/preferences
  unchanged. A wrong test filename and later log-link assertion failure remain
  retained; neither was an app regression pass. The29.5build passes143native
  preconditions, full package audit and modern/legacy/rollback update checks.
  In its actual held review, the baseline opens and all five disposable row
  add/remove checks preserve the visible originals. The control tool then
  refuses inline-cell activation with AXError.notImplemented while the exact
  review process remains alive. A single human-assisted invalid edit is requested;
  this is a tool blocker, not a passed native crash path or a second observed crash.
- **Status:** Native crash mitigation remains unqualified; blocks full student
  acceptance and final installation.
  Mac29.4passes its separate142-flag engineering/package checks but predates any
  repair for this crash. Do not call that native fixture pass a crash regression
  pass or silently retry a failed interactive action.

### DF-0069 — A startup record can outlive its review process

- **Date:** October29,2026; agent-owned Mac review workflow, not a simulator bug.
- **Problem:** The startup-only helper reports a visible window for PID11470,
  then exits. That process is absent before actual observation; a different
  default-profile app is found instead. It must not be treated as the qualified
  isolated review. No new data edit or Run occurs in that window.
- **Cause:** The helper did not retain its owning command session or check live
  identity before observation. Command-host descendant retirement is a hypothesis;
  the exact reason PID11470exited is unproved. No new native crash report appears.
- **Repair:** The [existing launcher](../scripts/launch_library_review.py) now
  offers foreground `--hold` and an explicit live-owner check, invoked by that
  runner before reporting readiness. It checks live parent/child ownership and refuses a dead,
  completed, startup-only or wrong-command/PID/profile/workspace record before
  control can bind or reopen an unrelated app. Readiness remains bounded; holding
  the ordinary review app is not a schedule, watcher or automatic experiment.
- **Verification:** Twelve [bounded protocol tests](../tests/test_library_review_launch.py)
  pass, including a real short held child, normal exit, bad parent/identity records
  and a wrong-profile child rejected through the held runner itself. Fourteen
  combined log/launcher tests pass, zero failures/errors/skips.
  The actual old startup-only record is refused. New held12157passes exact live
  identity, opens the correct Data Library through the same existing handle,
  completes DF66/67checks and normally exits0under its retained parent. The real
  user profile remains unchanged. These are same-agent checks, not an independent
  behavioral trial or universal control-service repair.
- **Status:** Narrow Mac workflow correction exercised. Windows live command
  verification explicitly refuses as unqualified. Full student acceptance and
  DF68native crash qualification remain unfinished.

### DF-0070 — All-compartment Results blocks on very large trace overlays

- **Date:** October30,2026, during an actual50-neuron NEURON student experiment.
- **Problem:** The completed run has70,549voltage traces. Results tries to draw
  every curve at once and the control tool times out. A native stack sample
  locates the main thread in the2Dtrace painter. The saved simulator run is intact;
  the test window subsequently quits normally with exit0.
- **Repair:** Large matching sets plot one60-trace page at a time. Every original
  sample and trace remains available through paging and search. Sets of up to240
  matching traces still overlay together. The count distinguishes plotted curves
  from saved traces, and comma-separated searches can compare different cells.
  The emphasis button is renamed to describe what it changes.
- **Verification:** The new page-bound regression fails before repair. All17
  affected trace/layout/playback tests pass afterward, including last-page search,
  selection persistence, unchanged sample counts and cached timeline repaint.
- **Status:** Source, compiled Mac30.1 and actual144,516-trace saved-run replay
  checks pass. The real app filters six full-length curves, shows measured
  compartment voltage, advances and pauses playback, and switches between both
  conditions. It is left open on the requested replay. The normal installed app
  is unchanged; this delivery is the isolated checked test app. Windows is deferred. This
  repair does not change simulator equations or the completed recording.

### DF-0071 — Student selection aid counted an unverified archive record

- **Date:** October30,2026; agent-authored read-only experiment selection aid.
- **Problem:** The initial50-cell choice includes an unlabelled SWC from a
  glia-filter archive. Its neuronal identity is unverified, so that run cannot
  establish the requested minimum of50neurons. It also uses an excessive1nApulse.
- **Repair:** For this counted test only, select named records in established
  neuronal-family folders. Replace the unverified entry with a named connected
  partner through Circuit Builder, then reapply and check the declared settings
  on all50cells. Keep the earlier failed design and its results.
- **Verification:** The corrected graph has50named neuronal records/76pairs and
  all49partners reachable from its source. The actual app reload,50overrides,
  six saved settings on every cell and18readiness checks pass. The bounded
  smaller-pulse/no-stimulus comparison completes0/NormalExit. All50have recorded
  compartment responses under stimulation; none crosses threshold without it.
  All4,769,028values and materialized settings pass the saved-data audit. The
  actual app's measured replay and complete trace access pass.
- **Status:** Corrected experiment and replay checked at the declared model scope.
  This is not a global UNKNOWN filter or a claim that every unlabelled SWC is
  glial. No library record or source morphology was removed or changed.

### DF-0072 — Experiment Builder cannot save or inspect native current traces

- **Date:** October 30, 2026.
- **Problem:** The app saves voltage and threshold events but exposes no current
  recording. Voltage replay alone cannot show axial or synaptic current.
- **Repair:** Add Off, Sparse and Full current tracking for NEURON. Sparse uses
  a small deterministic set plus edited, stimulation and connection sites. Full
  includes all native electrical compartments, including internal divisions of
  long SWC edges. Keep junction currents, units, directions and contact identities
  explicit. Store lossless compressed traces and display currents on an nA axis
  synchronized with the existing voltage replay. Do not change the model or
  spread a soma recording across unrecorded compartments.
- **Verification:** Real two-cell tests first catch native section-name collisions
  across cells and omitted zero-area synapse junction currents. Section-object
  identity and unique junction traversal repair both. The affected native test
  records 58 Full centers versus 14 Sparse, with 101 samples per trace. Whole-model
  membrane/electrode balance differs by less than 8e-15 nA, and recording does not
  change voltage within 1e-9 mV. Corrupted saved current blocks are rejected.
  The control inventory refuses the new uncovered setting before its real edit,
  save/reopen and pairwise capability checks are added.
- **Status:** Checked private Mac build 20261030.3 passes compiled native UI,
  package and actual Sparse/Full app Run checks. Each app Run completes normally
  on the connected two-cell MANC teaching model. Sparse saves 14 sites per
  condition; Full covers all 2,713 native centers plus 2,715 zero-area junctions.
  Across both conditions, Full stores 1,432,728 current samples in 12,633,312 bytes
  after lossless compression; Sparse stores 6,600 in 164,121 bytes. Complete
  center coverage and whole-model current balance pass within 1.44e-12 nA.
  The 179,058 saved voltages and 200 shared current channels agree between modes
  within their declared tolerances. Actual eight-type selection, replay,
  voltage/current unit switching, plot export and finalized compressed-result
  reload pass. See current-tracking-actual-review-20261030-v1.json and the
  terminal-bound current-tracking-actual-app-audit-20261030-v2.json under
  outputs/workstation. Other simulators and multiple experiment workers
  explicitly refuse this new setting. Windows remains deferred. Earlier
  voltage-only results and the 50-neuron experiment are intact; these new tests
  do not add currents retroactively or establish biological calibration.
  The checked build is installed locally; the previous app is preserved. Final
  protection checks confirm all 161 original run files and 50 source SWCs are
  unchanged. The review stays open on the real two-cell current/voltage
  comparison. Nothing is newly published.

  Follow-up tests also catch stale current-unit descriptions after switching
  back to voltage, and ramp playback vectors incorrectly treated as electrodes.
  Voltage views now restore their own labels, and the recorder captures only
  actual clamp processes while retaining ramp vectors for delivery. Native
  step/ramp/pulse-train, current-only, all three gap models and disk-budget
  refusal checks pass. The outdated candidate compile was cancelled before
  delivery; the corrected package passes 146 native UI checks with no uncaught
  Qt callbacks or normal-profile changes. The initial saved-data audit overlaps
  final compression; its numerical checks pass, but use v2 for finalized sizes.

### DF-0074 — Fine NEURON steps incorrectly forecast dense recorded output

- **Problem:** Actual 30.3 app refuses the authorized 0.0005 ms integration /
  0.02 ms recording pair before launch: 86,821,426 forecast values. It counts
  every integration step as a recording, although NEURON's vectors use the
  separate recording interval. No run or partial output is created.
- **Repair:** Forecast the real NEURON sampled report separately from BioNet's
  dense native report and Arbor's existing dense-probe limits. Retain finite
  sample, native-section, current and disk guards. Compact float64 trace/edge
  storage and lazy CSV rows avoid per-sample Python dictionaries and tuples;
  the bounded playback ceiling becomes 64 million samples with no decimation.
- **Verification:** Actual old refusal retained. Source positive fine-step and
  50-cell forecasts, over-budget refusal, legacy reader/identity checks, exact
  saved numeric values, duplicate timestamps and compact payload checks pass.
  First broader suite exposed two test expectations that incorrectly asked
  BioNet/Arbor to pass their unchanged dense-report limit; corrected fixtures
  preserve that refusal rather than weakening the engines' guards.
- **Status:** Separate Mac 30.4 package/archive/runtime/notices and native GUI
  checks pass, with no unhandled Qt callbacks or normal-preference changes.
  Actual F/G fine-step runs complete normally and pass their unaligned numerical
  targets. The separate50-cell run completes normally: all57,950,916voltage values
  and2,026,654current values pass audit, including all76chemical pairs. Actual
  measured replay, playback, both conditions, current choices, unit restoration,
  filtering and both exported PNGs pass. Normal30.4installation and input
  preservation pass:166new files checked,161original50run files/50sourceSWCs
  and earlier recordings unchanged, previous30.3/profile retained. See the
  follow-up install-copy/postflight receipts. No Windows, public release or
  whole-network convergence claim.
- **Remaining usability issue:** Opening the fine50-cell result takes roughly
  8.6CPUminutes/~3.2GBRSS. One tool observation times out during loading; the same
  handle works afterward and no crash is observed. Synchronous large-result
  preparation remains slow. Long legend labels can be clipped. These are not
  relabeled as repaired by passing the numerical or storage checks.

### DF-0073 — Coarse demonstration recording misses fast waveform detail

- **Date:** October 30, 2026.
- **Problem:** The current demonstration saves only 33 samples across 8 ms
  (0.25 ms interval), drawing visibly angular voltage/current traces. Current
  storage, coverage and balance tests do not establish time-resolution accuracy.
- **Initial measured cause:** Case A keeps the same 0.05 ms solver step but
  records every 0.05 ms. All shared-time voltages and currents agree within the
  declared tolerances, yet the source's sampled peak rises by 17.0844 mV and
  previously hidden synaptic-current detail appears. The coarse recording misses
  between-sample dynamics; neither lossless compression nor the plotted line
  changes the stored values.
- **Completed check:** Five actual app experiments finish normally, preserving
  the same circuit, stimulus and membrane settings. Solver steps fall from
  0.05 to 0.0025 ms; final recording is every 0.02 ms. The finest source/partner
  peaks are 57.8391/22.0731 mV. Between 0.005 and 0.0025 ms, peak differences
  are only 0.06965/0.10859 mV, but the worst whole-circuit voltage difference
  remains 6.94029 mV and chemical-current waveform difference remains 9.093%
  of its peak. These fail the prospective 1 mV / 2% limits. The trace looks
  smoother; temporal convergence is not demonstrated. The five-run bound is
  honored without a sixth experiment or changed acceptance limit.
- **Reader correction:** Native B/C/E vectors end at 7.98 ms instead of 8 ms.
  A too-strict 400/401 reader assertion is retained and corrected to report
  actual support and compare shared times only. No interior gap is accepted,
  no endpoint is invented and no completed simulation is repeated for roundoff.
  Four reader checks include valid comparisons, interior-gap refusal and an
  equal-peak but wrong-waveform rejection.
- **Original bounded status:** Diagnosis complete, numerical refinement was unresolved.
  Actual finest voltage/current plots are exported and visually checked; shared
  measured replay is left open at 2.74 ms. The prospective protocol, complete
  comparison, report and preservation checks are under
  outputs/workstation/current-resolution-20261030-v1. No source-code repair,
  new build, changed app defaults or physiological acceptance is claimed.
  Original runs, 50 source SWCs, installed app and normal settings are unchanged.
- **Separately authorized successor:** The follow-up preserves this negative
  result and uses two smaller steps without changing the model or limits.
  F/G pass at worst0.724778mV and0.950%chemical waveform difference. DF74 records
  the actual readiness repair, checked build,50-cell demonstration and remaining
  usability limitations. Evidence is under
  outputs/workstation/current-resolution-followup-20261030-v1.

### DF-0075 — Fine all-compartment results load slowly

- **Date:** October30,2026; actual checked Mac30.4 review.
- **Problem:** Preparing the fine50-cell result's viewers takes several minutes,
  roughly8.6CPUminutes and3.2GBresident memory for57,950,916voltage values. The
  window-control observation times out once while the app is busy; after loading,
  the same retained handle works and no app crash is observed.
- **Evidence:** Source inspection finds separate synchronous saved-voltage scans
  for activity flow, measured replay and traces. The actual load has not been
  separately instrumented, so their individual costs are not established. The
  compact storage repair prevents per-value Python-object inflation but does not
  make initial viewer preparation fast. Some long legend labels also clip.
- **Status:** Open usability limitation, not repaired or a scientific failure.
  Playback, filtering, unit changes, condition selection and native plot exports
  work after loading. The bounded numerical/50-cell task is complete; no extra
  model run, new performance claim or general student sign-off follows.
- **Record:** outputs/workstation/current-resolution-followup-20261030-v1/
  actual-review.json and REPORT.md.
- **October31 repair in progress:** All three viewers now share one full-precision
  voltage table. Cold reads parse CSV/gzip once; later reads use a verified,
  lossless indexed cache outside the run. Circuit geometry, samples and current
  channel reads prepare in background workers. Cancel, immediate retry, changed
  result and shutdown are generation-bound. Hidden artifact rows build only on
  expansion. Cache storage is bounded and cancellation removes only its own
  incomplete derived copy; original runs are never rewritten.
- **Measured source checks:** The actual57,950,916sample recording opens cold in
  83.66s/oneCSVscan and cached in6.25s/zeroCSVscans. Cold/cached heartbeat p99 is
  39.99/89.17ms; cancellation stops in25.01ms with no partial cache. Earlier
  warm index versions missed the100ms target; streaming its entries fixes that
  source measurement. The initial diagnostic's polling starved its reader and
  was stopped; its failure is retained rather than counted as an app timing.
  The corrected native-event-loop measurement and all failed intermediate
  records remain under outputs/workstation/result-loading-20261031-v1.
- **Delivery status:** Focused regression has79passes/oneoptional skip. The
  actual source-Cocoa fixture passes, including cache/Cancel/retry/background
  current checks and unchanged normal preferences. Separate31.1is building;
  compiled/native/actual large-result review, installation and final preservation
  checks remain pending. Neither source timings nor this log certify delivery.
- **Compiled discrepancy retained:**31.1passes151native engineering flags and
  exact source/archive/runtime/notice checks, but its real cached restart takes
  36.3s, failing the10starget. Cold Open completes130.86swith all samples and a
  responsive window; actual Cancel/retry works. The package lacks the standard
  library C JSON accelerator that the source uses; Nuitka's optional-import rule
  suppresses it. Explicit import and a native requirement are now under test in
  separate31.2. First-load buffers are also mapped to avoid a replacement-time
  allocation-release pause. Normal installed30.4remains unchanged;31.1is not
  promoted from functional tests alone.
- **October31 measured successor:** 31.2 includes the C JSON reader and passes
  152 native functional flags, unchanged settings and exact package checks. Real
  cold/cache opens take 100.28/6.81 s, preserving all saved values and trace IDs.
  A second cached open takes 7.63 s. Both cached p99 heartbeat measurements miss
  the original 100 ms target (214.86/252.98 ms), so normal installation remains
  blocked. Actual Cancel/retry, both conditions, all eight current types,
  filters, measured views and three inspected PNG exports work at their stated
  scope. Long voltage-axis/legend text and manual animated-Pause qualification
  remain separate limitations.
- **Further bounded correction:** A source diagnostic measures repeated full-
  heap collections during deep regional-map parsing after the large table exists.
  The reader now validates geometry bindings first and shares their compact
  edge/soma IDs between playback preparations; trace records no longer allocate
  per-instance dictionaries. No global garbage-collection policy, simulation
  equation, recorded value or anatomical binding changes. 41 focused checks pass,
  including one-time binding preparation and changed-geometry refusal. The
  source full-record cached check takes 5.59 s / p99 77.06 ms; source-Cocoa passes.
  Separate 31.3 is building. Its compiled/actual performance and final installation
  are not yet qualified. The connected installation check now refuses excessive
  heartbeat delays as well as slow duration; retained 31.2 fails that check.

- **31.3 outcome and31.4 correction:** The actual cold recording opens in101.17s,
  preserving all values, but a5.42s cached open still fails the100ms p99 target.
  An armed profile captures GIL waits during map decoding and full plot-index
  scans on the window thread.31.4 adds a compact checked ID-map cache, worker-
  prepared plot metadata, and one completed-preparation reuse path that verifies
  every saved input's full digest/identity plus a final unchanged-file check.
  Changed/additional/missing inputs cannot reuse that preparation. Both disk
  caches share the2GiB budget; unrelated folders and raw runs remain untouched.
  The final source pair takes3.134/0.547s with p9988.97/36.92ms; window plot setup
  is about20ms rather than135–145ms.53focused cases and157source-Cocoa flags pass.
  Build31.4 completes with157native fixture flags. Actual cold106.104s retains
  all data; checked prepared reuse takes0.363s, but first full cached observer
  p99674.23ms fails. Later quiet/in-load-inspection comparisons do not establish
  that accessibility inspection alone caused the outlier. No normal install.
  Build31.5 source avoids rebuilding a verified unchanged display and preserves
  its filters/paused selection; changed inputs replace it, failure/Cancel clears
  it.16async cases and6boundary/delivery cases pass. The broader source sweep's
  one test-only filter-field typo is corrected and the affected case passes.
  Native-source-v6 catches stale measured help during verification; paused-control
  disabling and help restoration repair it. Native-source-v7 passes159flags,
  zero callbacks and unchanged preferences;21async/gate cases pass. Final source
  indexed/reuse is3.023/0.431s, p9969.97/22.62ms.31.5package/actual checks remain
  pending. The first added delivery test has a test-only insertion error; its
  corrected six-case suite passes with the original failure transcript retained.

- **31.7 actual outcome:** Separate app/archive, full notices/runtime/source
  checks and161native engineering flags pass. Cold Open101.687s preserves all
  57,950,916samples/144,516traces/two tracks, with37.64ms p99. First cached Open
  1.094s still fails at658.61ms p99; explicit setup is637.08ms, worker422.79ms
  and display commit0.179ms. This locates the pause before the reader; GC is not
  established as its cause. Source latest-request setup36ms does not reproduce
  it. Both conditions/eight current kinds/filters/paging/views/Play-Pause and
  three native exports pass functionally; cache values/identities match the
  independent full saved-row oracle. Original failures stay retained and the
  connected gate refuses installation. Juan explicitly requests keeping the
  installed version unchanged. A separate native stack diagnosis is declared,
  not another qualification attempt. No model run or global GC policy change.

### DF-0076 — A late Cancel activation relabels an already loaded recording

- **Date found:** October31,2026; actual Mac31.3 review.
- **Problem:** The full cached load completes before the control tool's Cancel
  activation arrives. The old button handler still changes the completed result's
  note to “Loading cancelled.” Its timing record already says Loaded, with every
  saved sample present. That observation is not a successful cancellation test.
- **Repair:** Ignore the queued Cancel activation once its reader is absent.
  Do not discard the completed data or change its loaded note.
- **Verification/status:** A focused actual-Qt source test preserves the completed
  note and all samples after the queued activation. The corrected31.4 source
  fixture passes compiled native31.4 checks. Actual active-read Cancel31.4 has
  visible-response bound0.633s and observer-stop bound0.129s; the exact late-click
  case is fixture-tested, not newly reproduced through the human window. No
  simulation or raw recording is changed.
- **31.7 update:** Compiled fixture remains passed; actual active Cancel has
  0.626s visible-response and0.177s observer-stop bounds. The exact late queued
  activation remains fixture evidence, not a newly reproduced human action.

### DF-0077 — Loading timing can charge time before the request began

- **Date found:** October31,2026; source inspection during retained31.4 review.
- **Problem:** The first timer interval starts at the preceding tick, potentially
  including idle/native-picker time before loading. The historical674ms outlier
  cannot be assigned to this defect without its missing boundary evidence.
- **Repair:** Start at the actual Results request, before metadata/setup and after
  picker selection; include every overlapping interval and final display. Record
  setup separately and support reads finishing before the first observer tick.
- **Verification/status:** Deterministic source/native fixture distinguishes
  four seconds of pre-request idle plus20ms read from an actual520ms in-request
  stall. The delivery checker still rejects the latter and missing boundary
  evidence, under unchanged limits.16async/6boundary cases pass;31.5compiled
  and actual qualification remain pending. Earlier negative records are retained.
- **31.7 update:** Compiled request-boundary fixture passes. The real first
  cached request still fails at658.61ms despite the corrected clock; this is not
  excused as pre-request idle. No timing limit was changed.

### DF-0078 — Retained reload mislabels completed current traces as cancelled

- **Date found:** October31,2026;31.5source regression during candidate build.
- **Problem:** Quiet result cancellation always cancelled the current reader,
  even when no reader was active. Keeping the verified prior display then kept
  its completed current curves beside a misleading cancelled message.
- **Repair:** Preserve completed current state on a retained reload. If a current
  reader is active, use safe replacement instead. Changed input, failure and
  explicit Cancel still clear/cancel stale data through existing generation guards.
- **Verification/status:** Added test fails before repair with the exact wrong
  message. Corrected async/gate suite passes22cases; separate active-reader and
  native current-kind/sample/ready-state checks are pending. Candidate31.5build
  was stopped before it produced an app/archive; no installation or simulation.
  31.6 delivery remains pending. Prior failures and partial build are retained.
- **31.7 update:** Sixty focused source cases and161compiled native flags pass,
  including completed-current kind/sample/ready-state retention and active-reader
  replacement. Actual current choices pass, but the retained-reload current
  state itself is compiled-fixture evidence, not a second actual load.

### DF-0079 — In-memory preparation reuse misses external summary artifacts

- **Date found:** October31,2026; source input-boundary review.
- **Problem:** The complete saved-run snapshot cannot see an artifact outside
  that folder. A legacy summary could reuse old preparation after that external
  file changed; a failing source regression demonstrates it.
- **Repair:** Disable in-memory preparation reuse for summaries with external
  artifacts or caller-supplied geometry. Keep their ordinary preparation and
  checked disk readers; do not silently trust unverified external inputs.
- **Verification/status:** Sixty focused source cases pass, including the new
  external-change case. Native-source-v9 passes161flags with no callbacks or
  changed preferences. The actual50-cell summary's
  162artifacts all resolve inside its run.31.6build was stopped with no surviving
  recorded compilers and no app/archive produced. No user data or simulation
  changed.31.7compiled/actual/install checks remain pending.
- **31.7 update:** Compiled guard passes and the real full-record cache preserves
  every saved value and identity. Actual normal installation remains blocked by
  DF-0075 and Juan's explicit keep-installed-version decision; no unsafe external
  result was opened through the human review window.

### DF-0080 — Metadata reuse can bypass summary validation after a direct display

- **Date found:** October31,2026; source regression during31.8compilation.
- **Problem:** The optimization trusts a prepared display record even when
  supplied directly rather than read through supported-summary validation.
  A direct-display fixture backed by `{}` then bypasses the normal unsupported
  summary refusal on Open. The failing test is retained.
- **Repair:** Mark request metadata trusted only after the supported reader
  validates it in the snapshot-checked worker. Unchanged snapshots may reuse
  that marked record; direct display is not a substitute for validation.
  Changed/missing inputs still re-read metadata before publishing new data.
- **Verification/status:** The origin test fails before and passes after.
  Eighty focused cases and164native source flags pass, with zero callbacks or
  changed normal settings.31.8's exact owned compiler group is stopped with
  no children and no app/archive. Its partial stage and earlier checks remain.
  A distinct31.9native candidate is not yet qualified. No normal installation,
  user-data change or claimed student-observed incident.

### DF-0081 — Native fixture's timed stack dump crashes in its own diagnostic thread

- **Date found:** October31,2026; source native fixture12.
- **Problem/evidence:** The source check exits SIGSEGV before Results checks.
  The OS crash report identifies the faulting thread as the timed faulthandler
  watchdog, in `dump_frame`/`_Py_DumpTracebackThreads`; the main thread is in a
  file-path check. The45-second diagnostic trigger, not the120-second parent
  deadline, is involved. The exact frame-state corruption cause is unproved.
- **Repair:** Remove the timed in-process Python stack dump. Retain fatal-error
  logging, the phase journal, captured output and the unchanged120-second parent
  timeout. Use a separately owned OS sample when stack diagnosis is needed.
- **Verification/status:** Seventeen guard/log/delivery tests pass, including
  ordinary child success, timeout refusal with retained output, and an injected
  fixture failure that still writes a failure receipt without arming a timed
  Python dump. Serial pre-change source fixture13 passes166flags; that does not
  erase fixture12's crash or establish its root cause. Post-change native
  fixture14 passes166flags with no callbacks or normal-setting changes.
  This changes engineering diagnostics, not simulator
  equations or a claim that every app crash is fixed.

### DF-0082 — Reject an unrelated summary before scanning its folder

- **Date found:** October 31, 2026; targeted loading regression after the 31.9 build.
- **Problem:** The worker scanned and hashed the selected file's containing
  folder before validating that file as a supported run summary. Choosing an
  unrelated JSON file could therefore cause unnecessary reads of a large folder
  before the expected refusal. A summary alias also needed its validated run root.
- **Repair:** Validate the selected summary first, resolve its actual run folder,
  and check its digest before and after validation and against the full snapshot.
  Previously validated requests still require complete input checks before reuse.
- **Verification/status:** The refusal regression fails before the fix. Afterward,
  85 focused cases pass, including alias resolution and mutation during admission;
  the native source fixture passes 167 flags with zero callbacks and unchanged
  normal settings. The 31.9 candidate passed its earlier 166 native checks but is
  now retired from installation; an explicit installer refusal is tested.
  Distinct 31.10 compiled and passed its 167 native checks, but remains
  uninstalled because the separate responsiveness check fails. No new simulation, raw-data change,
  normal-app installation or student-observed incident is claimed.

### DF-0083 — Fresh-cache preparation can stall the window through the shared Python lock

- **Date found:** October 31, 2026; actual compiled 31.10 fresh-process check.
- **Problem/evidence:** Same-session reloads pass, including the repaired setup
  path, but a fresh app's checked-cache read takes 3.496 seconds with a 184 ms
  p99 heartbeat and a 457 ms maximum interval. All data remains intact. A corrected
  external OS sample captures the window waiting for the Python lock while the
  reader reconstructs cache entries and trace/replay objects; garbage collection
  contributes but is not established as the sole cause of every long interval.
- **Repair in progress:** Cooperate at existing cancellation checkpoints, at
  most once per 5 ms, and carry those checkpoints through trace indices, measured
  and inferred replay, and distance preparation. Do not change global collection
  policy, simulator equations, sample precision or cache integrity checks.
- **Verification/status:** The missing-cooperation regression fails before the
  change; 87 focused cases pass after its initial version. That initial source
  timing still fails at 172 ms p99. Shared-helper checkpoints pass targeted
  cancellation checks but source timing still fails at 231 ms. A positive
  1 ms backoff also fails (758 ms); a bounded window-acknowledgement attempt
  takes 9.84 seconds and still fails (223 ms), so it was retired before a build.
  Its source and tests remain in the task evidence. The first six-second OS capture
  missed the reader during native picker cleanup; its incomplete result remains.
  The corrected twelve-second Load latest capture includes the actual reader.
  Source tests and profiling are not delivery: normal 30.4 remains unchanged,
  and 31.10 is not accepted on its passing retained-view results alone.

### DF-0084 — Animated loading status repeatedly presents the whole 3D window

- **Date found:** October 31, 2026; saved-recording source diagnosis.
- **Problem/evidence:** An eight-second OS capture of the retired acknowledgement
  attempt spends most main-thread samples in native OpenGL frame presentation,
  while the reader waits for the window. This establishes another source of
  stalls; it does not explain every earlier lock wait or failed interval.
- **Repair:** Keep unknown loading progress static, labeled “Working…”, until
  actual percentage progress is available. Cancel and generation checks stay.
  No sample, clock, simulation or global interpreter setting changes.
- **Verification/status:** The corrected refusal regression fails on the old
  busy range and eight targeted checks pass after repair. Two earlier test-driver
  failures used the wrong fixture shape and remain recorded, not app defects.
  Source full-recording checks remain outside the pause target. A documented
  swap-interval diagnostic also fails and is not adopted. A separate driver
  correction starts requests only after an active, exposed window has entered
  its event loop; its timing still fails. The original native failure and limits
  are unchanged. Candidate qualification and general student readiness remain
  incomplete; installed 30.4 is deliberately untouched.

### DF-0085 — Repeated identical load progress queues unnecessary redraw work

- **Date found:** October 31, 2026; follow-up to failed compiled 31.11 timing.
- **Problem/evidence:** The reader emits an unchanged label and percentage every
  100 ms. A deterministic production-method test receives four identical updates
  where one is enough. This confirms redundant queued work; it does not by itself
  prove the cause of the measured 120 ms p99 cold-load interval.
- **Repair:** Suppress identical progress states. Changed percentages retain the
  existing rate limit, and changed phases still appear immediately. Cancellation,
  recorded values, clocks and full cache checks are unchanged.
- **Verification/status:** The regression fails before repair and nine targeted
  cases pass afterward. Its source-only cold/cache pair still fails the pause
  target: 106.875/1.306 seconds, p99 129.70/150.41 ms. No new build or performance
  acceptance. The failed positive sleeping backoff is then retired while retaining
  cancellation checkpoints; its explicit old-behavior test fails before removal
  and nine targeted checks pass after. Its next distinct source measurement still
  fails:93.435/0.605 seconds, p99 130.87/117.35 ms. Compiled 31.11 retains
  all values but fails its first completed cold-read heartbeat target; qualification
  stops there, with no retained/fresh-cache retry. Its Cancel worker stops within
  0.34 seconds, but the 1.17-second visible-observation bound does not verify the
  one-second UI target. Normal 30.4 remains unchanged, and the separate review is
  left on measured replay beside six full-length voltage curves.

### DF-0086 — Window-wide 3D composition stalls ordinary progress updates

- **Date found:** October 31, 2026; reader-free native rendering comparison.
- **Problem/evidence:** Raster progress and an idle 3D view each measure about
  22 ms p99. Updating progress beside the old 3D widget measures139 ms, even
  when that widget is hidden. The same native GL child inside a raster window
  measures22 ms. This isolates a rendering contributor, not every prior pause.
- **Repair:** Source prototype places the unchanged scene in an isolated native
  child. Geometry, camera, projection, picking, colors and recording values stay
  unchanged. Native outlines and explicit app-window capture composition preserve
  selection and screenshots. A new ancestor-clipping test caught an implementation
  error before packaging; the corrected1x/2x cases pass.
- **Verification/status:** The real production-class rendering comparison gives
  about22 ms in all four cases, with a real ready context/shader and zero callback
  errors. Five focused source tests and169native source checks pass. Physical
  input, outline stacking, scrolling, exports and actual full-recording tests are
  still required. No successor package, normal installation, Windows qualification
  or general student sign-off. All failed31.11records remain.

### DF-0087 — Loading controls resize an unchanged replay scene

- **Date found:** October 31, 2026; native event-dispatch diagnosis after DF-0086.
- **Problem/evidence:** Native surface presentation takes29–61 ms after short
  draw callbacks, and wrapped-label/layout changes cause additional redraws.
  A deterministic test confirms a retained reload replaces the replay help text.
  The source cold open passes93.757 seconds/46.62 ms p99, but its first retained
  reopen still fails134.13 ms. These are source measurements, not package acceptance.
- **Repair:** Reserve the progress row's layout space while its controls are
  hidden. Keep unchanged replay help in place and explain its paused state in
  the loading note and tooltip. Cancellation and input verification are unchanged.
- **Verification/status:** The new regression fails before repair; six focused
  reload/progress cases pass after. The subsequent source pair is3.232/0.428
  seconds: retained p99 improves to47.30 ms, but first indexed preparation still
  fails377.80 ms. One local reader-scheduling probe gives5.259/0.714 seconds and
  105.03/60.19 ms, still a failure; it is diagnostic only and is not adopted.
  The native interaction check passes24checks/zero callbacks; its first test-only
  color assertion incorrectly required three colors for one highlighted neuron
  and is retained as a test-driver failure. Further checked-source work remains;
  these partial improvements alone do not establish performance acceptance.
  Separate31.12later passes171native packaged flags, including stable layout,
  with zero callbacks and unchanged normal preferences. Its actual full-recording
  check is pending; normal installation is not authorized.

### DF-0088 — A timing diagnostic added callbacks to its quiet path

- **Date found:** October 31, 2026; source-only loading diagnostic.
- **Problem/evidence:** The event-dispatch tracer's application subclass is
  selected even with tracing disabled, adding Python callbacks throughout Qt
  painting. This is a test-driver defect, not a shipped app change. Preserve
  v21–23as instrumented observations rather than quiet qualification.
- **Repair:** Use ordinary QApplication unless dispatch tracing is explicitly
  requested. Record the actual class in subsequent results.
- **Verification/status:** Tests execute both branches of the actual driver's
  selection. The corrected plain run still fails the indexed pause target, so
  this correction does not excuse earlier native failures or claim an app fix.

### DF-0089 — Cached reconstruction competes with window painting

- **Date found:** October 31, 2026; source comparison after native scene isolation.
- **Problem/evidence:** Plain cached preparation takes3.394seconds but fails
  157.07ms p99. The retained display passes47.27ms. A local reader-yield probe
  gives6.464/0.705seconds and59.32/43.27ms without changing recorded data.
- **Repair:** After the native-renderer and stable-layout changes, the reader
  yields4ms between8ms CPU quanta. This is local to that worker; no global GC or
  interpreter-switch policy changes. Cancellation checks run before and after
  each yield. The older ineffective1msbackoff and acknowledgement remain retired.
- **Verification/status:** The fairness regression fails before the repair and
  three targeted cases pass afterward. The first after run has a preserved
  test-driver NameError, corrected separately. Production cold/cached timing,
  complete affected regression, native fixture and actual package qualification
  are recorded in the loading report. Separate31.12passes171native packaged
  checks; actual full-recording qualification remains pending. No normal
  installation is authorized.

### DF-0090 — Native test failure leaves window destruction to shutdown

- **Date found:** October 31, 2026; final source native check.
- **Problem/evidence:** The native fixture still expects replay help to be
  replaced during retained verification, contradicting DF-0087. It correctly
  reports that assertion, then exits with a native segmentation fault during
  Qt widget destruction at interpreter shutdown. The failure and crash report
  remain; the normal installed app and preferences are unchanged.
- **Repair:** Update that fixture to require unchanged measured help plus an
  explicit paused loading note/tooltip. Stop owned readers, then explicitly
  process the fixture window's deferred deletion while Qt is still live. This
  changes engineering-fixture cleanup, not the normal app's close guards.
- **Verification/status:** Two source lifecycle tests check real widget deletion
  and refusal to destroy an active reader. Twelve combined lifecycle/host/log/
  wording tests pass. Native source v19then completes normally with171trueflags,
  zero callbacks, unchanged normal preferences and checked fixture deletion.
  This is one repaired fixture-closure path, not universal shutdown or student
  verification. Existing native widget warnings remain logged. The separate
  candidate31.12later passes171native packaged checks and closes its fixture
  normally, with zero callbacks and unchanged normal preferences. The separate
  review copy is under actual recording tests, not normally installed.

### DF-0091 — A large saved skeleton is read without internal checkpoints

- **Date found:** October31,2026; separate31.12saved-cache stack diagnosis.
- **Problem/evidence:** The window waits for Python while the reader rebuilds
  skeletons and cached entries; the capture also includes garbage collection.
  The worker checks between neurons, but the entire node/topology/segment load
  of one neuron has no internal yield or cancellation point. Three discriminating
  tests fail before repair. This is a confirmed checkpoint gap, not proof that
  it explains every183msor726msinterval in the failed native startup check.
- **Repair:** An optional check runs within bounded node, parent-chain, ordering,
  segment and bounds loops. The saved-result reader passes its existing local
  cancellation/yield check into that loader. Other callers keep their no-op
  default. Coordinates, radii, topology checks, ordering and filler metadata are
  unchanged; no global garbage-collection policy or simulation change.
- **Verification/status:** All97related source tests pass, including unchanged
  full geometry, cancellation after256nodes, actual reader callback forwarding,
  fragments, saved-data loading and replay. Expanded134source cases and the
  172flag native source fixture pass with zero callbacks/unchanged preferences.
  Changed-source indexed/cold checks pass as recorded in the loading report;
  separate31.13is compiling. No packaged performance or installation acceptance.

### DF-0092 — Skeleton records carry a dictionary for every point and segment

- **Date found:** October31,2026; source memory audit after the captured reader
  and garbage-collection waits. The50saved skeletons contain72,308nodes and
  72,258segments, each with a separate attribute dictionary.
- **Repair:** Keep the same immutable data fields in slotted records. This
  removes144,566per-element dictionaries; no coordinate, radius, topology,
  ordering, unit, filler metadata or simulation equation changes.
- **Verification/status:** Two tests fail before repair; they require compact
  storage plus unchanged serialization and independent replacement. A full
  saved-geometry comparison has the same complete identity before and after.
  Shallow record memory falls24,865,552to12,143,744bytes. All134expanded source
  cases,172native source flags and changed-source fresh/cold timing checks pass
  at their declared scopes. Separate31.13is compiling; candidate12does not
  contain this change. Packaged speed remains unverified; normal30.4stays unchanged.

### DF-0093 — Source timing starts without the user's saved circuit

- **Date found:** October31,2026; source/native comparison after31.12.
- **Problem/evidence:** The source driver opens an empty MainWindow; actual
  native reviews first restore50neurons. This is not the same object/geometry
  context. Whether that difference explains a particular pause is unproved;
  earlier source results remain at their narrower scope, not native acceptance.
- **Repair:** The existing driver has an explicit saved-project mode using the
  retained project and isolated task profile. It requires all50saved IDs to be
  loaded before timing, records context counts/hash and verifies the profile
  stays unchanged. The default component-test mode remains labeled separately.
- **Verification/status:** The missing boundary fails before repair. The actual
  helper passes synthetic full-context and partial-restoration-refusal cases;
  all28delivery/driver cases pass. The actual matching-project measurement loads
  all50saved neurons and preserves the task profile. Indexed10.853s/p99126.65ms
  fails the original limits; retained1.011s/p9975.57ms passes. Both are kept,
  without an unchanged retry. Build31.13compiled separately; its actual native
  performance remains unverified. No new simulation, normal installation or
  claim that a source test proves native responsiveness.

### DF-0094 — Fresh-process cached loading still pauses with the full circuit

- **Date found:** October31,2026, actual31.13review after the compact geometry
  change. Primary cold149.074s and retained0.720s pass, but the first separate
  process read is6.891s/p99255.25ms. Qualification stops without another attempt;
  the existing checker refuses it. Both complete caches and all protected inputs
  match; normal30.4remains installed and unchanged.
- **Diagnosis/status:** A separate component audit retains608,740tracked objects
  after skeleton/table/plot preparation:144,516mapped series,144,516plot records
  and144,517memory views among them. Earlier matching-source GC pauses reach
  293ms. These support allocation/collection pressure as a repair direction,
  not a proved explanation of every native interval. A source successor shares
  replay/plot records with one-time atomic labels; a different label request
  keeps the already displayed record unchanged. Three meaningful regressions
  fail before repair; related source checks are running. The native fixture
  now checks actual shared identity, but no new build or latency pass is claimed.
- **Actual bounded behavior:** Both recorded conditions, measured replay,
  source/branch/receiver filtering, stable Pause, total/chemical current and
  two3840pxexports work in13. A choice-sheet coordinate action refuses without
  changing state; keyboard selection works. Six other current kinds and broader
  layouts keep prior12evidence only. General student verification is incomplete.

### DF-0095 — Results allocates duplicate copies of identical project geometry

- **Date found:** October31,2026, saved-only comparison after the failed13read.
  Of72,308saved nodes and72,258segments,50,600nodes/50,550segments exactly match
  existing project rows, with the same body/release keys.
- **Source repair:** Parse and validate the complete saved file as before. Reuse
  only identical frozen rows from an unambiguous same-body/release candidate;
  different rows are created from saved data. Never replace saved paths, bounds,
  unit/filler metadata, topology checks or input snapshots with project settings.
  MainWindow passes candidates separately from caller-provided display geometry.
- **Verification/status:** Two real duplicate-record regressions fail before
  repair. Changed rows/original preservation, wrong body/release, invalid saved
  rows, cancellation and ambiguous candidates are covered. An initial test call
  names a nonexistent test file and collects nothing; that record is retained,
  not counted as a pass. Corrected expanded checks pass108cases plus7roundoff
  cases. The complete audit preserves72,308nodes/72,258segments and all metadata,
  sharing50,600nodes/50,550segments only where identical. Native source v23checks
  this worker handoff. Separate14now passes package,60focused native Results/
  safety checks and the actual full-recording review, sharing the same validated
  rows. Final postflight preserves all original inputs; normal30.4stays unchanged.

### DF-0096 — Cached data eagerly creates a record and memory view for every trace

- **Problem:** The full table retains144,516mapped sample records plus144,517
  memory views, even when a plot shows only60rows. Earlier shared-record,
  metadata-column and exact-geometry reuse changes preserve data but still fail
  the original first-read pause target; their negative measurements are retained.
- **Source repair:** Keep primitive row indices over the same full-precision,
  checked mapped cache. Create sample views on demand with128recent strong
  entries and weak references for live views. Plot/legend selections stay lazy;
  measured replay interpolates directly from full stored clocks/values. No new
  cache format, dependencies, sample reduction, physical or globalGCpolicychange.
- **Checks/status:** Three eager-allocation cases fail before repair.83related
  cases pass; mixed/nonuniform clocks and support edges match the independent
  CSV path. A subsequent new counterexample catches equality comparing payload
  bytes without clock-block identity; it is fixed and the short suite passes.
  Native source v23and133final source cases pass. Full source measurements pass
  cold140.950s, indexed5.819s and retained1.280/1.574s, with p99inside100ms;
  all57,950,916stored values/144,516identities match the independent oracle.
  Separate14also passes its first actual cold149.411s, retained1.110s and
  fresh-process9.745s reads, with p99gaps35.39/49.05/27.91ms. Both full native
  caches match the saved-row oracle. One0.644smaximum gap remains; this is not
  pause-free or general student acceptance. Normal30.4remains untouched.

### DF-0097 — An in-memory result can reuse a damaged or replaced mapped cache

- **Problem:** Checking unchanged recording files does not detect a later write
  to the separate mapped display cache. A regression changes one cached sample
  and demonstrates the old in-memory preparation is reused incorrectly.
- **Source repair:** Before reusing a mapped preparation, verify its original
  file identities and complete cache digests with cancellation checkpoints.
  Changed/missing cache data clears the unverified old view and reloads from the
  unchanged saved recording. Preserve request generations and the actual
  discovered result when notifying the window.
- **Additional failures caught:** A late notice could relabel a completed view;
  a repaired path could name a new file while an older mapping still referenced
  its damaged unlinked file. Both counterexamples fail before their corrections.
  Fast trace equality now requires unchanged mapped-file references as well as
  data/clock identity; otherwise it compares actual samples.
- **Status:**49initial related cases and21focused final cases pass, including
  changed bytes, corrected reload, old-mapping refusal and stale/late notices.
  The native source fixture exercises real paused-view invalidation and recovery.
  These pass inside source v21, but its full wrapper times out before publishing
  the follow-on safety/callback checks; that partial record is not a total pass.
  Subsequent scoped source v23and actual compiled14pass the complete focused
  Results/safety route, including invalidation and recovery, with zero callbacks
  and unchanged preferences. No simulation or normal installation.

### DF-0098 — The all-library fixture outgrows its fixed native deadline

- **Observed failure:** Native source v21finishes the main fixture and live-Qt
  window deletion after about118seconds, then hits the unchanged120second total
  deadline during the later safety phase. Its partial successful receipt cannot
  certify the total check. The earlier suggestion of a shutdown/startup hang is
  not supported by these timestamps.
- **Diagnosis:** The unchanged safety phase alone completes15.54seconds, all16
  flags and zero callbacks. The timeout is retained; it is not relabeled a pass.
- **Scoped harness repair:** Add an explicit Results-and-safety app entrypoint
  using the complete existing Results fixture, window disposal, fragment check,
  safety phase and callback guard. Keep the original all-library route,120second
  limit and normal-preference check. Distinct schema/scope prevents a focused
  result from being called full-library or general student acceptance.
- **Status:** Ten focused routing, failure-receipt, settings and lifecycle cases
  pass. Native source v23and compiled14pass60focused flags within the unchanged
  deadline, with zero callbacks, unchanged preferences and checked shutdown.
  This is not the full-library fixture or general student acceptance; the
  original failed timeout remains. Recording values and normal installs stay.

### DF-0099 — Results digests the same voltage source twice per preparation

- **Observed inefficiency:** The complete saved-input snapshot and the voltage
  table reader both hash the entire voltage file in the same request. A focused
  counterexample records two complete digests where one suffices. This does not
  establish the sole cause of the earlier11.87second duration failure.
- **Repair:** Pass only the current request's full digest and original file
  identity from its complete snapshot. The table reader refuses a different or
  changed identity. It still verifies all cache bytes and checks the source again
  after loading; the final whole-run unchanged-file check remains. Independent
  reader callers retain their ordinary full hash.
- **Checks/status:** The initial optimization catches an external-voltage
  symlink regression; lexical snapshot lookup is corrected and that
  case passes. A test incorrectly compares a deliberately corrupted old mapping
  against the repaired mapping; it now retains expected samples before damage.
  Both failures remain recorded. Ten focused digest/mutation/replacement/cache
  cases and native Results v23pass. Source v38cached5.819s/p9982.86ms and
  retained1.574s/p9936.94ms pass. Final source cold140.950s and compiled14
  cold149.411s/retained1.110s/fresh-process9.745s pass the original limits.
  Complete data comparisons and preservation checks pass; installed30.4is
  unchanged. This does not establish the cause of every remaining pause.

### DF-0100 — A review can launch the preceding app before its checked copy finishes

- **Observed failure:** Root launched before waiting for the copy transaction's
  completion. The new review folder opened old13, not14. The existing copier
  refused its newly live target before replacement; the staged14candidate and
  preceding app remained intact. The empty window quit0and its owners are
  independently absent. No recording or simulation started.
- **Harness repair:** Require a completed copy receipt, correct executable
  digest/path and embedded build before spawning a review. Repeat the identity
  check in the held-owner validator. Reuse neither the failed attempt's folder
  nor an uncertain process. Resume the retained14stage only after complete
  resource/code/signature checks and closure; normal installation stays refused.
- **Checks/status:** Missing completion refuses before claim or child creation;
  changed bytes, wrong path/build and unsuccessful receipts refuse. An existing
  real-owner test is initially blocked by sandbox process-read permission; its
  host-permission check is separate, not a suppressed failure. Seven focused
  host cases pass. Actual checked-copy recovery69155exits0, retaining the exact
  preceding13app without re-copy/signing the staged14. Guarded14launch and live
  identity verification succeed in new review folders. Final preservation passes.
  This is launch admission, not general student-readiness acceptance.

### DF-0101 — Remaining Results labels and export layout need cleanup

- **Observed in checked14:** After a real successful Cancel, the placeholder
  header still begins with loading beside the explicit cancelled note and
  restart button. The worker has stopped and restart succeeds; this is stale
  presentation, not evidence of an active simulation.
- **Exports:** Full0–8msvoltage/current curves and units are preserved, but the
  export captures only the visible scrollable legend portion; long labels clip.
  One voltage-axis label overlaps a tick. These are not publication-ready layout.
- **Status:** Unfixed and explicitly retained in actual-review-31.14.json.
  Bounded loading acceptance and general student readiness remain distinct.
  No new scientific run or normal installation follows from these observations.

### DF-0102 — Reviewed radius copies are omitted from a managed SWC library

- **Observed in installed Mac 20261031.14:** A fresh six-cell Male CNS v1.0
  walkthrough downloads four missing neurons, preserves two existing TTMn
  files, and saves four separately reviewed radius-healed copies. The managed
  inventory omits those new copies. Circuit Builder selects two older PSI
  derivatives whose coordinate records lack the frame required by the newly
  downloaded contacts. Readiness correctly refuses before any simulation.
- **Cause:** Admission of an unlisted reviewed derivative handles synthetic
  bridge copies but not the app's radius-only copies. A same-release older
  variant can therefore remain preferred while the new reviewed copy is hidden.
- **Source repair:** Verify each radius copy against its committed original,
  regular review record, both file identities, unchanged geometry/topology,
  allowed radius changes and preserved coordinate contract before indexing it.
  Body counts remain unique and raw acquisitions remain unchanged. Bad records,
  edited geometry/radii, changed originals, unit changes and symlinks refuse.
  Frame refusals now identify the affected neuron and both declared frames.
- **Checks/status:** 46 radius/inventory tests and 33 coordinate/contact/library
  tests pass. A read-only check of the actual acquired inputs verifies all four
  copies, 44 radius edits, unchanged original points/edges and unchanged existing
  TTMn bytes. The repaired source selection has the required frame for all six
  cells and retains 228 located contacts across 16 pairs. A further 53
  connected-model regressions pass. The separate 20261101.1 package passed its
  source/archive checks but failed the capture check described in DF-0103.
  Corrected Mac 20261101.2 passes the complete package check, 177 native flags,
  modern and legacy installation, and failed-startup rollback. No callback
  errors or normal preference changes were recorded. It is installed in the
  normal app location, with the entire previous app preserved separately.
- **Actual installed walkthrough:** The app explicitly refreshes and selects
  the four reviewed copies plus two unchanged TTMn files. A separately saved
  project preserves the experiment and membrane settings. All 19 readiness
  checks pass. One eight-millisecond NEURON Run completes both conditions with
  exit 0. Independent reading checks 3,905,216 finite voltage values, complete
  clocks, all 12,128 locations per condition and all 228 original contact
  mappings. Saved Results reopens in 0.6 seconds. Play, Pause, seeking and
  condition switching work. The small input produces subthreshold DNp01 voltage
  differences with no threshold events or response in the other four cells.
  No repeat or outcome-driven tuning was used. Details are in the beginner
  walkthrough's actual-app-review-v1 and run-audit-v1 records.
- **Delivery status:** Installed Mac repair checked. Windows remains deferred,
  this repair has not been uploaded, and the broader student test remains open.

### DF-0103 - Early restored-window capture can crash the native scene

- **Observed in the separate Mac20261101.1candidate:** Its packaged library
  checks pass177flags. Install and legacy updater cases pass, but the injected
  failed-startup case restores its app files and then crashes while capturing
  the restored window. The normal installed31.14app remains untouched and no
  simulation starts. The candidate and failure records are retained.
- **Measured failure:** The native crash stack enters MainWindow.grab,
  composite_native_viewports and CircuitViewport.grabFramebuffer in QtOpenGL.
  A valid graphics context alone does not establish that the native child has
  presented an allocated framebuffer. The capture follows open_project in the
  same event callback, before newly shown children necessarily finish rendering.
- **Source repair:** Admit a native capture only after exposure and allocation
  of its actual framebuffer. Refuse an unready visible scene explicitly instead
  of omitting it from a composed image. The disposable updater capture uses a
  bounded10-second wait, preserving unrelated errors and refusing missing images.
  The library fixture waits up to3seconds for actual pixels without activating
  controls. An initial frame-swap heuristic blocked a valid resized view and is
  retired; both failed native source checks remain. The current guard uses the
  buffer handle described by Qt's documented API and6.10implementation:
  https://doc.qt.io/qt-6/qopenglwindow.html#defaultFramebufferObject
  and https://raw.githubusercontent.com/qt/qtbase/6.10/src/opengl/qopenglwindow.cpp.
  Source geometry, membrane settings and saved numerical values are unchanged.
- **Checks/status:** Both early-grab negative cases fail before the repair.
  The initial guard's62viewport/capture/updater regressions pass, including
  deadline, empty-image and unrelated-error cases. The updated allocated-buffer
  guard's64-case regression passes. The full native source check passes177flags
  with no callback errors or changed preferences. A separate capture of the exact
  restored-window draft observes the initial unallocated buffer, then saves the
  actual window without a crash. Distinct candidate 20261101.2 passes its package
  and 177 native flags without callbacks or preference changes. Its actual
  updater completes modern installation, legacy installation and failed-startup
  recovery with real saved window images and unchanged drafts, source files,
  profiles and shortcuts. The normal installed app then completes the six-cell
  walkthrough recorded under DF-0102. Candidate 1 and its crash remain retained.
- **Delivery status:** Installed on Mac 20261101.2. No Windows qualification or
  public upload of this repair occurred.

## DF-0104: No in-app path through the beginner experiment

- **Symptom:** A new user had to switch between the beginner PowerPoint and
  several app pages without a saved guide position or a way to load its exact
  teaching settings.
- **Change:** The initial inline guide was rejected. The replacement is a
  standard floating wizard dialog with bottom Previous/Next, Pause/Exit and a
  dimmed backdrop. Only the current step's real controls remain interactive.
  It is also available from Help and covers the six-cell Male CNS v1.0 NEURON workflow,
  keeps its step across launches, and asks before applying the example settings.
  Navigation starts no download, installation, shape repair or simulation.
  Existing custom overrides and pending edits are preserved. Finish requires a
  loaded completed result with matching saved circuit and experiment inputs.
- **Checks/status:** Fourteen focused source cases pass, including dialog
  geometry, spotlight hit testing, keyboard focus, Pause/Exit, saved progress
  and refusal guards. A complete source-level native Mac suite passes with
  all checks and its original deadline. The earlier banner-only Mac package
  is retained as a prototype, not the requested final design. The new dialog's
  package and installed-app verification are not yet complete. The existing
  completed teaching recording is reused; no new scientific Run is requested.
- **Limits:** Clean-Mac NEURON installation is still unverified. Windows is
  deferred. The wizard does not certify every student control or physiological
  validity.

## DF-0105: Mac compiler spends excessive time on the native test module

- **Symptom:** A first-use-guide candidate's Mac build spent more than 17
  minutes on one generated UI-test compilation unit, with a CPU core busy and
  modest memory use. No app or simulation was blocked by this compiler process.
- **Observed cause:** A one-second system sample placed the compiler in LLVM's
  SLP vectorizer and its cost calculations for the large generated function.
  This is build-time compiler work, not slow experiment execution.
- **Scoped workaround:** The next local build uses the compiler's supported
  `-fno-slp-vectorize` flag through Nuitka's documented-in-source inherited
  CFLAGS path. The app and external worker sources remain unchanged. No
  fast-math option, simulation parameter or recording value is changed.
- **Checks/status:** The earlier compilation was stopped and its source and
  compiler records retained. The flagged banner-prototype build completes and
  passes its focused native, package and updater checks. The replacement
  dialog still requires a separate compilation and checks. This is a local build workaround, not a repaired default for
  every toolchain or platform.

## DF-0106: Wizard focus and accessible step labels

- **Symptom:** The first dialog implementation looked embedded but keyboard
  focus remained in the main window. The earlier banner's accessible step
  chooser also retained an old label after Previous/Next navigation.
- **Cause/change:** QDialog creates an inappropriate native child handle when
  embedded in the app's native-scene window. Use a floating dialog card with
  ordinary named Qt controls and a separate masked shade. Preserve normal text-change signals while using a
  reentrancy guard for the step picker. Redirect keyboard focus away from
  dimmed controls without blocking the highlighted controls or their dialogs.
- **Checks/status:** The focused test first failed on the actual focus
  assertion, then passed after correction. Native source spotlight tests
  pass actual field hit testing, runtime-dialog cancellation and accessible
  label refresh. Final compiled and installed checks remain pending.

## DF-0107: Complete native library suite exceeds its deadline

- **Symptom:** Multiple source and packaged checks exceeded the original
  120-second limit, including the retained pre-wizard build. Focused checks
  passed, so they did not establish full-suite acceptance.
- **Diagnosis/change:** Keep the failures and every existing check. Added
  monotonic phase timing and retained a one-second system stack sample. The
  sample shows native modal processing and repeated Cocoa child-window
  warnings, rather than a blocked simulator or missing network response.
  Data Library menus and dialogs now use the actual top-level app window as
  their owner. This targets the native ownership defect, not the time limit.
- **Checks/status:** A later full source check completes, but compiled20261101.7
  still times out. Production-style and outer-loop comparisons also time out.
  Two inert-menu probes without Digifly reproduce multi-second native waits;
  an OS sample shows AppKit/SkyLight window-ordering and window-server IPC
  dominating the delay. Qt animation changes do not remove it. Exact host
  service cause remains unproved. Await host recovery before further unchanged
  attempts. No checks are dropped, deadline relaxed or failed partial promoted.
  The installed app is unchanged. Detailed records remain in
  outputs/workstation/first-use-wizard-20261101-v1/NATIVE_CHECK_DIAGNOSIS.md.

## DF-0108: Deferred wizard callbacks survive a closed window

- **Symptom:** A regression process printed successful assertions but exited
  abnormally. Explicit Qt teardown then caught pending layout callbacks
  accessing already-deleted window objects in eight cases.
- **Change:** Layout, focus and initial-page timers now belong to the wizard
  and are cancelled by Qt when it is destroyed. Pause also stops those timers.
  Tests flush window deletion while Qt is alive instead of accumulating closed
  windows until interpreter shutdown. A trial custom accessibility adapter
  was removed; the real named Qt controls remain.
- **Checks/status:** The failing process and eight teardown errors are kept.
  The final24case regression exits0, and build20261101.7 includes the repair
  with a successful package audit. Its full native suite remains blocked as
  described in DF-0107; it is not installed or publicly delivered. Passing
  assertions without a successful process exit do not qualify a build.

## DF-0109: Previous can leave keyboard focus in a newly dimmed field

- **Symptom:** Actual installed20261101.7 review opens and cancels the highlighted
  output-folder picker. Accessible Previous returns to the fully dimmed welcome
  step, but the output field still owns keyboard focus. Its text was not changed.
- **Cause/change:** Accessible press activates a navigation button without
  necessarily focusing it. Changing spotlight targets did not reconsider existing
  focus. The completed spotlight layout now applies the existing focus guard,
  which preserves highlighted fields and native dialogs but moves focus out of
  newly dimmed controls.
- **Checks/status:** keyboard-focus-before-v2.xml reproduces the retained-field
  failure. The first test attempt omitted Qt's timed press animation and is kept
  as a test-setup failure, not the defect proof. source-tests-spotlight-v12.xml
  passes26cases, including the real accessible press and a still-highlighted-field
  positive control. Separate20261102.1compiles and passes its exact package audit,
  complete native suite under the unchanged120second deadline and focused
  wizard/Results/safety route, including the new focus assertion and no callbacks.
  The updater cases subsequently pass, and 20261102.1 is installed with full
  recovery copies. Actual installed Previous moves focus out of the newly dimmed
  field. The saved six-cell experiment opens and its loaded recording matches
  the project. The next user request concerns the guide's writing and layout;
  see DF-0110. Earlier recordings and the full pre-wizard app remain preserved.

## DF-0110: Wizard instructions are dense and difficult to read

- **Symptom:** The user rejects the writing and appearance of the first-use
  wizard. Each page places instructions, parameter values and repeated cautions
  in a single text block. Default status text adds another explanation before
  the user has asked for a check.
- **Change:** Rewrite all thirteen pages using the user's Natural Writing rules.
  Keep the main task in short paragraphs or numbered actions. Put full settings,
  cell IDs and background notes in a collapsed Details panel. Increase instruction
  type to 15 pixels with paragraph spacing, simplify button labels and show status
  only after an action. Keep Previous and Next at the bottom.
- **Checks/status:** Source copy-v3 passes 28 cases. All thirteen tasks fit without
  scrolling; expanded references and navigation fit at the minimum window size.
  The source-native wizard/Results/safety fixture passes with unchanged preferences.
  Each task page and the long membrane reference were visually inspected.
  Copy review covers the user's phrasing exclusions and all cadence checks.
  Compiled .2 exposes a test-isolation error: the added guide traversal leaves
  More options expanded before the independent compact-default assertion. The
  fixture now saves and restores the expansion states it changes. The complete
  source-native route passes 194 true checks in 51.85 seconds with all prior
  assertions retained. Source copy-v4 passes 28 cases. Final .3 compiles and its
  exact package audit passes. Its focused fixture catches a click sent before
  the dialog settles; the fixture now waits for a stationary real hit target.
  Final expanded build .4 passes both complete and focused native checks with
  unchanged preferences. Updater and installed checks are in progress. The
  subsequent DLMn tutorial expansion has separate user approval and Run checks.

## DF-0111: Reviewed radius and fragment repairs do not load together

- **Symptom:** The expanded tutorial downloads all sixteen cells, but Circuit
  Builder reports two disconnected skeletons after both repair dialogs finish.
  It selects the intermediate radius copies instead of the saved joined copies.
- **Cause/change:** The joined-copy check requires its immediate input to be a
  raw downloaded file. It now verifies the radius review against that raw file,
  then verifies the joined plan, output and coordinate units. Download review
  also skips verified radius derivatives when identifying an existing raw file.
- **Checks/status:** A generated repair chain reproduces the rejection. The
  valid chain now loads, while four damaged or incomplete chains are refused.
  The combined source suite passes 141 cases. Read-only inspection of the actual
  sixteen-cell library selects one connected skeleton per cell. Build4 then
  loads both joined copies in the fresh tutorial project and completes its
  sixteen-cell Run. All 840 contacts avoid synthetic filler targets. Installed 5
  reopens that unchanged result; see DF-0112 for the final review repair.

## DF-0112: Wizard rejects the expanded run's contact-file size

- **Symptom:** The actual sixteen-cell run completes and Results displays its
  voltage recordings, but the wizard refuses Finish because the contact document
  exceeds its two-megabyte review cutoff. Its 840 located contacts occupy 3.05 MB.
- **Change:** At the user's request, remove the fixed file-size cutoff. Read
  chemical contacts one at a time from 64 KB chunks and calculate the digest from
  those same bytes. Continue checking cell identities, located contacts, counts,
  both conditions and the completed summary. Settings documents also have no
  byte-count cutoff. Memory follows an individual contact or metadata value.
- **Checks/status:** The generated 3.1 MB case reproduces the original refusal.
  Source now passes 149 cases, including 9 MB valid files, split UTF-8/numeric values,
  damaged contact digests and malformed/incomplete documents. The actual saved
  contact file yields 840 contacts and the expected digest in 0.017 seconds. The
  replacement Mac build 20261102.5 passes package, complete/focused native and all
  three updater cases. Actual installed Check settings and Finish accept the
  unchanged expanded result. Final postflight verifies previous and expanded
  data plus the recovery apps. No second simulation was run.

## Maintenance and GitHub handoff

For each new issue, record its symptom, cause when established, repair, before/
after checks, package/build status and remaining limitation. Add a stable DF ID;
update an entry when its delivery status changes rather than erasing the failure.
Keep failed tests, intentional refusals and scientific negative results distinct.

This file belongs in the repository and the next authorized source update to
GitHub. Before that upload, review additions for credentials, private device
paths, raw neurons and saved runs. Link detailed records using repository-relative
paths. Public release notes should list relevant user-visible changes in plain
language; do not paste test transcripts or digest lists. Internal integrity
checks remain required even though hashes are not public download assets.
