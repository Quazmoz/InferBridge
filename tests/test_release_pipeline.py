import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.build_info import load_build_info
from app.release_models import ReleaseManifest, artifact_filename
from app.version import __version__
from scripts.release_tools import (
    scan_release_path,
    verify_checksums,
    verify_release_requirements,
    write_checksums,
)


def test_build_info_fallback_uses_canonical_version(tmp_path):
    info = load_build_info(tmp_path)
    assert info.application_version == __version__
    assert info.build_channel == "development"
    assert not info.source_tree_clean


def test_release_output_directories_are_git_ignored():
    # The release pipeline (scripts/build_release.ps1) enforces a clean working
    # tree before building and writes its output to build/, dist/, and
    # artifacts/. If those generated directories are not ignored, a second run
    # fails its own clean-tree gate on leftover output. Guard the ignore rules.
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    patterns = {line.strip().strip("/") for line in gitignore.splitlines()}
    for generated in ("build", "dist", "artifacts"):
        assert generated in patterns, f"{generated}/ must be git-ignored for a re-runnable release"


def test_release_output_secret_file_is_rejected(tmp_path):
    (tmp_path / ".env").write_text("OV_LLM_API_KEY=secret-value")
    with pytest.raises(RuntimeError, match="Forbidden release file"):
        scan_release_path(tmp_path)


def test_release_output_model_cache_is_rejected(tmp_path):
    model = tmp_path / "models" / "weights.bin"
    model.parent.mkdir()
    model.write_bytes(b"x")
    with pytest.raises(RuntimeError, match="Model or cache"):
        scan_release_path(tmp_path)


def test_zip_path_traversal_is_rejected(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "bad")
    with pytest.raises(RuntimeError, match="Unsafe archive path"):
        scan_release_path(archive)


def test_checksums_are_generated_and_verified(tmp_path):
    version = "0.4.0"
    (tmp_path / artifact_filename(version, "release_notes")).write_text("notes")
    (tmp_path / artifact_filename(version, "portable")).write_bytes(b"portable")
    checksum = write_checksums(tmp_path, version)
    verify_checksums(checksum)
    assert artifact_filename(version, "portable") in checksum.read_text()


def test_manifest_schema_rejects_unknown_fields(tmp_path):
    payload = {"schema_version": 1, "unexpected": True}
    with pytest.raises(ValidationError):
        ReleaseManifest.model_validate(payload)


def test_zip_secret_and_model_cache_entries_are_rejected(tmp_path):
    secret_archive = tmp_path / "secret.zip"
    with zipfile.ZipFile(secret_archive, "w") as handle:
        handle.writestr("config/.env", "OV_LLM_API_KEY=secret-value")
    with pytest.raises(RuntimeError, match="Forbidden release archive entry"):
        scan_release_path(secret_archive)

    model_archive = tmp_path / "model.zip"
    with zipfile.ZipFile(model_archive, "w") as handle:
        handle.writestr("app/models/weights.bin", b"model")
    with pytest.raises(RuntimeError, match="Model or cache"):
        scan_release_path(model_archive)


def test_release_text_with_local_user_path_is_rejected(tmp_path):
    report = tmp_path / "report.json"
    report.write_text('{"path": "C:\\Users\\builder\\secret"}')
    with pytest.raises(RuntimeError, match="Local user path"):
        scan_release_path(report)


def test_release_text_with_posix_home_path_is_rejected(tmp_path):
    report = tmp_path / "notes.txt"
    report.write_text("Built at /home/builder/work/openvino-windows-llm on CI.\n")
    with pytest.raises(RuntimeError, match="Local user path"):
        scan_release_path(report)


def test_release_text_with_homepage_url_is_allowed(tmp_path):
    # A public URL that happens to contain a "/Home/" path segment (common in
    # bundled third-party license files) must not be mistaken for a local path.
    notices = tmp_path / "THIRD-PARTY-NOTICES.txt"
    notices.write_text(
        "See https://sites.google.com/site/gaviotachessengine/Home/endgame for details.\n"
    )
    scan_release_path(notices)


def _bundle_file(root: Path, relative: str, content: str) -> None:
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def test_certifi_ca_bundle_is_allowed(tmp_path):
    # certifi's public CA store is required for TLS and must not be forbidden.
    _bundle_file(
        tmp_path,
        "_internal/certifi/cacert.pem",
        "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n",
    )
    scan_release_path(tmp_path)


def test_non_ca_pem_is_still_rejected(tmp_path):
    _bundle_file(tmp_path, "_internal/app/server.pem", "-----BEGIN PRIVATE KEY-----\n")
    with pytest.raises(RuntimeError, match="Forbidden release file"):
        scan_release_path(tmp_path)


def test_third_party_source_with_example_paths_is_allowed(tmp_path):
    # transformers conversion scripts hardcode upstream maintainers' example
    # paths and tokenizer terminology; that is not a leak in our release.
    _bundle_file(
        tmp_path,
        "_internal/transformers/models/git/convert_git_to_pytorch.py",
        'default = "/Users/nielsrogge/Documents/GIT/git_large_model.pt"\n'
        'TOKEN = "hf_94wBhPGp6KrrTH3KDchhKpRxZwd6dmHWLL"\n',
    )
    _bundle_file(
        tmp_path,
        "_internal/transformers/pipelines/token_classification.py",
        "class TokenClassificationPipeline: ...\n",
    )
    scan_release_path(tmp_path)


def test_our_bundled_secret_value_is_still_rejected(tmp_path):
    # Application-owned files (directly under _internal or under app/runtime/web)
    # are still deep-scanned for secret values.
    _bundle_file(
        tmp_path,
        "_internal/build-info.json",
        '{"hf_token": "hf_94wBhPGp6KrrTH3KDchhKpRxZwd6dmHWLL"}\n',
    )
    with pytest.raises(RuntimeError, match="Secret-like value"):
        scan_release_path(tmp_path)


def test_our_local_path_in_bundle_is_still_rejected(tmp_path):
    _bundle_file(tmp_path, "_internal/app/generated.json", '{"cwd": "/home/builder/secret"}\n')
    with pytest.raises(RuntimeError, match="Local user path"):
        scan_release_path(tmp_path)


def test_tray_owned_smoke_regressions_are_guarded():
    root = Path(__file__).resolve().parents[1]
    runtime = (root / "app" / "tray_runtime.py").read_text(encoding="utf-8")
    polling = (root / "app" / "tray_polling.py").read_text(encoding="utf-8")
    smoke = (root / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")
    assert "headlss_seconds" not in runtime
    assert "self.args.headless_seconds" in runtime
    assert 'name == "quit"' in polling
    assert "--headless-seconds" in smoke
    assert "validate_api_contract.py" in smoke


def test_release_requirements_are_exactly_pinned():
    root = Path(__file__).resolve().parents[1]
    verify_release_requirements(root / "requirements" / "release.txt")


def test_build_script_stages_model_library_manifest():
    """The release build must copy and validate the model library manifest."""
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    assert "model-library-manifest.json" in script, (
        "build_release.ps1 must stage model-library-manifest.json as a release asset"
    )
    assert "validate_model_library_manifest.py" in script, (
        "build_release.ps1 must validate the model library manifest before staging"
    )
    assert "$Produced = @($InventoryJson, $InventoryText)" in script, (
        "the release summary must record both dependency provenance artifacts"
    )


def test_source_model_library_manifest_exists():
    """The curated model library manifest must exist at the repository root."""
    root = Path(__file__).resolve().parents[1]
    manifest = root / "model_library_manifest.json"
    assert manifest.is_file(), (
        "model_library_manifest.json must exist at repository root for release staging"
    )
