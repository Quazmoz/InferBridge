from pathlib import Path


def _publisher_script() -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / "scripts" / "publish_release.ps1").read_text(encoding="utf-8")


def _workflow(path: str) -> str:
    root = Path(__file__).resolve().parents[1]
    return (root / ".github" / "workflows" / path).read_text(encoding="utf-8")


def test_publisher_enforces_channel_branch_mapping():
    script = _publisher_script()

    assert "$ExpectedBranch = switch ($Channel)" in script
    assert '"stable" { "main" }' in script
    assert '"beta" { "beta" }' in script
    assert '"nightly" { "dev" }' in script
    assert "git branch --show-current" in script
    assert "$CurrentBranch -ne $ExpectedBranch" in script


def test_publisher_rejects_detached_head_and_wrong_branch():
    script = _publisher_script()

    assert "Publishing requires an attached release branch" in script
    assert "Promote dev -> beta -> main before publishing." in script


def test_publisher_never_allows_unsigned_stable_publication():
    script = _publisher_script()

    assert 'if ($Channel -eq "stable" -and $AllowUnsigned)' in script
    assert "-AllowUnsigned is not permitted for stable publication" in script
    assert 'if ($Channel -eq "stable") { $SigningGate += "--require-signed" }' in script


def test_beta_promotion_pull_requests_run_release_and_lifecycle_ci():
    for workflow_name in ("ci.yml", "model-lifecycle-windows.yml"):
        workflow = _workflow(workflow_name)
        pull_request_section = workflow.split("  pull_request:", 1)[1].split("\n\n", 1)[0]

        assert 'branches: ["main", "dev", "beta"]' in pull_request_section
