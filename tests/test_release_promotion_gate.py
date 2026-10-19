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


def test_publisher_allows_unsigned_stable_publication():
    script = _publisher_script()

    assert "[switch]$AllowUnsigned" not in script
    assert 'if ($Channel -eq "stable") { $SigningGate += "--require-signed" }' not in script
    assert '"--require-signed"' not in script
    assert "verify_release_signing.py" in script


def test_release_and_lifecycle_ci_support_manual_and_pr_validation():
    for workflow_name in ("ci.yml", "model-lifecycle-windows.yml"):
        workflow = _workflow(workflow_name)
        assert "on:\n" in workflow, f"{workflow_name} must declare workflow triggers"
        triggers = workflow.split("on:\n", 1)[1].split("\n\n", 1)[0]

        # Dev can run these workflows manually; promotion branches may also use PR CI.
        assert "  workflow_dispatch:" in triggers, f"{workflow_name} must be manually runnable"
        if "  pull_request:" in triggers:
            pull_request_section = triggers.split("  pull_request:", 1)[1]
            assert 'branches: ["main", "dev", "beta"]' in pull_request_section


def test_documented_stable_signing_policy_matches_publisher_gate():
    root = Path(__file__).resolve().parents[1]
    docs = (root / "docs" / "CODE_SIGNING.md").read_text(encoding="utf-8")
    script = _publisher_script()

    assert "unsigned stable publication" in docs
    assert "without** `--require-signed`" in docs
    assert '"--require-signed"' not in script
