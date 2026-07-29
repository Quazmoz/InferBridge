from app.config import Settings, _device_env, _int_env


def test_from_env_invalid_numeric_values_fall_back(monkeypatch, caplog):
    monkeypatch.setenv("OV_LLM_PORT", "not-a-port")
    monkeypatch.setenv("OV_LLM_RATE_LIMIT", "-2")
    monkeypatch.setenv("OV_LLM_MAX_REQUEST_BODY_MB", "0")

    settings = Settings.from_env()

    assert settings.port == 8000
    assert settings.rate_limit == 0
    assert settings.max_request_body_mb == 40
    assert "OV_LLM_PORT" in caplog.text
    assert "OV_LLM_RATE_LIMIT" in caplog.text
    assert "OV_LLM_MAX_REQUEST_BODY_MB" in caplog.text


def test_from_env_invalid_device_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("OV_LLM_DEVICE", "NPU;DROP")

    settings = Settings.from_env()

    assert settings.device == "NPU"
    assert "OV_LLM_DEVICE" in caplog.text


def test_int_env_enforces_optional_bounds(monkeypatch):
    monkeypatch.setenv("COUNT", "11")
    assert _int_env("COUNT", 5, minimum=1, maximum=10) == 5
    monkeypatch.setenv("COUNT", "0")
    assert _int_env("COUNT", 5, minimum=1, maximum=10) == 5
    monkeypatch.setenv("COUNT", "7")
    assert _int_env("COUNT", 5, minimum=1, maximum=10) == 7


def test_device_env_uses_default_for_blank(monkeypatch):
    monkeypatch.setenv("DEVICE", " ")
    assert _device_env("DEVICE", "CPU") == "CPU"
