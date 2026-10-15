from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_installer_launches_client_but_probes_engine_and_cleans_old_payload():
    text = (ROOT / "packaging/installer.iss").read_text(encoding="utf-8")
    native = text.split("#ifdef NativeFrontend", 1)[1].split("#else", 1)[0]
    assert 'FileExists(SourceRoot + "\\native\\InferBridge.Native.exe")' in native
    assert '#define MyLaunchExeName "native\\InferBridge.Native.exe"' in native
    assert "#define MyLaunchExeName MyAppExeName" in text
    for section in ("[Icons]", "[Run]"):
        entries = text.split("\n" + section + "\n", 1)[1].split("\n[", 1)[0]
        assert "{app}\\{#MyLaunchExeName}" in entries
    probe = text.split("function VerifyInstalledRuntime()", 1)[1].split("function ", 1)[0]
    assert "{app}\\{#MyAppExeName}" in probe
    assert "--bootstrap-smoke" in probe
    assert 'Type: filesandordirs; Name: "{app}\\native"' in text
    assert "*.pyd,InferBridge.Native.exe" in text


def test_native_evaluation_build_keeps_engine_smoke_and_native_contract_gates():
    text = (ROOT / "scripts/build_native_installer.ps1").read_text(encoding="utf-8")
    assert "InferBridge.Native.ContractTests.csproj" in text
    assert '"build_dev_package.ps1"' in text
    assert "SkipSmokeTest" not in text
    assert "--self-contained true -p:Platform=x64" in text
    assert "release_tools.py scan --path $BuiltRoot" in text
    assert "/DNativeFrontend" in text
    assert "Get-FileHash -LiteralPath $Installer -Algorithm SHA256" in text
    assert "publish_release.ps1" not in text
