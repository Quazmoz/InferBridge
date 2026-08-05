import os
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from app import desktop_launcher, desktop_server
from app.desktop_launcher import InstanceLock
from runtime.model_artifacts import validate_openvino_model_dir


def _write_ready_model(path: Path, payload: bytes = b"weights") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "openvino_model.xml").write_text(
        "<net name='model' version='11'></net>",
        encoding="utf-8",
    )
    (path / "openvino_model.bin").write_bytes(payload)
    (path / "config.json").write_text("{}", encoding="utf-8")


def test_port_selection_falls_back_when_preferred_is_busy():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        selected = desktop_launcher.choose_available_port(port)
    assert selected != port
    assert 1 <= selected <= 65535


def test_instance_verification_requires_matching_nonce(monkeypatch):
    metadata = desktop_launcher.InstanceMetadata(1, 8123, "expected", "app.exe", "now")
    monkeypatch.setattr(
        desktop_launcher,
        "_http_json",
        lambda url, timeout=1.5: (
            {"instance_nonce": "other"} if url.endswith("/desktop/instance") else {"status": "ok"}
        ),
    )
    assert desktop_launcher.verify_instance(metadata) is False


def test_stale_metadata_is_rejected(monkeypatch):
    metadata = desktop_launcher.InstanceMetadata(999999, 8123, "expected", "app.exe", "now")
    monkeypatch.setattr(desktop_launcher, "_http_json", lambda *args, **kwargs: None)
    assert desktop_launcher.verify_instance(metadata) is False


def test_second_lock_acquire_returns_false_without_raising(tmp_path):
    """A second launch while the first holds the lock must fail cleanly.

    On Windows the msvcrt byte-range lock held by the first instance makes the
    lock file's first byte unreadable from a second handle. Acquiring the lock
    a second time must report contention by returning False, not crash the
    launcher with an unhandled PermissionError.
    """
    lock_path = tmp_path / "launcher.lock"
    first = InstanceLock(lock_path)
    second = InstanceLock(lock_path)
    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        second.release()
        first.release()
    # Once the first instance releases, a fresh acquire must succeed again.
    third = InstanceLock(lock_path)
    assert third.acquire() is True
    third.release()


def _launcher_args(**overrides):
    values = {
        "portable": False,
        "data_dir": None,
        "mock": False,
        "control_token": "desktop-secret",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_legacy_child_command_excludes_control_token():
    metadata = desktop_launcher.InstanceMetadata(1, 8123, "nonce", "app.exe", "now")
    command = desktop_launcher._child_command(_launcher_args(), metadata)

    assert "--control-token" not in command
    assert "desktop-secret" not in command
    assert command[-4:] == ["--port", "8123", "--instance-nonce", "nonce"]


def test_legacy_spawn_passes_control_token_only_in_child_environment(monkeypatch, tmp_path):
    captured = {}
    sentinel = object()

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(desktop_launcher.subprocess, "Popen", fake_popen)
    metadata = desktop_launcher.InstanceMetadata(1, 8123, "nonce", "app.exe", "now")

    result = desktop_launcher._spawn_server(
        _launcher_args(),
        metadata,
        tmp_path / "desktop.log",
    )

    assert result is sentinel
    assert "desktop-secret" not in captured["command"]
    assert captured["env"]["OV_LLM_DESKTOP_CONTROL_TOKEN"] == "desktop-secret"


def test_prepare_desktop_environment_clears_stale_launch_flags(monkeypatch, tmp_path):
    monkeypatch.delenv("OV_LLM_DESKTOP", raising=False)
    monkeypatch.setenv("OV_LLM_PORTABLE", "1")
    monkeypatch.setenv("OV_LLM_DATA_DIR", "stale")
    monkeypatch.setenv("OV_LLM_MOCK", "1")

    desktop_server.prepare_desktop_environment()

    assert os.environ["OV_LLM_DESKTOP"] == "1"
    assert "OV_LLM_PORTABLE" not in os.environ
    assert "OV_LLM_DATA_DIR" not in os.environ
    assert "OV_LLM_MOCK" not in os.environ

    desktop_server.prepare_desktop_environment(
        portable=True,
        data_dir=str(tmp_path),
        mock=True,
    )
    assert os.environ["OV_LLM_PORTABLE"] == "1"
    assert os.environ["OV_LLM_DATA_DIR"] == str(tmp_path)
    assert os.environ["OV_LLM_MOCK"] == "1"


def test_server_child_consumes_control_token_environment(monkeypatch):
    captured = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)
        return 7

    monkeypatch.setattr(desktop_server, "run_server", fake_run_server)
    monkeypatch.setenv("OV_LLM_DESKTOP_CONTROL_TOKEN", "environment-secret")
    args = SimpleNamespace(
        port=8123,
        instance_nonce="nonce",
        control_token="",
        owner_pid=123,
        owner_created_at=456.0,
        portable=False,
        data_dir=None,
        mock=True,
    )

    assert desktop_launcher._server_child(args) == 7
    assert captured["control_token"] == "environment-secret"
    assert "OV_LLM_DESKTOP_CONTROL_TOKEN" not in os.environ


def test_explicit_control_token_wins_but_environment_is_still_removed(monkeypatch):
    captured = {}

    def fake_run_server(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(desktop_server, "run_server", fake_run_server)
    monkeypatch.setenv("OV_LLM_DESKTOP_CONTROL_TOKEN", "stale-secret")
    args = SimpleNamespace(
        port=8123,
        instance_nonce="nonce",
        control_token="explicit-secret",
        owner_pid=0,
        owner_created_at=0.0,
        portable=False,
        data_dir=None,
        mock=False,
    )

    assert desktop_launcher._server_child(args) == 0
    assert captured["control_token"] == "explicit-secret"
    assert "OV_LLM_DESKTOP_CONTROL_TOKEN" not in os.environ


def test_system_exit_code_is_bounded_and_predictable():
    assert desktop_launcher._system_exit_code(None) == 0
    assert desktop_launcher._system_exit_code(7) == 7
    assert desktop_launcher._system_exit_code(True) == 1
    assert desktop_launcher._system_exit_code("invalid arguments") == 2
    assert desktop_launcher._system_exit_code(-1) == 2
    assert desktop_launcher._system_exit_code(999) == 2


def test_packaged_converter_accepts_progress_emitter_and_restores_overrides(
    monkeypatch,
    tmp_path,
):
    from runtime import model_converter

    calls = {}
    optimum = ModuleType("optimum")
    commands = ModuleType("optimum.commands")
    optimum_cli = ModuleType("optimum.commands.optimum_cli")
    final = tmp_path / "model"

    def fake_optimum_main():
        calls["argv"] = list(sys.argv)
        _write_ready_model(Path(sys.argv[-1]), b"new")
        return 0

    optimum_cli.main = fake_optimum_main
    commands.optimum_cli = optimum_cli
    optimum.commands = commands
    monkeypatch.setitem(sys.modules, "optimum", optimum)
    monkeypatch.setitem(sys.modules, "optimum.commands", commands)
    monkeypatch.setitem(sys.modules, "optimum.commands.optimum_cli", optimum_cli)
    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)

    emitter = SimpleNamespace(
        emit=lambda *args, **kwargs: calls.setdefault("progress", (args, kwargs))
    )
    original_runner = model_converter._run_streaming_command
    original_which = model_converter.shutil.which

    def fake_converter_main(arguments):
        calls["arguments"] = arguments
        assert model_converter.shutil.which("optimum-cli") == sys.executable
        model_converter._run_model_export_command(
            ["optimum-cli", "export", "openvino", str(final)],
            progress_emitter=emitter,
        )
        return 0

    monkeypatch.setattr(model_converter, "main", fake_converter_main)

    assert desktop_launcher._run_packaged_converter(["--id", "tinyllama"]) == 0
    assert calls["arguments"] == ["--id", "tinyllama"]
    assert calls["argv"][:3] == ["optimum-cli", "export", "openvino"]
    assert calls["argv"][-1] == str(tmp_path / ".model.inferbridge-staging")
    assert calls["progress"][0][:2] == (
        "converting",
        "Running packaged OpenVINO conversion…",
    )
    assert validate_openvino_model_dir(final, thorough=True).ready is True
    assert (final / "openvino_model.bin").read_bytes() == b"new"
    assert model_converter._run_streaming_command is original_runner
    assert model_converter.shutil.which is original_which


def test_packaged_converter_failure_preserves_previous_model(monkeypatch, tmp_path, capsys):
    from runtime import model_converter

    optimum = ModuleType("optimum")
    commands = ModuleType("optimum.commands")
    optimum_cli = ModuleType("optimum.commands.optimum_cli")
    final = tmp_path / "model"
    _write_ready_model(final, b"old")

    def fake_optimum_main():
        staging = Path(sys.argv[-1])
        staging.mkdir(parents=True)
        (staging / "partial.bin").write_bytes(b"partial")
        raise SystemExit(7)

    optimum_cli.main = fake_optimum_main
    commands.optimum_cli = optimum_cli
    optimum.commands = commands
    monkeypatch.setitem(sys.modules, "optimum", optimum)
    monkeypatch.setitem(sys.modules, "optimum.commands", commands)
    monkeypatch.setitem(sys.modules, "optimum.commands.optimum_cli", optimum_cli)
    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)

    def fake_converter_main(_arguments):
        model_converter._run_model_export_command(
            ["optimum-cli", "export", "openvino", str(final)]
        )
        return 0

    monkeypatch.setattr(model_converter, "main", fake_converter_main)

    assert desktop_launcher._run_packaged_converter(["--id", "tinyllama"]) == 2
    assert "Packaged model conversion failed" in capsys.readouterr().err
    assert validate_openvino_model_dir(final, thorough=True).ready is True
    assert (final / "openvino_model.bin").read_bytes() == b"old"
    assert not (tmp_path / ".model.inferbridge-staging").exists()


def test_packaged_converter_contains_unexpected_failure_and_restores_overrides(monkeypatch, capsys):
    from runtime import model_converter

    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)
    original_runner = model_converter._run_streaming_command
    original_which = model_converter.shutil.which

    def fail(_arguments):
        raise RuntimeError("conversion exploded")

    monkeypatch.setattr(model_converter, "main", fail)

    assert desktop_launcher._run_packaged_converter(["--id", "tinyllama"]) == 2
    assert "Packaged model conversion failed: conversion exploded" in capsys.readouterr().err
    assert model_converter._run_streaming_command is original_runner
    assert model_converter.shutil.which is original_which


def test_packaged_converter_contains_optimum_string_system_exit(monkeypatch, tmp_path, capsys):
    from runtime import model_converter

    optimum = ModuleType("optimum")
    commands = ModuleType("optimum.commands")
    optimum_cli = ModuleType("optimum.commands.optimum_cli")

    def fake_optimum_main():
        raise SystemExit("invalid optimum arguments")

    optimum_cli.main = fake_optimum_main
    commands.optimum_cli = optimum_cli
    optimum.commands = commands
    monkeypatch.setitem(sys.modules, "optimum", optimum)
    monkeypatch.setitem(sys.modules, "optimum.commands", commands)
    monkeypatch.setitem(sys.modules, "optimum.commands.optimum_cli", optimum_cli)
    monkeypatch.setattr(desktop_launcher.sys, "frozen", True, raising=False)
    previous_argv = list(sys.argv)

    def fake_converter_main(_arguments):
        model_converter._run_model_export_command(
            ["optimum-cli", "export", "openvino", str(tmp_path / "model")]
        )
        return 0

    monkeypatch.setattr(model_converter, "main", fake_converter_main)

    assert desktop_launcher._run_packaged_converter(["--id", "tinyllama"]) == 2
    assert "invalid optimum arguments" in capsys.readouterr().err
    assert sys.argv == previous_argv


def test_native_runtime_smoke_loads_tokenizer_extension_by_name(monkeypatch):
    calls = []
    openvino = ModuleType("openvino")
    openvino_genai = ModuleType("openvino_genai")

    class FakeCore:
        def add_extension(self, path):
            calls.append(path)

    class FakePipeline:
        pass

    openvino.Core = FakeCore
    openvino_genai.LLMPipeline = FakePipeline
    monkeypatch.setitem(sys.modules, "openvino", openvino)
    monkeypatch.setitem(sys.modules, "openvino_genai", openvino_genai)

    assert desktop_launcher._native_runtime_smoke() == 0
    assert calls == ["openvino_tokenizers.dll"]
