"""Read-only facts from the hash-locked PDCM author source.

The source is parsed as syntax, never imported or executed in the UI process.
This preview is not a substitute for NetPyNE's generated network or run output.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .pdcm_replication import validate_model_source


@dataclass(frozen=True)
class PDCMSourceOverview:
    populations: tuple[str, ...]
    full_counts: tuple[int, ...]
    connection_probabilities: tuple[tuple[float, ...], ...]

    def scaled_counts(self, scale: float) -> tuple[int, ...]:
        if not 0.01 <= scale <= 1.0:
            raise ValueError("PDCM scale must be between 1% and 100%")
        return tuple(int(count * scale) for count in self.full_counts)


def _literal_assignment(tree: ast.Module, label: str) -> object:
    for statement in tree.body:
        if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
            continue
        target = statement.targets[0]
        if not isinstance(target, ast.Name) or target.id != label:
            continue
        value = statement.value
        if isinstance(value, ast.Call):
            function = value.func
            if (
                not isinstance(function, ast.Attribute)
                or not isinstance(function.value, ast.Name)
                or function.value.id != "np"
                or function.attr != "array"
                or len(value.args) != 1
                or value.keywords
            ):
                raise ValueError(f"Pinned source {label} has an unexpected expression")
            value = value.args[0]
        try:
            return ast.literal_eval(value)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError) as exc:
            raise ValueError(f"Pinned source {label} is not a literal") from exc
    raise ValueError(f"Pinned source is missing {label}")


def read_pinned_pdcm_overview(model_dir: str | Path) -> PDCMSourceOverview:
    """Verify exact author files and extract the eight cortical populations.

    The ninth ``N_Full`` entry is the source's thalamic count, not a cortical
    population. E/I effect is encoded by the original rule's presynaptic index.
    """

    root = Path(model_dir).expanduser().resolve()
    validate_model_source(root)
    tree = ast.parse((root / "netParams.py").read_text(encoding="utf-8"))
    populations = _literal_assignment(tree, "L")
    all_counts = _literal_assignment(tree, "N_Full")
    probabilities = _literal_assignment(tree, "C")
    if (
        not isinstance(populations, list)
        or len(populations) != 8
        or any(not isinstance(label, str) for label in populations)
        or not isinstance(all_counts, list)
        or len(all_counts) < 8
        or any(type(count) is not int or count <= 0 for count in all_counts[:8])
        or not isinstance(probabilities, list)
        or len(probabilities) != 8
        or any(not isinstance(row, list) or len(row) != 8 for row in probabilities)
        or any(
            type(value) not in (int, float) or not 0 <= value <= 1
            for row in probabilities for value in row
        )
    ):
        raise ValueError("Pinned PDCM population or connectivity constants are malformed")
    return PDCMSourceOverview(
        tuple(populations),
        tuple(all_counts[:8]),
        tuple(tuple(float(value) for value in row) for row in probabilities),
    )
