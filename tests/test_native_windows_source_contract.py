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


def test_native_chat_uses_real_accessible_xaml_controls():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    xaml_ns = "{http://schemas.microsoft.com/winfx/2006/xaml}"
    named = {
        node.get(xaml_ns + "Name"): node.tag
        for node in root.iter()
        if node.get(xaml_ns + "Name")
    }
    assert named["ChatModelPicker"] == XAML + "ComboBox"
    assert named["ChatHistory"] == XAML + "ListView"
    assert named["PromptBox"] == XAML + "TextBox"
    assert named["SendButton"] == XAML + "Button"
    assert named["CancelButton"] == XAML + "Button"
    assert named["ApiKeyBox"] == XAML + "PasswordBox"
    assert named["LoadButton"] == XAML + "Button"
    assert named["UnloadButton"] == XAML + "Button"
    assert not any("WebView" in node.tag for node in root.iter())


def test_native_chat_and_models_use_authoritative_bounded_backend_contracts():
    client = (PROJECT / "BackendClient.cs").read_text(encoding="utf-8")
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    for route in (
        "/desktop/instance", "/health/live", "/v1/models",
        "/v1/chat/completions", "/v1/models/load", "/v1/models/unload",
    ):
        assert route in client
    for safeguard in (
        "AllowAutoRedirect = false", "UseProxy = false",
        "Authorization", "CancellationTokenSource.CreateLinkedTokenSource",
        "MaximumChatBytes", "TimeSpan.FromSeconds(120)",
        "RequireVerifiedOriginAsync", "ready_to_load",
    ):
        assert safeguard in client
    assert "X-Desktop-Control" not in client
    assert "ApiKeyBox.Password" in window
    assert "CancelButton_Click" in window
    assert "NewChatButton_Click" in window
    assert "ChangeModelAsync" in window
