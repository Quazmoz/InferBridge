"""Release channel auto-resolution and actionable channel/version errors."""

from pathlib import Path

import pytest

from scripts import release_tools
from scripts.release_manifest import (
    channel_for_branch,
    channel_for_version,
    resolve_channel,
    validate_version,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    ("version", "channel"),
    [
        ("1.1.0", "stable"),
        ("1.1.0-beta.1", "beta"),
        ("1.1.0-rc.2", "beta"),
        ("1.1.1-dev.1", "nightly"),
        ("1.1.1-alpha.1", "nightly"),
    ],
)
def test_auto_channel_follows_version(version, channel):
    assert channel_for_version(version) == channel
    assert resolve_channel(version, "auto") == channel
    # Whatever the branch, auto never produces a channel the version refuses.
    for branch in ("refs/heads/main", "refs/heads/beta", "refs/heads/dev", ""):
        assert resolve_channel(version, "auto", branch) == channel
    assert str(validate_version(version, channel)) == version


def test_branch_channel_mapping():
    assert channel_for_branch("refs/heads/main") == "stable"
    assert channel_for_branch("beta") == "beta"
    assert channel_for_branch("refs/heads/dev") == "nightly"
    assert channel_for_branch("feature/x") is None


def test_explicit_stable_still_refuses_prerelease_with_instructions():
    with pytest.raises(ValueError) as error:
        resolve_channel("1.1.0-beta.1", "stable", "refs/heads/main")
    message = str(error.value)
    assert "Stable releases cannot use a pre-release version" in message
    assert "version is 1.1.0-beta.1; run on main" in message
    assert "choose channel beta (or auto)" in message


def test_explicit_beta_and_nightly_refuse_wrong_versions():
    with pytest.raises(ValueError, match="choose channel stable"):
        resolve_channel("1.1.0", "beta")
    with pytest.raises(ValueError, match="run on dev"):
        resolve_channel("1.1.0", "nightly")
    with pytest.raises(ValueError, match="Unknown release channel"):
        resolve_channel("1.1.0", "weekly")


def test_cli_resolve_channel(capsys):
    assert release_tools.main(["resolve-channel", "--version", "1.1.0-beta.1"]) == 0
    assert capsys.readouterr().out.strip() == "beta"
    args = ["resolve-channel", "--version", "1.1.0-beta.1", "--branch", "refs/heads/main"]
    assert release_tools.main(args) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "beta"
    assert "usually builds stable" in captured.err
    args = ["resolve-channel", "--version", "1.1.0-beta.1", "--channel", "stable"]
    assert release_tools.main(args) == 2
    assert "run on main" in capsys.readouterr().err


def test_build_script_and_pipeline_default_to_auto_and_log_resolution():
    script = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    assert '[ValidateSet("auto", "stable", "beta", "nightly")][string]$Channel = "auto"' in script
    assert "scripts/release_tools.py resolve-channel" in script
    assert "Release build: version $Version, channel $Channel" in script
    assert script.index("resolve-channel") < script.index('"Validate semantic version and channel"')

    pipeline = (ROOT / "azure-pipelines.yml").read_text(encoding="utf-8")
    assert "- name: releaseChannel" in pipeline
    assert "  default: auto" in pipeline
    assert "INFERBRIDGE_REQUESTED_CHANNEL: ${{ parameters.releaseChannel }}" in pipeline
    assert (
        "release_tools.py resolve-channel --version $version --channel $requestedChannel"
        in pipeline
    )
    assert "Resolved release: version $version, channel $channel" in pipeline
