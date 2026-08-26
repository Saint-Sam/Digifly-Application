from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Iterable, TYPE_CHECKING

from .circuit import ConnectomeRef

if TYPE_CHECKING:
    from .workspace import DigiflyWorkspace


NEURON_FAMILIES = ("AN", "DN", "IN", "MN", "SN")


@dataclass(frozen=True)
class NeuronRecord:
    neuron_id: str
    family: str
    neuron_type: str
    swc_path: str
    connectome_key: str


@dataclass(frozen=True)
class QueryResult:
    records: tuple[NeuronRecord, ...]
    unmatched: tuple[str, ...] = ()
    truncated: bool = False


@dataclass(frozen=True)
class ConnectionClassSummary:
    """Read-only evidence for one connection class between a neuron pair.

    ``available`` means that a supported edge source was opened successfully,
    so a zero count is a real zero *within that loaded source*.  ``False``
    means no supported source was available (or it could not be read), and must
    never be presented as evidence that the connectome has no connections.
    """

    available: bool
    a_to_b_count: int = 0
    b_to_a_count: int = 0
    source_path: str = ""
    source_label: str = ""
    scope_note: str = ""
    unavailable_reason: str = ""
    connection_kind: str = "connection"

    @property
    def total_count(self) -> int:
        return self.a_to_b_count + self.b_to_a_count

    @property
    def has_connections(self) -> bool:
        return self.available and self.total_count > 0

    @property
    def known_zero(self) -> bool:
        """Whether the loaded source explicitly supports a scoped zero."""

        return self.available and self.total_count == 0

    @property
    def status(self) -> str:
        if not self.available:
            return "unavailable"
        return "present" if self.total_count else "known_zero"

    @property
    def source_text(self) -> str:
        """Short human-readable source label suitable for the pair panel."""

        return self.source_label or self.source_path or "No loaded source"

    @property
    def status_text(self) -> str:
        """Human-readable result that never conflates unknown with zero."""

        if not self.available:
            return self.unavailable_reason or "Connection data are unavailable."
        if self.connection_kind == "gap junction":
            noun = "gap-junction contact" if self.total_count == 1 else "gap-junction contacts"
            return f"{self.total_count} {noun} in the loaded scoped bundle."
        if self.total_count == 0:
            return "No chemical contacts in the loaded local cache (scoped zero)."
        return (
            f"Chemical contacts: {self.a_to_b_count} A→B and "
            f"{self.b_to_a_count} B→A in the loaded local cache."
        )


@dataclass(frozen=True)
class PairConnectivitySummary:
    neuron_a: str
    neuron_b: str
    chemical: ConnectionClassSummary
    gap_junction: ConnectionClassSummary


_MANC_KEY = "manc:v1.2.1"
_MANC_DATASET = "manc_v1.2.1"
_MANC_RELATIVE_ROOT = Path("Phase 1") / _MANC_DATASET / "export_swc"
_ARBOR_GAP_RELATIVE_ROOT = (
    Path("Phase 2_Arbor_staging")
    / "Projects"
    / "Escape-SIZ"
    / "arbor_inputs"
    / "giant_fiber_ablation"
)
_ARBOR_GAP_FILENAME = "gap_contacts_arbor.csv"


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    # ``mode=ro`` blocks writes and ``immutable=1`` prevents SQLite from
    # creating journal/WAL sidecars beside native Digifly Public inputs.
    return sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
    )


def _sqlite_tables(path: Path) -> set[str]:
    with _readonly_sqlite(path) as connection:
        return {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }


def _sqlite_pair_counts(
    path: Path,
    table: str,
    neuron_a: str,
    neuron_b: str,
) -> tuple[int, int]:
    # Table names are selected exclusively from our fixed allow-list or the
    # literal native ``edges`` table. Neuron IDs remain bound parameters.
    with _readonly_sqlite(path) as connection:
        columns = {
            str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if not {"pre_id", "post_id"}.issubset(columns):
            raise ValueError(f"{table} lacks pre_id/post_id columns")
        rows = connection.execute(
            f"SELECT pre_id, post_id, COUNT(*) FROM {table} "
            "WHERE (pre_id=? AND post_id=?) OR (pre_id=? AND post_id=?) "
            "GROUP BY pre_id, post_id",
            (neuron_a, neuron_b, neuron_b, neuron_a),
        )
        counts = {(str(pre), str(post)): int(count) for pre, post, count in rows}
    return counts.get((neuron_a, neuron_b), 0), counts.get((neuron_b, neuron_a), 0)


def _csv_pair_counts(
    path: Path,
    neuron_a: str,
    neuron_b: str,
) -> tuple[int, int, int]:
    forward = reverse = 0
    row_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        fields = set(reader.fieldnames or ())
        if not {"pre_id", "post_id"}.issubset(fields):
            raise ValueError("gap edge CSV lacks pre_id/post_id columns")
        for row in reader:
            row_count += 1
            pre, post = str(row.get("pre_id", "")), str(row.get("post_id", ""))
            if pre == neuron_a and post == neuron_b:
                forward += 1
            elif pre == neuron_b and post == neuron_a:
                reverse += 1
    return forward, reverse, row_count


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_manc_v121(source: ConnectomeRef) -> bool:
    return source.key.casefold() == _MANC_KEY or source.dataset.casefold() == _MANC_DATASET


def _derive_public_root(source_root: Path) -> Path | None:
    """Derive Digifly Public only from the exact canonical MANC path tail."""

    tail = _MANC_RELATIVE_ROOT.parts
    if len(source_root.parts) < len(tail) or source_root.parts[-len(tail) :] != tail:
        return None
    return source_root.parents[len(tail) - 1]


def _gap_manifest_record(manifest_path: Path) -> tuple[set[str], str, int]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError("manifest has no records list")
    matches = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("kind") == "gap_contacts"
        and record.get("path") == _ARBOR_GAP_FILENAME
    ]
    if len(matches) != 1:
        raise ValueError("manifest does not identify one validated Arbor gap table")
    record = matches[0]
    ids = record.get("selected_neuron_ids")
    expected_hash = str(record.get("sha256") or "")
    expected_rows = record.get("row_count")
    if not isinstance(ids, list) or not ids:
        raise ValueError("gap manifest has no selected-neuron scope")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("gap manifest has no valid SHA-256")
    if not isinstance(expected_rows, int) or expected_rows < 0:
        raise ValueError("gap manifest has no valid row count")
    return {str(value) for value in ids}, expected_hash, expected_rows


class ConnectomeEdgeCatalog:
    """Inspect supported local edge sources without writing to them.

    Chemical contacts use Digifly's native MANC v1.2.1
    ``edges/master_edges_cache.sqlite``. Electrical contacts use only the
    manifest-validated Escape-SIZ Arbor input bundle. We intentionally do not
    scan arbitrary project caches or the multi-gigabyte Male CNS dataset.
    """

    def __init__(
        self,
        source: ConnectomeRef,
        workspace: DigiflyWorkspace | None = None,
    ):
        self.source = source
        self.root = Path(source.root).expanduser().resolve()
        explicit_root = Path(workspace.root).expanduser().resolve() if workspace else None
        self.public_root = explicit_root or _derive_public_root(self.root)
        self._canonical_manc = bool(
            _is_manc_v121(source)
            and self.public_root is not None
            and self.root == (self.public_root / _MANC_RELATIVE_ROOT).resolve()
        )
        self.chemical_db = self.root / "edges" / "master_edges_cache.sqlite"
        self.arbor_gap_root = (
            self.public_root / _ARBOR_GAP_RELATIVE_ROOT if self.public_root else None
        )

    def _chemical_summary(self, neuron_a: str, neuron_b: str) -> ConnectionClassSummary:
        if not self._canonical_manc:
            return ConnectionClassSummary(
                available=False,
                connection_kind="chemical",
                unavailable_reason=(
                    "Chemical connection counts are unavailable for this connectome. "
                    "Only the indexed local MANC v1.2.1 cache is supported."
                ),
            )
        path = self.chemical_db
        if not path.is_file():
            return ConnectionClassSummary(
                available=False,
                source_path=str(path),
                source_label="MANC v1.2.1 local chemical edge cache",
                connection_kind="chemical",
                unavailable_reason="The indexed local MANC chemical edge cache is not loaded.",
            )
        try:
            if "edges" not in _sqlite_tables(path):
                raise ValueError("native edge cache has no edges table")
            forward, reverse = _sqlite_pair_counts(path, "edges", neuron_a, neuron_b)
        except (OSError, sqlite3.Error, ValueError) as exc:
            return ConnectionClassSummary(
                available=False,
                source_path=str(path),
                source_label="MANC v1.2.1 local chemical edge cache",
                connection_kind="chemical",
                unavailable_reason=f"Chemical edge cache could not be read: {exc}",
            )
        return ConnectionClassSummary(
            available=True,
            a_to_b_count=forward,
            b_to_a_count=reverse,
            source_path=str(path),
            source_label="MANC v1.2.1 indexed local chemical edge cache",
            scope_note=(
                "Selected MANC v1.2.1 local cache only. A zero means no rows in "
                "this loaded cache; it is not a claim about an unloaded remote connectome."
            ),
            connection_kind="chemical",
        )

    def _gap_summary(self, neuron_a: str, neuron_b: str) -> ConnectionClassSummary:
        if not self._canonical_manc or self.arbor_gap_root is None:
            return ConnectionClassSummary(
                available=False,
                connection_kind="gap junction",
                unavailable_reason=(
                    "Gap-junction counts are unavailable for this connectome; no "
                    "validated pair source is loaded. This is unknown, not zero."
                ),
            )

        path = self.arbor_gap_root / _ARBOR_GAP_FILENAME
        manifest = self.arbor_gap_root / "manifest.json"
        source_label = "Validated Escape-SIZ Arbor gap-contact bundle"
        if not path.is_file() or not manifest.is_file():
            return ConnectionClassSummary(
                available=False,
                source_path=str(path),
                source_label=source_label,
                connection_kind="gap junction",
                unavailable_reason=(
                    "The validated Escape-SIZ gap-contact bundle is not loaded. "
                    "Gap status is unknown, not zero."
                ),
            )

        try:
            selected_ids, expected_hash, expected_rows = _gap_manifest_record(manifest)
            if neuron_a not in selected_ids or neuron_b not in selected_ids:
                return ConnectionClassSummary(
                    available=False,
                    source_path=str(path),
                    source_label=source_label,
                    scope_note=(
                        f"Escape-SIZ active-case bundle ({len(selected_ids)} selected neurons)."
                    ),
                    connection_kind="gap junction",
                    unavailable_reason=(
                        "One or both neurons are outside the validated Escape-SIZ "
                        f"{len(selected_ids)}-neuron bundle. Gap status is unknown, not zero."
                    ),
                )
            actual_hash = _sha256(path)
            if actual_hash != expected_hash:
                raise ValueError("gap table SHA-256 does not match its manifest")
            forward, reverse, actual_rows = _csv_pair_counts(path, neuron_a, neuron_b)
            if actual_rows != expected_rows:
                raise ValueError(
                    f"gap table has {actual_rows} rows; manifest declares {expected_rows}"
                )
        except (OSError, csv.Error, json.JSONDecodeError, ValueError) as exc:
            return ConnectionClassSummary(
                available=False,
                source_path=str(path),
                source_label=source_label,
                connection_kind="gap junction",
                unavailable_reason=f"Validated gap-contact bundle could not be trusted: {exc}",
            )

        return ConnectionClassSummary(
            available=True,
            a_to_b_count=forward,
            b_to_a_count=reverse,
            source_path=str(path),
            source_label=source_label,
            scope_note=(
                f"Escape-SIZ Giant Fiber Ablation active-case snapshot only "
                f"({len(selected_ids)} selected neurons). A zero is valid only within this bundle."
            ),
            connection_kind="gap junction",
        )

    def pair_summary(self, neuron_a: str | int, neuron_b: str | int) -> PairConnectivitySummary:
        first, second = str(neuron_a).strip(), str(neuron_b).strip()
        if not first or not second:
            raise ValueError("Pair connectivity requires two neuron IDs")
        if first == second:
            raise ValueError("Pair connectivity requires two different neuron IDs")
        return PairConnectivitySummary(
            first,
            second,
            self._chemical_summary(first, second),
            self._gap_summary(first, second),
        )


def pair_connection_summary(
    workspace: DigiflyWorkspace,
    source: ConnectomeRef,
    neuron_a: str | int,
    neuron_b: str | int,
) -> PairConnectivitySummary:
    """Convenience API for UI callers with the app's resolved workspace."""

    return ConnectomeEdgeCatalog(source, workspace).pair_summary(neuron_a, neuron_b)


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return text or "connectome"


def _label_for_dataset(name: str) -> str:
    labels = {
        "manc_v1.2.1": "MANC v1.2.1",
        "male-cns_v0.9": "Male CNS v0.9",
        "fafb": "FAFB",
        "banc": "BANC",
    }
    return labels.get(name, name.replace("_", " "))


def _key_for_dataset(name: str) -> str:
    canonical = {
        "manc_v1.2.1": "manc:v1.2.1",
        "male-cns_v0.9": "male-cns:v0.9",
    }
    return canonical.get(name, _slug(name))


def discover_connectomes(
    digifly_public_root: str | Path,
    *,
    morphology_library_root: str | Path | None = None,
) -> tuple[ConnectomeRef, ...]:
    """Discover local SWC sources without importing Phase 1 or accessing neuPrint."""
    public_root = Path(digifly_public_root).expanduser()
    sources: list[ConnectomeRef] = []
    seen: set[str] = set()

    phase1 = public_root / "Phase 1"
    if phase1.is_dir():
        for dataset_dir in sorted(path for path in phase1.iterdir() if path.is_dir()):
            swc_root = dataset_dir / "export_swc"
            if not swc_root.is_dir():
                continue
            key = _key_for_dataset(dataset_dir.name)
            sources.append(
                ConnectomeRef(
                    key=key,
                    label=_label_for_dataset(dataset_dir.name),
                    root=str(swc_root.resolve()),
                    dataset=dataset_dir.name,
                )
            )
            seen.add(str(swc_root.resolve()))

    phase2_swc = public_root / "Phase 2" / "data" / "export_swc"
    if phase2_swc.is_dir() and str(phase2_swc.resolve()) not in seen:
        sources.append(
            ConnectomeRef(
                key="phase2-local-swc",
                label="Phase 2 local SWCs",
                root=str(phase2_swc.resolve()),
                dataset="phase2-local",
            )
        )
        seen.add(str(phase2_swc.resolve()))

    if morphology_library_root is not None:
        library = Path(morphology_library_root).expanduser()
        if library.is_dir() and str(library.resolve()) not in seen:
            bundles = [
                child
                for child in sorted(library.iterdir())
                if child.is_dir()
                and (child / "digifly-biophysics.json").is_file()
                and any(child.rglob("*.swc"))
            ]
            if bundles:
                for bundle in bundles:
                    sources.append(
                        ConnectomeRef(
                            key=f"custom:{_slug(bundle.name)}",
                            label=f"Custom · {bundle.name}",
                            root=str(bundle.resolve()),
                            dataset="custom",
                        )
                    )
            elif any(library.rglob("*.swc")):
                sources.append(
                    ConnectomeRef(
                        key="custom:library",
                        label="Digifly custom morphology library",
                        root=str(library.resolve()),
                        dataset="custom",
                    )
                )

    sources.sort(key=lambda item: (0 if item.key.startswith("manc:") else 1, item.label.casefold()))
    return tuple(sources)


def _candidate_rank(path: Path, neuron_id: str) -> tuple[int, int, str]:
    name = path.name.casefold()
    if name == f"{neuron_id}_axodendro_with_synapses.swc".casefold():
        rank = 0
    elif "axodendro_with_synapses" in name and "old" not in name:
        rank = 1
    elif "healed_final" in name:
        rank = 2
    elif "healed" in name:
        rank = 3
    elif name == f"{neuron_id}.swc".casefold():
        rank = 4
    else:
        rank = 5
    return rank, len(str(path)), str(path)


def _infer_id(path: Path) -> str | None:
    for part in reversed(path.parts[:-1]):
        if part.isdigit():
            return part
    match = re.match(r"(\d+)(?:_|\.|$)", path.name)
    return match.group(1) if match else None


def _infer_labels(path: Path, root: Path, neuron_id: str) -> tuple[str, str]:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    id_index = next((index for index, part in enumerate(parts) if part == neuron_id), -1)
    neuron_type = "Unknown"
    family = "UNKNOWN"
    if id_index > 0:
        neuron_type = parts[id_index - 1]
    if id_index > 1 and parts[id_index - 2].upper() in NEURON_FAMILIES:
        family = parts[id_index - 2].upper()
    if family == "UNKNOWN":
        upper = neuron_type.upper()
        family = next((prefix for prefix in NEURON_FAMILIES if upper.startswith(prefix)), "UNKNOWN")
    return family, neuron_type


def _structured_swc_paths(root: Path) -> Iterable[Path]:
    # Native exports use family/type/id/file. Scanning that shape with cached
    # DirEntry metadata is much faster than a generic pathlib walk for Male
    # CNS, while nonstandard siblings still receive a recursive fallback.
    with os.scandir(root) as top_entries:
        for top in top_entries:
            if top.name.startswith("."):
                continue
            if top.is_file(follow_symlinks=True):
                if top.name.casefold().endswith(".swc"):
                    yield Path(top.path)
                continue
            if not top.is_dir(follow_symlinks=True):
                continue
            if top.name.upper() not in {*NEURON_FAMILIES, "UNKNOWN", "UNCLASSIFIED"}:
                yield from Path(top.path).rglob("*.swc")
                continue
            with os.scandir(top.path) as type_entries:
                for neuron_type in type_entries:
                    if neuron_type.name.startswith(".") or not neuron_type.is_dir(follow_symlinks=True):
                        continue
                    with os.scandir(neuron_type.path) as id_entries:
                        for neuron_dir in id_entries:
                            if neuron_dir.name.startswith(".") or not neuron_dir.is_dir(follow_symlinks=True):
                                continue
                            if not neuron_dir.name.isdigit():
                                yield from Path(neuron_dir.path).rglob("*.swc")
                                continue
                            with os.scandir(neuron_dir.path) as files:
                                for file in files:
                                    if file.is_file(follow_symlinks=True) and file.name.casefold().endswith(".swc"):
                                        yield Path(file.path)


class ConnectomeCatalog:
    def __init__(self, source: ConnectomeRef, records: Iterable[NeuronRecord]):
        self.source = source
        self.records = tuple(sorted(records, key=lambda item: (item.family, item.neuron_type, item.neuron_id)))
        self.by_id = {record.neuron_id: record for record in self.records}

    @classmethod
    def scan(cls, source: ConnectomeRef) -> "ConnectomeCatalog":
        root = Path(source.root).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Connectome SWC root does not exist: {root}")
        chosen: dict[str, Path] = {}
        for path in _structured_swc_paths(root):
            if any(part.startswith(".") for part in path.parts):
                continue
            neuron_id = _infer_id(path)
            if neuron_id is None:
                continue
            previous = chosen.get(neuron_id)
            if previous is None or _candidate_rank(path, neuron_id) < _candidate_rank(previous, neuron_id):
                chosen[neuron_id] = path
        records = []
        for neuron_id, path in chosen.items():
            family, neuron_type = _infer_labels(path, root, neuron_id)
            records.append(
                NeuronRecord(
                    neuron_id=neuron_id,
                    family=family,
                    neuron_type=neuron_type,
                    # ``root`` is already absolute, so every rglob result is
                    # absolute. Resolving tens of thousands of files here made
                    # the Male CNS catalog spend seconds on redundant syscalls.
                    swc_path=str(path),
                    connectome_key=source.key,
                )
            )
        return cls(source, records)

    def query(self, expression: str, *, limit: int = 64) -> QueryResult:
        limit = max(1, int(limit))
        raw_parts = [part.strip() for part in re.split(r"[,;\n]+", expression) if part.strip()]
        tokens: list[str] = []
        for part in raw_parts:
            if re.fullmatch(r"\d+(?:\s+\d+)+", part):
                tokens.extend(part.split())
            else:
                tokens.append(part)
        if not tokens:
            return QueryResult((), ("No neuron query was provided.",), False)

        selected: dict[str, NeuronRecord] = {}
        unmatched: list[str] = []
        for token in tokens:
            token_folded = token.casefold().strip()
            matches: list[NeuronRecord] = []
            if token_folded.isdigit():
                record = self.by_id.get(token_folded)
                matches = [record] if record is not None else []
            else:
                key = ""
                value = token_folded
                if ":" in token_folded:
                    key, value = (part.strip() for part in token_folded.split(":", 1))
                elif token_folded.startswith("all "):
                    key, value = "class", token_folded[4:].strip()

                if key in {"id", "body", "bodyid", "rootid"}:
                    record = self.by_id.get(value)
                    matches = [record] if record is not None else []
                elif key in {"class", "family", "all"}:
                    matches = [record for record in self.records if record.family.casefold() == value]
                elif key in {"type", "celltype"}:
                    matches = [record for record in self.records if record.neuron_type.casefold() == value]
                elif token_folded.upper() in NEURON_FAMILIES:
                    matches = [record for record in self.records if record.family == token_folded.upper()]
                else:
                    matches = [
                        record
                        for record in self.records
                        if record.neuron_type.casefold() == value
                        or record.neuron_type.casefold().startswith(value.rstrip("*"))
                    ]
            if not matches:
                unmatched.append(token)
                continue
            for record in matches:
                selected.setdefault(record.neuron_id, record)

        ordered = sorted(selected.values(), key=lambda item: (item.family, item.neuron_type, item.neuron_id))
        truncated = len(ordered) > limit
        return QueryResult(tuple(ordered[:limit]), tuple(unmatched), truncated)
