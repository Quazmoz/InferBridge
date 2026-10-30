"""Guard the real Optimum/OpenVINO export CLI used by model_converter."""

import pytest

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
