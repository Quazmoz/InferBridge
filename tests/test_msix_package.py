"""The Store manifest must stay valid and launch the native client beside the engine."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from app.paths import resolve_runtime_paths
from scripts.msix_package import ASSETS, msix_version, native_entries, render_manifest, stage

FOUNDATION = "{http://schemas.microsoft.com/appx/manifest/foundation/windows10}"
DESKTOP = "{http://schemas.microsoft.com/appx/manifest/desktop/windows10}"
IDENTITY = {
    "identity_name": "Quazmoz.InferBridge",
    "publisher": "CN=00000000-0000-0000-0000-000000000000",
    "publisher_display_name": 'Quazmoz & "Co"',
}
NATIVE_RUNTIME = (
    "InferBridge.Native.exe",
    "InferBridge.Native.deps.json",
    "InferBridge.Native.runtimeconfig.json",
    "coreclr.dll",
)


def _python_dist(root):
    (root / "_internal").mkdir(parents=True)
    (root / "InferBridge.exe").write_bytes(b"MZ")
    (root / "_internal" / "python311.dll").write_bytes(b"MZ")
    return root


def _native_publish(root):
    (root / "runtimes" / "win-x64").mkdir(parents=True)
    for filename in NATIVE_RUNTIME:
        (root / filename).write_bytes(b"MZ")
    (root / "runtimes" / "win-x64" / "runtime.dll").write_bytes(b"MZ")
    return root


def _stage(tmp_path, output="msix", **overrides):
    arguments = {
        "distribution": tmp_path / "dist",
        "native_publish": tmp_path / "native-publish",
        "output_dir": tmp_path / output,
        "version": "1.0.0",
        **IDENTITY,
    }
    return stage(**{**arguments, **overrides})


def test_semantic_versions_map_to_store_versions_with_zero_revision():
    assert msix_version("1.2.3") == "1.2.3.0"
    assert msix_version("1.2.3-beta.4+build") == "1.2.3.0"
    with pytest.raises(ValueError):
        msix_version("1.2.70000")


def test_manifest_launches_native_client_on_activation_and_user_enabled_startup():
    root = ET.fromstring(render_manifest(version="1.0.0", **IDENTITY))
    identity = root.find(f"{FOUNDATION}Identity")
    assert identity.get("Name") == "Quazmoz.InferBridge"
    assert identity.get("Version") == "1.0.0.0"
    assert root.find(f"{FOUNDATION}Properties/{FOUNDATION}PublisherDisplayName").text == (
        'Quazmoz & "Co"'
    )
    application = root.find(f"{FOUNDATION}Applications/{FOUNDATION}Application")
    # The Store rejected a browser-shell primary UI; the package opens the WinUI client.
    assert application.get("Executable") == r"native\InferBridge.Native.exe"
    startup = application.find(f"{FOUNDATION}Extensions/{DESKTOP}Extension")
    assert startup.get("Executable") == application.get("Executable")
    capabilities = root.find(f"{FOUNDATION}Capabilities")
    assert [item.get("Name") for item in capabilities] == ["runFullTrust"]
    text = ET.tostring(root, encoding="unicode")
    assert "WriteVirtualization" not in text and "unvirtualizedResources" not in text


def test_manifest_rejects_values_partner_center_would_not_issue():
    with pytest.raises(ValueError):
        render_manifest(version="1.0.0", **{**IDENTITY, "publisher": "Quazmoz"})
    with pytest.raises(ValueError):
        render_manifest(version="1.0.0", **{**IDENTITY, "identity_name": "a b"})


def test_stage_maps_engine_native_client_and_assets(tmp_path):
    _python_dist(tmp_path / "dist")
    _native_publish(tmp_path / "native-publish")

    mapping = _stage(tmp_path)

    text = mapping.read_text(encoding="utf-8")
    assert '"InferBridge.exe"' in text and '"_internal\\python311.dll"' in text
    assert '"native\\InferBridge.Native.exe"' in text
    assert '"native\\runtimes\\win-x64\\runtime.dll"' in text
    assert all(f'"Assets\\{name}"' in text for name in ASSETS)

    (tmp_path / "dist" / "portable.flag").write_text("portable")
    with pytest.raises(RuntimeError, match="installed-mode"):
        _stage(tmp_path, output="msix2")


def test_staging_reuses_native_client_already_in_release_distribution(tmp_path):
    dist = _python_dist(tmp_path / "dist")
    native = _native_publish(dist / "native")
    mapping = _stage(tmp_path, native_publish=native)

    lines = mapping.read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if '"native\\InferBridge.Native.exe"' in line]) == 1
    assert len([line for line in lines if '"native\\coreclr.dll"' in line]) == 1
    assert any('"_internal\\python311.dll"' in line for line in lines)

    # A different published client must never be swapped into the same package.
    separate = _native_publish(tmp_path / "native-publish")
    with pytest.raises(RuntimeError, match="different native/ payload"):
        _stage(tmp_path, output="conflict", native_publish=separate)


def test_store_data_root_preserves_downloads_without_appdata_virtualization(tmp_path, monkeypatch):
    monkeypatch.setattr("app.paths.is_packaged", lambda: True)
    profile = tmp_path / "profile"
    env = {
        "USERPROFILE": str(profile),
        "LOCALAPPDATA": str(profile / "AppData" / "Local"),
    }
    paths = resolve_runtime_paths(portable=False, desktop=True, env=env)
    assert paths.data_root == profile / "InferBridgeStoreData"
    assert paths.models_dir == profile / "InferBridgeStoreData" / "models"
    env["OV_LLM_DATA_DIR"] = str(tmp_path / "opted-in-root")
    assert resolve_runtime_paths(portable=False, desktop=True, env=env).data_root == (
        tmp_path / "opted-in-root"
    )


def test_stage_rejects_incomplete_native_or_engine_payload_without_partial_output(tmp_path):
    dist = _python_dist(tmp_path / "dist")
    native = tmp_path / "native-publish"
    native.mkdir()
    with pytest.raises(FileNotFoundError, match="InferBridge.Native.exe"):
        native_entries(native)
    (native / "InferBridge.Native.exe").write_bytes(b"MZ")
    with pytest.raises(FileNotFoundError, match="deps.json"):
        _stage(tmp_path, output="staging")
    assert not (tmp_path / "staging").exists()

    _native_publish(tmp_path / "native-ok")
    (dist / "_internal" / "python311.dll").unlink()
    with pytest.raises(FileNotFoundError, match="Python runtime DLL"):
        _stage(tmp_path, output="staging", native_publish=tmp_path / "native-ok")
    assert not (tmp_path / "staging").exists()


def test_stage_rejects_overlapping_native_and_engine_layouts(tmp_path):
    dist = _python_dist(tmp_path / "dist")
    nested = _native_publish(dist / "staged-native")
    with pytest.raises(ValueError, match="outside"):
        _stage(tmp_path, native_publish=nested)

    native = _native_publish(tmp_path / "native-publish")
    (dist / "native").mkdir()
    with pytest.raises(RuntimeError, match="already contains"):
        _stage(tmp_path, native_publish=native)


def test_staging_rejects_nested_output_and_case_insensitive_collisions(tmp_path):
    dist = _python_dist(tmp_path / "dist")
    _native_publish(tmp_path / "native-publish")
    with pytest.raises(ValueError, match="outside"):
        _stage(tmp_path, output_dir=dist / "stage")

    (dist / "ASSETS").mkdir()
    (dist / "ASSETS" / "StoreLogo.png").write_bytes(b"bad")
    with pytest.raises(RuntimeError, match="collides"):
        _stage(tmp_path, output="stage")
    assert not (tmp_path / "stage").exists()


def test_staging_rejects_symlinked_python_payload(tmp_path):
    dist = _python_dist(tmp_path / "dist")
    _native_publish(tmp_path / "native-publish")
    link = dist / "outside.txt"
    try:
        link.symlink_to(tmp_path / "anything")
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links not available in this test environment.")
    with pytest.raises(RuntimeError, match="symbolic links"):
        _stage(tmp_path)
