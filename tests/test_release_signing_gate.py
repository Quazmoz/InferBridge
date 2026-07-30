import json
import zipfile
from pathlib import Path

import pytest

from scripts.verify_release_signing import SigningVerificationError, verify_release_signing


def _write_release(tmp_path: Path, *, installer: bool, launcher: bool) -> None:
    version = "9.9.9"
    installer_name = f"InferBridge-{version}-windows-x64-installer.exe"
    portable_name = f"InferBridge-{version}-windows-x64-portable.zip"
    (tmp_path / installer_name).write_bytes(b"installer")
    with zipfile.ZipFile(tmp_path / portable_name, "w") as archive:
        archive.writestr(f"InferBridge-{version}/InferBridge.exe", b"launcher")
    manifest = {
        "artifacts": [
            {
                "type": "installer",
                "filename": installer_name,
                "signed": installer,
                "signature_verified": installer,
            },
            {
                "type": "portable",
                "filename": portable_name,
                "contained_launcher_signed": launcher,
                "contained_launcher_signature_verified": launcher,
            },
        ]
    }
    summary = {
        "installer_signature_verified": installer,
        "launcher_signature_verified": launcher,
    }
    (tmp_path / f"InferBridge-{version}-release-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (tmp_path / f"InferBridge-{version}-release-summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )


def test_unsigned_release_needs_no_signtool(tmp_path, monkeypatch):
    _write_release(tmp_path, installer=False, launcher=False)
    monkeypatch.delenv("OV_LLM_SIGNTOOL_PATH", raising=False)
    assert verify_release_signing(tmp_path, "9.9.9") is False


def test_unsigned_release_is_rejected_when_signature_is_required(tmp_path, monkeypatch):
    _write_release(tmp_path, installer=False, launcher=False)
    monkeypatch.delenv("OV_LLM_SIGNTOOL_PATH", raising=False)
    with pytest.raises(SigningVerificationError, match="requires verified Authenticode"):
        verify_release_signing(tmp_path, "9.9.9", require_signed=True)


def test_partial_signed_claim_is_rejected(tmp_path):
    _write_release(tmp_path, installer=True, launcher=False)
    with pytest.raises(SigningVerificationError, match="both installer and launcher"):
        verify_release_signing(tmp_path, "9.9.9")


def test_signed_claim_requires_signtool(tmp_path, monkeypatch):
    _write_release(tmp_path, installer=True, launcher=True)
    monkeypatch.setattr("scripts.verify_release_signing.shutil.which", lambda _name: None)
    monkeypatch.delenv("OV_LLM_SIGNTOOL_PATH", raising=False)
    with pytest.raises(SigningVerificationError, match="require signtool"):
        verify_release_signing(tmp_path, "9.9.9")


def test_signed_claim_runs_exact_verification_for_both_artifacts(tmp_path, monkeypatch, capsys):
    _write_release(tmp_path, installer=True, launcher=True)
    tool = tmp_path / "signtool.exe"
    tool.write_bytes(b"tool")
    calls = []

    class Result:
        returncode = 0
        stdout = "Successfully verified"

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)
        return Result()

    monkeypatch.setenv("OV_LLM_SIGNTOOL_PATH", str(tool))
    monkeypatch.setattr("scripts.verify_release_signing.subprocess.run", fake_run)
    assert verify_release_signing(tmp_path, "9.9.9") is True
    assert len(calls) == 2
    assert all(call[1:4] == ["verify", "/pa", "/all"] for call in calls)
    assert "Successfully verified" in capsys.readouterr().out


def test_failed_signtool_verification_blocks_signed_claim(tmp_path, monkeypatch):
    _write_release(tmp_path, installer=True, launcher=True)
    tool = tmp_path / "signtool.exe"
    tool.write_bytes(b"tool")

    class Result:
        returncode = 1
        stdout = "SignTool Error: No signature found."

    monkeypatch.setenv("OV_LLM_SIGNTOOL_PATH", str(tool))
    monkeypatch.setattr(
        "scripts.verify_release_signing.subprocess.run", lambda *_args, **_kwargs: Result()
    )
    with pytest.raises(SigningVerificationError, match="exit code 1"):
        verify_release_signing(tmp_path, "9.9.9")
