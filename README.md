# Digifly Workstation

<p align="center">
  <img src="src/digifly_app/assets/digifly_icon.png" width="180" alt="Digifly paired giant-fiber mark">
</p>

Digifly Workstation is a desktop app for building Drosophila neural circuits from
connectome data and running electrophysiology experiments. Download neurons from
neuPrint, edit their membrane properties and connections, then inspect voltage
propagation, spike events and recorded currents.

[Download 0.2.2 for macOS or Windows](https://github.com/Saint-Sam/Digifly-Application/releases/tag/v0.2.2)

## Install

- **macOS:** Apple silicon, macOS 14 or later. Extract the `macOS-arm64` ZIP
  and open Digifly Workstation. Current builds are ad-hoc signed; Developer ID
  signing and notarization are pending.
- **Windows:** Extract the `Windows-x86_64` ZIP and open
  `Digifly Workstation.exe`. Keep the extracted files together. An offline
  NEURON installation is included and installed with your approval.

The app includes its interface runtime. On Mac, select an existing NEURON
installation or use the runtime setup in **Workspace**.

## First experiment

Open **First experiment** in the app for a guided Male CNS v1.0 experiment with
DNp01, TTMn, PSI and DLMn. The guide covers neuron downloads, NEURON setup, SWC
repairs and the first run.

The main pages follow the same workflow:

1. **Data Library:** Choose a connectome and release. Download SWCs and
   connectivity from neuPrint with your account token, or link a local SWC
   folder. Review any suggested morphology repairs before saving a repaired copy.
2. **Circuit Builder:** Select neurons and inspect their skeletons. Set membrane
   properties, channel densities and connection parameters for the circuit,
   individual neurons or selected compartments.
3. **Experiment Builder:** Choose the simulator, stimulus, conditions and
   recording settings. Check the draft, resolve any readiness errors, then run.
4. **Results:** Open the saved experiment to inspect traces and replay.
   Select **Morphology voltage** for measured voltage colors. Choose
   **All compartments** when setting up the recording to capture propagation
   along the full skeleton; soma-only recordings color the recorded soma edges.

## Data and saved experiments

SWCs, simulator installations and run data stay outside the application.
Each connectome release has a shared SWC library organized by family, type and
neuron ID. Existing neurons are kept unless you approve replacement. Local
library shortcuts remember folders stored elsewhere on the device.

Connectivity is stored by connectome and release. The included Windows MANC
connectivity Parquet is explicitly assigned to MANC v1.2.1. SWCs and contact
locations are acquired separately. Saved runs retain their circuit, experiment
settings and recordings.

## Current support

This is alpha software. The [bug and fix log](docs/BUG_FIX_LOG.md) records known
issues and repairs; the [student-readiness review](docs/STUDENT_READINESS_MAC_20261028.md)
tracks controls still awaiting tests.

Windows package checks on Server 2022 cover NEURON installation, a small
experiment, saved Results and updater recovery. Windows 11 testing is still
pending.

Arbor and BMTK BioNet support selected channel and connection models. The app
checks settings before Run; BioNet electrical connections currently use serial
execution. The [channel execution record](docs/CHANNEL_EXECUTION_REPAIR.md)
documents supported settings and simulator comparisons. The older 49-cell
Escape-SIZ comparison has an unresolved voltage mismatch.

## Coming soon

Planned additions:

- Download and install custom ion channels from Channelpedia and ModelDB in Circuit Builder.
- Leaky integrate-and-fire models, with point-neuron and compartment-based options.
- Additional simulators, including Brian2.

## Development

With Python 3.11 or later installed, run:

```bash
./scripts/setup_dev.sh
./scripts/run_dev.sh
```

See [Contributing](CONTRIBUTING.md) for tests,
[Architecture](docs/ARCHITECTURE.md) for the code structure and
[Packaging](docs/PACKAGING_BOUNDARY.md) for distribution requirements.

## License

Digifly's original code, documentation and interface assets use the
[MIT License](LICENSE). Third-party software, models and datasets retain their
own licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).
