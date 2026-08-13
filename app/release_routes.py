"""Desktop-only release metadata and optional update-check routes."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException, Request

from app.build_info import load_build_info
from app.release_models import SemanticVersion
from app.update_checker import UpdateChecker, UpdatePreferences, UpdateStore, check_due
from app.version import DATA_SCHEMA_VERSION


def _require_local_ui(request: Request) -> None:
    if request.headers.get("X-OV-LLM-UI") != "1":
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
        build = load_build_info(paths.resource_root)
        return {
            "build": build.model_dump(mode="json"),
            "installation_mode": installation_mode,
            "data_schema_version": DATA_SCHEMA_VERSION,
            "update_checks": preferences.model_dump(mode="json"),
            "latest_checked_version": cache.latest_checked_version,
            "last_update_check_time": cache.last_checked_at,
            "check_due": check_due(cache.last_checked_at, datetime.now(UTC)),
            "cached_manifest": cache.manifest,
        }

    @app.post("/desktop/release/check", include_in_schema=False)
    async def check_release(request: Request):
        _require_local_ui(request)
        return (
            UpdateChecker(store=store, installation_mode=installation_mode)
            .check(force=True)
            .model_dump(mode="json")
        )

    @app.put("/desktop/release/settings", include_in_schema=False)
    async def update_release_settings(request: Request):
        _require_local_ui(request)
        try:
            # Decoding belongs inside the guard: a malformed body raises JSONDecodeError,
            # which is a client mistake and must not surface as a server fault.
            preferences = UpdatePreferences.model_validate(await request.json())
            for version in preferences.skipped_versions:
                SemanticVersion.parse(version)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="Invalid update settings.") from exc
        store.save_preferences(preferences)
        return preferences.model_dump(mode="json")
