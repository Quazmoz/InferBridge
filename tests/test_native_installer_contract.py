from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_installer_launches_client_but_probes_engine_and_cleans_old_payload():
    text = (ROOT / "packaging/installer.iss").read_text(encoding="utf-8")
    assert 'FileExists(SourceRoot + "\\native\\InferBridge.Native.exe")' in text
    assert '#define MyLaunchExeName "native\\InferBridge.Native.exe"' in text
    assert "#define MyLaunchExeName MyAppExeName" not in text
    assert '#define MyInstallerPlatform "windows-x64"' in text
    assert '#define MyInstallerPlatform "native-windows-x64"' in text
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
    dev_package = (ROOT / "scripts/build_dev_package.ps1").read_text(encoding="utf-8")
    assert "--self-contained true -p:Platform=x64" in dev_package
    assert "InferBridge.Native.exe" in text
    assert "coreclr.dll" in text
    assert "release_tools.py scan --path $BuiltRoot" in text
    assert "/DNativeFrontend /DNativeEvaluation" in text
    assert "Get-FileHash -LiteralPath $Installer -Algorithm SHA256" in text
    assert "publish_release.ps1" not in text


def test_normal_release_installer_and_portable_launch_winui_by_default():
    release = (ROOT / "scripts/build_release.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "Publish self-contained native Windows frontend" in release
    assert "windows-native/InferBridge.Native/InferBridge.Native.csproj" in release
    assert "Native WinUI launcher is missing" in release
    assert '"/DAppIconPath=$AppIcon" /DNativeFrontend packaging/installer.iss' in release
    assert "native\\InferBridge.Native.exe for the Windows app" in release
    assert 'dotnet-version: "10.0.x"' in workflow
    assert "artifact-configuration-slug: native" in workflow
    assert "Install signed native WinUI launcher" in workflow
    assert "Run native backend contract tests" in release
    assert "coreclr.dll" in (ROOT / "packaging/installer.iss").read_text(encoding="utf-8")


def test_engine_only_development_bundle_is_not_mistaken_for_a_windows_app():
    script = (ROOT / "scripts/build_dev_package.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $BuiltRoot "native"' in script
    assert "dotnet publish windows-native/InferBridge.Native/InferBridge.Native.csproj" in script
    assert "Native-first development package completed" in script
    assert "InferBridge.Native.runtimeconfig.json" in script
