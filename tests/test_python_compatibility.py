import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PYTHON = ("3.11", "3.12", "3.13", "3.14")


def test_supported_python_versions_are_consistent() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    installer = (ROOT / "setup" / "windows" / "install_deps.ps1").read_text(encoding="utf-8")
    preflight = (ROOT / "setup" / "windows" / "check_hardware.ps1").read_text(encoding="utf-8")

    assert project["project"]["requires-python"] == ">=3.11,<3.15"
    assert 'python-version: ["3.11", "3.12", "3.13", "3.14"]' in ci
    supported_versions = '$SupportedPythonVersions = @("3.11", "3.12", "3.13", "3.14")'
    assert supported_versions in installer
    assert supported_versions in preflight
    assert "Get-PythonVersion" in installer
    assert "Get-PythonVersion" in preflight
    assert "$venvVersion -notin $SupportedPythonVersions" in installer
    assert "$version -in $SupportedPythonVersions" in preflight
    assert "UNSUPPORTED version detected" in preflight

    for version in SUPPORTED_PYTHON:
        launcher = f'"py -{version}"'
        assert launcher in installer
        assert launcher in preflight

    assert '"py -3.15"' not in installer
    assert '"py -3.15"' not in preflight


def test_windows_ci_parses_source_setup_scripts() -> None:
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    for path in (
        "setup/windows/check_hardware.ps1",
        "setup/windows/install_deps.ps1",
        "setup/windows/setup_all.ps1",
    ):
        assert f'"{path}"' in ci
