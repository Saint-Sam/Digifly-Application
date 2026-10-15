"""Non-destructive quantitative audit of reconstructed PDCM Figures 5–7.

The numeric reference is the authors' arXiv v1 supplementary Tables S1–S6,
not digitized main-figure bars. This audit reports differences but never
declares scientific acceptance from an arbitrary numerical tolerance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


POPULATIONS = ("L2e", "L2i", "L4e", "L4i", "L5e", "L5i", "L6e", "L6i")
DRIVES = ("balanced_poisson", "balanced_dc")
PAPER_URL = "https://pmc.ncbi.nlm.nih.gov/articles/PMC8382011/"
PREPRINT_URL = "https://arxiv.org/pdf/2005.03764"
SUPPLEMENT_URL = "https://pmc.ncbi.nlm.nih.gov/articles/instance/8382011/bin/NIHMS1700194-supplement-1.pdf"

# Transcribed from the authors' arXiv v1 PDF, pages 22–24 (PDF indices 21–23).
# The PDF text extraction duplicates the L6e rate into L6i in S1/S2. Exclude
# those rate cells until the final journal supplement can be checked visually.
# Array order is POPULATIONS. Synchrony is the 60 s population-total Fano table,
# not the 5 s main-figure panel or the paper's printed per-cell-normalized form.
REFERENCE = {
    5: {
        "balanced_poisson": {
            "rate_hz_60s": (0.69, 3.06, 4.71, 5.98, 8.34, 8.45, 1.18, None),
            "cv_isi_60s": (0.929, 0.913, 0.887, 0.873, 0.822, 0.807, 0.931, 0.815),
            "fano_3ms_60s": (1.9, 1.7, 2.1, 1.4, 5.9, 1.1, 1.3, 0.9),
        },
        "balanced_dc": {
            "rate_hz_60s": (0.66, 2.93, 4.53, 5.71, 8.46, 8.21, 1.05, None),
            "cv_isi_60s": (0.929, 0.906, 0.867, 0.857, 0.784, 0.777, 0.921, 0.777),
            "fano_3ms_60s": (2.5, 2.5, 2.8, 2.0, 7.8, 1.3, 1.5, 1.1),
        },
    },
    6: {
        "balanced_poisson": {
            "rate_hz_60s": (0.75, 3.28, 4.76, 6.20, 6.55, 8.97, 1.10, None),
            "cv_isi_60s": (0.937, 0.919, 0.883, 0.869, 0.853, 0.800, 0.938, 0.810),
            "fano_3ms_60s": (2.6, 2.1, 3.1, 1.9, 4.5, 1.1, 1.7, 1.0),
        },
        "balanced_dc": {
            "rate_hz_60s": (0.87, 3.06, 4.25, 5.46, 6.62, 8.17, 0.80, None),
            "cv_isi_60s": (0.937, 0.896, 0.866, 0.855, 0.817, 0.766, 0.920, 0.771),
            "fano_3ms_60s": (10.7, 10.3, 15.6, 8.5, 11.4, 2.4, 3.3, 2.3),
        },
    },
}
TABLE = {"rate_hz_60s": ("S1", "S2"), "cv_isi_60s": ("S3", "S4"),
         "fano_3ms_60s": ("S5", "S6")}
CSV_FIELDS = ("figure", "drive", "population", "metric", "digifly_value",
              "reference_value", "delta", "absolute_percent_difference",
              "sampled_cells", "reference_table", "interpretation")


def _regular(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular audit input: {path}")
    return path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with _regular(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_rows(path: Path, field: str) -> dict[tuple[str, str], dict[str, str]]:
    with _regular(path).open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or field not in reader.fieldnames:
            raise ValueError(f"Missing {field} in {path}")
        rows = list(reader)
    indexed = {(row.get("drive", ""), row["population"]): row for row in rows}
    if len(indexed) != len(rows):
        raise ValueError(f"Duplicate drive/population row in {path}")
    return indexed


def _finite(row: dict[str, str], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid {field}: {row[field]}")
    return value


def _load_figure(directory: Path, number: int) -> tuple[dict, dict, dict[str, str]]:
    root = directory.resolve(strict=True)
    manifest_path = _regular(root / "figure_manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scale = 0.1 if number == 6 else 0.3
    if manifest.get("paper_figure") != number or not math.isclose(float(manifest["scale"]), scale):
        raise ValueError(f"Wrong PDCM figure or scale at {root}")
    csv_path = _regular(root / f"figure{number}_source_data.csv")
    if manifest.get("source_data_sha256", {}).get(csv_path.name) != _sha(csv_path):
        raise ValueError(f"Figure {number} source-data hash mismatch")
    rows = _read_rows(csv_path, "population")
    expected = {(drive, pop) for drive in DRIVES for pop in POPULATIONS}
    with csv_path.open(newline="", encoding="utf-8") as stream:
        count = sum(1 for _ in csv.DictReader(stream))
    if set(rows) != expected or count != len(expected):
        raise ValueError(f"Figure {number} must have one row per drive/population")
    for drive in DRIVES:
        subset = [rows[drive, pop] for pop in POPULATIONS]
        sampled = sum(int(row["statistic_cells"]) for row in subset)
        raster = sum(int(row["raster_cells"]) for row in subset)
        if (sampled, raster) != ({5: (7203, 1862), 6: (5015, 1862),
                                  7: (23147, 23147)}[number]):
            raise ValueError(f"Figure {number} has unexpected sampling totals")
        if number == 7 and any(row["statistic_cells"] != row["raster_cells"] for row in subset):
            raise ValueError("Figure 7 must include all cells in both panels and statistics")
        for row in subset:
            for field in ("mean_rate_hz_60s", "mean_cv_isi_60s", "population_total_fano_3ms_5s"):
                _finite(row, field)
    hashes = {str(manifest_path): _sha(manifest_path), str(csv_path): _sha(csv_path)}
    return manifest, rows, hashes


def _sidecar_fano(manifest: dict, rows: dict, hashes: dict[str, str]) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for drive in DRIVES:
        source = manifest["source_runs"][drive]
        path = Path(source["analysis"]) / "synchrony_metric_comparison.csv"
        digest = _sha(path)
        if source["analysis_sha256"].get(path.name) != digest:
            raise ValueError(f"Figure sidecar hash mismatch: {path}")
        hashes[str(path)] = digest
        entries = _read_rows(path, "population")
        if set(entries) != {("", pop) for pop in POPULATIONS}:
            raise ValueError(f"Sidecar must contain all eight populations: {path}")
        for pop in POPULATIONS:
            row = entries["", pop]
            figure_row = rows[drive, pop]
            if int(row["sampled_cells"]) != int(figure_row["statistic_cells"]):
                raise ValueError(f"Sidecar sample differs for {drive}/{pop}")
            five = _finite(row, "population_total_fano_synchrony_3ms_5s")
            if not math.isclose(five, _finite(figure_row, "population_total_fano_3ms_5s"),
                                rel_tol=1e-7, abs_tol=1e-8):
                raise ValueError(f"Sidecar 5 s Fano differs for {drive}/{pop}")
            values[drive, pop] = _finite(row, "population_total_fano_synchrony_3ms_60s")
    return values


def _assert_same_runs(first: dict, second: dict) -> None:
    for drive in DRIVES:
        a, b = first["source_runs"][drive], second["source_runs"][drive]
        if a["run"] != b["run"] or a["source_sha256"] != b["source_sha256"]:
            raise ValueError("Figure 5 and 7 must derive from the same source runs")


def _reference_doc() -> dict:
    return {
        "authors": "Romaro et al.", "preprint_url": PREPRINT_URL,
        "journal_article_url": PAPER_URL, "journal_supplement_url": SUPPLEMENT_URL,
        "extracted_from": "arXiv:2005.03764v1 PDF pp. 22–24, Tables S1–S6",
        "population_order": POPULATIONS,
        "excluded_cells": "S1/S2 L6i rates: available PDF text repeats L6e; final supplement not visually verified",
        "table_values": REFERENCE,
        "caveat": "Author-preprint table values are context, not journal-supplement verification or exact panel acceptance.",
    }


def _write_csv(path: Path, fields: tuple[str, ...], records: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def assess(figure5: Path, figure6: Path, figure7: Path, output: Path) -> Path:
    """Validate inputs and write a new versioned report without touching source runs."""
    if output.exists():
        raise FileExistsError(output)
    source_hashes: dict[str, str] = {}
    loaded = {}
    for number, directory in ((5, figure5), (6, figure6), (7, figure7)):
        manifest, rows, hashes = _load_figure(directory, number)
        loaded[number] = (manifest, rows)
        source_hashes.update(hashes)
    _assert_same_runs(loaded[5][0], loaded[7][0])
    fano60 = {number: _sidecar_fano(loaded[number][0], loaded[number][1], source_hashes)
              for number in (5, 6)}

    comparisons: list[dict] = []
    summaries: list[dict] = []
    for number in (5, 6):
        rows = loaded[number][1]
        for drive_index, drive in enumerate(DRIVES):
            for metric, field in (("rate_hz_60s", "mean_rate_hz_60s"),
                                  ("cv_isi_60s", "mean_cv_isi_60s"),
                                  ("fano_3ms_60s", None),
                                  ("fano_3ms_5s", "population_total_fano_3ms_5s")):
                group = []
                for index, pop in enumerate(POPULATIONS):
                    reference = None if metric == "fano_3ms_5s" else REFERENCE[number][drive][metric][index]
                    measured = fano60[number][drive, pop] if field is None else _finite(rows[drive, pop], field)
                    delta = None if reference is None else measured - reference
                    pct = None if reference is None else 100.0 * abs(delta) / reference
                    interpretation = ("no_reliable_reference_cell" if reference is None and metric != "fano_3ms_5s"
                                      else "no_numeric_5s_paper_panel" if reference is None
                                      else "window_matched_supplement_only" if metric == "fano_3ms_60s"
                                      else "window_matched_sampling_unverified")
                    record = {
                        "figure": number, "drive": drive, "population": pop, "metric": metric,
                        "digifly_value": measured, "reference_value": reference,
                        "delta": delta, "absolute_percent_difference": pct,
                        "sampled_cells": int(rows[drive, pop]["statistic_cells"]),
                        "reference_table": "" if reference is None else TABLE[metric][drive_index],
                        "interpretation": interpretation,
                    }
                    comparisons.append(record)
                    if pct is not None:
                        group.append(record)
                if group:
                    worst = max(group, key=lambda row: row["absolute_percent_difference"])
                    summaries.append({"figure": number, "drive": drive, "metric": metric,
                                      "numeric_cells": len(group), "max_absolute_percent_difference":
                                      worst["absolute_percent_difference"],
                                      "worst_population": worst["population"]})

    internal: list[dict] = []
    for drive in DRIVES:
        for pop in POPULATIONS:
            first, all_cells = loaded[5][1][drive, pop], loaded[7][1][drive, pop]
            for metric, field in (("rate_hz_60s", "mean_rate_hz_60s"),
                                  ("cv_isi_60s", "mean_cv_isi_60s"),
                                  ("fano_3ms_5s", "population_total_fano_3ms_5s")):
                sample_value, all_value = _finite(first, field), _finite(all_cells, field)
                internal.append({"drive": drive, "population": pop, "metric": metric,
                                 "figure5_sample_value": sample_value,
                                 "figure7_all_cells_value": all_value,
                                 "all_to_sample_ratio": None if sample_value == 0 else all_value / sample_value,
                                 "figure5_sample_cells": int(first["statistic_cells"]),
                                 "figure7_all_cells": int(all_cells["statistic_cells"]),
                                 "interpretation": "same_run_sampling_effect_only"})

    summary = {
        "schema_version": 1, "workflow": "pdcm_figures_5_7_quantitative_audit_v1",
        "overall_status": "not_scientifically_accepted",
        "compared_figures": [5, 6, 7], "reference": _reference_doc(),
        "comparison_rows": len(comparisons), "figure7_internal_rows": len(internal),
        "maxima": summaries,
        "limitations": [
            "Author-preprint supplementary values were transcribed; final journal supplement was not independently verified.",
            "Authors' exact sampled cell IDs and random streams are unavailable; Figure 5/6 samples differ or are unverified.",
            "At 10% only 7,713 cells exist, so the stated 8,000-cell sample cannot consist of unique cells.",
            "Figure 5/6 synchrony panels use 5 s; supplementary S5/S6 use 60 s and cannot validate those panels.",
            "Published Figure 7 has no numeric all-cell benchmark; Figure 5-versus-7 is an internal same-run comparison only.",
            "L6i rates in S1/S2 excluded because the available preprint PDF text extraction repeats L6e values.",
            "Population-total Fano matches the reported table scale, but conflicts with the paper's printed normalization equation.",
            "Single Digifly seed 3 is not a multi-seed uncertainty estimate; no tolerance or acceptance threshold is imposed.",
        ],
    }

    output.mkdir(parents=True, exist_ok=False)
    comparison_path = output / "comparison.csv"
    _write_csv(comparison_path, CSV_FIELDS, comparisons)
    internal_path = output / "figure7_sampling_effect.csv"
    _write_csv(internal_path,
               ("drive", "population", "metric", "figure5_sample_value", "figure7_all_cells_value",
                "all_to_sample_ratio", "figure5_sample_cells", "figure7_all_cells", "interpretation"), internal)
    reference_path = output / "reference.json"
    reference_path.write_text(json.dumps(_reference_doc(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = [
        "# PDCM Figures 5–7 quantitative audit", "",
        "_Non-destructive, single-seed comparison of Digifly reconstructions to the authors' supplementary tables._", "", "---", "",
        "## 📋 Outcome", "",
        "**Not scientifically accepted.** Figures 5 and 6 have window-matched rate and CV context, but the authors' sample IDs are unavailable. "
        "The 5 s synchrony panels lack published numeric targets; the 60 s supplement is a different analysis window. "
        "Figure 7 has no published all-cell numeric table.[^1][^2]", "",
        "## 🔍 Method", "",
        "The audit verifies every input CSV and sidecar against its recorded SHA-256 before comparison. It uses the authors' arXiv v1 supplementary Tables S1–S6 "
        "as transcribed in `reference.json`; final journal-supplement values remain unverified.[^2]", "",
        "```mermaid", "flowchart LR", "    accTitle: PDCM Figure Audit Inputs", "    accDescr: Verified Digifly figure data and authors' supplementary table values feed a comparison report that cannot by itself establish paper replication.",
        "    figures[📊 Verify figure data] --> compare[🔍 Compare numeric values]", "    sidecars[💾 Verify analysis sidecars] --> compare", "    reference[📚 Review author tables] --> compare", "    compare --> report[📝 Mark unresolved acceptance]", "```", "",
        "| Figure | Drive | Metric | Numeric cells | Largest absolute difference | Population |", "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for item in summaries:
        report.append(f"| {item['figure']} | {item['drive'].replace('balanced_', '')} | {item['metric']} | "
                      f"{item['numeric_cells']} | {item['max_absolute_percent_difference']:.2f}% | {item['worst_population']} |")
    report += ["", "The table reports absolute percentage differences from rounded author-preprint numbers, **not** pass/fail thresholds. "
               "The S5/S6 rows use separate 60 s Digifly sidecars; they are not Figure 5/6 synchrony-panel comparisons.[^2]", "",
               "## 📊 Figure 7 sampling effect", "",
               "Figure 5 and Figure 7 share the exact source runs, but Figure 5 statistics use 7,203 distinct cells while Figure 7 uses all 23,147. "
               "`figure7_sampling_effect.csv` records each population's within-run all-to-sample ratio; these are not comparisons to published numbers.[^1]", "",
               "## ⚠️ Limitations", ""]
    report.extend(f"- {item}" for item in summary["limitations"])
    report += ["", "## 🔗 References", "",
               f"[^1]: Romaro et al. (2021). \"NetPyNE implementation and scaling of the Potjans-Diesmann cortical microcircuit model.\" _Neural Computation_. {PAPER_URL}",
               f"[^2]: Romaro et al. (2020). \"NetPyNE implementation and rescaling of the Potjans-Diesmann cortical microcircuit model,\" arXiv:2005.03764v1, pp. 22–24, Tables S1–S6. {PREPRINT_URL}", ""]
    report_path = output / "report.md"
    report_path.write_text("\n".join(report), encoding="utf-8")
    for path, digest in source_hashes.items():
        if _sha(Path(path)) != digest:
            raise ValueError(f"Source changed while generating audit: {path}")
    artifacts = (comparison_path, internal_path, reference_path, summary_path, report_path)
    manifest = {"schema_version": 1, "workflow": summary["workflow"],
                "audit_script_sha256": _sha(Path(__file__)),
                "source_sha256": source_hashes,
                "output_sha256": {path.name: _sha(path) for path in artifacts}}
    (output / "audit_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--figure5", type=Path, required=True)
    parser.add_argument("--figure6", type=Path, required=True)
    parser.add_argument("--figure7", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(assess(args.figure5, args.figure6, args.figure7, args.output))


if __name__ == "__main__":
    main()
