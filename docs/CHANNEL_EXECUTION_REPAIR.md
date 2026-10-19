# Circuit Builder channel execution

Active user request, October 2, 2026: make the exposed settings execute, including
translation and testing in NEURON, Arbor and BMTK BioNet. This is an app-feature
task, not authorization to resume PDCM production, walking training or schedules.

Expanded app-feature repair verified in packaged build20261002.12. The original
one-cell spiking protocol now differs from NEURON by0.002318mV maximum in Arbor
(previously1.178390mV), and9.85e-13mV in actual BioNet (previously0.078642mV).
All three produce one threshold crossing. Default/per-neuron/per-source-SWC-edge
HH and locked native-channel settings, geometric soma/AIS distribution, and
all/AIS/selected-edge stimulation and recording now use explicit shared maps.
Actual packaged GUI edit/apply/save/reopen/Run and artifact checks pass; details
and caveats are in the current receipt section below. This is engineering feature
acceptance on declared small controls, not biological/statistical replication.

Build20261002.11 is preserved intermediate evidence: its legacy soma electrode
yielded0.027018mV Arbor /0.013484mV BioNet errors. A macOS Qt accessibility crash
during same-size morphology-table refresh is preserved in the v2 observation
record. Retaining table items and blocking intermediate selection signals repairs
the reproduced path: actual .12 reloads and hierarchy reads did not crash. This
does not certify every macOS accessibility path. Earlier .8 entries below are
historical; their non-soma/AIS gates and unresolved voltage error are superseded.

Latest follow-up authorized this lane: user rejects the remaining Arbor voltage
difference and requests per-SWC-compartment settings, AIS mapping and non-soma
recording support in all three engines. Earlier completion is historical for
the soma/branch feature subset only. Require explicit source-node/segment-to-CV
maps, precedence and recording identities, no hidden shifts or copied soma
voltage. Diagnose electrode-site interpolation, spatial mesh and TABLE rates
separately before claiming a parity repair. Preserve all old app/run evidence.

Confirmed failure: disclosure sections open; the generic adapter and both workers
reject every active native density channel. Menu availability is not execution
support. Existing explicit apply semantics preserve the chosen target scope.

Continue from the current source tree, preserving unrelated edits. Implement and
exercise one backend at a time. Never mark a channel ready on compile success alone.
Use the locked mechanism definitions in core/mechanisms.py and exact upstream
sources. Store translations separately; never edit the user's Phase 2 originals.
Distinguish exploratory biological surrogates from original Augustin mechanisms.

Acceptance per backend: exact source/translation/compiler/library identities;
soma/branch densities, HH replacement and leak, ion reversals, temperature and
optional parameters are materialized; enabled/disabled and changed-density small
controls alter measured currents/voltages as expected. Compare translated channels
against native NEURON over declared voltages, temperatures and timesteps, reporting
errors and convergence rather than assuming identical solver output. Exercise the
actual generic worker, selected-cell and network paths, SONATA roundtrip for BioNet,
and real packaged GUI applying, saving/reloading and running a bounded experiment.
Check failure cases (changed hashes, unsupported constructs/parameters, unmapped
segments) still fail before accepting a result. Retain existing morphology guards.

Serial local checks: at most two cells / short protocols, one compiler or simulator
at a time; use fresh versioned output folders, timeout compilers at 300 seconds and
small runs at 120 seconds. No large simulation or whole-connectome test is required.
If a construct needs a changed model assumption, stop that translation for user
direction and continue independent supported work. Do not silently substitute HH.

Current status: all eleven locked channels execute through actual NEURON, Arbor
and BioNet workers, including two-cell controls and BioNet SONATA materialization.
The exact same scientific model/experiment inputs are mechanically compared by
`scripts/audit_three_simulator_test.py`; source locations and engine/name are the
only excluded execution metadata. Source-bound channel execution is now verified
in packaged build20261002.8 through actual GUI apply/save/reload/run checks in
all three engines. Numerical equivalence and biological/statistical replication
are separate claims, not certified by this feature completion.

Measured source-bound controls: `outputs/workstation/channel-current-audit-20261002-v4/audit.json`
pairs330 native/Arbor controls over6.3/23/35C and three integration timesteps.
Zero/double-density checks pass; all eleven step-current errors decrease at each
halving. Worst steady normalized current difference is0.313% (Augustin NaP TABLE
interpolation versus direct rate evaluation). These are engineering controls,
not biological validation of the mammalian channel surrogates.

The identical two-cell, all-channel test exposed a4.5mV Arbor voltage discrepancy
that did not improve under time refinement. A radius-scaled artificial root cable
was adding backend-specific membrane area. Reducing this topology-only adapter to
0.001um, preserving regional mesh boundaries, using40um target spacing and placing
stimulation/recording at the selected SWC node reduces the refined discrepancy to
1.176mV maximum/0.385mV RMS. BioNet is0.0776mV maximum/0.0197mV RMS in that same
shared observed window; all three produce one threshold crossing per cell.
`outputs/workstation/three-simulator-identical-test-20261002-v3/audit.json` binds
inputs and measured traces. The corrected coarse-step comparison is
`outputs/workstation/three-simulator-identical-test-20261002-v4/audit.json`:
Arbor1.147mV maximum/0.376mV RMS. The remaining voltage difference does not shrink
over these two timesteps; its attribution to regional/spatial discretization or
amplification of measured rate-table differences remains unresolved. Do not claim
exact cross-backend voltage parity or numerical convergence of this whole-cell
trace. Input identity, channel-current refinement and matching spike counts are
separate verified findings.
BioNet's post-fadvance sampling offset is retained in raw output and explicitly
interpolated only over common observed support; endpoints are not invented.

Arbor translations use official modcc-generated CPU mechanism-ABI plugins, avoiding
linking an incompatible second static solver from the wheel. This follows the
[official ABI](https://docs.arbor-sim.org/en/latest/dev/mechanism_abi.html) and
[regional mesh policy](https://docs.arbor-sim.org/en/v0.12.2/concepts/morphology.html).
Native compilation rejects imported/compiler runtime mismatches and ambient suffix
collisions. Import3d is parsed before original Augustin SUFFIXk is registered,
preventing a hoc variable-name collision without editing the upstream source.

The separate compatible runtime is
`../Digifly-Runtimes/envs/digifly-channels-neuron-bionet-py312-v1/bin/python`
(NEURON9.0.1/BMTK1.2.0). The app's runtime picker was set to this verified environment;
earlier environments and app builds are preserved. Per-SWC segment overrides, AIS
mapping, nonsoma recording and BioNet electrical edges remain explicitly gated.
This repair must not claim those unsupported selections now execute.

Packaged20261002.6 GUI editing/apply/save/reload preserved Para soma0.0015 and
branch0.003S/cm². Build20261002.7 ran the exact same manually edited one-cell
experiment through all three real Run buttons. Its trace audit exposed a separate
single-cell bug: NEURON Import3d rewrote soma geometry, unlike NEURON networks and
BioNet's shared branch constructor. Despite one threshold crossing in each engine,
the roughly14–15mV voltage differences do not qualify as close trace reproduction.
`outputs/workstation/three-simulator-packaged-test-20261002-v1/audit.json` preserves
that failed numerical comparison, not a accepted feature-completion receipt.

NEURON now uses the same SWC branch-run constructor at every cell count. A real
all-eleven-channel one-cell run matches the preserved two-unconnected-cell control
exactly over100samples:0mV difference, recorded in
`outputs/workstation/neuron-cell-count-audit-20261002-v1/audit.json`.
The production-path suite after this repair passed147tests with1optional-runtime
skip in54.88seconds. No neuron geometry is shifted or replaced; the old run is
preserved. macOS simulator/profile selectors now explicitly admit keyboard focus;
the actual final-build GUI and a focused traversal test exercise engine changes
without changing the scientific protocol. An audit failure control deliberately
doubled one copied stimulus and was rejected before producing acceptance output.

This amends the existing harness; it is not a background runner. Evaluation is
same-agent production-path testing, not an independent harness benchmark.

## Historical build20261002.8 feature receipt

`outputs/workstation/channel-gui-acceptance-20261002-v1/receipt.json` binds the
actual20261002.8 executable/archive, observed GUI record, saved version3 project,
three completed job plans/logs, all run-file checksums and preserved executed
workers/translator/build helper. Original datasets and earlier failed controls
remain unchanged. NEURON and BioNet use the compatible isolated Python runtime;
the verified runtime choices and restored normal output root survive restart.

In the real app, Para was manually changed to0.0016 soma/0.0032 branchS/cm²,
applied as defaults, saved and reopened. The same one-cell morphology, all eleven
channels, HH replacement, Shabq10=1.5,23C,5ms window,dt0.0125ms,0.01nA step1–3ms
and soma recording interval0.05ms ran via all three actual Run buttons.
The immutable comparison is
`outputs/workstation/three-simulator-packaged-test-20261002-v3/audit.json`:

| Comparison to NEURON | Maximum voltage difference | RMS difference | Threshold crossings |
|---|---:|---:|---:|
| Arbor |1.178390mV |0.383124mV |1 in both |
| BioNet |0.078642mV |0.020295mV |1 in both |

Comparison uses99 shared observed samples; BioNet's real0.0125ms recording
offset is preserved. No extrapolation, silent acceptance margin or exact-parity
claim. BioNet's native SONATA voltage report is finite100×1; its node mapping,
single spike and materialized Para densities/Shab parameter pass artifact checks.
These measurements reproduce the same short test input and spike count; they do
not establish outcome equivalence over biological protocols or independent networks.
The residual Arbor voltage difference and lack of whole-cell time convergence
described above remain unresolved.

The final audit failure control
`outputs/workstation/three-simulator-audit-failure-control-20261002-v2/verification.json`
doubles a copied stimulus and rejects mismatched scientific inputs before creating
acceptance output. All originals are preserved. The final app boundary audit uses
the build's explicit approved bundled-Arbor policy:3038files/273166855bytes,
zero issues; deep/strict ad-hoc signature and ZIP integrity pass. A default core-only
audit initially rejected the intended bundled runtime, then the same declared
release-policy audit passed. The app is not notarized or newly published.

At build .8, AIS, per-SWC-segment changes, non-soma recording and BioNet electrical
edges were gated. The first three gates are superseded by .12 acceptance below;
BioNet electrical edges remain gated. The NetPyNE/BioNet paper-outcome campaign
remains blocked at separate decision gates; schedules remain stopped.

## Current build20261002.12 acceptance

The delivered app is `dist/Digifly Workstation.app`; its preserved archive is
`dist/Digifly-Workstation-0.1.0-macOS-arm64-20261002.12.zip`. Two independent
artifact receipts bind this exact build, saved inputs, observed GUI actions,
three completed job logs/plans per fixture, run hashes, source/translation/compiler/
library identities and copied executed workers:
`outputs/workstation/channel-classic-gui-acceptance-20261002-v2/receipt.json` and
`outputs/workstation/channel-regional-gui-acceptance-20261002-v1/receipt.json`.
These are independent fixture checks by the same agent, not independent reviewers.

The original Augustin TABLE1600 interpolation over−120..1000mV is now preserved
in Arbor's equation translation rather than replaced by direct rates. Original
MOD sources remain unchanged. Fresh330 current controls in
`outputs/workstation/channel-current-audit-20261002-v5/audit.json` span6.3/23/35C
and three timesteps. Zero/double-density checks pass; all eleven step errors
decrease at each timestep halving. Worst steady normalized difference is
8.3364e-5 (0.008336%, low-current Para), absolute1.732e-9mA/cm2. NaP steady error
is4.526e-10 relative. Direct-rate and interpolation policies are explicit, and
unrecognized TABLE declarations fail closed.

All native-channel models now use one region/section per incoming source SWC edge,
an odd common CV grid with40um target spacing, and central stimulation/recording
sites. This intentionally replaces divergent legacy endpoint electrodes and
BioNet's extra soma proxy; it is not a hidden voltage/time adjustment. Source IDs
survive normalized SWC reindexing, SONATA reports, CSV and Results legends. Default
then per-neuron then per-edge precedence is frozen in each regional_map.json.
The tiny0.001um Arbor topology-only root stub remains an explicit constructor
difference. No soma voltage is copied to non-soma edges.

The geometric AIS proxy selects the nearest bounded type2 axon edges by soma
distance (ceil5%, at most16); it is not anatomical AIS identification. Roots,
missing/unknown edges, absent axons, unmapped selections and oversized all-CV
reports fail before acceptance, rather than falling back to soma. Total clamp
current is divided over selected edges. BioNet runs real BioNetwork/BioSimulator
from run-owned SONATA configuration, retains its full finite integration-step
report, and selects actual mapped central columns on the requested sample grid.
No installed BMTK files were altered or standalone NEURON substituted for BioNet.

Actual GUI classic fixture:5ms,dt0.0125ms,0.01nA step1–3ms,23C, all eleven
channels, Para0.0016 soma/0.0032 branchS/cm2, HH replacement, Shabq10=1.5 and
soma recording0.05ms. Only engine/name change. Audit:
`outputs/workstation/three-simulator-classic-packaged-audit-20261002-v2/audit.json`.

Actual GUI regional fixture: four source edges2/3/4/5, global soma/AIS scope,
edge3 all-region channel override and Para branch0.003S/cm2. In the real app,
edge3 capacitance was changed1.5→1.6uF/cm2, applied to selected SWC segments,
saved to a new file and reopened; saved value reappeared on that edge. The exact
same saved circuit then completed through each real Run button. Results displayed
four distinct traces/400samples and four source-edge legends. SONATA-native
report shape400×4 and materialized settings pass. Audit:
`outputs/workstation/three-simulator-regional-packaged-audit-20261002-v1/audit.json`.

| Actual .12 GUI control | Arbor max / RMS difference from NEURON (mV) | BioNet maximum (mV) | Threshold crossings |
|---|---:|---:|---:|
| Original one-cell spiking protocol |0.00231766 /0.00067141 |9.85e-13 |1 in each |
| Regional saved override, worst of4edges |0.00231177 /0.00037458 |8.89e-13 |1 per recorded edge in each |

Comparisons use99 shared observed samples per trace. BioNet's real first exported
sample0.05ms is retained; raw high-frequency report peaks differ from coarsely
sampled peaks and are not presented as peak agreement. Interpolation is audit-only
within actual shared support, without extrapolation. Explicit tiny-fixture
engineering checks (<0.01mV Arbor/<1e-6mV BioNet) pass; these are not scientifically
justified outcome-equivalence margins or proof for all neurons/protocols.

Separate actual-worker controls exercise AIS-only stimulation/recording,
selected-edge stimulation/recording, and two unconnected branched multi-CV cells:
`three-simulator-ais-audit-20261002-v1`,
`three-simulator-selected-audit-20261002-v1`, and
`three-simulator-multicv-pair-audit-20261002-v1` under outputs/workstation.
The multi-CV pair preserves8source traces/800samples and reaches0.001696mV
Arbor maximum /3.77e-13mV BioNet maximum; those5ms controls have no spikes,
and are mapping controls, not biological parity evidence.

Failure control `outputs/workstation/three-simulator-regional-audit-failure-control-20261002-v1/verification.json`
doubles a copied Arbor stimulus: same-input acceptance rejects it and creates no
acceptance output. Original runs stay unchanged. Unknown-source/edge, budget,
changed-source hashes and unsupported translations are exercised in regressions.
Package boundary check:3038files/274143533bytes, zero issues under the declared
bundled-Arbor policy. Deep/strict ad-hoc signature and archive integrity pass;
this is not notarization or a new published release. Verified external runtime
choices and normal workspace output root survive actual app restart.

Final post-package focused regression:117 collected cases,116passed/1optional
BioNet-runtime smoke skip (its opt-in environment variable was not set). The
preceding wider pre-package suite passed128/1skip in52.43s. Actual GUI BioNet
and separate worker controls above are real execution evidence, not a relabeling
of the skipped pytest. No failed focused tests remain.
Postcheck rehashed196 bound files per GUI receipt (392checks total), all matching.
Final package/test/runtime restart observations are recorded in
`outputs/workstation/channel-regional-gui-acceptance-20261002-v1/postcheck.json`.

Remaining distinct gates: BioNet electrical contacts, MPI/PointNet and actual
morphology-voltage coloring are not implemented by this repair. The current
playback stays explicitly inferred soma/path flow; real per-edge voltages are
available as separate traces. Larger models, biological validation, anatomical
AIS claims and independent-network outcome replication are not certified.
Other scientific campaign blockers and stopped schedules are unchanged.

## Manual parallel follow-up: electrical execution and measured morphology

The previous paragraph describes build .12, not the current limit. Builds .13
and .14 executed source-bound serial BioNet electrical controls on actual
framework-owned cells; measured edge-center coloring and playback were verified
on full-edge and selected-edge recordings. Build .15 is the checked layout-only
delivery: Results controls fit, runtime chooser fields have useful visible
widths, actual loaded NEURON version is reported, and normal workspace/runtime
choices survive restart. The three bundled scientific workers retain their
executed hashes. The final 65 focused cases and package/signature/ZIP checks pass.

Exact scopes, residual Arbor electrical discrepancies, unsupported combinations,
GUI observations and root rehashes are recorded in
[manual parallel delivery](MANUAL_PARALLEL_DELIVERY_20261002_V1.md) and
`outputs/workstation/manual-parallel-root-verification-20261002-v2.json`.
Electrical SONATA portability, MPI/PointNet and quantitative Arbor electrical
equivalence remain unqualified. No schedules resumed or publication acceptance
was inferred from these small controls.

## October 3: bounded regional electrical agreement and fresh-build delivery

The preceding residual limitation is superseded for the declared regional
two-cell control, not arbitrary circuits. Build20261002.17 passes all three
actual packaged Run buttons with the common source-bound contact-local grid.
Nine production worker controls cover ohmic, rectifying and heterotypic modes:
Arbor maximum errors0.005240/0.005692/0.003872mV and BioNet3.624e-13mV satisfy
the unchanged0.01/1e-6mV engineering gates. Independent review checks579files
and all six actual400x88BioNet reports. The packaged heterotypic audit gives
0.003871978mVArbor/3.6238e-13mVBioNet;221bound files root-rehashed. Measured
edge-voltage playback works and normal roots/runtimes survive restart.

A real .16 fresh Arbor preflight exposed LLVM17static-wheel/LLVM16local-linker
failure, despite passing earlier controls using a verified older catalogue.
The repaired hash-locked standalone CPU mechanism ABI builder freshly compiles
and loads successfully, without linking a second static solver or changing
equations. A newv2cache leaves old failure evidence intact. Exact provenance,
source freeze, negative checks, package/signature/ZIP evidence and caveats are
in [regional electrical delivery](ELECTRICAL_CONTACT_MESH_DELIVERY_20261003_V1.md).
Classic nonregional branch-only electrodes, arbitrary biological circuits,
standard portable electrical SONATA/MPI/PointNet and outcome cohorts remain
unqualified. This is bounded engineering acceptance, not publication replication.

## October 5: actual app replication and expanded controls — delivery in progress

The human requires packaged-app execution, declines further pilots and permits
exclusive GUI testing with a separately saved draft. Builds20261005.1/.3 executed
new source-bound 1%/60,100ms independent BioNet Poisson/DC observations; both are
technically accepted, not statistical or publication acceptance. See the measured
app-pair section in [the scientific ledger](PDCM_REPLICATION.md). Older apps and
accepted runs remain unchanged. Build20261005.4 is an intermediate tester, not a
completed replacement.

Actual .4 GUI controls now execute saved/reopened ramp, global voltage/temperature,
soma/AIS pair placement, runtime ablation, stimulus/channel scaling, spike-only
output and chemical kinetics overrides. Native/BioNet Workers2 runs use two
isolated condition processes with recorded completion/cleanup; Arbor Workers2
means two CPU threads, not isolated trial processes. The source identity repair
keeps original contact hashes separate from enriched mapped-contact identities;
the earlier .3 pre-integration BioNet rejection remains preserved.

| Same-input .4 app control | Measured disposition |
| --- | --- |
| Ramp/soma/global settings/Workers2 | BioNet maximum9.4236e−13mV passes1e−6mV; Arbor0.0671407mV fails unchanged0.01mV. |
| Ablation + Para0.5 + stimulus0.5 | Two cells become one; incident electrical edges and cell2 recordings disappear;12traces/1200finite samples. Built-in HHNa was already replaced/zero, so its0.5 request is not evidence of an active HH effect. Arbor voltage gate still fails. |
| Spike-only + AIS pair | No exported voltage agreement inferred. Geometric pair aggregation preserves2×0.001µS at one mapped AIS midpoint; original contact identities remain distinct from the synthetic execution site. |
| Chemical global override | Actual .8/4ms/+5mV overrides preserve the source database .2/2.5ms/−15mV. BioNet3.6238e−13mV; Arbor0.0340092mV fails unchanged0.01mV. |

Source/output checks and normal exits are not numerical-equivalence acceptance.
Receipts: `outputs/workstation/three-simulator-ramp-soma-workers-independent-discrepancy-review-20261005-v1.json`,
`app-ablation-scales-independent-review-20261005-v1.json`, and the separate
`three-simulator-spike-only-ais-audit-20261005-v1` /
`three-simulator-chemical-override-audit-20261005-v1` directories.

The ramp discrepancy localizes to stimulated cells even with gaps disabled.
Version-bound solver source shows Arbor evaluates the clamp at the step's left
endpoint and NEURON continuous play at its midpoint. A provenance-labelled
quadrature translation is being repaired and independently checked, including
short steps, chemical epochs and exact floating-point cutoff boundaries. Its
first22pure cases passed but an independent boundary oracle found an additional
failure; that source is not yet admitted for delivery. Do not loosen either gate
or shift recorded times. The chemical step discrepancy requires its own cause.

A real UI wheel action also changed numeric fields unintentionally; its invalid
spike-only draft was rejected before creating a run and preserved asv1. Corrected
v2 saved/reopened5ms/24C/−64mV inputs execute. The source scroll-safe editor repair
passes10focused cases, but final packaged verification remains required.
Only the11 hash-locked catalog mechanisms are covered: this does not implement
arbitrary user NMODL import or certify their biological applicability. The final
build must also exercise repaired read-only loading of raw saved app BioNet
simulation folders without rerunning the accepted scientific conditions.

### Actual .5 controls and remaining chemical discrepancy

Separate build20261005.5 now passes the saved/reopened actual GUI ramp/soma/
global/Workers2 control at the unchanged0.01mV Arbor gate: maximum0.00335431mV,
versus .4's0.0671407mV. BioNet9.4236e-13mV passes1e-6mV. Ablation/Para/stimulus
scaling passes0.00270666mV/9.4236e-13mV; spike-only/AIS produces8spikes perengine
and no exported voltage or inferred voltage agreement. Independent reviews bind
source, native/compiler, child cleanup and new run hashes. The actual focused
Duration wheel action leaves all seven simulation settings unchanged; saved/
reopened circuit and experiment match exactly.

Both accepted full1% BioNet observations reopen from their raw simulation folders
through the actual packaged app. New read-only views reproduce eight population
rows, Poisson1,139→1,075→1,139 filter counts, and DC's zero settled activity with
blank undefined CVs and six startup spikes. The native accepted60%DC summary
also opens and explicitly labels the sampled display. No accepted scientific run
was rerun or re-audited. These views do not grant scientific acceptance.

Chemical overrides execute with the correct source/physical settings, but Arbor
still fails0.01mV at0.03461191783217288mV. Raw exports exactly select actual native
rows; receiver-only error after activation and the recorded4.009135ms Arbor versus
4.012500ms BioNet detector events support native detection/delivery conventions
as the leading explanation. Receptor conductance/arrival receipts are absent, so
full causal attribution is not proved. No threshold, delay or output time is tuned.
Changing the native event contract requires a documented method decision.

A prospectively declared matched .00625/.003125ms convergence sequence stopped
at its first actual .00625ms Arbor run: the exact clock guard rejects a single
one-ULP multiply-add representation difference among899observed timestamps.
All7,192 raw values remain preserved; this is not a missing sample. A scoped
source-derived separate/FMA arithmetic qualification is underway, not a widened
time tolerance or chemical event change. Previous voltage failures and failed
GUI output remain unchanged. The .5 app is not yet promoted as a finished update.

Root GUI journal: `outputs/workstation/packaged-app-final-gui-observations-20261005-v1.json`.
Final feature reviews: `app-final-ramp-independent-review-20261005-v1.json`,
`app-final-ablation-scales-independent-review-20261005-v1.json`,
`app-final-spike-only-ais-independent-review-20261005-v1.json`, and
`app-final-saved-bionet-view-independent-review-20261005-v1.json` under
`outputs/workstation/`. Chemical review:
`three-simulator-chemical-native-event-independent-discrepancy-review-20261005-v1.json`.
Prospective plan: `chemical-matched-dt-plan-20261005-v1.json`. None is publication
acceptance or arbitrary user-channel import support.

### Actual .6 refinement and sampling-request failure

Build20261005.6 preserves .5 and carries the independently qualified complete
separate/FMA clock representation plus a109-entry staged-source identity receipt.
Its actual package boundary/signature/archive checks pass. This narrows source
provenance; it does not prove reproducible compiler output or scientific validity.

The same saved/reopened chemical control executes through all three actual Run
buttons at0.00625ms. Arbor error0.01775330338288228mV still fails unchanged0.01mV;
BioNet3.623767952376511e-13mV passes1e-6mV. The physical shared specifications
are identical apart from declared timestep. One refinement approximately halves
the error but does not establish final convergence or a common event contract.

The0.003125ms Arbor test then fails strict timestamp ordering; remaining engines
were not launched. All13,592raw rows/8probes and hash-bound rejection remain.
Exact version-pinned source projection independently reproduces every observed
clock: request ordinal30 lies one ULP before its actual FMA native-step left and
samples step29 again. Step30state was not observed. It cannot be backfilled or
accepted by dropping the duplicate. A prospective observation-only strict-interior
request repair is being qualified; native integration, values, events, delays,
thresholds, complete-clock guards and engineering tolerance remain unchanged.

Both accepted raw BioNet folders also open in actual .6 Results with the same
eight-row tables, Poisson1,139→1,075→1,139 population filter and DC zero settled/
six startup events. New read-only sidecars do not rerun or re-audit the original
scientific observations. The unchanged saved-results suite passes34/34, no skips;
earlier collection/import failures are preserved as environment evidence.

Actual GUI journal: `outputs/workstation/packaged-app-v6-gui-observations-20261005-v1.json`.
Independent stopped-sequence review:
`outputs/workstation/chemical-matched-dt-stopped-independent-review-20261005-v1.json`.
No finished app promotion or chemical/publication acceptance is claimed yet.

### Delivered .7 packaged-app qualification, October5

The prospective strict-interior observation-request repair is now source-reviewed
and delivered at `dist/Digifly Workstation.app`, build20261005.7. It changes the
probe request, not the native left timestamps, integration, voltage values,
thresholds, delays or chemical event contract. A complete separate or exact-FMA
clock must still match; mixed clocks, missing states and extrapolation remain
rejected. The qualified source suite passes246pure cases with8execution cases
deliberately deselected; root and independent focused suites each pass72cases.
These are source checks, separate from the actual GUI measurements below.

The previously declared identical-input chemical control was saved, reopened,
executed once and inspected in actual Results for each of NEURON, Arbor and
BioNet at0.003125ms. All three new runs returned0. The unchanged maximum-voltage
gates pass: Arbor0.009263603916863872mV against0.01mV, and
BioNet3.694822225952521e-13mV against1e-6mV. Comparison uses99common actually
observed timestamps per site, without extrapolating endpoints. Every one of
800canonical Arbor Control values/times selects an actual raw native row;
all13,592raw probe values retain their strict complete-clock identity. The shared
physical inputs, regional eleven-channel settings and applied chemical ON/OFF
parameters match. Root read the independent actual-triplet findings and rehashed
all228bound new artifacts without drift.

This accepts only the declared finer-step engineering condition. The0.0125ms and
0.00625ms numerical failures and prior .6 sampling failure remain failures. Native
detector event times still differ; receptor arrival/conductance receipts are not
available, so general event-contract equivalence or complete causal attribution
is not claimed. No threshold/delay tuning, raw timestamp rewriting, omitted-state
backfill or widened error gate was used. Arbitrary user NMODL import, universal
biological-circuit agreement, portable electrical SONATA/MPI and publication-level
replication remain outside this acceptance.

Both accepted app BioNet scientific observations also reopened read-only in .7
using existing sidecars: BP eight populations and1,139→1,075→1,139 filter counts;
DC zero settled rate with undefined CV blank and six startup spikes in WholeRun.
Actual rasters were inspected. Neither scientific run was rerun or re-audited.

Independent package checks pass source pins,3058inventory entries, arm64,
deep/strict ad-hoc signing, ZIP CRC and payload identity. The previous standard
app20261002.17 and older standard ZIP20261001.1 remain recoverable in
`dist/previous_builds/`; versioned .7 remains. A stale standard archive checksum
was caught at final review, preserved beside the old archive, and replaced with
the actual .7 checksum; verification passes. The exact standard path restarted,
reopened a separate restored original draft, and retained its experiment values
and six MaleCNS0.9 neurons/119,346segments. Normal resource roots and four external
runtime choices persisted. A final BioNet readiness probe first timed out; a
read-only unrestricted import check passed in1.862s, then actual app doctor
completed5/5PASS. No runtime/source change or simulation retry was used. The app
is left open on the restored full-skeleton Circuit Builder. This local delivery
is not notarization, publication, universal crash freedom or scientific acceptance.

Evidence: `outputs/workstation/packaged-app-v7-gui-observations-20261005-v1.json`,
`app-v7-package-independent-review-20261005-v1.json`,
`chemical-strict-interior-actual-triplet-independent-review-20261005-v1.json`,
`arbor-strict-interior-sampling-independent-source-review-20261005-v1.json`, and
`app-v7-delivery-promotion-20261005-v1.json` in the same evidence directory.
Final independent local-delivery receipt:
`outputs/workstation/app-v7-delivery-independent-review-20261005-v1.json`
(SHA256 `0b48c3fa9511562140923050b8fdb7e2477816e2fa34a9f3e6451ddf3e5ff05e`).
Root read its complete findings and rehashed the receipt plus six bound final
metadata/checksum/draft files, all passing. GUI observations remain root-owned;
offline delivery/preservation checks are independent and do not certify science.
