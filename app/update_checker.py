"""Privacy-preserving, opt-in release discovery for official GitHub releases."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.release_models import (
    InstallationMode,
    ReleaseChannel,
    ReleaseManifest,
    SemanticVersion,
    channel_accepts,
    is_official_release_url,
    platform_compatibility,
    schema_compatibility,
)
from app.version import DATA_SCHEMA_VERSION, __version__

_RELEASES_API = "https://api.github.com/repos/Quazmoz/openvino-windows-llm/releases?per_page=20"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_DEFAULT_INTERVAL = timedelta(hours=24)


class UpdatePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    channel: ReleaseChannel = "stable"
    skipped_versions: list[str] = Field(default_factory=list, max_length=50)


class UpdateCache(BaseModel):
    model_config = ConfigDict(extra="forbid")

    releases_etag: str | None = None
    last_checked_at: datetime | None = None
    latest_checked_version: str | None = None
    manifest: dict | None = None


class UpdateCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["disabled", "not_due", "offline", "current", "available", "rejected"]
    current_version: str = __version__
    latest_version: str | None = None
    checked_at: datetime | None = None
    manifest: ReleaseManifest | None = None
    selected_artifact_type: str | None = None
    compatibility_warning: str | None = None
    message: str | None = None


class UpdateStore:
    def __init__(self, config_dir: Path) -> None:
        self.preferences_path = config_dir / "update-settings.json"
        self.cache_path = config_dir / "update-cache.json"

    @staticmethod
    def _load(path: Path, model_type, default):
        try:
            return model_type.model_validate_json(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write(path: Path, model: BaseModel) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)

    def load_preferences(self) -> UpdatePreferences:
        return self._load(self.preferences_path, UpdatePreferences, UpdatePreferences())

    def save_preferences(self, preferences: UpdatePreferences) -> None:
        self._write(self.preferences_path, preferences)

    def load_cache(self) -> UpdateCache:
        return self._load(self.cache_path, UpdateCache, UpdateCache())

    def save_cache(self, cache: UpdateCache) -> None:
        self._write(self.cache_path, cache)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def check_due(
    last_checked_at: datetime | None,
    now: datetime,
    interval: timedelta = _DEFAULT_INTERVAL,
) -> bool:
    if last_checked_at is None:
        return True
    current = _as_utc(now)
    previous = _as_utc(last_checked_at)
    if previous > current:
        # A corrected system clock must not suppress updates until a future cache
        # timestamp is reached.
        return True
    return current - previous >= interval


def _read_json(response) -> object:
    raw = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError("Update response exceeded the size limit.")
    return json.loads(raw.decode("utf-8"))


def _candidate_manifest_url(releases: object, channel: ReleaseChannel) -> tuple[str, str] | None:
    if not isinstance(releases, list):
        raise ValueError("GitHub releases response is not a list.")
    candidates: list[tuple[SemanticVersion, str, str]] = []
    for release in releases:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        if channel == "stable" and release.get("prerelease"):
            continue
        tag = str(release.get("tag_name") or "")
        version = tag[1:] if tag.startswith("v") else tag
        try:
            parsed = SemanticVersion.parse(version)
        except ValueError:
            continue
        if not channel_accepts(channel, version):
            continue
        expected = f"OpenVINO-Windows-LLM-{version}-release-manifest.json"
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict) or asset.get("name") != expected:
                continue
            url = str(asset.get("browser_download_url") or "")
            if is_official_release_url(url):
                candidates.append((parsed, version, url))
    if not candidates:
        return None
    _parsed, version, url = max(candidates, key=lambda item: item[0])
    return version, url


class UpdateChecker:
    def __init__(
        self,
        *,
        store: UpdateStore,
        installation_mode: InstallationMode,
        opener: Callable = urllib.request.urlopen,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout_seconds: float = 4.0,
    ) -> None:
        self.store = store
        self.installation_mode = installation_mode
        self.opener = opener
        self.now = now
        self.timeout_seconds = max(float(timeout_seconds), 0.1)

    def _validated_cached_manifest(self, cache: UpdateCache) -> ReleaseManifest | None:
        if not cache.manifest:
            return None
        try:
            return ReleaseManifest.model_validate(cache.manifest)
        except ValueError:
            cache.releases_etag = None
            cache.last_checked_at = None
            cache.latest_checked_version = None
            cache.manifest = None
            self.store.save_cache(cache)
            return None

    def check(self, *, force: bool = False) -> UpdateCheckResult:
        preferences = self.store.load_preferences()
        cache = self.store.load_cache()
        cached_manifest = self._validated_cached_manifest(cache)
        checked_at = _as_utc(self.now())
        if not preferences.enabled:
            return UpdateCheckResult(status="disabled", checked_at=cache.last_checked_at)
        if not force and not check_due(cache.last_checked_at, checked_at):
            return self._result_for_manifest(
                cached_manifest, preferences, "not_due", cache.last_checked_at
            )

        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"OpenVINO-Windows-LLM/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if cache.releases_etag:
            headers["If-None-Match"] = cache.releases_etag
        request = urllib.request.Request(_RELEASES_API, headers=headers)
        try:
            with self.opener(request, timeout=self.timeout_seconds) as response:
                releases = _read_json(response)
                etag = response.headers.get("ETag")
            candidate = _candidate_manifest_url(releases, preferences.channel)
            if candidate is None:
                cache.last_checked_at = checked_at
                cache.latest_checked_version = None
                cache.manifest = None
                cache.releases_etag = etag
                self.store.save_cache(cache)
                return UpdateCheckResult(status="current", checked_at=checked_at)
            release_version, manifest_url = candidate
            manifest_request = urllib.request.Request(
                manifest_url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": f"OpenVINO-Windows-LLM/{__version__}",
                },
            )
            with self.opener(manifest_request, timeout=self.timeout_seconds) as response:
                manifest = ReleaseManifest.model_validate(_read_json(response))
            channel_allowed = (
                manifest.channel == "stable"
                if preferences.channel == "stable"
                else manifest.channel in {"stable", preferences.channel}
            )
            if (
                manifest.version != release_version
                or not channel_accepts(preferences.channel, manifest.version)
                or not channel_allowed
            ):
                raise ValueError(
                    "Release manifest version or channel does not match the GitHub release."
                )
            cache.releases_etag = etag
            cache.last_checked_at = checked_at
            cache.latest_checked_version = manifest.version
            cache.manifest = manifest.model_dump(mode="json")
            self.store.save_cache(cache)
            return self._result_for_manifest(manifest, preferences, "current", checked_at)
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached_manifest:
                cache.last_checked_at = checked_at
                self.store.save_cache(cache)
                return self._result_for_manifest(
                    cached_manifest, preferences, "current", checked_at
                )
            return UpdateCheckResult(
                status="offline", checked_at=checked_at, message="Update check unavailable."
            )
        except ValueError:
            return UpdateCheckResult(
                status="rejected",
                checked_at=checked_at,
                message="The published update metadata was invalid and was rejected.",
                compatibility_warning="The published update metadata could not be validated.",
            )
        except (TimeoutError, OSError):
            return UpdateCheckResult(
                status="offline", checked_at=checked_at, message="Update check unavailable."
            )

    def _result_for_manifest(
        self,
        manifest: ReleaseManifest | None,
        preferences: UpdatePreferences,
        fallback_status: Literal["not_due", "current"],
        checked_at: datetime | None,
    ) -> UpdateCheckResult:
        if manifest is None:
            return UpdateCheckResult(status=fallback_status, checked_at=checked_at)
        latest = SemanticVersion.parse(manifest.version)
        current = SemanticVersion.parse(__version__)
        if latest <= current or manifest.version in preferences.skipped_versions:
            return UpdateCheckResult(
                status=fallback_status,
                latest_version=manifest.version,
                checked_at=checked_at,
                manifest=manifest,
            )
        schema_ok, schema_warning = schema_compatibility(
            DATA_SCHEMA_VERSION, manifest.data_compatibility
        )
        platform_ok, platform_warning = platform_compatibility(manifest)
        artifact = manifest.select_artifact(self.installation_mode)
        if not schema_ok or not platform_ok or artifact is None:
            return UpdateCheckResult(
                status="rejected",
                latest_version=manifest.version,
                checked_at=checked_at,
                manifest=manifest,
                compatibility_warning=(
                    schema_warning or platform_warning or "No compatible artifact is available."
                ),
            )
        return UpdateCheckResult(
            status="available",
            latest_version=manifest.version,
            checked_at=checked_at,
            manifest=manifest,
            selected_artifact_type=artifact.type,
        )
