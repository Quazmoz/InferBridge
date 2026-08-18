"""Desktop-only release metadata and optional update-check routes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import HTTPException, Request

from app.build_info import load_build_info
from app.local_request_security import require_safe_browser_origin
from app.release_models import SemanticVersion
from app.update_checker import UpdateChecker, UpdatePreferences, UpdateStore, check_due
from app.version import DATA_SCHEMA_VERSION

_LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _require_local_ui(request: Request) -> None:
    require_safe_browser_origin(request)
    client = str(request.client.host if request.client else "").strip().lower()
    request_host = str(request.url.hostname or "").strip().lower()
    if (
        request.headers.get("X-OV-LLM-UI") != "1"
        or client not in _LOOPBACK_CLIENTS
        or request_host not in {"127.0.0.1", "::1", "localhost"}
    ):
        raise HTTPException(
            status_code=403, detail="This action requires the local application UI."
        )


def register_release_routes(app, *, paths) -> None:
    store = UpdateStore(paths.config_dir)
    installation_mode = "portable" if paths.portable else "installed"

    @app.get("/desktop/release/status", include_in_schema=False)
    async def release_status():
        preferences = store.load_preferences()
        cache = store.load_cache()
        cache_matches_channel = cache.channel == preferences.channel
        relevant_last_checked_at = cache.last_checked_at if cache_matches_channel else None
        build = load_build_info(paths.resource_root)
        return {
            "build": build.model_dump(mode="json"),
            "installation_mode": installation_mode,
            "data_schema_version": DATA_SCHEMA_VERSION,
            "update_checks": preferences.model_dump(mode="json"),
            "latest_checked_version": (
                cache.latest_checked_version if cache_matches_channel else None
            ),
            "last_update_check_time": relevant_last_checked_at,
            "check_due": check_due(relevant_last_checked_at, datetime.now(UTC)),
            "cached_manifest": cache.manifest if cache_matches_channel else None,
        }

    @app.post("/desktop/release/check", include_in_schema=False)
    async def check_release(request: Request):
        _require_local_ui(request)
        checker = UpdateChecker(store=store, installation_mode=installation_mode)
        result = await asyncio.to_thread(checker.check, force=True)
        return result.model_dump(mode="json")

    @app.put("/desktop/release/settings", include_in_schema=False)
    async def update_release_settings(request: Request):
        _require_local_ui(request)
        payload = await request.json()
        try:
            preferences = UpdatePreferences.model_validate(payload)
            for version in preferences.skipped_versions:
                SemanticVersion.parse(version)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="Invalid update settings.") from exc
        store.save_preferences(preferences)
        return preferences.model_dump(mode="json")
