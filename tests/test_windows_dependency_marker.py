from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_reinstalls_when_runtime_requirements_change():
    launcher = (ROOT / "start_server.bat").read_text(encoding="utf-8")

    assert "Get-FileHash" in launcher
    assert "$env:REQ_FILE" in launcher
    assert "$env:DEPS_MARKER" in launcher
    assert "$actual -ceq $saved" in launcher
    assert 'python -m pip install -r "%REQ_FILE%"' in launcher
    assert "Dependencies installed, but their version marker could not be updated" in launcher
    assert "python -m app.server %*\nexit /b %errorlevel%" in launcher


def test_launcher_registers_editable_source_package_for_cwd_independent_commands():
    launcher = (ROOT / "start_server.bat").read_text(encoding="utf-8")

    assert 'set "PROJECT_FILE=%~dp0pyproject.toml"' in launcher
    assert 'set "PROJECT_MARKER=%~dp0.source_package_installed"' in launcher
    assert "$env:PROJECT_FILE" in launcher
    assert "$env:PROJECT_MARKER" in launcher
    assert 'python -m pip install --no-deps --editable "%~dp0"' in launcher
    assert "Failed to register the InferBridge source package" in launcher


def test_launcher_keeps_conversion_dependencies_aligned_without_expanding_minimal_setup():
    launcher = (ROOT / "start_server.bat").read_text(encoding="utf-8")

    assert 'set "CONVERT_REQ_FILE=%~dp0requirements-convert.txt"' in launcher
    assert 'set "CONVERT_DEPS_MARKER=%~dp0.convert_deps_installed"' in launcher
    assert 'python -m pip show optimum-intel >nul 2>&1' in launcher
    assert "if defined CONVERT_PROFILE" in launcher
    assert "$env:CONVERT_REQ_FILE" in launcher
    assert "$env:CONVERT_DEPS_MARKER" in launcher
    assert 'python -m pip install -r "%CONVERT_REQ_FILE%"' in launcher
    assert "Failed to install model conversion dependencies" in launcher


def test_windows_setup_records_the_installed_requirements_profiles():
    installer = (ROOT / "setup" / "windows" / "install_deps.ps1").read_text(
        encoding="utf-8"
    )

    assert "$RequirementsPath" in installer
    assert "$DependencyMarker" in installer
    assert "Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256" in installer
    assert "Set-Content -LiteralPath $DependencyMarker" in installer
    assert "$ProjectFile" in installer
    assert "$ProjectMarker" in installer
    assert '"--no-deps", "--editable", $RepoRoot' in installer
    assert "Get-FileHash -LiteralPath $ProjectFile -Algorithm SHA256" in installer
    assert "Set-Content -LiteralPath $ProjectMarker" in installer
    assert "$ConversionRequirementsPath" in installer
    assert "$ConversionDependencyMarker" in installer
    assert "if ($WithConvert)" in installer
    assert "Get-FileHash -LiteralPath $ConversionRequirementsPath -Algorithm SHA256" in installer
    assert "Set-Content -LiteralPath $ConversionDependencyMarker" in installer
    assert "elseif ($CreatedVenv)" in installer
    assert "Remove-Item -LiteralPath $ConversionDependencyMarker" in installer
