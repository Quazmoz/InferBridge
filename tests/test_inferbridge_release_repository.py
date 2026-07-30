import pytest

from scripts.release_manifest import release_repository


def test_release_repository_defaults_to_canonical_after_rename():
    assert release_repository({}) == "Quazmoz/InferBridge"


def test_release_repository_uses_renamed_github_repository():
    assert release_repository({"GITHUB_REPOSITORY": "Quazmoz/InferBridge"}) == "Quazmoz/InferBridge"


def test_release_repository_allows_explicit_legacy_transition_override():
    assert (
        release_repository({"OV_LLM_RELEASE_REPOSITORY": "Quazmoz/openvino-windows-llm"})
        == "Quazmoz/openvino-windows-llm"
    )


def test_release_repository_rejects_lookalike_repository():
    with pytest.raises(ValueError):
        release_repository({"OV_LLM_RELEASE_REPOSITORY": "Quazmoz/InferBridge-malicious"})
