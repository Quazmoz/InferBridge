from pathlib import Path

from app.brand import (
    APPLICATION_DESCRIPTION,
    ARTIFACT_PREFIX,
    DISPLAY_NAME,
    EXECUTABLE_BASENAME,
    LEGACY_DISPLAY_NAME,
    LEGACY_EXECUTABLE_BASENAME,
    LEGACY_REPOSITORY_NAME,
    REPOSITORY_NAME,
    REPOSITORY_OWNER,
)


def test_brand_constants_are_consistent():
    assert DISPLAY_NAME == "InferBridge"
    assert LEGACY_DISPLAY_NAME == "OpenVINO Windows LLM"
    assert EXECUTABLE_BASENAME == "InferBridge"
    assert LEGACY_EXECUTABLE_BASENAME == "OpenVINOWindowsLLM"
    assert REPOSITORY_OWNER == "Quazmoz"
    assert REPOSITORY_NAME == "InferBridge"
    assert LEGACY_REPOSITORY_NAME == "openvino-windows-llm"
    assert ARTIFACT_PREFIX == "InferBridge"
    assert "OpenVINO GenAI" in APPLICATION_DESCRIPTION


def test_python_distribution_and_legacy_commands_remain_compatible():
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "openvino-windows-llm"' in pyproject
    assert 'ov-llm = "app.server:main"' in pyproject
    assert 'ov-llm-desktop = "app.desktop_launcher:main"' in pyproject
    assert 'inferbridge = "app.server:main"' in pyproject
    assert 'inferbridge-desktop = "app.desktop_launcher:main"' in pyproject
