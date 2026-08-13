"""Credential comparison must reject bad input, never crash on it.

``secrets.compare_digest`` raises TypeError when either ``str`` operand holds a
non-ASCII character. ASGI servers hand header values to the application latin-1
decoded, so one high byte in an Authorization header — or an accented character in a
configured key — used to turn every protected route into a 500. These tests pin the
rejection path for the shared helper and for each route family that authenticates.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import BASE_DIR, Settings
from app.local_request_security import matches_any_secret, secret_matches
from app.server import create_app

# httpx refuses to encode a non-ASCII ``str`` header, so the byte sequence is supplied
# directly the way a real client would put it on the wire.
NON_ASCII_BEARER = b"Bearer wr\xf8ng"
API_KEY = "configured-secret"


def _settings(tmp_path, *, api_key: str | None = API_KEY) -> Settings:
    return Settings(
        host="127.0.0.1",
        port=8000,
        device="CPU",
        models_file=BASE_DIR / "models.json",
        models_dir=tmp_path / "models",
        cache_dir=tmp_path / "cache",
        benchmark_results_file=tmp_path / "benchmarks.json",
        default_model=None,
        api_key=api_key,
        force_mock=True,
    )


def _client(tmp_path, **kwargs) -> TestClient:
    return TestClient(create_app(_settings(tmp_path, **kwargs)), raise_server_exceptions=False)


# --- shared helper -------------------------------------------------------------


def test_secret_matches_accepts_identical_ascii_values() -> None:
    assert secret_matches("shared-key", "shared-key") is True
    assert secret_matches("shared-key", "other-key") is False


def test_secret_matches_compares_non_ascii_values_without_raising() -> None:
    assert secret_matches("clé-locale", "clé-locale") is True
    assert secret_matches("clé-locale", "cle-locale") is False
    # A non-ASCII supplied value against an ASCII key is the crash case that used to
    # escape as a 500 rather than a rejection.
    assert secret_matches("wrøng", "shared-key") is False
    assert secret_matches("shared-key", "clé-locale") is False


def test_secret_matches_treats_missing_credentials_as_no_match() -> None:
    assert secret_matches(None, "shared-key") is False
    assert secret_matches("shared-key", None) is False
    assert secret_matches(None, None) is False
    assert secret_matches("", "") is True


def test_secret_matches_accepts_bytes_on_either_side() -> None:
    assert secret_matches(b"shared-key", "shared-key") is True
    assert secret_matches("shared-key", b"shared-key") is True
    assert secret_matches("clé", "clé".encode()) is True


def test_matches_any_secret_scans_every_configured_key() -> None:
    assert matches_any_secret("second", ["first", "second", "third"]) is True
    assert matches_any_secret("missing", ["first", "second"]) is False
    assert matches_any_secret("first", []) is False
    # A non-ASCII candidate anywhere in the list must not abort the scan before a
    # later, valid key is reached.
    assert matches_any_secret("third", ["clé", "wrøng", "third"]) is True


def test_matches_any_secret_does_not_short_circuit_on_the_first_match() -> None:
    """Timing must not reveal which configured key matched."""

    consumed: list[str] = []

    def keys():
        for key in ("first", "second", "third"):
            consumed.append(key)
            yield key

    assert matches_any_secret("first", keys()) is True
    assert consumed == ["first", "second", "third"]


# --- route families ------------------------------------------------------------

PROTECTED_GET_ROUTES = (
    "/v1/models",  # app.server core OpenAI-compatible auth
    "/v1/models/status",  # app.status_split
    "/v1/model-library",  # app.model_library_routes
    "/v1/huggingface/status",  # app.huggingface_access
    "/v1/system/status",  # app.server
)


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_non_ascii_bearer_is_rejected_not_crashed(tmp_path, path) -> None:
    with _client(tmp_path) as client:
        response = client.get(path, headers={"Authorization": NON_ASCII_BEARER})

    assert response.status_code == 401, f"{path} returned {response.status_code}"


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_correct_key_still_authenticates(tmp_path, path) -> None:
    with _client(tmp_path) as client:
        response = client.get(path, headers={"Authorization": f"Bearer {API_KEY}"})

    assert response.status_code == 200, f"{path} returned {response.status_code}"


@pytest.mark.parametrize("path", PROTECTED_GET_ROUTES)
def test_unauthenticated_server_still_serves_every_route(tmp_path, path) -> None:
    with _client(tmp_path, api_key=None) as client:
        assert client.get(path).status_code == 200


def test_non_ascii_bearer_is_rejected_on_authenticated_post_routes(tmp_path) -> None:
    posts = {
        "/v1/models/cancel": {"model": "unknown-model"},
        "/v1/chat/context-budget": {"model": "unknown-model", "messages": []},
    }
    # No Origin header: a local API client, so the cross-site guard stays out of the way
    # and the credential check is what decides the response.
    with _client(tmp_path) as client:
        for path, body in posts.items():
            response = client.post(path, json=body, headers={"Authorization": NON_ASCII_BEARER})
            assert response.status_code == 401, f"{path} returned {response.status_code}"


def test_non_ascii_bearer_is_rejected_on_model_recovery(tmp_path) -> None:
    with _client(tmp_path) as client:
        response = client.get(
            "/v1/models/recovery/some-model",
            headers={"Authorization": NON_ASCII_BEARER},
        )

    assert response.status_code == 401


# --- desktop-only dependencies -------------------------------------------------
#
# Storage manager and runtime health routes register only in the packaged desktop
# process, so their shared dependency is exercised directly.


class _StubRequest:
    def __init__(self, settings, method: str = "GET") -> None:
        self.app = type("_App", (), {"state": type("_State", (), {"settings": settings})()})()
        self.method = method
        self.headers: dict[str, str] = {}


def _run_dependency(dependency, settings, authorization):
    return asyncio.run(dependency(_StubRequest(settings), authorization=authorization))


@pytest.mark.parametrize(
    "module_name",
    [
        "app.storage_manager",
        "app.runtime_health",
        "app.status_split",
        "app.context_budget",
        "app.model_recovery",
        "app.model_cancellation",
    ],
)
def test_desktop_dependencies_reject_non_ascii_credentials(tmp_path, module_name) -> None:
    import importlib

    dependency = importlib.import_module(module_name)._require_access
    settings = _settings(tmp_path)

    with pytest.raises(HTTPException) as rejected:
        _run_dependency(dependency, settings, "Bearer wrøng")
    assert rejected.value.status_code == 401

    # A configured key that is not ASCII must authenticate rather than fault.
    unicode_settings = _settings(tmp_path, api_key="clé-locale")
    assert _run_dependency(dependency, unicode_settings, "Bearer clé-locale") is None

    with pytest.raises(HTTPException) as mismatched:
        _run_dependency(dependency, unicode_settings, "Bearer cle-locale")
    assert mismatched.value.status_code == 401


def test_desktop_control_token_rejects_non_ascii_header() -> None:
    from app.desktop_operations_routes import _control_dependency

    dependency = _control_dependency("control-token")

    class _LoopbackRequest:
        client = type("_Client", (), {"host": "127.0.0.1"})()

    with pytest.raises(HTTPException) as rejected:
        asyncio.run(dependency(_LoopbackRequest(), x_desktop_control="tokén"))
    assert rejected.value.status_code == 403

    with pytest.raises(HTTPException) as missing:
        asyncio.run(dependency(_LoopbackRequest(), x_desktop_control=None))
    assert missing.value.status_code == 403

    assert asyncio.run(dependency(_LoopbackRequest(), x_desktop_control="control-token")) is None
