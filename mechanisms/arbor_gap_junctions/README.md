# Digifly Arbor gap-junction catalogue

This directory contains app-owned Arbor NMODL ports of the three gap-junction
mechanisms in `Digifly Public/Phase 2/data`. The public project remains an
input/reference tree: the app does not edit it, load its NEURON binaries, or
pretend that a NEURON mechanism library can be shared with Arbor.

| NEURON source | Arbor mechanism | Preserved behavior |
| --- | --- | --- |
| `Gap.mod` | `gap` | Ohmic current |
| `RectGap.mod` | `rect_gap` | Hard directional rectification |
| `HeteroRectGap.mod` | `hetero_rect_gap` | Oriented logistic gate, empirical residual floor, and asymmetric open/close gate dynamics |

The source hashes are frozen in `source_manifest.json`. They make source drift
visible without coupling this standalone repository to a particular absolute
path on one machine.

## Units and connection semantics

Arbor junction current is in nA and junction conductance is in uS, so each
current equation is written as `i = g*(v - v_peer)`. The original NEURON
mechanisms expose conductance in nS and therefore multiply by `0.001` in their
current equation. Convert a NEURON conductance before passing it to an Arbor
mechanism:

```text
g_Arbor_uS = 0.001 * g_NEURON_nS
```

Arbor also applies the dimensionless weight on each
`gap_junction_connection`. Use a connection weight of `1` when the mechanism
parameter contains the complete endpoint conductance. As in Arbor generally,
each direction is represented by an endpoint mechanism and an incoming gap
connection; the two endpoint parameterizations may differ for a heterotypic
junction.

`hetero_rect_gap` uses Arbor's supported `cnexp` integration for the same state
ODE that the NEURON source integrates with `derivimplicit`:

```text
d gate_state / dt = (gate_inf - gate_state) / tau_gate
tau_gate = tau_open_ms  when gate_inf > gate_state
           tau_close_ms otherwise
```

This preserves the model equation, residual floor, and open/close constants.
The numerical update method is backend-specific, so cross-backend validation
still needs finite-step tolerance; these ports alone are not a parity claim.

## Local catalogue build

Compiled catalogues are machine/runtime-specific build artifacts and must stay
outside the repository. The build helper enforces that boundary:

```bash
python scripts/build_arbor_gap_catalogue.py --keep-generated
```

Load a successful build with `arbor.load_catalogue(...)` and extend the model's
global cable-cell catalogue under an explicit prefix such as `digifly_gap`.
Do not commit the generated `.so`, generated C++, or CMake build directory.

### Validated macOS build

On 2026-08-02, all three sources passed Arbor 0.12.2 `modcc`, generated CPU
code and metadata successfully, and passed direct generated-kernel behavior
checks. The installed Arbor wheel initially exposed an Apple LLVM 17/16 bitcode
link mismatch. Rebuilding the same Arbor 0.12.2 revision with the active local
compiler resolved that ABI/toolchain mismatch without changing the mechanism
equations. The resulting catalogue loaded in the configured Arbor runtime,
matched the expected mechanism schema, and passed real two-cell simulations for
`gap`, `rect_gap`, and `hetero_rect_gap`. `build_status.json` records the build
revision, compiler, catalogue hash, and checks. The first full-network audit
completed with these mechanism checks passing, but the required gap-disabled
stimulus-source baseline gate failed 0/11. Full-network agreement with NEURON
therefore remains unclaimed while the upstream CV-layout and clamp-placement
compatibility work proceeds.
