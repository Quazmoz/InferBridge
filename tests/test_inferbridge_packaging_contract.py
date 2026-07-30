from pathlib import Path

APP_ID = "{F94A3938-C943-4E6D-B482-852D4AAE06F8}"


def test_installer_preserves_upgrade_identity_and_migrates_branding():
    installer = Path("packaging/installer.iss").read_text(encoding="utf-8")
    assert f"AppId={{{APP_ID}" in installer
    assert '#define MyAppName "InferBridge"' in installer
    assert '#define MyAppExeName "InferBridge.exe"' in installer
    assert '#define MyLegacyAppExeName "OpenVINOWindowsLLM.exe"' in installer
    assert "CloseApplicationsFilter={#MyAppExeName},{#MyLegacyAppExeName}" in installer
    assert 'Name: "{app}\\{#MyLegacyAppExeName}"' in installer
    assert "{#MyLegacyAppName}" in installer
    assert "UsePreviousAppDir=yes" in installer
    assert "DefaultDirName={localappdata}\\Programs\\InferBridge" in installer
    assert "OutputBaseFilename=InferBridge-" in installer


def test_uninstall_preserves_data_by_default_and_removes_only_named_roots_on_request():
    installer = Path("packaging/installer.iss").read_text(encoding="utf-8")
    assert "IDYES" in installer
    assert "{localappdata}\\InferBridge" in installer
    assert "{localappdata}\\OpenVINOWindowsLLM" in installer


def test_pyinstaller_outputs_inferbridge_executable_and_directory():
    spec = Path("packaging/openvino_windows_llm.spec").read_text(encoding="utf-8")
    assert spec.count('name="InferBridge"') == 2
    assert 'name="OpenVINOWindowsLLM"' not in spec
