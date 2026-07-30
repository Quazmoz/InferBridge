import json
import urllib.error
from datetime import UTC, datetime, timedelta

from app.brand import USER_AGENT_PRODUCT
from app.release_models import artifact_filename
from app.update_checker import (
    UpdateCache,
    UpdateChecker,
    UpdatePreferences,
    UpdateStore,
    check_due,
)
from app.version import __version__


class Response:
    def __init__(self, payload, *, headers=None):
        self.payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, limit=-1):
        return self.payload if limit < 0 else self.payload[:limit]


def manifest_payload(version: str, channel: str):
    def item(kind, suffix):
        filename = artifact_filename(version, kind)
        return {
            "type": kind,
            "filename": filename,
            "url": (
                "https://github.com/Quazmoz/openvino-windows-llm/"
                f"releases/download/v{version}/{filename}"
            ),
            "sha256": suffix * 64,
            "size_bytes": 10,
            "signed": False,
            "signature_verified": False,
        }

    return {
        "schema_version": 1,
        "version": version,
        "channel": channel,
        "published_at": "2026-07-22T12:00:00Z",
        "minimum_windows_version": "Windows 10 2004 (10.0.19041)",
        "minimum_windows_build": 19041,
        "architecture": "x64",
        "source_commit": "a" * 40,
        "source_tree_clean": True,
        "artifacts": [item("installer", "a"), item("portable", "b")],
        "api_compatibility": {
            "chat_completions": True,
            "responses": True,
            "embeddings": True,
        },
        "openvino": {
            "minimum_version": "2025.1.0",
            "bundled_version": "2025.1.0",
            "genai_version": "2025.1.0",
        },
        "data_compatibility": {
            "minimum_supported_schema": 1,
            "current_schema": 1,
            "downgrade_compatible_from_schema": 1,
            "model_cache_compatible": True,
            "compiled_cache_policy": "invalidate on runtime change",
        },
        "release_notes_url": (
            f"https://github.com/Quazmoz/openvino-windows-llm/releases/tag/v{version}"
        ),
        "known_issues_url": (
            f"https://github.com/Quazmoz/openvino-windows-llm/blob/v{version}/docs/KNOWN_ISSUES.md"
        ),
        "compatibility_matrix_url": (
            "https://github.com/Quazmoz/openvino-windows-llm/"
            f"blob/v{version}/docs/COMPATIBILITY_MATRIX.md"
        ),
        "summary": "Update available",
        "highlights": ["Reliable release metadata"],
        "dependency_inventory_filename": "inventory.json",
    }


def release_payload(version: str, prerelease: bool):
    filename = artifact_filename(version, "manifest")
    return [
        {
            "tag_name": f"v{version}",
            "draft": False,
            "prerelease": prerelease,
            "assets": [
                {
                    "name": filename,
                    "browser_download_url": (
                        "https://github.com/Quazmoz/openvino-windows-llm/"
                        f"releases/download/v{version}/{filename}"
                    ),
                }
            ],
        }
    ]


def opener_for(version: str, channel: str):
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, timeout, dict(request.header_items())))
        if "api.github.com" in request.full_url:
            return Response(
                release_payload(version, channel != "stable"),
                headers={"ETag": '"abc"'},
            )
        return Response(manifest_payload(version, channel))

    return opener, calls


def test_update_check_interval_behavior():
    now = datetime(2026, 7, 22, 12, tzinfo=UTC)
    assert check_due(None, now)
    assert not check_due(now - timedelta(hours=23), now)
    assert check_due(now - timedelta(hours=24), now)


def test_fresh_update_checks_are_disabled_and_make_no_request(tmp_path):
    store = UpdateStore(tmp_path)
    assert store.load_preferences().enabled is False

    def fail(*_args, **_kwargs):
        raise AssertionError("network should not be used before opt-in")

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=fail,
    ).check(force=True)
    assert result.status == "disabled"


def test_stable_user_ignores_beta_release(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True))
    opener, _calls = opener_for("0.8.0-beta.1", "beta")
    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=opener,
    ).check(force=True)
    assert result.status == "current"
    assert result.manifest is None


def test_beta_user_sees_beta_release_and_installed_artifact(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True, channel="beta"))
    opener, calls = opener_for("0.8.0-beta.1", "beta")
    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=opener,
    ).check(force=True)
    assert result.status == "available"
    assert result.selected_artifact_type == "installer"
    assert len(calls) == 2
    assert all(timeout == 4.0 for _url, timeout, _headers in calls)
    assert all(
        f"{USER_AGENT_PRODUCT}/{__version__}" in headers.get("User-agent", "")
        for _url, _timeout, headers in calls
    )


def test_portable_user_receives_portable_artifact(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True))
    opener, _calls = opener_for("0.8.0", "stable")
    result = UpdateChecker(
        store=store,
        installation_mode="portable",
        opener=opener,
    ).check(force=True)
    assert result.status == "available"
    assert result.selected_artifact_type == "portable"


def test_skip_version_persists_and_suppresses_prompt(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True, skipped_versions=["0.8.0"]))
    opener, _calls = opener_for("0.8.0", "stable")
    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=opener,
    ).check(force=True)
    assert result.status == "current"
    assert store.load_preferences().skipped_versions == ["0.8.0"]


def test_disabled_update_checks_make_no_request(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=False))

    def fail(*_args, **_kwargs):
        raise AssertionError("network should not be used")

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=fail,
    ).check(force=True)
    assert result.status == "disabled"


def test_offline_update_check_fails_silently(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True))

    def offline(request, timeout):
        raise urllib.error.URLError("offline")

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=offline,
    ).check(force=True)
    assert result.status == "offline"
    assert result.message == "Update check unavailable."


def test_malformed_manifest_is_rejected(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True))

    def opener(request, timeout):
        if "api.github.com" in request.full_url:
            return Response(release_payload("0.8.0", False))
        return Response({"schema_version": 999})

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=opener,
    ).check(force=True)
    assert result.status == "rejected"


def test_malformed_cached_manifest_is_cleared_and_refetched(tmp_path):
    store = UpdateStore(tmp_path)
    store.save_preferences(UpdatePreferences(enabled=True))
    store.save_cache(
        UpdateCache(
            releases_etag='"stale"',
            last_checked_at=datetime.now(UTC),
            latest_checked_version="9.9.9",
            manifest={"schema_version": 999},
        )
    )
    opener, calls = opener_for("0.8.0", "stable")

    result = UpdateChecker(
        store=store,
        installation_mode="installed",
        opener=opener,
    ).check()

    assert result.status == "available"
    assert len(calls) == 2
    assert "If-none-match" not in calls[0][2]
    assert store.load_cache().latest_checked_version == "0.8.0"
