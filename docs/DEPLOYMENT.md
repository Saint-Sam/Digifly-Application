# Deployment

Digifly Workstation uses Qt for Python's supported `pyside6-deploy` path. The checked-in
`pysidedeploy.spec` keeps the simulator worker and architecture guide inside the
bundle while leaving NEURON, Arbor, BMTK, and VND as external runtime profiles.

## Local macOS build

From the repository root:

```bash
./scripts/setup_dev.sh
./scripts/build_macos.sh
```

The build appears at `dist/Digifly Workstation.app`. It is ad-hoc signed so its modified
bundle metadata remains internally consistent. This is suitable for local
testing only. Public releases require an Apple Developer ID, hardened runtime,
notarization, and release checksums.

Generated Nuitka and Qt deployment directories are ignored by Git.
