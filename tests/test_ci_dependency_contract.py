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


def test_ci_pr_filters_cover_main_and_dev_without_expanding_beta() -> None:
    ci = _read(".github/workflows/ci.yml")

    assert 'push:\n    branches: ["main", "dev", "beta"]' in ci
    assert 'pull_request:\n    branches: ["main", "dev"]' in ci
    assert 'pull_request:\n    branches: ["main", "dev", "beta"]' not in ci


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


def test_windows_lifecycle_covers_dev_dependency_prs_with_release_pins() -> None:
    lifecycle = _read(".github/workflows/model-lifecycle-windows.yml")

    assert 'push:\n    branches: ["main", "dev", "beta"]' in lifecycle
    assert 'pull_request:\n    branches: ["main", "dev"]' in lifecycle
    assert '- "pyproject.toml"' in lifecycle
    assert '- "requirements/release.txt"' in lifecycle
    assert "requirements/release.txt\n            pyproject.toml" in lifecycle
    assert "-r requirements/release.txt" in lifecycle
    assert "--no-deps --no-build-isolation ." in lifecycle
