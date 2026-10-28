from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_smoke_supports_installed_and_portable_modes():
    smoke = (ROOT / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")
    release = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8")

    assert '[ValidateSet("installed", "portable")]' in smoke
    assert '$IsPortable = $ExpectedMode -eq "portable"' in smoke
    assert "Installed-mode smoke test refuses a distribution containing portable.flag." in smoke
    assert "Portable-mode smoke test requires portable.flag" in smoke
    assert "$Release.installation_mode -ne $ExpectedMode" in smoke

    assert "Run installed-mode packaged mock smoke test" in release
    assert "-ExpectedMode installed" in release
    assert "Run portable packaged mock smoke test" in release
    assert "-ExpectedMode portable" in release
    assert "packaged_installed_mode_smoke_test" in release
    assert "packaged_portable_mode_smoke_test" in release


def test_packaged_smoke_certifies_configured_port_not_just_selected_port():
    smoke = (ROOT / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")

    assert "$env:OV_LLM_PORT = [string]$RequestedPort" in smoke
    assert "$Metadata.port -ne $RequestedPort" in smoke
    assert "Packaged desktop ignored OV_LLM_PORT" in smoke
    assert "Remove-Item Env:OV_LLM_PORT" in smoke
    assert '"--data-dir"' in smoke
    assert '$ExpectedMode -eq "portable"' in smoke


def test_packaged_smoke_prints_redacted_tray_diagnostics_on_failure() -> None:
    # The tray logs its startup exception only inside the temporary data directory,
    # which the smoke deletes; a failure must surface those logs before cleanup.
    smoke = (ROOT / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")
    catch = smoke.index("catch {\n    $FailureReason")
    cleanup = smoke.index("Remove-Item $Data -Recurse -Force")
    assert catch < cleanup
    assert "Write-PackagedStartupDiagnostics -DataRoot $Data" in smoke
    assert "-RedirectStandardError $TrayStderr" in smoke
    assert '$TrayStderr = "$Data.tray-stderr.log"' in smoke
    for secret_pattern in (
        "token|nonce|secret|password",
        "instance-nonce|control-token",
        "Bearer",
        "hf_",
    ):
        assert secret_pattern in smoke
    assert 'Join-Path $DataRoot "logs"' in smoke
    assert '"desktop-instance.json"' in smoke
