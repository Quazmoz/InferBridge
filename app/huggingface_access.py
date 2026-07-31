"""Secure Hugging Face credentials and gated-model access preflight.

Windows persists user-supplied tokens with DPAPI. Other platforms keep tokens in
memory for the current process only. ``HF_TOKEN`` remains a read-only advanced
fallback and is never returned by an API response.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import contextvars
import ctypes
import functools
import hashlib
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.brand import DISPLAY_NAME, LEGACY_DISPLAY_NAME
from app.local_request_security import require_safe_browser_origin

_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
_HF_TOKEN_RE = re.compile(r"^hf_[A-Za-z0-9]{8,500}$")
_DPAPI_ENTROPY = b"InferBridge/HuggingFace/v1"
_TOKEN_CONTEXT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "inferbridge_hf_token", default=None
)
_ACCESS_CACHE_TTL_SECONDS = 60.0
_KNOWN_GATED_REPOS = frozenset(
    {
        "meta-llama/Llama-3.2-3B-Instruct",
        "meta-llama/Llama-3.2-1B-Instruct",
        "meta-llama/Llama-3.1-8B-Instruct",
        "google/gemma-2-2b-it",
    }
)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _safe_repo_id(value: str) -> str:
    repo_id = str(value or "").strip()
    if not _HF_REPO_RE.fullmatch(repo_id):
        raise ValueError("Hugging Face model IDs must use the form owner/model.")
    return repo_id


def _token_fingerprint(token: str | None) -> str:
    if not token:
        return "anonymous"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _model_url(repo_id: str) -> str:
    return f"https://huggingface.co/{quote(repo_id, safe='/')}"


def _utc_timestamp() -> int:
    return int(time.time())


def _access_payload(
    code: str,
    message: str,
    *,
    source_model: str | None = None,
    token_configured: bool = False,
    username: str | None = None,
    recoverable: bool = True,
    action: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "code": code,
        "message": message,
        "recoverable": recoverable,
        "token_configured": token_configured,
    }
    if source_model:
        payload.update(
            {
                "source_model": source_model,
                "model_url": _model_url(source_model),
                "license_url": _model_url(source_model),
            }
        )
    if username:
        payload["username"] = username
    if action:
        payload["action"] = action
    return payload


class HuggingFaceCredentialStore:
    """Store one Hugging Face token without exposing it through status APIs."""

    def __init__(self, settings: Any) -> None:
        config_dir = Path(settings.models_file).expanduser().resolve().parent
        self.token_path = config_dir / "huggingface-token.dpapi"
        self.metadata_path = config_dir / "huggingface-access.json"
        self._memory_token: str | None = None
        self._lock = threading.RLock()

    @property
    def persistence(self) -> str:
        return "windows_dpapi" if os.name == "nt" else "memory_only"

    def _environment_token(self) -> str | None:
        value = (
            os.environ.get("HF_TOKEN")
            or os.environ.get("HUGGING_FACE_HUB_TOKEN")
            or ""
        ).strip()
        return value or None

    @staticmethod
    def validate_token(token: str) -> str:
        clean = str(token or "").strip()
        if not _HF_TOKEN_RE.fullmatch(clean):
            raise ValueError(
                "Enter a valid Hugging Face user access token beginning with hf_."
            )
        return clean

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, Any]:
        buffer = ctypes.create_string_buffer(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        return blob, buffer

    @classmethod
    def _protect_windows(cls, data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("DPAPI is available only on Windows.")
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_blob, in_buffer = cls._blob(data)
        entropy_blob, entropy_buffer = cls._blob(_DPAPI_ENTROPY)
        out_blob = _DataBlob()
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
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "InferBridge Hugging Face token",
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
            kernel32.LocalFree(out_blob.pbData)

    @classmethod
    def _unprotect_windows(cls, data: bytes) -> bytes:
        if os.name != "nt":
            raise OSError("DPAPI is available only on Windows.")
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        in_blob, in_buffer = cls._blob(data)
        entropy_blob, entropy_buffer = cls._blob(_DPAPI_ENTROPY)
        out_blob = _DataBlob()
        description = ctypes.c_wchar_p()
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
            kernel32.LocalFree(out_blob.pbData)
            if description:
                kernel32.LocalFree(description)

    def set_token(self, token: str) -> None:
        clean = self.validate_token(token)
        with self._lock:
            self._memory_token = clean
            if os.name != "nt":
                return
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            protected = self._protect_windows(clean.encode("utf-8"))
            encoded = base64.b64encode(protected)
            temp = self.token_path.with_suffix(".tmp")
            temp.write_bytes(encoded)
            with contextlib.suppress(OSError):
                os.chmod(temp, 0o600)
            temp.replace(self.token_path)

    def get_token(self) -> str | None:
        with self._lock:
            if self._memory_token:
                return self._memory_token
            if os.name == "nt" and self.token_path.is_file():
                try:
                    encrypted = base64.b64decode(self.token_path.read_bytes(), validate=True)
                    token = self._unprotect_windows(encrypted).decode("utf-8")
                    self._memory_token = self.validate_token(token)
                    return self._memory_token
                except (OSError, ValueError, UnicodeError):
                    pass
            return self._environment_token()

    def source(self) -> str | None:
        with self._lock:
            if self._memory_token:
                return "secure_store"
            if os.name == "nt" and self.token_path.is_file():
                try:
                    encrypted = base64.b64decode(self.token_path.read_bytes(), validate=True)
                    token = self._unprotect_windows(encrypted).decode("utf-8")
                    self._memory_token = self.validate_token(token)
                    return "secure_store"
                except (OSError, ValueError, UnicodeError):
                    pass
            if self._environment_token():
                return "environment"
            return None

    def remove(self) -> bool:
        with self._lock:
            existed = bool(self._memory_token) or self.token_path.exists()
            self._memory_token = None
            with contextlib.suppress(OSError):
                self.token_path.unlink()
            self.write_metadata({})
            return existed

    def read_metadata(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_metadata(self, payload: dict[str, Any]) -> None:
        safe = {
            "state": str(payload.get("state") or "unverified")[:40],
            "username": str(payload.get("username") or "")[:200] or None,
            "last_checked": int(payload.get("last_checked") or 0) or None,
        }
        try:
            self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.metadata_path.with_suffix(".tmp")
            temp.write_text(json.dumps(safe, separators=(",", ":")), encoding="utf-8")
            with contextlib.suppress(OSError):
                os.chmod(temp, 0o600)
            temp.replace(self.metadata_path)
        except OSError:
            pass

    def status(self) -> dict[str, Any]:
        token = self.get_token()
        source = self.source()
        metadata = self.read_metadata()
        return {
            "configured": bool(token),
            "status": metadata.get("state")
            or ("unverified" if token else "not_configured"),
            "username": metadata.get("username"),
            "last_checked": metadata.get("last_checked"),
            "token_masked": "••••••••" if token else None,
            "source": source,
            "persistence": self.persistence if source == "secure_store" else source,
            "removable": source == "secure_store",
        }


class HuggingFaceAccessService:
    def __init__(
        self,
        store: HuggingFaceCredentialStore,
        *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self.store = store
        self._client_factory = client_factory
        self._cache: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
        self._cache_lock = asyncio.Lock()

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=True,
            headers={"User-Agent": "InferBridge/0.7 HuggingFaceAccess"},
        )

    def status(self) -> dict[str, Any]:
        return self.store.status()

    def _record(self, state: str, username: str | None = None) -> None:
        self.store.write_metadata(
            {"state": state, "username": username, "last_checked": _utc_timestamp()}
        )

    async def test_token(
        self, token: str | None = None, *, persist: bool = False
    ) -> dict[str, Any]:
        candidate = token.strip() if isinstance(token, str) else self.store.get_token()
        if not candidate:
            self._record("not_configured")
            return _access_payload(
                "hf_token_missing",
                "Add a Hugging Face token to access gated models.",
                action="configure_token",
            )
        try:
            candidate = self.store.validate_token(candidate)
        except ValueError as exc:
            return _access_payload(
                "hf_token_invalid",
                str(exc),
                token_configured=True,
                action="replace_token",
            )

        try:
            async with self._client() as client:
                response = await client.get(
                    "https://huggingface.co/api/whoami-v2",
                    headers={"Authorization": f"Bearer {candidate}"},
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            self._record("network_error")
            return _access_payload(
                "hf_network_error",
                "Hugging Face could not be reached. Check the connection and try again.",
                token_configured=True,
                action="test_again",
            )
        except httpx.HTTPError:
            self._record("network_error")
            return _access_payload(
                "hf_network_error",
                "Hugging Face access could not be verified.",
                token_configured=True,
                action="test_again",
            )

        if response.status_code == 200:
            try:
                data = response.json() if response.content else {}
            except (ValueError, json.JSONDecodeError):
                data = {}
            username = str(data.get("name") or data.get("fullname") or "").strip() or None
            if persist:
                try:
                    self.store.set_token(candidate)
                except OSError:
                    return _access_payload(
                        "hf_secure_storage_error",
                        "The token was valid, but Windows secure storage could not save it.",
                        token_configured=False,
                        username=username,
                        action="replace_token",
                    )
            self._record("connected", username)
            return _access_payload(
                "hf_access_granted",
                "Hugging Face access is connected.",
                token_configured=True,
                username=username,
                action="none",
            )
        if response.status_code == 429:
            self._record("rate_limited")
            return _access_payload(
                "hf_rate_limited",
                "Hugging Face is rate limiting access checks. Try again shortly.",
                token_configured=True,
                action="test_again",
            )
        self._record("invalid_token")
        return _access_payload(
            "hf_token_invalid",
            "The Hugging Face token is invalid, expired, or lacks read access.",
            token_configured=True,
            action="replace_token",
        )

    async def preflight(
        self, source_model: str, *, access_type: str = "unknown"
    ) -> dict[str, Any]:
        repo_id = _safe_repo_id(source_model)
        normalized_type = str(access_type or "unknown").strip().lower()
        if normalized_type not in {"public", "gated", "unknown"}:
            normalized_type = "unknown"
        token = self.store.get_token()
        if normalized_type == "public":
            return _access_payload(
                "hf_access_granted",
                "This model is public on Hugging Face.",
                source_model=repo_id,
                token_configured=bool(token),
                action="none",
            )
        if normalized_type == "gated" and not token:
            self._record("token_required")
            return _access_payload(
                "hf_token_missing",
                "This model requires publisher approval and a Hugging Face token.",
                source_model=repo_id,
                action="configure_token",
            )

        cache_key = (repo_id, _token_fingerprint(token), normalized_type)
        now = time.monotonic()
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < _ACCESS_CACHE_TTL_SECONDS:
                return dict(cached[1])

        username: str | None = None
        if token:
            token_result = await self.test_token()
            if token_result["code"] != "hf_access_granted":
                return token_result | {
                    "source_model": repo_id,
                    "model_url": _model_url(repo_id),
                    "license_url": _model_url(repo_id),
                }
            username = token_result.get("username")

        headers = {"Range": "bytes=0-0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        probe_url = f"{_model_url(repo_id)}/resolve/main/config.json"
        try:
            async with self._client() as client:
                response = await client.get(probe_url, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError):
            result = _access_payload(
                "hf_network_error",
                "Hugging Face could not be reached. The conversion was not queued.",
                source_model=repo_id,
                token_configured=bool(token),
                username=username,
                action="check_again",
            )
        except httpx.HTTPError:
            result = _access_payload(
                "hf_network_error",
                "Hugging Face model access could not be verified.",
                source_model=repo_id,
                token_configured=bool(token),
                username=username,
                action="check_again",
            )
        else:
            if response.status_code in {200, 206}:
                self._record("connected", username)
                result = _access_payload(
                    "hf_access_granted",
                    "Hugging Face model access is ready.",
                    source_model=repo_id,
                    token_configured=bool(token),
                    username=username,
                    action="none",
                )
            elif response.status_code == 429:
                result = _access_payload(
                    "hf_rate_limited",
                    "Hugging Face is rate limiting access checks. Try again shortly.",
                    source_model=repo_id,
                    token_configured=bool(token),
                    username=username,
                    action="check_again",
                )
            elif token:
                self._record("approval_required", username)
                result = _access_payload(
                    "hf_approval_required",
                    "Your token is valid, but this account has not been approved for the model.",
                    source_model=repo_id,
                    token_configured=True,
                    username=username,
                    action="open_model_agreement",
                )
            else:
                self._record("token_required")
                result = _access_payload(
                    "hf_token_missing",
                    "This model is gated or private. Configure a Hugging Face token to continue.",
                    source_model=repo_id,
                    action="configure_token",
                )

        async with self._cache_lock:
            self._cache[cache_key] = (now, dict(result))
        return result

    async def clear_cache(self) -> None:
        async with self._cache_lock:
            self._cache.clear()


class TokenRequest(BaseModel):
    token: str = Field(min_length=11, max_length=503)


class PreflightRequest(BaseModel):
    model_id: str | None = Field(default=None, max_length=128)
    source_model: str | None = Field(default=None, max_length=260)
    access_type: str | None = Field(default=None, max_length=20)


def _metadata_from_catalog(
    settings: Any, model_id: str | None, source_model: str
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "access_type": "unknown",
        "model_url": _model_url(source_model),
        "license_url": _model_url(source_model),
    }
    if not model_id:
        if source_model in _KNOWN_GATED_REPOS:
            metadata["access_type"] = "gated"
        return metadata

    entries: list[dict[str, Any]] = []
    catalog_paths = [
        Path(settings.models_file),
        Path(__file__).resolve().parent.parent / "models.json",
    ]
    for path in dict.fromkeys(catalog_paths):
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            entry = raw.get(model_id) if isinstance(raw, dict) else None
        except (OSError, ValueError, json.JSONDecodeError):
            entry = None
        if isinstance(entry, dict):
            entries.append(entry)

    for entry in entries:
        explicit = str(entry.get("access_type") or "").strip().lower()
        if explicit in {"public", "gated"}:
            metadata["access_type"] = explicit
            break
    if metadata["access_type"] == "unknown":
        metadata["access_type"] = (
            "gated" if source_model in _KNOWN_GATED_REPOS else "public"
        )

    for entry in entries:
        if entry.get("model_url"):
            metadata["model_url"] = str(entry["model_url"])
            break
    for entry in entries:
        if entry.get("license_url"):
            metadata["license_url"] = str(entry["license_url"])
            break
    if not metadata["license_url"]:
        metadata["license_url"] = metadata["model_url"]
    return metadata


def _store_for_state(state: Any) -> HuggingFaceCredentialStore:
    manager = getattr(state, "manager", None)
    store = getattr(manager, "_hf_credential_store", None)
    if store is not None:
        return store
    settings = getattr(state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Server settings are unavailable.")
    store = HuggingFaceCredentialStore(settings)
    if manager is not None:
        manager._hf_credential_store = store
    return store


def _service_for_state(state: Any) -> HuggingFaceAccessService:
    service = getattr(state, "huggingface_access_service", None)
    if service is None:
        service = HuggingFaceAccessService(_store_for_state(state))
        state.huggingface_access_service = service
    return service


async def _require_access(
    request: Request,
    authorization: str | None = Header(default=None),
) -> None:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="Server settings are unavailable.")
    if request.method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        require_safe_browser_origin(request)
    configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
    if not configured:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    supplied = authorization.removeprefix("Bearer ")
    if not any(secrets.compare_digest(supplied, key) for key in configured):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _authorized_for_preflight(scope: dict[str, Any], settings: Any) -> bool:
    configured = [item.strip() for item in (settings.api_key or "").split(",") if item.strip()]
    if not configured:
        return True
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    raw = headers.get(b"authorization", b"").decode("latin-1")
    if not raw.startswith("Bearer "):
        return False
    supplied = raw.removeprefix("Bearer ")
    return any(secrets.compare_digest(supplied, key) for key in configured)


class HuggingFacePreflightMiddleware:
    """Reject gated conversions before a background operation is queued."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path not in {"/v1/models/convert", "/v1/models/download-custom"}:
            await self.app(scope, receive, send)
            return

        app = scope.get("app")
        state = getattr(app, "state", None)
        settings = getattr(state, "settings", None)
        manager = getattr(state, "manager", None)
        if settings is None or manager is None or not _authorized_for_preflight(scope, settings):
            await self.app(scope, receive, send)
            return
        try:
            require_safe_browser_origin(Request(scope))
        except HTTPException as exc:
            response = JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
            await response(scope, receive, send)
            return

        chunks: list[bytes] = []
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunks.append(message.get("body", b""))
            more_body = bool(message.get("more_body"))
        body = b"".join(chunks)
        replayed = False

        async def replay_receive() -> dict[str, Any]:
            nonlocal replayed
            if replayed:
                return {"type": "http.request", "body": b"", "more_body": False}
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            await self.app(scope, replay_receive, send)
            return
        if not isinstance(payload, dict):
            await self.app(scope, replay_receive, send)
            return

        model_id = str(payload.get("model") or payload.get("model_id") or "").strip() or None
        source_model = str(payload.get("source_model") or "").strip()
        if path == "/v1/models/convert" and model_id:
            cfg = manager.catalog.get(model_id)
            if cfg is not None:
                source_model = cfg.source_model
        if not source_model:
            await self.app(scope, replay_receive, send)
            return

        metadata = _metadata_from_catalog(settings, model_id, source_model)
        requested_type = str(payload.get("access_type") or metadata["access_type"])
        try:
            result = await _service_for_state(state).preflight(
                source_model,
                access_type=requested_type,
            )
        except ValueError as exc:
            response = JSONResponse(status_code=400, content={"detail": str(exc)})
            await response(scope, replay_receive, send)
            return
        if result["code"] != "hf_access_granted":
            response = JSONResponse(status_code=409, content={"detail": result})
            await response(scope, replay_receive, send)
            return
        await self.app(scope, replay_receive, send)


def register_huggingface_access_routes(app: FastAPI) -> None:
    if getattr(app.state, "huggingface_access_routes_registered", False):
        return
    router = APIRouter(
        prefix="/v1/huggingface",
        tags=["huggingface-access"],
        dependencies=[Depends(_require_access)],
    )

    @router.get("/status")
    async def status(request: Request):
        return _service_for_state(request.app.state).status()

    @router.post("/token")
    async def save_token(request: Request, body: TokenRequest):
        service = _service_for_state(request.app.state)
        result = await service.test_token(body.token, persist=True)
        if result["code"] != "hf_access_granted":
            raise HTTPException(status_code=400, detail=result)
        await service.clear_cache()
        return {
            "status": service.status(),
            "message": "Hugging Face token saved securely.",
        }

    @router.delete("/token")
    async def remove_token(request: Request):
        service = _service_for_state(request.app.state)
        removed = service.store.remove()
        await service.clear_cache()
        status_payload = service.status()
        message = "Stored Hugging Face token removed."
        if status_payload.get("source") == "environment":
            message += " HF_TOKEN is still configured as an environment fallback."
        return {"removed": removed, "status": status_payload, "message": message}

    @router.post("/test")
    async def test_access(request: Request):
        service = _service_for_state(request.app.state)
        result = await service.test_token()
        if result["code"] != "hf_access_granted":
            raise HTTPException(status_code=409, detail=result)
        return {"status": service.status(), "result": result}

    @router.post("/preflight")
    async def preflight(request: Request, body: PreflightRequest):
        manager = request.app.state.manager
        source_model = str(body.source_model or "").strip()
        access_type = str(body.access_type or "unknown")
        if body.model_id:
            cfg = manager.catalog.get(body.model_id)
            if cfg is None:
                raise HTTPException(status_code=404, detail="Unknown model.")
            source_model = cfg.source_model
            metadata = _metadata_from_catalog(
                request.app.state.settings, body.model_id, source_model
            )
            access_type = metadata["access_type"]
        if not source_model:
            raise HTTPException(
                status_code=400, detail="A Hugging Face source model is required."
            )
        result = await _service_for_state(request.app.state).preflight(
            source_model, access_type=access_type
        )
        if result["code"] != "hf_access_granted":
            raise HTTPException(status_code=409, detail=result)
        return result

    app.include_router(router)
    app.add_middleware(HuggingFacePreflightMiddleware)
    app.state.huggingface_access_routes_registered = True


def install_huggingface_access_routes_extension() -> None:
    if getattr(FastAPI, "_inferbridge_hf_access_routes_installed", False):
        return
    original_init = FastAPI.__init__

    @functools.wraps(original_init)
    def init_with_hf_access(self: FastAPI, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        if getattr(self, "title", "") in {DISPLAY_NAME, LEGACY_DISPLAY_NAME}:
            register_huggingface_access_routes(self)

    FastAPI.__init__ = init_with_hf_access  # type: ignore[method-assign]
    FastAPI._inferbridge_hf_access_routes_installed = True  # type: ignore[attr-defined]


def install_huggingface_access_manager_extension() -> None:
    """Attach secure token injection and structured access metadata to managers."""

    from app import model_manager

    cls = model_manager.ModelManager
    if getattr(cls, "_inferbridge_hf_access_installed", False):
        return

    original_init = cls.__init__
    original_convert = cls._convert_task
    original_entry = cls.catalog_entry
    original_create_subprocess_exec = asyncio.create_subprocess_exec

    @functools.wraps(original_init)
    def init_with_store(self: Any, settings: Any) -> None:
        original_init(self, settings)
        self._hf_credential_store = HuggingFaceCredentialStore(settings)

    @functools.wraps(original_convert)
    async def convert_with_token(self: Any, *args: Any, **kwargs: Any) -> Any:
        token = self._hf_credential_store.get_token()
        context_token = _TOKEN_CONTEXT.set(token)
        try:
            return await original_convert(self, *args, **kwargs)
        finally:
            _TOKEN_CONTEXT.reset(context_token)

    @functools.wraps(original_entry)
    def entry_with_access(self: Any, model_id: str) -> dict[str, Any]:
        entry = original_entry(self, model_id)
        cfg = self.catalog[model_id]
        metadata = _metadata_from_catalog(self.settings, model_id, cfg.source_model)
        entry["huggingface_access"] = metadata
        entry["is_gated"] = metadata["access_type"] == "gated"
        return entry

    async def create_subprocess_with_hf_token(*args: Any, **kwargs: Any) -> Any:
        is_converter = any(str(arg) == "runtime.model_converter" for arg in args)
        token = _TOKEN_CONTEXT.get() if is_converter else None
        if token:
            environment = dict(kwargs.get("env") or os.environ)
            environment["HF_TOKEN"] = token
            environment["HUGGING_FACE_HUB_TOKEN"] = token
            kwargs["env"] = environment
        return await original_create_subprocess_exec(*args, **kwargs)

    cls.__init__ = init_with_store
    cls._convert_task = convert_with_token
    cls.catalog_entry = entry_with_access
    asyncio.create_subprocess_exec = create_subprocess_with_hf_token
    cls._inferbridge_hf_access_installed = True


__all__ = [
    "HuggingFaceAccessService",
    "HuggingFaceCredentialStore",
    "install_huggingface_access_manager_extension",
    "install_huggingface_access_routes_extension",
]
