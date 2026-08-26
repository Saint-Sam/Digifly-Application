from __future__ import annotations

import json

from digifly_app.core.results import load_escape_siz_result


def test_result_loader_validates_contract_and_artifacts(tmp_path):
    image = tmp_path / "figure.png"
    pdf = tmp_path / "figure.pdf"
    image.write_bytes(b"not-a-real-image-but-present")
    pdf.write_bytes(b"%PDF-test")
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "complete",
                "completed_at": "2026-07-30T15:11:50",
                "contact_count_policy": "deduplicated_visible_contact_sites_post_xyz",
                "visible_contact_counts": [{"pre_id": 10000, "post_id": 10110, "count": 146}],
                "run_summaries": [
                    {"ordering_basis": "saved_gf_camera_vertical_then_tree_distance"}
                ],
                "plots": {"png": str(image), "pdf": str(pdf), "heatmap_neuron_ids": [10002, 10000]},
                "stim_target_maps": {"gap_enabled": {"13127": 0.9}},
            }
        ),
        encoding="utf-8",
    )
    result = load_escape_siz_result(summary)
    assert result.status == "complete"
    assert result.primary_image == image
    assert all(check.state.value == "pass" for check in result.checks)


def test_missing_ordering_is_a_warning(tmp_path):
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "complete",
                "contact_count_policy": "deduplicated_visible_contact_sites_post_xyz",
                "visible_contact_counts": [{}],
            }
        ),
        encoding="utf-8",
    )
    result = load_escape_siz_result(summary)
    ordering = next(check for check in result.checks if check.key == "result_ordering")
    assert ordering.state.value == "warning"


def test_ablation_notebook_composite_is_the_primary_result_image(tmp_path):
    native_image = tmp_path / "native.png"
    notebook_image = tmp_path / "notebook_3d.png"
    native_pdf = tmp_path / "native.pdf"
    notebook_pdf = tmp_path / "notebook_3d.pdf"
    for path in (native_image, notebook_image):
        path.write_bytes(b"image")
    for path in (native_pdf, notebook_pdf):
        path.write_bytes(b"%PDF")
    summary = tmp_path / "summary.json"
    summary.write_text(
        json.dumps(
            {
                "status": "complete",
                "contact_count_policy": "deduplicated_visible_contact_sites_post_xyz",
                "visible_contact_counts": [{}],
                "plots": {"png": str(native_image), "pdf": str(native_pdf)},
                "notebook_plot_bundle": {
                    "png": str(notebook_image),
                    "pdf": str(notebook_pdf),
                },
            }
        ),
        encoding="utf-8",
    )
    result = load_escape_siz_result(summary)
    assert result.primary_image == notebook_image
    assert result.metadata["notebook plot"] == "heatmaps + postsynaptic 3D"
