# Early tester guide

Digifly Application is currently a private alpha. A collaborator invited to
this private repository may clone and run it only for private evaluation and
feedback. Do not redistribute the repository, source archives, application
builds, or any Digifly-owned material.

## Privacy and data boundary

Do not put connectome datasets, SWC collections, simulation runs, local resource
profiles, or provider credentials in the checkout. Never paste a neuPrint token,
credential file, full environment dump, or unredacted home-directory path into
an issue. Screenshots and logs must be checked for personal paths, dataset
details, and tokens before upload.

The base smoke test needs no simulator and no scientific data. NEURON, Arbor,
BMTK, and external morphology/connectome sources are optional follow-up tests
and remain installed or stored elsewhere on the tester's machine.

## Install and automated smoke

Use Git plus Python 3.11 or 3.12. On macOS or Linux:

```bash
git clone https://github.com/Saint-Sam/Digifly-Application.git
cd Digifly-Application
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
QT_QPA_PLATFORM=offscreen python -m pytest \
  tests/test_package_foundation.py tests/test_ui_smoke.py
```

Python 3.11 may be substituted for 3.12. On Windows, create the environment with
`py -3.12 -m venv .venv` and replace `python` above with
`.venv\Scripts\python`.

The two smoke modules verify the package boundary and construct the interface
without importing a simulator or reading machine-local settings.

## Manual interface smoke

Start the source checkout:

```bash
./scripts/run_dev.sh
```

Then perform this bounded check:

1. Confirm the Digifly Workstation window opens without a fatal dialog.
2. Open Workspace, Data Library, Circuit Builder, Experiment Builder, Results,
   and Engines once each.
3. Toggle light and dark themes and confirm the interface remains readable.
4. Return to Workspace and confirm the output location is outside the cloned
   repository. Do not create or select a data folder inside the checkout.
5. Close and relaunch the app once.

The absence of a local Digifly workspace or simulator may leave scientific
features unavailable; that is not a failure of this base smoke. Only test a
runtime or dataset after its owner has separately authorized access. Use the
Workspace controls to select it, and keep the selected path outside the repo.

## Reporting a problem

Use the repository's **Early tester bug report** issue form. Include the commit,
operating system and architecture, Python version, affected work area, exact
steps, and expected versus observed behavior. A short redacted traceback is
helpful. Do not upload datasets or credentials; describe the resource by type
and approximate size instead.

The `dist/` application is a maintainer-generated artifact and is intentionally
absent from Git. Testers should launch from source unless a maintainer sends a
specific build and instructions.
