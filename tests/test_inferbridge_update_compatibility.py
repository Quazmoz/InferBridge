from app.release_models import artifact_filenames, is_official_release_url
from app.update_checker import _candidate_manifest_url


def test_new_and_legacy_release_urls_are_approved():
    assert is_official_release_url(
        "https://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/InferBridge-0.7.0-windows-x64-portable.zip"
    )
    assert is_official_release_url(
        "https://github.com/Quazmoz/openvino-windows-llm/releases/download/v0.6.3/OpenVINO-Windows-LLM-0.6.3-windows-x64-portable.zip"
    )


def test_release_url_lookalikes_and_prefix_tricks_are_rejected():
    rejected = (
        "https://github.com/Quazmoz/InferBridge-malicious/releases/download/v0.7.0/file.zip",
        "https://github.com/Quazmoz/openvino-windows-llm.evil/releases/download/v0.7.0/file.zip",
        "https://github.com/Other/InferBridge/releases/download/v0.7.0/file.zip",
        "https://user:pass@github.com/Quazmoz/InferBridge/releases/download/v0.7.0/file.zip",
        "https://github.com:444/Quazmoz/InferBridge/releases/download/v0.7.0/file.zip",
        "http://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/file.zip",
    )
    assert not any(is_official_release_url(value) for value in rejected)


def test_manifest_and_artifact_aliases_remain_accepted():
    names = artifact_filenames("0.7.0", "manifest")
    assert "InferBridge-0.7.0-release-manifest.json" in names
    assert "OpenVINO-Windows-LLM-0.7.0-release-manifest.json" in names
    assert "OpenVINOWindowsLLM-0.7.0-release-manifest.json" in names


def test_candidate_selection_accepts_both_manifest_names_and_prefers_canonical():
    releases = [
        {
            "draft": False,
            "prerelease": False,
            "tag_name": "v0.7.0",
            "assets": [
                {
                    "name": "OpenVINO-Windows-LLM-0.7.0-release-manifest.json",
                    "browser_download_url": "https://github.com/Quazmoz/openvino-windows-llm/releases/download/v0.7.0/OpenVINO-Windows-LLM-0.7.0-release-manifest.json",
                },
                {
                    "name": "InferBridge-0.7.0-release-manifest.json",
                    "browser_download_url": "https://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/InferBridge-0.7.0-release-manifest.json",
                },
            ],
        }
    ]
    assert _candidate_manifest_url(releases, "stable") == (
        "0.7.0",
        "https://github.com/Quazmoz/InferBridge/releases/download/v0.7.0/InferBridge-0.7.0-release-manifest.json",
    )
