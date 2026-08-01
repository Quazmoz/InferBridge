import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PYTHON = ("3.11", "3.12", "3.13")


def test_supported_python_versions_are_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    installer = (ROOT / "setup" / "windows" / "install_deps.ps1").read_text(
        encoding="utf-8"
    )
    preflight = (ROOT / "setup" / "windows" / "check_hardware.ps1").read_text(
        encoding="utf-8"
    )

    assert project["project"]["requires-python"] == ">=3.11,<3.14"
    assert 'python-version: ["3.11", "3.12", "3.13"]' in ci
    assert '$SupportedPythonVersions = @("3.11", "3.12", "3.13")' in installer
    assert "Get-PythonVersion" in installer
    assert "$venvVersion -notin $SupportedPythonVersions" in installer

    for version in SUPPORTED_PYTHON:
        launcher = f'"py -{version}"'
        assert launcher in installer
        assert launcher in preflight

    assert '"py -3.14"' not in installer
    assert '"py -3.14"' not in preflight


def test_windows_ci_parses_source_setup_scripts() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for path in (
        "setup/windows/check_hardware.ps1",
        "setup/windows/install_deps.ps1",
        "setup/windows/setup_all.ps1",
    ):
        assert f'"{path}"' in ci
