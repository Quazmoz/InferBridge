from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dependabot_targets_dev_with_conservative_dependency_domains() -> None:
    config = _read(".github/dependabot.yml")

    assert config.count('target-branch: "dev"') == 2
    assert "python-runtime:" not in config

    expected_groups = (
        "openvino-inference-stack:",
        "conversion-model-toolchain:",
        "server-runtime:",
        "build-release-tooling:",
        "developer-test-tooling:",
    )
    for group in expected_groups:
        assert group in config

    for dependency in ("openvino", "openvino-genai", "openvino-tokenizers"):
        assert f'- "{dependency}"' in config

    assert '"optimum-intel"' in config
    assert '"transformers"' in config
    assert '"torch"' in config
    assert '"fastapi"' in config
    assert '"pydantic"' in config
    assert '"pyinstaller"' in config
    assert '"pytest"' in config
    assert '"ruff"' in config


def test_expensive_github_qualification_and_release_workflows_remain_manual() -> None:
    for path in (
        ".github/workflows/ci.yml",
        ".github/workflows/model-lifecycle-windows.yml",
        ".github/workflows/release.yml",
    ):
        workflow = _read(path)
        assert "  workflow_dispatch:" in workflow
        assert "\n  push:" not in workflow
        assert "\n  pull_request:" not in workflow


def test_native_windows_build_is_scoped_to_dev_and_heavy_candidate_is_opt_in() -> None:
    native = _read(".github/workflows/native-windows.yml")

    assert "  push:\n    branches: [dev]\n    paths:" in native
    assert '- "windows-native/**"' in native
    assert "  workflow_dispatch:" in native
    assert "\n  pull_request:" not in native
    assert "github.event_name == 'workflow_dispatch' && inputs.full_combined_candidate" in native


def test_ruff_preflight_only_automatically_checks_dev() -> None:
    preflight = _read(".github/workflows/ruff-preflight.yml")

    assert "  push:\n    branches: [dev]" in preflight
    assert "  pull_request:\n    branches: [dev]" in preflight
    assert "  workflow_dispatch:" in preflight
    assert "python -m ruff check ." in preflight
    assert "python -m ruff format --check ." in preflight


def test_ci_distinguishes_qualified_baseline_from_latest_canary() -> None:
    ci = _read(".github/workflows/ci.yml")

    assert "Qualified Release Baseline (Windows / Python 3.11)" in ci
    assert "Latest-Compatible Canary (Windows / Python 3.11)" in ci
    assert "-r requirements/release.txt" in ci
    assert "--no-deps --no-build-isolation ." in ci
    assert "requirements/release.txt\n            pyproject.toml" in ci
    assert '-e ".[dev,convert,distribution]"' in ci
    assert "github.ref == 'refs/heads/dev'" in ci
    assert "github.base_ref == 'dev'" in ci


def test_windows_lifecycle_manual_run_retains_qualified_dependency_contract() -> None:
    lifecycle = _read(".github/workflows/model-lifecycle-windows.yml")

    assert "  workflow_dispatch:" in lifecycle
    assert "Download, Convert, Load, Recover" in lifecycle
    assert "requirements/release.txt\n            pyproject.toml" in lifecycle
    assert "-r requirements/release.txt" in lifecycle
    assert "--no-deps --no-build-isolation ." in lifecycle


def test_azure_candidate_uses_python_312_and_fast_fails_pinned_quality_gates() -> None:
    pipeline = _read("azure-pipelines.yml")
    release_requirements = _read("requirements/release.txt")
    pinned_ruff = next(
        line for line in release_requirements.splitlines() if line.startswith("ruff==")
    )

    assert "versionSpec: '3.12'" in pipeline
    assert "displayName: 'Use Python 3.12'" in pipeline
    assert "sys.version_info[:2] == (3, 12)" in pipeline
    assert "sys.maxsize > 2**32" in pipeline
    assert f"python -m pip install --disable-pip-version-check --no-deps {pinned_ruff}" in pipeline
    assert "python -m ruff check ." in pipeline
    assert "python -m ruff format --check ." in pipeline

    fast_fail = pipeline.index("displayName: 'Fast-fail Python 3.12 and Ruff checks'")
    assert fast_fail < pipeline.index("displayName: 'Install Inno Setup'")
    assert fast_fail < pipeline.index("displayName: 'Prepare qualified release dependencies'")

    # The Azure candidate runtime must not silently migrate the independently
    # qualified GitHub Windows workflows.
    github_ci = _read(".github/workflows/ci.yml")
    assert "Qualified Release Baseline (Windows / Python 3.11)" in github_ci


def test_azure_build_binds_config_and_artifacts_to_selected_source_revision() -> None:
    pipeline = _read("azure-pipelines.yml")

    assert "trigger: none\npr: none" in pipeline
    assert "  - checkout: self" in pipeline
    assert "    clean: true" in pipeline
    assert "    persistCredentials: false" in pipeline
    assert "git fetch" not in pipeline
    assert "git checkout" not in pipeline
    assert "refs/remotes/origin/main" not in pipeline

    # The Azure-selected repository, branch, and commit must match the checkout.
    for identity in ("BUILD_REPOSITORY_URI", "BUILD_SOURCEBRANCH", "BUILD_SOURCEVERSION"):
        assert f"$env:{identity}" in pipeline
    assert "BUILD_REASON -ne 'Manual'" in pipeline
    assert "git rev-parse --verify HEAD" in pipeline
    assert "$sha -ne $env:BUILD_SOURCEVERSION" in pipeline
    assert "git diff --exit-code HEAD -- azure-pipelines.yml" in pipeline
    assert "git status --porcelain" in pipeline
    assert "Get-FileHash -LiteralPath 'azure-pipelines.yml' -Algorithm SHA256" in pipeline

    # Mismatched artifact commit or checksums must block publication.
    assert "scripts/verify_release_provenance.py" in pipeline
    assert "--expected-commit $env:BUILD_SOURCEVERSION" in pipeline
    assert "pipeline_config_sha256 = $env:INFERBRIDGE_PIPELINE_SHA256" in pipeline
    assert "source_ref = $env:BUILD_SOURCEBRANCH" in pipeline
    assert "source_commit = $env:BUILD_SOURCEVERSION.ToLowerInvariant()" in pipeline
    assert "-Unsigned" in pipeline
    assert "'refs/heads/main' { 'stable' }" in pipeline
    assert "'refs/heads/beta' { 'beta' }" in pipeline
    assert "'refs/heads/dev'  { 'nightly' }" in pipeline
    assert "--channel $channel --expected-commit $env:BUILD_SOURCEVERSION" in pipeline
    assert "PublishPipelineArtifact@1" in pipeline
    assert "condition: succeeded()" in pipeline
