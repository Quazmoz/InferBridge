from pathlib import Path

import pytest

from scripts.release_scan import verify_native_distribution

ROOT = Path(__file__).resolve().parent.parent


def test_installer_reuses_identity_and_removes_only_immutable_runtime_payload():
    script = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")

    assert "AppId={{F94A3938-C943-4E6D-B482-852D4AAE06F8}" in script
    assert "UsePreviousAppDir=yes" in script
    assert "CloseApplications=yes" in script
    assert "CloseApplicationsFilter={#MyAppExeName},{#MyLegacyAppExeName},*.dll,*.pyd" in script
    assert "RestartApplications=yes" in script
    assert "Flags: nowait postinstall skipifsilent" in script
    assert "runascurrentuser" not in script

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


def test_pyinstaller_collects_openvino_tokenizer_distribution_explicitly():
    spec = (ROOT / "packaging" / "openvino_windows_llm.spec").read_text(encoding="utf-8")

    assert '"openvino_tokenizers"' in spec
    assert '"openvino-tokenizers",' in spec


def _native_distribution(tmp_path: Path) -> Path:
    (tmp_path / "InferBridge.exe").write_bytes(b"exe")
    native = tmp_path / "_internal" / "native"
    native.mkdir(parents=True)
    for name in (
        "openvino.dll",
        "openvino_tokenizers.dll",
        "openvino_intel_cpu_plugin.dll",
        "openvino_intel_gpu_plugin.dll",
        "openvino_intel_npu_plugin.dll",
    ):
        (native / name).write_bytes(b"dll")
    return tmp_path


def test_native_release_gate_requires_one_psutil_windows_extension(tmp_path):
    root = _native_distribution(tmp_path)
    psutil_dir = root / "_internal" / "psutil"
    psutil_dir.mkdir()
    (psutil_dir / "_psutil_windows.cp313-win_amd64.pyd").write_bytes(b"pyd")

    verify_native_distribution(root)

    (psutil_dir / "_psutil_windows_duplicate.pyd").write_bytes(b"pyd")
    with pytest.raises(RuntimeError, match="exactly one psutil Windows extension"):
        verify_native_distribution(root)


@pytest.mark.parametrize(
    "relative_path",
    (
        "_psutil_windows.pyd",
        "_internal-old/psutil/_psutil_windows.pyd",
    ),
)
def test_native_release_gate_rejects_psutil_extension_outside_internal(tmp_path, relative_path):
    root = _native_distribution(tmp_path)
    extension = root / relative_path
    extension.parent.mkdir(parents=True, exist_ok=True)
    extension.write_bytes(b"pyd")

    with pytest.raises(RuntimeError, match="must be contained under _internal"):
        verify_native_distribution(root)


def test_packaged_smoke_rejects_missing_duplicate_or_sibling_psutil_extensions():
    script = (ROOT / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")

    assert 'Get-ChildItem $Root -Recurse -File -Filter "_psutil_windows*.pyd"' in script
    assert "$PsutilWindowsBinaries.Count -ne 1" in script
    assert "must contain exactly one psutil Windows extension" in script
    assert "$InternalPrefix = $InternalRoot + [IO.Path]::DirectorySeparatorChar" in script
    assert ".StartsWith($InternalPrefix, [StringComparison]::OrdinalIgnoreCase)" in script
    assert "must be contained under _internal" in script


def test_packaged_smoke_executes_openvino_native_preflight():
    script = (ROOT / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")

    assert 'Start-Process -FilePath $Exe -ArgumentList "--native-smoke"' in script
    assert "-PassThru -Wait -WindowStyle Hidden" in script
    assert "Packaged OpenVINO native runtime smoke test failed" in script
