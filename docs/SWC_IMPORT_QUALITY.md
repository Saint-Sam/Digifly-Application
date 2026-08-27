# Post-import SWC quality and radius healing

## Recovered DNp01 history

The earlier MANC DNp01 correction was recovered from
`Digifly Public/Phase 1/manc_v1.2.1/archived_swcs/`
`radius_match_10002_to_10000/20260429T163545Z/`.

That operation was a manual, reference-based radius match. Eight highlighted
compartments in body 10002 were changed from 0.032–0.149 µm to the 0.332 µm
radius of the currently selected body-10000 reference compartments. The
archived verification records exactly eight radius changes and zero changes to
node count, types, coordinates, parents, or topology. A pre-change SWC was
archived before the active file was updated.

This must not be confused with the separate Escape-SIZ partner-thinning
experiment under `Phase 2_Arbor_staging`. That experiment intentionally reduced
five different radii and is not an import-quality repair.

The historical operation is useful calibration evidence, but it is not a
general detector: the eight compartments were selected by the user and matched
to a homolog. A fresh neuPrint SWC can reindex those same coordinates, and an
unrelated neuron need not have the same node IDs, absolute size, or shape.

## Adaptive rule

The Workstation checker therefore does not hard-code DNp01 node IDs, coordinate
anchors, or a universal micrometre cutoff. It:

1. Determines the source unit from provider provenance, with a conservative
   coordinate-extent fallback for unlabelled SWCs.
2. Validates numeric fields, positive radii, IDs, parents, roots, and cycles.
3. Builds the graph described by each individual SWC.
4. Estimates a local radius reference for every compartment from its four-hop
   topology neighbourhood.
5. Scores relative drops as a log ratio and flags only the most extreme two
   percent of candidates *within that SWC*.
6. Proposes a conservative replacement from the lower quartile of larger local
   neighbours, rather than copying a fixed radius from another neuron.

This makes the same algorithm useful across connectomes while respecting each
neuron's own scale and radius diversity. A flag remains a review request, not a
claim that biological taper is wrong. The user must explicitly choose whether
to heal it.

## Mutation and provenance contract

- **Save healed copy** is the default. It creates a sibling named
  `*_radius_healed.swc`; Circuit Builder prefers that reviewed copy while the
  provider's original acquisition bytes remain untouched.
- **Overwrite original** retains the original filename but first writes the
  source bytes into a sibling `.digifly-backups` directory.
- Both modes reparse the result and prove that only radii changed. A
  `*.swc.quality.json` sidecar records the input/output hashes, findings,
  changed node IDs, backup, and verification.
- **Keep original** records the review decision without changing the SWC.
- An SWC with structural errors cannot use automatic radius healing.

The recent-import scanner is bounded by manifest file inventories and import
timestamps. It does not recursively inspect or copy large legacy connectome
trees. The same core is available through Circuit Builder's **Check recent
imports** button and the `digifly-swc-quality scan|review` command.
