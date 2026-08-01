from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_dev_package.ps1"


def test_dev_package_is_explicitly_incremental_and_non_release():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "build\\dev-package" in text
    assert "build\\dev-environments" in text
    assert "release_environment.py fingerprint" in text
    assert "release_environment.py validate" in text
    assert "--no-deps --no-build-isolation" in text
    assert "PyInstaller --noconfirm --workpath" in text
    assert "PyInstaller --noconfirm --clean" not in text
    assert "smoke_test_packaged.ps1" in text
    assert "-ExpectedMode installed" in text
    assert "unsigned and is not a release artifact" in text


def test_dev_package_does_not_bypass_or_redefine_release_publication():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "build_release.ps1" not in text
    assert "publish_release.ps1" not in text
    assert "Sign-AndVerify" not in text
    assert "Compile Inno Setup installer" not in text
    assert "create_portable_archive.py" not in text
    assert "Generate SHA-256 checksums" not in text
    assert "release_tools.py manifest" not in text
