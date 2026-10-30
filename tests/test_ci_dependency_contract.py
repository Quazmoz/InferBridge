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


def test_github_hosted_build_workflows_are_retired() -> None:
    # Manual Azure DevOps and the tag-driven github-release.yml (both through
    # scripts/build_release.ps1) are the only packaged Windows/release build paths.
    for path in (
        ".github/workflows/native-windows.yml",
        ".github/workflows/release.yml",
        ".github/workflows/ruff-preflight.yml",
    ):
        assert not (ROOT / path).exists()

    # Remaining GitHub workflows are manual-only; pushes never start a job.
    for path in (
        ".github/workflows/ci.yml",
        ".github/workflows/model-lifecycle-windows.yml",
        ".github/workflows/packaged-smoke.yml",
    ):
        workflow = _read(path)
        assert "  workflow_dispatch:" in workflow
        assert "\n  push:" not in workflow
        assert "\n  pull_request:" not in workflow


def test_manual_azure_build_keeps_native_and_python_qualification() -> None:
    pipeline = _read("azure-pipelines.yml")
    builder = _read("scripts/build_release.ps1")
    assert "trigger: none\npr: none" in pipeline
    assert "BUILD_REASON -ne 'Manual'" in pipeline
    assert "scripts/build_release.ps1" in pipeline
    assert 'Invoke-Checked "Pytest"' in builder
    assert "dotnet publish windows-native/InferBridge.Native/InferBridge.Native.csproj" in builder
    assert "InferBridge.Native.ContractTests.csproj" in builder
    assert "-SkipTests" not in pipeline


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
    sdk_install = pipeline.index("task: UseDotNet@2")
    sdk_verify = pipeline.index("displayName: 'Verify .NET 10 SDK before packaging'")
    assert "version: '10.0.x'" in pipeline
    assert "dotnet --version" in pipeline
    assert (
        fast_fail < sdk_install < sdk_verify < pipeline.index("displayName: 'Install Inno Setup'")
    )
    assert sdk_verify < pipeline.index("displayName: 'Build and verify unsigned Windows release'")

    # Retained manual GitHub CI remains a separate qualification profile.
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
    selector = _read("scripts/select_azure_artifacts.py")
    assert "--pipeline-hash $env:INFERBRIDGE_PIPELINE_SHA256" in pipeline
    assert '"pipeline_config_sha256": pipeline_hash.lower()' in selector
    assert "--source-ref $env:BUILD_SOURCEBRANCH" in pipeline
    assert '"source_ref": source_ref' in selector
    assert "--source-commit $env:BUILD_SOURCEVERSION" in pipeline
    assert '"source_commit": source_commit.lower()' in selector
    assert "-Unsigned" in pipeline
    assert "'refs/heads/main' { 'stable' }" in pipeline
    assert "'refs/heads/beta' { 'beta' }" in pipeline
    assert "'refs/heads/dev'  { 'nightly' }" in pipeline
    assert "--channel $channel --expected-commit $env:BUILD_SOURCEVERSION" in pipeline
    assert "PublishPipelineArtifact@1" in pipeline
    assert "condition: succeeded()" in pipeline


def test_azure_queue_time_artifact_selection_preserves_full_release_gates() -> None:
    pipeline = _read("azure-pipelines.yml")
    selector = _read("scripts/select_azure_artifacts.py")

    assert "default: installer-portable" in pipeline
    for output in ("installer-portable", "installer", "portable", "msix", "all"):
        assert f"    - {output}" in pipeline
    assert "INFERBRIDGE_ARTIFACT_TYPE: ${{ parameters.artifactType }}" in pipeline
    assert "INFERBRIDGE_MSIX_IDENTITY_NAME: ${{ parameters.msixIdentityName }}" in pipeline
    assert "INFERBRIDGE_MSIX_PUBLISHER: ${{ parameters.msixPublisher }}" in pipeline
    assert "scripts/build_msix.ps1" in pipeline
    assert "scripts/select_azure_artifacts.py" in pipeline
    assert "release-internal" in pipeline
    assert "scripts/verify_release_provenance.py" in pipeline
    assert pipeline.index("scripts/verify_release_provenance.py") < pipeline.index(
        "scripts/build_msix.ps1"
    )
    assert pipeline.index("scripts/select_azure_artifacts.py") < pipeline.index(
        "PublishPipelineArtifact@1"
    )
    assert "condition: succeeded()" in pipeline
    assert "trigger: none\npr: none" in pipeline
    assert "-SkipTests" not in pipeline
    assert (
        "verify_msix(source, identity=identity, publisher=publisher, version=version)" in selector
    )
    assert '"azure-selected-checksums.txt"' in selector
    assert '"azure-build-provenance.json"' in selector


def test_windows_312_ci_compiles_native_frontend_without_releasing() -> None:
    workflow = _read(".github/workflows/python312-windows.yml")
    release_requirements = _read("requirements/release.txt")
    pinned_ruff = next(
        line for line in release_requirements.splitlines() if line.startswith("ruff==")
    )

    assert "branches: [dev]" in workflow
    assert "runs-on: windows-2022" in workflow
    assert "python-version: '3.12'" in workflow
    assert "architecture: 'x64'" in workflow
    assert f"--no-deps {pinned_ruff}" in workflow
    assert "python -m ruff check ." in workflow
    assert "python -m ruff format --check ." in workflow
    assert "python -m pytest" in workflow
    assert "actions/setup-dotnet@v4" in workflow
    assert "dotnet-version: '10.0.x'" in workflow
    assert (
        "windows-native/InferBridge.Native.ContractTests/InferBridge.Native.ContractTests.csproj"
        in workflow
    )
    assert "dotnet publish windows-native/InferBridge.Native/InferBridge.Native.csproj" in workflow
    assert "--self-contained true -p:Platform=x64" in workflow
    assert "'InferBridge.Native.exe'" in workflow
    assert "'coreclr.dll'" in workflow
    assert "PublishPipelineArtifact@" not in workflow
    assert "actions/upload-artifact@" not in workflow


def test_manual_packaged_smoke_runs_both_packaged_modes_through_release_builder() -> None:
    # GitHub source CI never freezes the app, so packaged startup regressions used to
    # reach main untested. This manual workflow runs the real release builder, whose
    # installed and portable smoke tests launch the frozen tray headless.
    workflow = _read(".github/workflows/packaged-smoke.yml")
    builder = _read("scripts/build_release.ps1")
    assert "./scripts/build_release.ps1 @arguments" in workflow
    assert "Unsigned = $true" in workflow
    assert "MockSmokeTest = $true" in workflow
    assert "secrets." not in workflow
    assert "Sign = $true" not in workflow
    assert "-ExpectedMode installed" in builder
    assert "-ExpectedMode portable" in builder
    # The frozen tray must report a failed headless startup, not just exit 6.
    assert "InferBridge headless startup failed: Configured port $port is unavailable" in workflow
    assert "if ($process.ExitCode -ne 6)" in workflow


def test_tag_release_workflow_builds_verifies_and_publishes_through_release_builder() -> None:
    # Pushing a v<version> tag publishes the unsigned build as a GitHub Release.
    workflow = _read(".github/workflows/github-release.yml")
    assert "  push:\n    tags:\n      - 'v*'" in workflow
    assert "\n    branches:" not in workflow
    assert "\n  pull_request:" not in workflow
    assert "  workflow_dispatch:" in workflow
    assert "permissions:\n  contents: write" in workflow
    assert "secrets." not in workflow
    # The tag must name the canonical version at the tagged commit.
    assert "scripts/release_tools.py canonical-version" in workflow
    assert 'if ($tag -ne "v$version")' in workflow
    assert "resolve-channel --version $version --channel auto" in workflow
    # Same builder (tests + packaged smoke) and provenance gate as Azure.
    assert (
        "./scripts/build_release.ps1 -Version $version -Channel $channel -Unsigned -Clean"
        in workflow
    )
    assert "-SkipTests" not in workflow
    assert "-Sign " not in workflow
    assert "scripts/verify_release_provenance.py" in workflow
    assert workflow.index("verify_release_provenance.py") < workflow.index("'release', 'create'")
    assert "'--verify-tag'" in workflow
    assert "'--prerelease'" in workflow
    assert '"docs/releases/$version.md"' in workflow
    for suffix in ("windows-x64-installer.exe", "windows-x64-portable.zip", "checksums.txt"):
        assert f'"InferBridge-$version-{suffix}"' in workflow


def test_conversion_cli_preflight_is_required_before_windows_package_freeze() -> None:
    preflight = "scripts/check_conversion_cli.py"
    for path in ("scripts/build_release.ps1", "scripts/build_dev_package.ps1"):
        contents = _read(path)
        assert preflight in contents
        assert contents.index(preflight) < contents.index("PyInstaller")

    windows = _read(".github/workflows/python312-windows.yml")
    assert f"run: python {preflight}" in windows
    assert "tests/test_conversion_cli_preflight.py" in windows
