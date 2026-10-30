from pathlib import Path

import pytest

from app.config import BASE_DIR, Settings, _bool_env, _resolve
from runtime.device_check import DEFAULT_DEVICE

_ENV_VARS = [
    "OV_LLM_HOST",
    "OV_LLM_PORT",
    "OV_LLM_DEVICE",
    "OV_LLM_MODELS_FILE",
    "OV_LLM_MODELS_DIR",
    "OV_LLM_CACHE_DIR",
    "OV_LLM_BENCHMARK_RESULTS",
    "OV_LLM_MODEL",
    "OV_LLM_API_KEY",
    "OV_LLM_MOCK",
    "OV_LLM_AUTO_CONVERT",
    "OV_LLM_CORS_ORIGINS",
    "OV_LLM_RATE_LIMIT",
    "OV_LLM_MAX_REQUEST_BODY_MB",
]


@pytest.fixture()
def clean_env(monkeypatch):
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_from_env_defaults(clean_env):
    s = Settings.from_env()
    assert s.host == "127.0.0.1"
    assert s.port == 8000
    assert s.device == DEFAULT_DEVICE
    assert s.models_file == BASE_DIR / "models.json"
    assert s.models_dir == BASE_DIR / "models" / "openvino"
    assert s.cache_dir == BASE_DIR / "models" / "cache"
    assert s.default_model is None
    assert s.api_key is None
    assert s.force_mock is False
    assert s.auto_convert is False
    assert s.cors_origins == ""
    assert s.max_request_body_mb == 40


def test_from_env_parses_overrides(clean_env):
    clean_env.setenv("OV_LLM_HOST", "0.0.0.0")
    clean_env.setenv("OV_LLM_PORT", "9001")
    clean_env.setenv("OV_LLM_DEVICE", "npu")  # lower-cased input
    clean_env.setenv("OV_LLM_CACHE_DIR", "custom_cache")
    clean_env.setenv("OV_LLM_MODEL", "  tinyllama-1.1b-chat-fp16  ")  # trimmed
    clean_env.setenv("OV_LLM_API_KEY", " sk-secret ")
    clean_env.setenv("OV_LLM_MOCK", "yes")
    clean_env.setenv("OV_LLM_AUTO_CONVERT", "true")
    clean_env.setenv("OV_LLM_CORS_ORIGINS", "http://localhost:3000,http://localhost:8080")
    clean_env.setenv("OV_LLM_MAX_REQUEST_BODY_MB", "48")

    s = Settings.from_env()
    assert s.host == "0.0.0.0"
    assert s.port == 9001
    assert s.device == "NPU"
    assert s.cache_dir == BASE_DIR / "custom_cache"
    assert s.default_model == "tinyllama-1.1b-chat-fp16"
    assert s.api_key == "sk-secret"
    assert s.force_mock is True
    assert s.auto_convert is True
    assert s.cors_origins == "http://localhost:3000,http://localhost:8080"
    assert s.max_request_body_mb == 48


def test_from_env_normalizes_composite_device(clean_env):
    clean_env.setenv("OV_LLM_DEVICE", " auto:npu, gpu, cpu ")
    s = Settings.from_env()
    assert s.device == "AUTO:NPU,GPU,CPU"


def test_from_env_blank_optional_values_become_none(clean_env):
    clean_env.setenv("OV_LLM_MODEL", "   ")
    clean_env.setenv("OV_LLM_API_KEY", "")
    s = Settings.from_env()
    assert s.default_model is None
    assert s.api_key is None


def test_validate_warns_for_unauthenticated_wildcard_cors(tmp_path):
    models_file = tmp_path / "models.json"
    models_file.write_text("{}", encoding="utf-8")
    settings = Settings(models_file=models_file, cors_origins="*", api_key=None)
    warnings = settings.validate({})
    assert any("Wildcard CORS" in warning for warning in warnings)


def test_resolve_relative_against_base_dir():
    assert _resolve("models.json") == BASE_DIR / "models.json"


def test_resolve_absolute_kept_as_is(tmp_path):
    abs_path = tmp_path / "elsewhere.json"
    assert _resolve(str(abs_path)) == Path(str(abs_path))


def test_bool_env_truthy_and_falsy(monkeypatch):
    for truthy in ("1", "true", "YES", "On"):
        monkeypatch.setenv("FLAG", truthy)
        assert _bool_env("FLAG") is True
    for falsy in ("0", "false", "no", "", "maybe"):
        monkeypatch.setenv("FLAG", falsy)
        assert _bool_env("FLAG") is False


def test_replace_drops_none_and_applies_overrides(clean_env):
    s = Settings.from_env()
    out = s.replace(port=1234, device=None, force_mock=True)
    assert out.port == 1234
    assert out.device == s.device  # None ignored
    assert out.force_mock is True
    assert s.port == 8000  # original is frozen / unchanged


def test_validate_warns_for_wildcard_host_without_api_key():
    warnings = Settings(host="0.0.0.0", api_key=None).validate()

    assert any("OV_LLM_API_KEY is not set" in warning for warning in warnings)


def test_validate_warns_for_ipv6_wildcard_host_without_api_key():
    warnings = Settings(host="::", api_key=None).validate()

    assert any("OV_LLM_API_KEY is not set" in warning for warning in warnings)


def test_validate_does_not_warn_for_localhost_or_wildcard_with_api_key():
    localhost_warnings = Settings(host="127.0.0.1", api_key=None).validate()
    wildcard_warnings = Settings(host="0.0.0.0", api_key="sk-secret").validate()

    assert not any("OV_LLM_API_KEY is not set" in warning for warning in localhost_warnings)
    assert not any("OV_LLM_API_KEY is not set" in warning for warning in wildcard_warnings)
