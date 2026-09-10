# Digifly NEURON gap mechanisms

These are app-owned, byte-exact copies of `Gap.mod`, `RectGap.mod`, and
`HeteroRectGap.mod` from `Digifly Public/Phase 2/data`. Their SHA-256 hashes are
frozen in `source_manifest.json`; the workstation never compiles in or writes
to the Digifly Public source tree.

Compiled NEURON libraries are runtime-, platform-, and architecture-specific.
Keep them outside the application repository and build them with the packaged
helper:

```bash
python scripts/build_neuron_gap_mechanisms.py \
  --python /path/to/neuron/python \
  --output-dir /path/to/external/cache
```

Pass `--nrnivmodl /path/to/nrnivmodl` to select the compiler explicitly. The
helper sanitizes inherited Python, Conda, dynamic-loader, and Qt variables,
stages the verified sources under `OUTPUT_DIR/sources`, compiles from that
external directory, and validates the resulting library with the selected
NEURON Python. A successful cache contains `mechanism_manifest.json`; all paths
in that runtime contract are relative to `OUTPUT_DIR`.
