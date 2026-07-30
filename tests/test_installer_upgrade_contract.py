from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_installer_reuses_identity_and_removes_only_immutable_runtime_payload():
    script = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")

    assert "AppId={{F94A3938-C943-4E6D-B482-852D4AAE06F8}" in script
    assert "UsePreviousAppDir=yes" in script
    assert "CloseApplications=yes" in script
    assert "CloseApplicationsFilter={#MyAppExeName},*.dll,*.pyd" in script
    assert "RestartApplications=yes" in script
    assert "runascurrentuser" in script

    install_delete = script.split("[InstallDelete]", 1)[1].split("[Files]", 1)[0]
    assert 'Name: "{app}\\_internal"' in install_delete
    assert 'Name: "{app}\\{#MyAppExeName}"' in install_delete
    assert 'Name: "{app}\\*.pyd"' in install_delete
    assert 'Name: "{app}\\*.dll"' in install_delete
    assert "{localappdata}\\OpenVINOWindowsLLM" not in install_delete
    assert "models" not in install_delete.lower()
    assert "cache" not in install_delete.lower()


def test_installer_detects_existing_per_user_or_per_machine_registration():
    script = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")

    assert "RegQueryStringValue(HKCU" in script
    assert "RegQueryStringValue(HKLM" in script
    assert "DisplayVersion" in script


def test_pyinstaller_collects_psutil_python_and_native_files_from_one_environment():
    spec = (ROOT / "packaging" / "openvino_windows_llm.spec").read_text(encoding="utf-8")

    assert 'collect_all("psutil")' in spec
    assert "psutil_binaries" in spec
    assert "binaries += psutil_binaries" in spec
    assert '"psutil",' in spec


def test_packaged_smoke_rejects_missing_or_duplicate_psutil_windows_extensions():
    script = (ROOT / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")

    assert 'Get-ChildItem $Root -Recurse -File -Filter "_psutil_windows*.pyd"' in script
    assert "$PsutilWindowsBinaries.Count -ne 1" in script
    assert "must contain exactly one psutil Windows extension" in script
    assert "must be contained under _internal" in script
