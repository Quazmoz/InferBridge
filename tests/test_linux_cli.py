"""The Linux ``inferbridge`` command: install, upgrade, uninstall, autostart, secrets."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import linux_cli, linux_secrets

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux integration files")


@pytest.fixture
def env(monkeypatch, tmp_path):
    home = tmp_path / "home λ"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    # runtime_paths() applies the desktop launch mode to os.environ; restore it afterwards.
    for name in ("OV_LLM_DATA_DIR", "OV_LLM_DESKTOP", "OV_LLM_PORTABLE", "OV_LLM_MOCK"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    calls = []
    monkeypatch.setattr(
        linux_cli,
        "_systemctl",
        lambda *arguments, check=False: (
            calls.append(arguments) or SimpleNamespace(returncode=0, stdout="inactive\n", stderr="")
        ),
    )
    monkeypatch.setattr(linux_cli, "instance", lambda paths: None)
    return SimpleNamespace(path=home, systemctl_calls=calls)


def _bundle(path: Path) -> Path:
    (path / "resources").mkdir(parents=True)
    (path / "resources/InferBridge.png").write_bytes(b"\x89PNG icon")
    (path / "InferBridge").write_text("#!/bin/sh\nexit 0\n")
    (path / "InferBridge").chmod(0o755)
    return path


def _cli(root: Path, *argv: str) -> int:
    return linux_cli.main(list(argv), root)


def test_seal_detects_any_edit():
    sealed = linux_cli._seal("[Unit]\nDescription=x\n")
    assert linux_cli.managed_unmodified(sealed)
    assert not linux_cli.managed_unmodified(sealed.replace("x", "y"))
    assert not linux_cli.managed_unmodified("[Unit]\n")


def test_install_upgrade_and_uninstall_leave_only_user_data(env, tmp_path):
    home = env.path
    old = _bundle(tmp_path / "InferBridge-1.1.0-linux-x86_64")
    new = _bundle(tmp_path / 'InferBridge 1.1.1 "λ" %')
    data = home / ".local/share/InferBridge"
    (data / "config").mkdir(parents=True)
    (data / "models").mkdir()
    (data / "models/keep.bin").write_bytes(b"user model")

    assert _cli(old, "install") == 0
    link = home / ".local/bin/inferbridge"
    assert link.resolve() == (old / "InferBridge").resolve()
    assert linux_cli.install_kind(old) == "user"
    assert _cli(old, "service", "install") == 0
    assert _cli(old, "autostart", "enable") == 0
    unit = home / ".config/systemd/user/inferbridge.service"
    assert str(old) in unit.read_text()

    # Upgrade: a newer bundle takes over every unmodified managed file.
    assert _cli(new, "install") == 0
    assert link.resolve() == (new / "InferBridge").resolve()
    assert "%%" in unit.read_text() and linux_cli.managed_unmodified(unit.read_text())
    autostart = home / ".config/autostart/inferbridge.desktop"
    assert "--startup" in autostart.read_text() and "%%" in autostart.read_text()
    entry = home / ".local/share/applications/inferbridge.desktop"
    assert "Icon=inferbridge" in entry.read_text()
    assert (home / ".local/share/icons/hicolor/256x256/apps/inferbridge.png").is_file()

    assert _cli(new, "uninstall") == 0
    leftovers = sorted(str(path.relative_to(home)) for path in home.rglob("*") if path.is_file())
    assert leftovers == [".local/share/InferBridge/models/keep.bin"]
    assert new.is_dir()  # A portable bundle is only removed on request.


def test_edited_unit_and_autostart_are_preserved(env, tmp_path):
    home = env.path
    root = _bundle(tmp_path / "bundle")
    other = _bundle(tmp_path / "other")
    assert _cli(root, "service", "install") == 0
    unit = home / ".config/systemd/user/inferbridge.service"
    unit.write_text(unit.read_text() + "Environment=OV_LLM_PORT=9000\n")
    assert _cli(other, "service", "install") == 2
    assert "OV_LLM_PORT=9000" in unit.read_text()
    _cli(other, "service", "uninstall")
    assert unit.exists()

    assert _cli(root, "autostart", "enable") == 0
    autostart = home / ".config/autostart/inferbridge.desktop"
    autostart.write_text(autostart.read_text() + "Hidden=true\n")
    assert _cli(root, "autostart", "enable") == 2
    _cli(root, "autostart", "disable")
    assert autostart.exists()


def test_install_never_replaces_a_foreign_command(env, tmp_path):
    home = env.path
    root = _bundle(tmp_path / "bundle")
    link = home / ".local/bin/inferbridge"
    link.parent.mkdir(parents=True)
    link.write_text("#!/bin/sh\necho mine\n")
    assert _cli(root, "install") == 2
    assert "mine" in link.read_text()


def test_purge_requires_confirmation_and_an_inferbridge_directory(env, tmp_path):
    home = env.path
    root = _bundle(tmp_path / "bundle")
    data = home / ".local/share/InferBridge"
    (data / "config").mkdir(parents=True)
    assert _cli(root, "uninstall", "--purge") == 2
    assert data.is_dir()
    assert _cli(root, "uninstall", "--purge", "--yes", "--remove-app") == 0
    assert not data.exists() and not root.exists()
    assert not linux_cli._data_root_is_safe_to_purge(home)


def test_service_enable_refuses_while_a_desktop_instance_runs(env, tmp_path, monkeypatch):
    root = _bundle(tmp_path / "bundle")
    monkeypatch.setattr(linux_cli, "instance", lambda paths: {"url": "http://127.0.0.1:1/"})
    assert _cli(root, "service", "enable") == 2
    monkeypatch.setattr(linux_cli, "instance", lambda paths: None)
    assert _cli(root, "service", "enable") == 0
    assert ("enable", "--now", "inferbridge.service") in env.systemctl_calls


def test_system_install_is_detected_and_not_reregistered(env, tmp_path, monkeypatch):
    home = env.path
    monkeypatch.setattr(linux_cli, "SYSTEM_PREFIXES", (str(tmp_path) + "/",))
    root = _bundle(tmp_path / "opt/inferbridge")
    root.chmod(0o555)
    try:
        if os.access(root, os.W_OK):
            pytest.skip("root can write everywhere")
        assert linux_cli.install_kind(root) == "system"
        assert _cli(root, "install") == 0
        assert not (home / ".local/bin/inferbridge").exists()
    finally:
        root.chmod(0o755)


def test_latest_release_follows_channel_and_lists_linux_assets():
    releases = [
        {"tag_name": "v1.2.0-beta.1", "html_url": "b", "assets": [{"name": "x.deb"}]},
        {
            "tag_name": "v1.1.2",
            "html_url": "s",
            "assets": [
                {
                    "name": "InferBridge-1.1.2-windows-x64-installer.exe",
                    "browser_download_url": "w",
                },
                {"name": "InferBridge-1.1.2-linux-x86_64.tar.gz", "browser_download_url": "l"},
            ],
        },
        {"tag_name": "v9.9.9", "draft": True, "assets": []},
        {"tag_name": "not-a-version", "assets": []},
    ]
    stable = linux_cli.latest_release(releases, "stable")
    assert stable["version"] == "1.1.2"
    assert [asset["url"] for asset in stable["linux_assets"]] == ["l"]
    for asset in releases[0]["assets"]:
        asset["browser_download_url"] = "d"
    assert linux_cli.latest_release(releases, "beta")["version"] == "1.2.0-beta.1"


class _FakeSecretTool:
    def __init__(self, *, accept=True):
        self.accept = accept
        self.saved: str | None = None
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, command, *, input=None, **_kwargs):
        self.calls.append((command, input))
        action = command[1]
        if action == "store" and self.accept:
            self.saved = input
        if action == "clear":
            self.saved = None
        stdout = (self.saved or "") if action == "lookup" else ""
        ok = self.accept and (action != "lookup" or self.saved is not None)
        return SimpleNamespace(returncode=0 if ok else 1, stdout=stdout, stderr="")


@pytest.fixture
def keyring(monkeypatch):
    monkeypatch.delenv(linux_secrets.DISABLE_VARIABLE, raising=False)
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(linux_secrets.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1/bus")
    return _FakeSecretTool()


def test_secret_travels_on_stdin_and_lookups_are_cached(keyring, tmp_path):
    store = linux_secrets.SecretServiceStore("desktop-api-key", tmp_path, runner=keyring)
    assert store.store("label", "ib_secret-value") is True
    assert all("ib_secret-value" not in " ".join(command) for command, _ in keyring.calls)
    fresh = linux_secrets.SecretServiceStore("desktop-api-key", tmp_path, runner=keyring)
    assert fresh.lookup() == "ib_secret-value"
    assert fresh.lookup() == "ib_secret-value"
    assert [command[1] for command, _ in keyring.calls] == ["store", "lookup"]
    assert fresh.clear() and fresh.lookup() is None
    other_scope = linux_secrets.SecretServiceStore(
        "desktop-api-key", tmp_path / "x", runner=keyring
    )
    assert other_scope.scope != fresh.scope


def test_api_key_store_uses_keyring_and_falls_back_to_memory(keyring, tmp_path, monkeypatch):
    from app import desktop_network

    monkeypatch.setattr(desktop_network.os, "name", "posix")
    store = desktop_network.DesktopApiKeyStore(tmp_path)
    store._keyring = linux_secrets.SecretServiceStore("desktop-api-key", tmp_path, runner=keyring)
    assert store.persistence == "secret_service"
    key = store.generate()
    store.set_key(key)
    restarted = desktop_network.DesktopApiKeyStore(tmp_path)
    restarted._keyring = linux_secrets.SecretServiceStore(
        "desktop-api-key", tmp_path, runner=keyring
    )
    assert restarted.get_key() == key
    assert restarted.remove() is True and keyring.saved is None

    refusing = _FakeSecretTool(accept=False)
    store = desktop_network.DesktopApiKeyStore(tmp_path)
    store._keyring = linux_secrets.SecretServiceStore("desktop-api-key", tmp_path, runner=refusing)
    store.set_key(key)
    assert store.get_key() == key and store.persistence == "memory_only"


def test_disabled_or_missing_secret_tool_is_memory_only(monkeypatch, tmp_path):
    from app import desktop_network

    monkeypatch.setattr(desktop_network.os, "name", "posix")
    assert desktop_network.DesktopApiKeyStore(tmp_path).persistence == "memory_only"


def test_new_data_root_is_private(monkeypatch, tmp_path):
    from app.paths import ensure_runtime_directories, resolve_runtime_paths

    monkeypatch.setenv("OV_LLM_DATA_DIR", str(tmp_path / "fresh"))
    paths = resolve_runtime_paths(desktop=True)
    ensure_runtime_directories(paths)
    assert paths.data_root.stat().st_mode & 0o777 == 0o700
    assert paths.config_dir.stat().st_mode & 0o777 == 0o700
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    shared.chmod(0o755)
    monkeypatch.setenv("OV_LLM_DATA_DIR", str(shared))
    paths = resolve_runtime_paths(desktop=True)
    ensure_runtime_directories(paths)
    assert shared.stat().st_mode & 0o777 == 0o755  # An existing chosen root keeps its mode.
    assert paths.logs_dir.stat().st_mode & 0o777 == 0o700


def test_default_data_root_from_an_older_build_becomes_private(monkeypatch, tmp_path):
    from app.desktop_launcher import InstanceLock
    from app.paths import ensure_runtime_directories, resolve_runtime_paths

    monkeypatch.delenv("OV_LLM_DATA_DIR", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    paths = resolve_runtime_paths(desktop=True, portable=False)
    paths.data_root.mkdir(parents=True)
    paths.data_root.chmod(0o755)  # Created by an earlier build or by hand.
    ensure_runtime_directories(paths)
    assert paths.data_root.stat().st_mode & 0o777 == 0o700
    # The launcher lock is often the first write; a new explicit root starts private too.
    fresh = tmp_path / "explicit" / "root"
    lock = InstanceLock(fresh / "desktop-instance.lock")
    assert lock.acquire()
    lock.release()
    assert fresh.stat().st_mode & 0o777 == 0o700


def test_host_environment_restores_the_users_runtime():
    from app.desktop_shell import host_environment

    environment = host_environment(
        {
            "PATH": "/opt/inferbridge/python/bin:/usr/bin",
            "INFERBRIDGE_HOST_PATH": "/usr/bin",
            "LD_LIBRARY_PATH": "/opt/inferbridge/python/lib",
            "INFERBRIDGE_HOST_LD_LIBRARY_PATH": "",
            "PYTHONPATH": "/opt/inferbridge/resources",
            "OV_LLM_API_KEY": "secret",
            "HF_TOKEN": "hf_secret",
            "LANG": "C.UTF-8",
        }
    )
    assert environment == {"PATH": "/usr/bin", "LANG": "C.UTF-8"}


def test_huggingface_token_persists_in_the_keyring(keyring, tmp_path, monkeypatch):
    from app import huggingface_access

    monkeypatch.setattr(huggingface_access.os, "name", "posix")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)
    settings = SimpleNamespace(models_file=str(tmp_path / "config" / "models.json"))

    def store():
        created = huggingface_access.HuggingFaceCredentialStore(settings)
        created._keyring = linux_secrets.SecretServiceStore(
            "huggingface-token", tmp_path, runner=keyring
        )
        return created

    first = store()
    assert first.persistence == "secret_service"
    first.set_token("hf_" + "a" * 30)
    assert store().get_token() == "hf_" + "a" * 30
    assert store().remove() is True and keyring.saved is None


def test_clear_reports_false_when_nothing_was_stored(keyring, tmp_path):
    # secret-tool clear exits 0 even when nothing matched; removal is reported from lookup.
    store = linux_secrets.SecretServiceStore("huggingface-token", tmp_path, runner=keyring)
    assert store.clear() is False
    assert store.store("label", "hf_value") and store.clear() is True


def test_remove_app_refuses_a_directory_that_is_not_a_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "not-a-bundle"
    root.mkdir()
    (root / "keep.txt").write_text("x")
    monkeypatch.setattr(
        linux_cli,
        "runtime_paths",
        lambda args: SimpleNamespace(
            data_root=tmp_path / "data", logs_dir=tmp_path / "data" / "logs"
        ),
    )
    monkeypatch.setattr(linux_cli, "instance", lambda paths: None)
    monkeypatch.setattr(linux_cli, "command_service", lambda root, args: 0)
    monkeypatch.setattr(linux_cli, "integrate", lambda root, remove=False: 0)
    args = argparse.Namespace(
        purge=False, yes=False, remove_app=True, portable=False, data_dir=None
    )
    assert linux_cli.command_uninstall(root, args) == 2
    assert (root / "keep.txt").exists()
