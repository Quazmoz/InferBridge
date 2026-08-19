from pathlib import Path

import pytest

from app.release_models import SemanticVersion

ROOT = Path(__file__).resolve().parent.parent


def test_installer_downgrade_guard_uses_semantic_prerelease_ordering():
    script = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")

    assert "function CompareSemanticVersions" in script
    assert "function VersionPrerelease" in script
    assert "function ComparePrereleaseIdentifier" in script
    assert "function IsNumericIdentifier" in script
    assert "function StripBuildMetadata" in script
    assert "CompareSemanticVersions(Existing, '{#MyAppVersion}') > 0" in script
    assert "CompareCoreVersions" not in script


@pytest.mark.parametrize(
    ("newer", "older"),
    [
        ("0.9.6-beta.2", "0.9.6-beta.1"),
        ("0.9.6-rc.1", "0.9.6-beta.9"),
        ("0.9.6", "0.9.6-rc.9"),
        ("0.9.7-beta.1", "0.9.6"),
    ],
)
def test_installer_downgrade_guard_scenarios_follow_release_semver(newer, older):
    assert SemanticVersion.parse(newer) > SemanticVersion.parse(older)


def test_build_metadata_does_not_change_release_precedence():
    assert SemanticVersion.parse("0.9.6-beta.2+build.7") == SemanticVersion.parse(
        "0.9.6-beta.2+build.8"
    )
