import json
from pathlib import Path

import pytest

from app.release_models import artifact_filename
from scripts.release_manifest import build_manifest
from scripts.release_tools import write_checksums
from scripts.verify_release_provenance import (
    expected_release_filenames,
    verify_release_provenance,
)

COMMIT = "a" * 40


def _artifacts(tmp_path: Path, *, commit: str = COMMIT) -> tuple[Path, Path]:
    version = "0.6.2"
    source = tmp_path / "source.json"
    source.write_bytes(b'{"catalog":"bound"}\n')
    for name in expected_release_filenames(version):
        if name.endswith("-release-manifest.json") or name.endswith("-checksums.txt"):
            continue
        (tmp_path / name).write_bytes(
            source.read_bytes() if name == "model-library-manifest.json" else b"x"
        )
    manifest = build_manifest(
        tmp_path,
        version=version,
        channel="stable",
        published_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        commit=commit,
        clean=True,
        signed_types=set(),
        inventory_filename=f"InferBridge-{version}-dependency-inventory.json",
    )
    (tmp_path / artifact_filename(version, "manifest")).write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    summary_path = tmp_path / f"InferBridge-{version}-release-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "version": version,
                "channel": "stable",
                "source_commit": commit,
                "source_tree_clean": True,
                "artifacts": sorted(
                    expected_release_filenames(version)
                    - {
                        artifact_filename(version, "checksums"),
                        summary_path.name,
                    }
                ),
            }
        ),
        encoding="utf-8",
    )
    write_checksums(tmp_path, version)
    return source, summary_path


def _verify(tmp_path: Path, source: Path, **kwargs) -> None:
    verify_release_provenance(
        tmp_path,
        version=kwargs.get("version", "0.6.2"),
        channel=kwargs.get("channel", "stable"),
        expected_commit=kwargs.get("commit", COMMIT),
        source_model_manifest=source,
    )


def test_matching_release_provenance_succeeds(tmp_path):
    source, _ = _artifacts(tmp_path)
    _verify(tmp_path, source)


@pytest.mark.parametrize("field", ["version", "channel", "source_commit", "source_tree_clean"])
def test_summary_provenance_mismatch_fails(tmp_path, field):
    source, summary_path = _artifacts(tmp_path)
    summary = json.loads(summary_path.read_text())
    summary[field] = {
        "version": "9.9.9",
        "channel": "beta",
        "source_commit": "b" * 40,
        "source_tree_clean": False,
    }[field]
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    write_checksums(tmp_path, "0.6.2")
    with pytest.raises(RuntimeError):
        _verify(tmp_path, source)


def test_head_commit_mismatch_fails(tmp_path):
    source, _ = _artifacts(tmp_path)
    with pytest.raises(RuntimeError, match="different source commit"):
        _verify(tmp_path, source, commit="b" * 40)


def test_missing_or_modified_model_manifest_fails(tmp_path):
    source, _ = _artifacts(tmp_path)
    (tmp_path / "model-library-manifest.json").write_bytes(b"different")
    write_checksums(tmp_path, "0.6.2")
    with pytest.raises(RuntimeError, match="differs"):
        _verify(tmp_path, source)


def test_missing_expected_artifact_fails(tmp_path):
    source, _ = _artifacts(tmp_path)
    (tmp_path / artifact_filename("0.6.2", "portable")).unlink()
    with pytest.raises(RuntimeError, match="incomplete"):
        _verify(tmp_path, source)


def test_publisher_validates_before_tag_and_never_replaces_manifest():
    script = (Path(__file__).parents[1] / "scripts" / "publish_release.ps1").read_text()
    validation = script.index("verify_release_provenance.py")
    tagging = script.index("git tag -a")
    assert validation < tagging
    assert "Copy-Item $LibraryManifestSource" not in script
    assert "release_tools.py checksums" not in script
