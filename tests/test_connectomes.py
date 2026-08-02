from __future__ import annotations

import json
from pathlib import Path

from digifly_app.core.circuit import ConnectomeRef
from digifly_app.core.connectomes import ConnectomeCatalog, discover_connectomes


def _write_swc(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1 1 0 0 0 1 -1\n2 2 1 0 0 0.5 1\n", encoding="utf-8")


def test_discovery_catalog_and_local_queries(tmp_path):
    swc_root = tmp_path / "Digifly Public" / "Phase 1" / "manc_v1.2.1" / "export_swc"
    _write_swc(swc_root / "DN" / "DNp01" / "10000" / "10000_healed.swc")
    preferred = swc_root / "DN" / "DNp01" / "10000" / "10000_axodendro_with_synapses.swc"
    _write_swc(preferred)
    _write_swc(swc_root / "IN" / "GFC2" / "14662" / "14662_axodendro_with_synapses.swc")

    sources = discover_connectomes(tmp_path / "Digifly Public")
    assert [source.label for source in sources] == ["MANC v1.2.1"]
    assert sources[0].key == "manc:v1.2.1"
    catalog = ConnectomeCatalog.scan(sources[0])
    assert catalog.by_id["10000"].swc_path == str(preferred.resolve())
    assert [item.neuron_id for item in catalog.query("class:DN").records] == ["10000"]
    assert [item.neuron_id for item in catalog.query("type:GFC2").records] == ["14662"]
    assert [item.neuron_id for item in catalog.query("10000 14662").records] == ["10000", "14662"]
    assert catalog.query("missing").unmatched == ("missing",)


def test_generic_custom_library_is_scanned_recursively(tmp_path):
    root = tmp_path / "library"
    path = root / "bundle" / "IN" / "GFC2" / "14662" / "14662_axodendro_with_synapses.swc"
    _write_swc(path)
    source = ConnectomeRef("custom", "Custom", str(root))
    record = ConnectomeCatalog.scan(source).by_id["14662"]
    assert record.family == "IN"
    assert record.neuron_type == "GFC2"


def test_custom_versions_are_separate_sources_instead_of_collapsing_by_id(tmp_path):
    public = tmp_path / "Digifly Public"
    library = tmp_path / "library"
    for name in ("GFC2-14662-first", "GFC2-14662-second"):
        bundle = library / name
        _write_swc(bundle / "IN" / "GFC2" / "14662" / "14662_healed.swc")
        (bundle / "digifly-biophysics.json").write_text(
            json.dumps({"schema_version": 1, "neuron_id": "14662"}), encoding="utf-8"
        )
    sources = discover_connectomes(public, morphology_library_root=library)
    custom = [source for source in sources if source.dataset == "custom"]
    assert len(custom) == 2
    assert len({source.key for source in custom}) == 2
    assert all(ConnectomeCatalog.scan(source).by_id["14662"] for source in custom)


def test_structured_catalog_includes_symlinked_overlay_swc(tmp_path):
    root = tmp_path / "export_swc"
    target = tmp_path / "native" / "10000_healed.swc"
    _write_swc(target)
    overlay = root / "DN" / "DNp01" / "10000" / "10000_healed.swc"
    overlay.parent.mkdir(parents=True)
    overlay.symlink_to(target)
    source = ConnectomeRef("overlay", "Overlay", str(root))
    assert ConnectomeCatalog.scan(source).by_id["10000"].swc_path == str(overlay)
