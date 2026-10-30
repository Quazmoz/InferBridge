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
