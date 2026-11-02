"""Microsoft Store readiness: no outside updates, reachable privacy policy, labelled icons."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.tray_menu import TrayMenuMixin

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "windows-native" / "InferBridge.Native"
XAML = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
NAME = "{http://schemas.microsoft.com/winfx/2006/xaml}Name"
PRIVACY_URL = "https://consultant.quinnfavo.com/privacy/inferbridge"


def _named(root, name):
    return next(node for node in root.iter() if node.get(NAME) == name)


def test_settings_about_links_privacy_policy_support_and_source():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    settings = _named(root, "SettingsPanel")
    links = {
        node.get(NAME): node.get("NavigateUri")
        for node in settings.iter(XAML + "HyperlinkButton")
        if node.get("NavigateUri")
    }
    assert links["PrivacyPolicyLink"] == PRIVACY_URL
    assert links["IssuesLink"] == "https://github.com/Quazmoz/InferBridge/issues"
    assert links["SupportLink"].startswith("https://consultant.quinnfavo.com/")
    assert all(url.startswith("https://") for url in links.values())
    assert _named(settings, "AboutVersionText") is not None


def test_chat_offers_a_way_to_report_ai_replies():
    # Microsoft Store Policies 11.16 (live generative AI content).
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    chat = _named(root, "ChatPanel")
    report = _named(chat, "ReportReplyLink")
    assert report.get("NavigateUri", "").startswith("https://consultant.quinnfavo.com/")
    assert report.get("AutomationProperties.Name")


def test_store_install_hides_every_update_control():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    panel = _named(root, "UpdateControlsPanel")
    inside = {node.get(NAME) for node in panel.iter()}
    for control in (
        "UpdateChecksToggle",
        "UpdateChannelPicker",
        "SaveUpdateSettingsButton",
        "CheckUpdatesButton",
    ):
        assert control in inside
    maintenance = (PROJECT / "MainWindow.Maintenance.cs").read_text(encoding="utf-8")
    about = maintenance.split("private void InitializeAbout()", 1)[1].split("\n    private ", 1)[0]
    assert "AppRelease.ShowsUpdateControls(packaged, null)" in about
    render = maintenance.split("private void RenderReleaseStatus", 1)[1].split("\n    private ", 1)[
        0
    ]
    assert (
        "AppRelease.ShowsUpdateControls(AppRelease.IsPackaged, status.InstallationMode)" in render
    )
    check = maintenance.split("private async void CheckUpdatesButton_Click", 1)[1]
    check = check.split("\n    private ", 1)[0]
    assert check.index("ShowsUpdateControls") < check.index("CheckForUpdateAsync")
    window = (PROJECT / "MainWindow.xaml.cs").read_text(encoding="utf-8")
    assert "InitializeAbout();" in window


def test_icon_only_buttons_have_names_and_tooltips():
    root = ET.parse(PROJECT / "MainWindow.xaml").getroot()
    for node in root.iter():
        if node.tag not in (XAML + "Button", XAML + "DropDownButton"):
            continue
        has_text = node.get("Content") or any(
            child.tag == XAML + "TextBlock" for child in node.iter()
        )
        if not has_text:
            assert node.get("AutomationProperties.Name"), node.attrib
            assert node.get("ToolTipService.ToolTip"), node.attrib


def test_release_ui_has_no_developer_wording():
    xaml = (PROJECT / "MainWindow.xaml").read_text(encoding="utf-8")
    for phrase in (
        "Python/OpenVINO",
        "tray controller",
        "web GUI",
        "WinUI 3 client",
        "Mock results",
    ):
        assert phrase not in xaml


def test_native_client_never_requests_elevation():
    manifest = (PROJECT / "app.manifest").read_text(encoding="utf-8")
    assert 'level="asInvoker"' in manifest
    assert "requireAdministrator" not in manifest


class _MenuStub(TrayMenuMixin):
    def __getattr__(self, _name):  # actions from other tray mixins are not invoked here
        return lambda *_args, **_kwargs: None

    def _enabled(self, _field):
        return lambda _item: True

    def _snapshot(self):
        return SimpleNamespace(server_status="ready", active_model_name=None, active_model_id=None)


class _FakeItem:
    def __init__(self, text, action=None, **options):
        self.text = text
        self.action = action
        self.options = options


def _menu_texts(packaged, monkeypatch):
    monkeypatch.setattr("app.tray_menu.is_packaged", lambda: packaged)
    fake = SimpleNamespace(Menu=lambda *items: list(items), MenuItem=_FakeItem)
    items = _MenuStub()._build_menu(fake)
    return {
        item.text: item.options.get("visible", True) for item in items if isinstance(item.text, str)
    }


@pytest.mark.parametrize("packaged", [True, False])
def test_tray_offers_update_checks_only_outside_the_store(packaged, monkeypatch):
    assert _menu_texts(packaged, monkeypatch)["Check for Updates"] is (not packaged)


def test_installed_msix_smoke_rejects_unrelated_or_stale_engine_sessions():
    script = (ROOT / "scripts" / "smoke_test_msix.ps1").read_text(encoding="utf-8")
    identity_check = script.split("function Test-PackagedSession", 1)[1].split(
        "\n$Certificate = ", 1
    )[0]
    assert "Get-Process -Id $ServerPid" in identity_check
    assert 'Join-Path $InstallRoot "InferBridge.exe"' in identity_check
    assert "$StartedAt.UtcDateTime -lt $TestStartedAt.ToUniversalTime()" in identity_check

    readiness = script.split("$Session = Wait-Until", 1)[1].split(
        '"the bundled engine to report a verified, live session"', 1
    )[0]
    assert readiness.index("Test-PackagedSession") < readiness.index("Invoke-RestMethod")

    relaunch = script.split("# A second launch must focus", 1)[1]
    assert "Test-PackagedSession $Owners $InstallRoot" in relaunch
    assert "$Owners.pid -ne $Session.pid" in relaunch


def test_installed_msix_smoke_fresh_profile_gate_preserves_existing_data():
    script = (ROOT / "scripts" / "smoke_test_msix.ps1").read_text(encoding="utf-8")
    assert "[switch]$RequireFreshProfile" in script
    assert "$RequireFreshProfile -and (Test-Path -LiteralPath $StoreDataRoot)" in script
    assert "Use a new Windows test account" in script
    assert script.index("if ($RequireFreshProfile") < script.index("Add-AppxPackage -Path $Signed")


def test_msix_smoke_does_not_replace_an_existing_package():
    script = (ROOT / "scripts" / "smoke_test_msix.ps1").read_text(encoding="utf-8")
    preflight = "Get-AppxPackage -Name $IdentityName -ErrorAction Stop"
    assert preflight in script
    assert script.index(preflight) < script.index("New-SelfSignedCertificate")
    assert script.index(preflight) < script.index("Add-AppxPackage -Path $Signed")
    assert "the smoke test will not replace an existing package" in script


def test_msix_smoke_verifies_process_start_and_wack_cleanup():
    script = (ROOT / "scripts" / "smoke_test_msix.ps1").read_text(encoding="utf-8")
    session = script.split("function Test-PackagedSession", 1)[1].split("\n$Certificate = ", 1)[0]
    assert "$Server.StartTime.ToUniversalTime() -lt $TestStartedAt.ToUniversalTime()" in session
    assert "$Server.Path" in session

    finder = script.split("function Get-PackageProcesses", 1)[1].split(
        "\nfunction Test-PackagedSession", 1
    )[0]
    assert "$Root.TrimEnd" in finder
    assert "$_.Path.StartsWith($Prefix" in finder

    wack = script.split("if ($RunCertificationKit)", 1)[1].split("} catch {", 1)[0]
    assert 'Get-PackageProcesses "InferBridge" $InstallRoot | Stop-Process' in wack
    assert "$Installed = $null" not in wack
    cleanup = script.split("} finally {", 1)[-1]
    assert "Get-AppxPackage -Name $IdentityName" in cleanup
    assert "$_.PackageFullName -eq $Installed.PackageFullName" in cleanup
    assert "Remove-AppxPackage -Package $Copy.PackageFullName -ErrorAction Stop" in cleanup



def test_msix_smoke_cleans_temporary_signed_copy_but_retains_evidence():
    script = (ROOT / "scripts" / "smoke_test_msix.ps1").read_text(encoding="utf-8")
    assert "if (-not $EvidenceDirectory) {" in script
    assert '"artifacts\\msix-smoke-" + [guid]::NewGuid()' in script
    assert "$EvidenceDirectory = $Work" not in script
    assert script.index("Get-AppxPackage -Name $IdentityName -ErrorAction Stop") < script.index(
        "New-Item $Work -ItemType Directory"
    )
    cleanup = script.rsplit("} finally {", 1)[-1]
    assert "Remove-Item -LiteralPath $Work -Recurse -Force" in cleanup
    assert "Write-Host \"Evidence: $EvidenceDirectory\"" in cleanup
