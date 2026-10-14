"""Prevent a browser-shell regression while the native Store frontend is built."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "windows-native" / "InferBridge.Native"
MSBUILD = PROJECT / "InferBridge.Native.csproj"
XAML = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"


def test_native_project_uses_winui_and_a_pinned_windows_sdk():
    root = ET.parse(MSBUILD).getroot()
    props = {
        element.tag: element.text
        for group in root.findall("PropertyGroup")
        for element in group
    }
    assert props["UseWinUI"] == "true"
    assert props["WindowsPackageType"] == "None"
    assert props["WindowsAppSDKSelfContained"] == "true"
    packages = root.findall(".//PackageReference")
    assert any(
        item.get("Include") == "Microsoft.WindowsAppSDK"
        and item.get("Version") == "2.5.1"
        for item in packages
    )


def test_native_primary_views_use_windows_controls_not_a_browser():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    assert root.tag == XAML + "Window"
    assert root.find(".//" + XAML + "NavigationView") is not None
    assert root.find(".//" + XAML + "ListView") is not None
    assert not any("WebView" in node.tag or "Frame" in node.tag for node in root.iter())


def test_native_client_is_loopback_only_and_checks_instance_identity():
    content = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    assert "127.0.0.1" in content
    assert "/desktop/instance" in content
    assert "instance_nonce" in content
    assert "AllowAutoRedirect = false" in content
    assert "UseProxy = false" in content
    assert "MaximumJsonBytes" in content
    assert "/v1/models" in content
