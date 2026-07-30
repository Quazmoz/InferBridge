"""Runtime resource and writable-data path resolution for desktop distributions."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from app.brand import DATA_DIR_NAME, DISPLAY_NAME, LEGACY_DATA_DIR_NAME

_APP_DIR_NAME = DATA_DIR_NAME
_TRUTHY = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimePaths:
    resource_root: Path
    data_root: Path
    config_dir: Path
    logs_dir: Path
    models_dir: Path
    huggingface_cache_dir: Path
    compiled_cache_dir: Path
    benchmarks_dir: Path
    diagnostics_dir: Path
    onboarding_dir: Path
    models_file: Path
    portable: bool
    packaged: bool

    @property
    def onboarding_file(self) -> Path:
        return self.onboarding_dir / "state.json"

    @property
    def launcher_metadata_file(self) -> Path:
        return self.data_root / "desktop-instance.json"

    @property
    def launcher_lock_file(self) -> Path:
        return self.data_root / "desktop-instance.lock"

    @property
    def tray_heartbeat_file(self) -> Path:
        return self.data_root / "tray-heartbeat.json"

    @property
    def tray_command_file(self) -> Path:
        return self.data_root / "tray-command.json"

    @property
    def restart_request_file(self) -> Path:
        return self.data_root / "restart-server.request"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def packaged_resource_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root).resolve()
    return Path(__file__).resolve().parent.parent


def executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return packaged_resource_root()


def _default_local_app_data(env: Mapping[str, str]) -> Path:
    configured = str(env.get("LOCALAPPDATA") or "").strip()
    if configured:
        return Path(configured).expanduser()
    if os.name == "nt":
        return Path.home() / "AppData" / "Local"
    return Path.home() / ".local" / "share"


def _contains_data(path: Path) -> bool:
    try:
        return path.is_dir() and next(path.iterdir(), None) is not None
    except OSError:
        return False


def _desktop_data_root(env: Mapping[str, str]) -> Path:
    local_app_data = _default_local_app_data(env)
    current = local_app_data / DATA_DIR_NAME
    legacy = local_app_data / LEGACY_DATA_DIR_NAME
    if current.exists():
        if _contains_data(current) and _contains_data(legacy):
            warnings.warn(
                "Both InferBridge and legacy application data directories contain data; "
                "InferBridge was selected without moving or merging files.",
                RuntimeWarning,
                stacklevel=2,
            )
        return current
    if legacy.exists():
        return legacy
    return current


def resolve_runtime_paths(
    *,
    portable: bool | None = None,
    desktop: bool | None = None,
    env: Mapping[str, str] | None = None,
) -> RuntimePaths:
    values = os.environ if env is None else env
    packaged = bool(getattr(sys, "frozen", False))
    desktop_mode = packaged or _truthy(values.get("OV_LLM_DESKTOP")) if desktop is None else desktop
    portable_mode = _truthy(values.get("OV_LLM_PORTABLE")) if portable is None else portable
    resource_root = packaged_resource_root()

    explicit_root = str(values.get("OV_LLM_DATA_DIR") or "").strip()
    if explicit_root:
        data_root = Path(explicit_root).expanduser()
    elif portable_mode:
        data_root = executable_dir() / "data"
    elif desktop_mode:
        data_root = _desktop_data_root(values)
    else:
        data_root = resource_root

    data_root = data_root.resolve()
    if desktop_mode:
        config_dir = data_root / "config"
        models_dir = data_root / "models"
        models_file = config_dir / "models.json"
        logs_dir = data_root / "logs"
        hf_cache = data_root / "cache" / "huggingface"
        compiled_cache = data_root / "cache" / "openvino"
        benchmarks = data_root / "benchmarks"
        diagnostics = data_root / "diagnostics"
        onboarding = data_root / "onboarding"
    else:
        config_dir = resource_root
        models_dir = resource_root / "models" / "openvino"
        models_file = resource_root / "models.json"
        logs_dir = resource_root / "logs"
        hf_cache = resource_root / "models" / "cache" / "huggingface"
        compiled_cache = resource_root / "models" / "cache"
        benchmarks = resource_root / "benchmark" / "results"
        diagnostics = resource_root / "diagnostics"
        onboarding = resource_root / ".state" / "onboarding"

    return RuntimePaths(
        resource_root=resource_root,
        data_root=data_root,
        config_dir=config_dir,
        logs_dir=logs_dir,
        models_dir=models_dir,
        huggingface_cache_dir=hf_cache,
        compiled_cache_dir=compiled_cache,
        benchmarks_dir=benchmarks,
        diagnostics_dir=diagnostics,
        onboarding_dir=onboarding,
        models_file=models_file,
        portable=portable_mode,
        packaged=packaged,
    )


def ensure_runtime_directories(paths: RuntimePaths) -> None:
    for directory in (
        paths.data_root,
        paths.config_dir,
        paths.logs_dir,
        paths.models_dir,
        paths.huggingface_cache_dir,
        paths.compiled_cache_dir,
        paths.benchmarks_dir,
        paths.diagnostics_dir,
        paths.onboarding_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def ensure_data_root_writable(paths: RuntimePaths) -> None:
    ensure_runtime_directories(paths)
    probe = paths.data_root / ".write-test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"{DISPLAY_NAME} cannot write to its application data directory. "
            "Choose a writable portable location or set OV_LLM_DATA_DIR."
        ) from exc


def _rebased_entry(raw: dict, models_dir: Path, model_id: str) -> dict:
    entry = dict(raw)
    configured = Path(str(entry.get("model_path") or model_id))
    leaf_name = configured.name or model_id
    entry["model_path"] = str((models_dir / leaf_name).resolve())
    return entry


def materialize_user_catalog(paths: RuntimePaths) -> Path:
    if paths.models_file == paths.resource_root / "models.json":
        return paths.models_file

    ensure_runtime_directories(paths)
    source_file = paths.resource_root / "models.json"
    if not source_file.is_file():
        raise RuntimeError("The packaged model catalog is missing.")

    source = json.loads(source_file.read_text(encoding="utf-8-sig"))
    if not isinstance(source, dict):
        raise RuntimeError("The packaged model catalog is invalid.")

    existing: dict = {}
    if paths.models_file.exists():
        try:
            parsed = json.loads(paths.models_file.read_text(encoding="utf-8-sig"))
            if not isinstance(parsed, dict):
                raise ValueError("catalog is not an object")
            existing = parsed
        except (OSError, ValueError, json.JSONDecodeError):
            backup = paths.models_file.with_suffix(".json.corrupt")
            with contextlib.suppress(OSError):
                shutil.copy2(paths.models_file, backup)
            existing = {}

    changed = not paths.models_file.exists()
    for model_id, raw in source.items():
        if model_id in existing or not isinstance(raw, dict):
            continue
        existing[model_id] = _rebased_entry(raw, paths.models_dir, model_id)
        changed = True

    if changed:
        temp = paths.models_file.with_suffix(".json.tmp")
        temp.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        temp.replace(paths.models_file)
    return paths.models_file
