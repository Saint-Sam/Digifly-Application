from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Iterable

from .circuit import ConnectomeRef


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
