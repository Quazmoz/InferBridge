"""Guard the real Optimum/OpenVINO export CLI used by model_converter."""

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_conversion_cli as cli
from scripts.check_conversion_cli import required_flags, validate_help


def test_preflight_checks_every_flag_in_model_export_command():
    assert required_flags() == {
        "--model",
        "--weight-format",
        "--task",
        "--trust-remote-code",
        "--group-size",
        "--ratio",
        "--sym",
    }


def test_preflight_accepts_valid_cli_help_without_running_a_model():
    validate_help("usage: optimum-cli export openvino\n" + " ".join(sorted(required_flags())))


def test_preflight_rejects_missing_optional_flags():
    available = required_flags() - {"--ratio"}
    with pytest.raises(RuntimeError, match="--ratio"):
        validate_help("usage: optimum-cli export openvino\n" + " ".join(sorted(available)))


def test_preflight_does_not_accept_prefix_similar_flags():
    available = (required_flags() - {"--ratio"}) | {"--ratio-fallback"}
    with pytest.raises(RuntimeError, match="--ratio"):
        validate_help("usage: optimum-cli export openvino\n" + " ".join(sorted(available)))


def test_preflight_rejects_other_command_help():
    with pytest.raises(RuntimeError, match="OpenVINO"):
        validate_help("usage: optimum-cli export onnx --model --weight-format")

def _fixture_executable(monkeypatch, tmp_path: Path) -> Path:
    scripts = tmp_path / "venv-scripts"
    scripts.mkdir()
    executable = scripts / ("optimum-cli.exe" if sys.platform == "win32" else "optimum-cli")
    executable.write_bytes(b"stub")
    monkeypatch.setattr(cli.sysconfig, "get_path", lambda key: str(scripts))
    return executable


def test_preflight_uses_exact_interpreter_environment_not_path(monkeypatch, tmp_path, capsys):
    executable = _fixture_executable(monkeypatch, tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "unrelated-python"))
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        help_text = "usage: optimum-cli export openvino\n" + " ".join(sorted(required_flags()))
        return subprocess.CompletedProcess(args, 0, stdout=help_text, stderr="")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main() == 0
    assert capsys.readouterr().err == ""
    assert len(calls) == 1
    assert calls[0][0] == [executable, "export", "openvino", "--help"]
    assert calls[0][1]["timeout"] == 120
    assert calls[0][1]["check"] is False


def test_preflight_rejects_missing_venv_cli_even_if_global_path_contains_one(
    monkeypatch, tmp_path, capsys
):
    scripts = tmp_path / "venv-scripts"
    scripts.mkdir()
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / ("optimum-cli.exe" if sys.platform == "win32" else "optimum-cli")).touch()
    monkeypatch.setenv("PATH", str(foreign))
    monkeypatch.setattr(cli.sysconfig, "get_path", lambda key: str(scripts))
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("must not execute unrelated CLI"),
    )
    assert cli.main() == 1
    assert "missing from this Python environment" in capsys.readouterr().err


def test_preflight_fails_closed_on_cli_process_error(monkeypatch, tmp_path, capsys):
    _fixture_executable(monkeypatch, tmp_path)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args, 2, stdout="", stderr="missing OpenVINO registration"
        ),
    )
    assert cli.main() == 1
    assert "exited 2" in capsys.readouterr().err


def test_preflight_fails_closed_on_cli_timeout(monkeypatch, tmp_path, capsys):
    _fixture_executable(monkeypatch, tmp_path)

    def fake_run(args, **_kwargs):
        raise subprocess.TimeoutExpired(args, 120)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert cli.main() == 1
    assert "timed out" in capsys.readouterr().err.lower()
