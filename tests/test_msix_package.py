"""The Store manifest must stay valid and match the installed-mode layout."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from scripts.msix_package import ASSETS, msix_version, render_manifest, stage

FOUNDATION = "{http://schemas.microsoft.com/appx/manifest/foundation/windows10}"
IDENTITY = {
    "identity_name": "Quazmoz.InferBridge",
    "publisher": "CN=00000000-0000-0000-0000-000000000000",
    "publisher_display_name": 'Quazmoz & "Co"',
}


def test_semantic_versions_map_to_store_versions_with_zero_revision():
    assert msix_version("1.2.3") == "1.2.3.0"
    assert msix_version("1.2.3-beta.4+build") == "1.2.3.0"
    with pytest.raises(ValueError):
        msix_version("1.2.70000")


def test_manifest_is_well_formed_and_escapes_partner_center_values():
    root = ET.fromstring(render_manifest(version="1.0.0", **IDENTITY))
    identity = root.find(f"{FOUNDATION}Identity")
    assert identity.get("Name") == "Quazmoz.InferBridge"
    assert identity.get("Version") == "1.0.0.0"
    assert root.find(f"{FOUNDATION}Properties/{FOUNDATION}PublisherDisplayName").text == (
        'Quazmoz & "Co"'
    )
    application = root.find(f"{FOUNDATION}Applications/{FOUNDATION}Application")
    assert application.get("Executable") == "InferBridge.exe"


def test_manifest_rejects_values_partner_center_would_not_issue():
    with pytest.raises(ValueError):
        render_manifest(version="1.0.0", **{**IDENTITY, "publisher": "Quazmoz"})
    with pytest.raises(ValueError):
        render_manifest(version="1.0.0", **{**IDENTITY, "identity_name": "a b"})


def test_stage_maps_every_built_file_and_refuses_portable_layouts(tmp_path):
    dist = tmp_path / "dist"
    (dist / "_internal").mkdir(parents=True)
    (dist / "InferBridge.exe").write_bytes(b"MZ")
    (dist / "_internal" / "python311.dll").write_bytes(b"MZ")

    mapping = stage(distribution=dist, output_dir=tmp_path / "msix", version="1.0.0", **IDENTITY)

    text = mapping.read_text(encoding="utf-8")
    assert '"InferBridge.exe"' in text and '"_internal\\python311.dll"' in text
    assert all(f'"Assets\\{name}"' in text for name in ASSETS)

    (dist / "portable.flag").write_text("portable")
    with pytest.raises(RuntimeError, match="installed-mode"):
        stage(distribution=dist, output_dir=tmp_path / "msix2", version="1.0.0", **IDENTITY)
