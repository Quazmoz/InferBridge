"""Secure packaged-desktop LAN configuration and endpoint discovery."""

from __future__ import annotations

import base64
import contextlib
import ctypes
import ipaddress
import os
import secrets
import socket
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings
from app.local_request_security import require_safe_browser_origin

LOOPBACK_BIND_HOST = "127.0.0.1"
LAN_BIND_HOST = "0.0.0.0"
_NETWORK_STATE_LAN = "lan_access_enabled"
_NETWORK_STATE_CORS = "network_cors_origins"
_DPAPI_ENTROPY = b"InferBridge/DesktopApiKey/v1"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LOOPBACK_CLIENTS = frozenset({"127.0.0.1", "localhost", "::1", "testclient"})
_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_NO_STORE_HEADERS = {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _dpapi_libraries() -> tuple[Any, Any]:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(ctypes.c_wchar_p),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


class DesktopApiKeyStore:
    """Persist the packaged API key with Windows DPAPI, never plaintext."""

    def __init__(self, config_dir: str | Path) -> None:
        self.key_path = Path(config_dir) / "api-key.dpapi"
        self._memory_key: str | None = None
        self._lock = threading.RLock()

    @property
    def persistence(self) -> str:
        return "windows_dpapi" if os.name == "nt" else "memory_only"

    @staticmethod
    def validate_user_key(value: str) -> str:
        key = str(value or "").strip()
        if not 24 <= len(key) <= 512:
            raise ValueError("API keys entered in the desktop UI must be 24 to 512 characters.")
        if any(char.isspace() or not char.isprintable() for char in key):
            raise ValueError("API keys cannot contain whitespace or control characters.")
        if "," in key:
            raise ValueError(
                "The desktop UI stores one API key. Comma-separated keys are environment-only."
            )
        return key

    @staticmethod
    def generate() -> str:
        return f"ib_{secrets.token_urlsafe(32)}"

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, Any]:
        buffer = ctypes.create_string_buffer(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    @classmethod
    def _protect_windows(cls, data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("DPAPI is available only on Windows.")
        crypt32, kernel32 = _dpapi_libraries()
        in_blob, in_buffer = cls._blob(data)
        entropy_blob, entropy_buffer = cls._blob(_DPAPI_ENTROPY)
        out_blob = _DataBlob()
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "InferBridge desktop API key",
            ctypes.byref(entropy_blob),
            None,
            None,
            0x1,
            ctypes.byref(out_blob),
        )
        _ = (in_buffer, entropy_buffer)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))

    @classmethod
    def _unprotect_windows(cls, data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("DPAPI is available only on Windows.")
        crypt32, kernel32 = _dpapi_libraries()
        in_blob, in_buffer = cls._blob(data)
        entropy_blob, entropy_buffer = cls._blob(_DPAPI_ENTROPY)
        out_blob = _DataBlob()
        description = ctypes.c_wchar_p()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            ctypes.byref(description),
            ctypes.byref(entropy_blob),
            None,
            None,
            0x1,
            ctypes.byref(out_blob),
        )
        _ = (in_buffer, entropy_buffer)
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(out_blob.pbData, ctypes.c_void_p))
            if description:
                kernel32.LocalFree(ctypes.cast(description, ctypes.c_void_p))

    def set_key(self, value: str) -> str:
        key = self.validate_user_key(value)
        with self._lock:
            if os.name != "nt":
                self._memory_key = key
                return key
            self.key_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = base64.b64encode(self._protect_windows(key.encode("utf-8")))
            temp = self.key_path.with_suffix(".tmp")
            try:
                temp.write_bytes(encoded)
                with contextlib.suppress(OSError):
                    os.chmod(temp, 0o600)
                temp.replace(self.key_path)
            except OSError:
                with contextlib.suppress(OSError):
                    temp.unlink()
                raise
            self._memory_key = key
            return key

    def _stored_key(self) -> str | None:
        if os.name != "nt" or not self.key_path.is_file() or self.key_path.is_symlink():
            return None
        try:
            encrypted = base64.b64decode(self.key_path.read_bytes(), validate=True)
            key = self._unprotect_windows(encrypted).decode("utf-8")
            return self.validate_user_key(key)
        except (OSError, ValueError, UnicodeError):
            return None

    def get_key(self) -> str | None:
        with self._lock:
            if self._memory_key:
                return self._memory_key
            key = self._stored_key()
            if key:
                self._memory_key = key
            return key

    def remove(self) -> bool:
        with self._lock:
            existed = bool(self._memory_key) or self.key_path.exists()
            self._memory_key = None
            with contextlib.suppress(OSError):
                self.key_path.unlink()
            return existed


def _clean_host(value: Any) -> str:
    return str(value or "").strip()


def is_loopback_host(host: str) -> bool:
    clean = _clean_host(host).lower().strip("[]")
    if clean in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(clean).is_loopback
    except ValueError:
        return False


def bind_exposes_network(host: str) -> bool:
    return not is_loopback_host(host)


def resolve_desktop_bind_host(
    state: Mapping[str, Any], env: Mapping[str, str] | None = None
) -> tuple[str, str]:
    values = os.environ if env is None else env
    configured = _clean_host(values.get("OV_LLM_HOST"))
    if configured:
        return configured, "environment"
    return (
        (LAN_BIND_HOST, "desktop_setting")
        if bool(state.get(_NETWORK_STATE_LAN, False))
        else (LOOPBACK_BIND_HOST, "default")
    )


def normalize_cors_origins(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts:
        return ""
    if "*" in parts:
        if len(parts) != 1:
            raise ValueError(
                "Wildcard CORS must be used by itself, not mixed with explicit origins."
            )
        return "*"
    normalized: list[str] = []
    for origin in parts:
        try:
            parsed = urlsplit(origin)
            _ = parsed.port
        except ValueError as exc:
            raise ValueError(f"Invalid browser origin: {origin}") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Browser origins must be complete http:// or https:// origins.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "Browser origins cannot contain credentials, query strings, or fragments."
            )
        if parsed.path not in {"", "/"}:
            raise ValueError("Browser origins cannot contain URL paths.")
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port is not None else ""
        candidate = f"{parsed.scheme}://{host}{port}"
        if candidate not in normalized:
            normalized.append(candidate)
    return ",".join(normalized)


@dataclass(frozen=True)
class DesktopNetworkResolution:
    settings: Settings
    host_source: str
    cors_source: str
    api_key_source: str | None
    lan_blocked_reason: str | None = None
    cors_blocked_reason: str | None = None


def resolve_desktop_network_settings(
    base: Settings,
    *,
    state: Mapping[str, Any],
    credential_store: DesktopApiKeyStore,
    env: Mapping[str, str] | None = None,
) -> DesktopNetworkResolution:
    values = os.environ if env is None else env
    host, host_source = resolve_desktop_bind_host(state, values)

    cors_blocked_reason = None
    if "OV_LLM_CORS_ORIGINS" in values:
        try:
            cors = normalize_cors_origins(str(values.get("OV_LLM_CORS_ORIGINS") or ""))
            cors_source = "environment"
        except ValueError as exc:
            cors = ""
            cors_source = "security_fallback"
            cors_blocked_reason = (
                f"OV_LLM_CORS_ORIGINS was not applied in packaged mode: {str(exc)[:220]}"
            )
    else:
        try:
            cors = normalize_cors_origins(str(state.get(_NETWORK_STATE_CORS) or ""))
        except ValueError as exc:
            cors = ""
            cors_blocked_reason = f"Saved browser origins were not applied: {str(exc)[:220]}"
        cors_source = (
            "desktop_setting"
            if cors
            else ("security_fallback" if cors_blocked_reason else "default")
        )

    if "OV_LLM_API_KEY" in values:
        api_key = str(values.get("OV_LLM_API_KEY") or "").strip() or None
        api_key_source = "environment" if api_key else None
    else:
        api_key = credential_store.get_key()
        api_key_source = "secure_store" if api_key else None

    blocked_reason = None
    if bind_exposes_network(host) and not api_key:
        if host_source == "environment":
            blocked_reason = (
                "OV_LLM_HOST requests network exposure, but no API key is configured. "
                "InferBridge stayed on 127.0.0.1 until authentication is configured."
            )
        else:
            blocked_reason = (
                "LAN access was requested, but the stored API key is unavailable. InferBridge "
                "stayed on 127.0.0.1 until a new API key is configured."
            )
        host = LOOPBACK_BIND_HOST
        host_source = "security_fallback"

    if cors == "*" and not api_key:
        cors_blocked_reason = (
            "Wildcard CORS was requested without API authentication. InferBridge kept CORS "
            "disabled until an API key is configured."
        )
        cors = ""
        cors_source = "security_fallback"

    settings = base.replace(host=host, cors_origins=cors, api_key=api_key)
    return DesktopNetworkResolution(
        settings=settings,
        host_source=host_source,
        cors_source=cors_source,
        api_key_source=api_key_source,
        lan_blocked_reason=blocked_reason,
        cors_blocked_reason=cors_blocked_reason,
    )


def _private_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(address.version == 4 and any(address in network for network in _RFC1918_NETWORKS))


def _primary_route_ipv4() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("192.0.2.1", 9))
            candidate = str(probe.getsockname()[0])
        return candidate if _private_ipv4(candidate) else None
    except OSError:
        return None


def detect_private_lan_ipv4(
    *,
    interface_addresses: Mapping[str, Any] | None = None,
    interface_stats: Mapping[str, Any] | None = None,
    primary_address: str | None = None,
) -> tuple[str, ...]:
    """Return usable RFC1918 IPv4 addresses, with the active route first when known."""
    if interface_addresses is None or interface_stats is None:
        try:
            import psutil

            interface_addresses = psutil.net_if_addrs()
            interface_stats = psutil.net_if_stats()
        except Exception:
            interface_addresses = {}
            interface_stats = {}
    addresses: list[str] = []
    for name, entries in (interface_addresses or {}).items():
        stat = (interface_stats or {}).get(name)
        if stat is not None and not bool(getattr(stat, "isup", False)):
            continue
        if stat is None:
            continue
        for entry in entries or ():
            if getattr(entry, "family", None) != socket.AF_INET:
                continue
            candidate = str(getattr(entry, "address", "") or "").strip()
            if _private_ipv4(candidate) and candidate not in addresses:
                addresses.append(candidate)

    primary = primary_address if primary_address is not None else _primary_route_ipv4()
    if primary and _private_ipv4(primary):
        if primary in addresses:
            addresses.remove(primary)
        addresses.insert(0, primary)
    return tuple(addresses)


def endpoint_url(host: str, port: int, *, path: str = "/v1") -> str:
    clean = _clean_host(host).strip("[]")
    if not 1 <= int(port) <= 65535:
        raise ValueError("Port must be between 1 and 65535.")
    if clean in {"0.0.0.0", "::", ""}:
        raise ValueError("A wildcard bind address is not a client destination.")
    rendered = f"[{clean}]" if ":" in clean else clean
    suffix = "/" + str(path or "").lstrip("/")
    return f"http://{rendered}:{int(port)}{suffix.rstrip('/') or '/'}"


def is_trusted_desktop_loopback_request(request: Request) -> bool:
    client = str(request.client.host if request.client else "").strip().lower()
    request_host = str(request.url.hostname or "").strip().lower()
    return client in _LOOPBACK_CLIENTS and request_host in _LOOPBACK_HOSTS


class DesktopNetworkStatusResponse(BaseModel):
    active_bind_host: str
    host_source: str
    lan_setting_enabled: bool
    lan_active: bool
    lan_blocked_reason: str | None = None
    local_endpoint: str
    lan_endpoints: list[str] = Field(default_factory=list)
    api_key_configured: bool
    api_key_source: str | None = None
    api_key_persistence: str | None = None
    cors_origins: str
    cors_source: str
    host_environment_override: bool
    cors_environment_override: bool
    api_key_environment_override: bool
    wildcard_cors: bool
    restart_required: bool
    warnings: list[str] = Field(default_factory=list)


class DesktopNetworkUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow_lan: bool
    cors_origins: str = Field(default="", max_length=2048)
    api_key: str | None = Field(default=None, max_length=512, repr=False)
    generate_api_key: bool = False
    remove_stored_api_key: bool = False
    acknowledge_wildcard_cors: bool = False


class DesktopNetworkUpdateResponse(BaseModel):
    status: DesktopNetworkStatusResponse
    generated_api_key: str | None = Field(default=None, repr=False)
    message: str


class DesktopNetworkService:
    def __init__(
        self,
        *,
        active_resolution: DesktopNetworkResolution,
        base_settings: Settings,
        paths: Any,
        state_store: Any,
        credential_store: DesktopApiKeyStore,
        endpoint_port: int,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self.active_resolution = active_resolution
        self.active_settings = active_resolution.settings
        self.base_settings = base_settings
        self.paths = paths
        self.state_store = state_store
        self.credential_store = credential_store
        self.endpoint_port = int(endpoint_port)
        self.env = os.environ if env is None else env

    def _desired(self, state: Mapping[str, Any]) -> DesktopNetworkResolution:
        return resolve_desktop_network_settings(
            self.base_settings,
            state=state,
            credential_store=self.credential_store,
            env=self.env,
        )

    def status(self) -> DesktopNetworkStatusResponse:
        state = self.state_store.load().state
        desired = self._desired(state)
        active = self.active_resolution
        active_lan = bind_exposes_network(active.settings.host)
        addresses = detect_private_lan_ipv4() if active_lan else ()
        lan_endpoints = [endpoint_url(address, self.endpoint_port) for address in addresses]
        restart_required = any(
            (
                desired.settings.host != active.settings.host,
                desired.settings.cors_origins != active.settings.cors_origins,
                desired.settings.api_key != active.settings.api_key,
            )
        )
        wildcard = desired.settings.cors_origins.strip() == "*"
        warnings: list[str] = []
        blocked = desired.lan_blocked_reason or active.lan_blocked_reason
        if blocked:
            warnings.append(blocked)
        cors_blocked = desired.cors_blocked_reason or active.cors_blocked_reason
        if cors_blocked:
            warnings.append(cors_blocked)
        if wildcard:
            warnings.append(
                "Wildcard CORS allows browser scripts from any origin to attempt API requests. "
                "Prefer explicit trusted browser origins."
            )
        if active_lan or bool(state.get(_NETWORK_STATE_LAN)):
            warnings.append(
                "Windows Firewall may prompt when LAN access starts. Allow InferBridge only on "
                "trusted Private network profiles, not Public profiles."
            )
        return DesktopNetworkStatusResponse(
            active_bind_host=active.settings.host,
            host_source=active.host_source,
            lan_setting_enabled=bool(state.get(_NETWORK_STATE_LAN, False)),
            lan_active=active_lan,
            lan_blocked_reason=blocked,
            local_endpoint=endpoint_url(LOOPBACK_BIND_HOST, self.endpoint_port),
            lan_endpoints=lan_endpoints,
            api_key_configured=bool(desired.settings.api_key),
            api_key_source=desired.api_key_source,
            api_key_persistence=(
                self.credential_store.persistence
                if desired.api_key_source == "secure_store"
                else None
            ),
            cors_origins=desired.settings.cors_origins,
            cors_source=desired.cors_source,
            host_environment_override=bool(_clean_host(self.env.get("OV_LLM_HOST"))),
            cors_environment_override="OV_LLM_CORS_ORIGINS" in self.env,
            api_key_environment_override="OV_LLM_API_KEY" in self.env,
            wildcard_cors=wildcard,
            restart_required=restart_required,
            warnings=warnings,
        )

    def update(self, request: DesktopNetworkUpdateRequest) -> DesktopNetworkUpdateResponse:
        if request.api_key and request.generate_api_key:
            raise ValueError("Enter an API key or generate one, not both.")
        if request.remove_stored_api_key and (request.api_key or request.generate_api_key):
            raise ValueError("Remove the stored API key or replace it, not both at once.")
        if "OV_LLM_API_KEY" in self.env and (
            request.api_key or request.generate_api_key or request.remove_stored_api_key
        ):
            raise ValueError("OV_LLM_API_KEY is controlling authentication for this process.")

        cors = normalize_cors_origins(request.cors_origins)
        generated: str | None = None
        replacement_key: str | None = None
        if request.generate_api_key:
            generated = DesktopApiKeyStore.generate()
            replacement_key = DesktopApiKeyStore.validate_user_key(generated)
        elif request.api_key:
            replacement_key = DesktopApiKeyStore.validate_user_key(request.api_key)

        if "OV_LLM_API_KEY" in self.env:
            proposed_key = str(self.env.get("OV_LLM_API_KEY") or "").strip() or None
        elif replacement_key:
            proposed_key = replacement_key
        elif request.remove_stored_api_key:
            proposed_key = None
        else:
            proposed_key = self.credential_store.get_key()

        if request.allow_lan and not proposed_key:
            raise ValueError("LAN access requires an API key before it can be enabled.")
        if cors == "*":
            if not proposed_key:
                raise ValueError("Wildcard CORS requires an API key in the desktop application.")
            if not request.acknowledge_wildcard_cors:
                raise ValueError("Confirm the wildcard CORS warning before applying '*'.")

        if replacement_key:
            self.credential_store.set_key(replacement_key)
        elif request.remove_stored_api_key:
            self.credential_store.remove()

        self.state_store.update(
            **{
                _NETWORK_STATE_LAN: bool(request.allow_lan),
                _NETWORK_STATE_CORS: cors,
            }
        )
        status = self.status()
        return DesktopNetworkUpdateResponse(
            status=status,
            generated_api_key=generated,
            message=(
                "Network settings saved. Restart InferBridge to apply the listener changes."
                if status.restart_required
                else "Network settings saved."
            ),
        )


def _local_ui_dependency():
    async def require_local_ui(
        request: Request,
        x_ov_llm_ui: str | None = Header(default=None),
    ) -> None:
        require_safe_browser_origin(request)
        if not is_trusted_desktop_loopback_request(request) or x_ov_llm_ui != "1":
            raise HTTPException(
                status_code=403, detail="Network settings are available only locally."
            )

    return require_local_ui


def register_desktop_network_routes(app: Any, *, service: DesktopNetworkService) -> None:
    local_ui = [Depends(_local_ui_dependency())]

    @app.get(
        "/internal/desktop-network",
        response_model=DesktopNetworkStatusResponse,
        include_in_schema=False,
        dependencies=local_ui,
    )
    async def desktop_network_status():
        response = service.status()
        from fastapi.responses import JSONResponse

        return JSONResponse(response.model_dump(mode="json"), headers=_NO_STORE_HEADERS)

    @app.post(
        "/internal/desktop-network",
        response_model=DesktopNetworkUpdateResponse,
        include_in_schema=False,
        dependencies=local_ui,
    )
    async def desktop_network_update(body: DesktopNetworkUpdateRequest):
        try:
            response = service.update(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail="The desktop API key could not be saved securely.",
            ) from exc
        from fastapi.responses import JSONResponse

        return JSONResponse(response.model_dump(mode="json"), headers=_NO_STORE_HEADERS)


__all__ = [
    "DesktopApiKeyStore",
    "DesktopNetworkResolution",
    "DesktopNetworkService",
    "DesktopNetworkStatusResponse",
    "DesktopNetworkUpdateRequest",
    "LAN_BIND_HOST",
    "LOOPBACK_BIND_HOST",
    "bind_exposes_network",
    "detect_private_lan_ipv4",
    "endpoint_url",
    "is_trusted_desktop_loopback_request",
    "normalize_cors_origins",
    "register_desktop_network_routes",
    "resolve_desktop_bind_host",
    "resolve_desktop_network_settings",
]
