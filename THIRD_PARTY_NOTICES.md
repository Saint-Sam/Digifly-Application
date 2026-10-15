# Licensing scope and third-party materials

Digifly's original code, documentation and original interface assets are
licensed under the repository's [MIT License](LICENSE). This covers the
independently authored replication implementation, not the original authors'
software, figures or data. The MIT transition was authorized on October5,2026.

Third-party software, mechanisms, models, datasets, trademarks and other
materials retain their respective terms. A provenance manifest, local copy or
successful simulation does not grant redistribution rights. Do not relabel
third-party code or data as Digifly-authored MIT material.

The bundled Augustin2019 `nat`, `nap` and `k` mechanisms are adaptations of
the code identified as Extended Data1 of Augustin, Zylbertal and Partridge,
eNeuro6(2), DOI10.1523/ENEURO.0423-18.2019. The original article is CC BY4.0;
its Code accessibility section links ModelDB245415 and that code supplement.
Retain the authors' copyright, attribution and change notice in
`mechanisms/augustin_2019/NOTICE.md`. These materials are not relicensed as
exclusively MIT. The October15 paper/full-text inspection supersedes the
earlier incomplete review that looked only for a LICENSE in the model mirror.

Before a public release, record exact versions, original licenses and required
notices for every bundled dependency and mechanism source. In particular, the
Qt/PySide interface runtime, optional bundled Arbor runtime, Augustin mechanism
sources and any externally derived NEURON mechanism sources require their own
distribution review. This notice is a scope statement, not a completed license
inventory or certification that a native bundle is ready to redistribute.

User-managed NEURON/BMTK/NetPyNE runtimes, connectomes, SWCs, downloaded resources
and provider credentials remain outside the core application artifact. Publish
replication results as a separate, explicitly inventoried research artifact;
do not bypass the application's data/credential exclusion checks to bundle
them into the app. Third-party reference material must be cited and may be
distributed only under its own terms.

The user-requested Windows NEURON edition additionally includes an offline
private runtime under `installers/neuron/`, not a user's existing environment.
Its unmodified official NEURON9.0.2 installer is checksum-pinned. The private
payload retains NEURON Copyright/incorporated notices, CPython3.12.10 LICENSE,
and all included NumPy/Matplotlib/DuckDB wheel license/copyright records.
`DIGIFLY_NEURON_RUNTIME.json` and `UPSTREAM-SOURCES.json` identify the exact
upstream files, versions and hashes. Those third-party terms remain in force;
the bundle does not relicense NEURON, its toolchain or dependencies as MIT.
Private setup extracts these files after consent, not the upstream installer's
registry/file-association/PATH actions. No other simulator or user data follows
from this narrowly scoped packaging exception.

Existing built applications are preserved unchanged. Updating the source
license does not replace the license bytes or attest the notices in an older
app bundle. New release candidates must pass the current exact-license and
artifact checks before publication.
