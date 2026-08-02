"""Authoritative tray-owned lifecycle controller for the packaged local server."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import secrets
import subprocess
import sys
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.desktop_launcher import (
    InstanceMetadata,
    _read_metadata,
    _write_metadata,
    choose_available_port,
    verify_instance,
    wait_for_readiness,
)
from app.desktop_shell import open_browser
from app.paths import RuntimePaths

logger = logging.getLogger("ov-llm.tray.controller")


def _current_process_created_at() -> float:
    try:
        import psutil

        return float(psutil.Process(os.getpid()).create_time())
    except Exception:
        return 0.0


@dataclass(frozen=True)
class ServerControllerOptions:
    preferred_port: int = 8000
    portable: bool = False
    data_dir: str | None = None
    mock: bool = False
    startup_timeout_seconds: float = 90.0
    graceful_shutdown_seconds: float = 20.0


def owned_process_matches(
    child: subprocess.Popen[Any] | None,
    metadata: InstanceMetadata | None,
) -> bool:
    return bool(child and metadata and child.pid == metadata.pid)


def _sanitize_message(value: Any, *, limit: int = 300) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = "".join(char for char in text if ord(char) >= 32)
    return text[:limit]


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url,
        data=payload,
        headers=request_headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = response.read(512 * 1024)
            if not data:
                return {}
            parsed = json.loads(data.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else None
    except (OSError, ValueError):
        return None


class DesktopServerController:
    """Own exactly one packaged server process and no unrelated Python process."""

    def __init__(
        self,
        *,
        paths: RuntimePaths,
        options: ServerControllerOptions,
        log_path: Path,
    ) -> None:
        self.paths = paths
        self.options = options
        self.log_path = Path(log_path)
        self.child: subprocess.Popen[Any] | None = None
        self.metadata: InstanceMetadata | None = None
        self.control_token: str | None = None
        self.last_exit_code: int | None = None
        self.last_error: str | None = None
        self.starting = False
        self.stopping = False
        self._expected_exit = False

    @property
    def port(self) -> int | None:
        return self.metadata.port if self.metadata else None

    @property
    def running(self) -> bool:
        return bool(self.child and self.child.poll() is None)

    @property
    def origin(self) -> str | None:
        return f"http://127.0.0.1:{self.port}" if self.port else None

    def _server_command(self, metadata: InstanceMetadata) -> list[str]:
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--server-child"]
        else:
            command = [sys.executable, "-m", "app.desktop_launcher", "--server-child"]
        command.extend(
            [
                "--port",
                str(metadata.port),
                "--instance-nonce",
                metadata.nonce,
                "--owner-pid",
                str(os.getpid()),
                "--owner-created-at",
                str(_current_process_created_at()),
            ]
        )
        if self.options.portable:
            command.append("--portable")
        if self.options.data_dir:
            command.extend(["--data-dir", self.options.data_dir])
        if self.options.mock:
            command.append("--mock")
        return command

    def _spawn(self, metadata: InstanceMetadata, control_token: str) -> subprocess.Popen[Any]:
        creationflags = 0
        startupinfo = None
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        stream = open(self.log_path, "a", encoding="utf-8")  # noqa: SIM115
        try:
            environment = os.environ.copy()
            environment["OV_LLM_DESKTOP_CONTROL_TOKEN"] = control_token
            return subprocess.Popen(
                self._server_command(metadata),
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=stream,
                cwd=str(
                    Path(sys.executable).resolve().parent
                    if getattr(sys, "frozen", False)
                    else Path.cwd()
                ),
                creationflags=creationflags,
                startupinfo=startupinfo,
                close_fds=os.name != "nt",
                env=environment,
            )
        finally:
            stream.close()

    def recover_stale_metadata(self) -> None:
        stale = _read_metadata(self.paths.launcher_metadata_file)
        if stale and verify_instance(stale):
            # If a prior tray crashed, the child owner monitor should stop the orphan.
            # Wait briefly for that bounded cleanup, but never kill an unowned process.
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline and verify_instance(stale):
                time.sleep(0.25)
            if verify_instance(stale):
                raise RuntimeError(
                    "A healthy server instance is already running and is not owned by "
                    "this tray process."
                )
        with contextlib.suppress(OSError):
            self.paths.launcher_metadata_file.unlink()

    def start(self, *, open_chat: bool = False) -> InstanceMetadata:
        if self.running and self.metadata:
            if open_chat:
                self.open_chat()
            return self.metadata
        if self.starting:
            raise RuntimeError("The server is already starting.")

        self.starting = True
        self.stopping = False
        self.last_error = None
        self.last_exit_code = None
        self._expected_exit = False
        try:
            self.recover_stale_metadata()
            preferred = self.port or self.options.preferred_port
            port = choose_available_port(preferred)
            nonce = secrets.token_urlsafe(24)
            control_token = secrets.token_urlsafe(32)
            provisional = InstanceMetadata(
                pid=os.getpid(),
                port=port,
                nonce=nonce,
                executable=str(Path(sys.executable).resolve()),
                started_at=datetime.now(UTC).isoformat(),
            )
            child = self._spawn(provisional, control_token)
            metadata = InstanceMetadata(
                pid=child.pid,
                port=port,
                nonce=nonce,
                executable=str(Path(sys.executable).resolve()),
                started_at=provisional.started_at,
            )
            self.child = child
            self.metadata = metadata
            self.control_token = control_token
            _write_metadata(self.paths.launcher_metadata_file, metadata)
            if not wait_for_readiness(
                metadata,
                timeout=self.options.startup_timeout_seconds,
                is_alive=lambda: child.poll() is None,
            ):
                exit_code = child.poll()
                self.last_exit_code = exit_code
                self._force_stop_child()
                if exit_code is not None:
                    raise RuntimeError(
                        f"The local server exited during startup with code {exit_code}. "
                        "Review the sanitized tray and desktop logs."
                    )
                raise RuntimeError(
                    "The local server did not become ready. Review the sanitized tray "
                    "and desktop logs."
                )
            if open_chat and not self.open_chat():
                logger.warning(
                    "The server is ready, but the browser could not be opened. Visit %s/",
                    self.origin,
                )
            return metadata
        except Exception as exc:
            self.last_error = _sanitize_message(exc)
            self._force_stop_child()
            self._clear_runtime_metadata(keep_error=True)
            raise
        finally:
            self.starting = False

    def _control_headers(self) -> dict[str, str]:
        if not self.control_token:
            raise RuntimeError("The tray does not have an active server control token.")
        return {"X-Desktop-Control": self.control_token}

    def control_get(self, path: str, *, timeout: float = 3.0) -> dict[str, Any] | None:
        origin = self.origin
        if origin is None:
            return None
        return _json_request(
            f"{origin}{path}",
            headers=self._control_headers(),
            timeout=timeout,
        )

    def control_post(
        self,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any] | None:
        origin = self.origin
        if origin is None:
            return None
        return _json_request(
            f"{origin}{path}",
            method="POST",
            headers=self._control_headers(),
            body=body,
            timeout=timeout,
        )

    def status_payload(self) -> dict[str, Any] | None:
        if not self.running:
            return None
        return self.control_get("/desktop/control/status", timeout=2.0)

    def run_hardware_scan(self) -> dict[str, Any]:
        result = self.control_post("/desktop/control/hardware-scan", timeout=60.0)
        if not result:
            raise RuntimeError("The hardware scan did not return a valid response.")
        return result

    def run_short_benchmark(self) -> dict[str, Any]:
        result = self.control_post("/desktop/control/benchmark", timeout=20 * 60)
        if not result:
            raise RuntimeError("The short benchmark did not return a valid response.")
        return result

    def open_chat(self) -> bool:
        origin = self.origin
        return bool(origin and open_browser(f"{origin}/"))

    def stop(self) -> None:
        if not self.running:
            self._clear_runtime_metadata()
            return
        metadata = self.metadata
        if not owned_process_matches(self.child, metadata):
            raise RuntimeError("Refusing to stop a server process that is not owned by this tray.")

        self.stopping = True
        self._expected_exit = True
        try:
            response = self.control_post("/desktop/control/shutdown", timeout=5.0)
            if not response or response.get("status") != "shutting_down":
                logger.warning("Graceful shutdown acknowledgement was unavailable")
            child = self.child
            if child is not None:
                try:
                    child.wait(timeout=self.options.graceful_shutdown_seconds)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                self.last_exit_code = child.returncode
        finally:
            self._clear_runtime_metadata()
            self.stopping = False

    def restart(self, *, open_chat: bool = False) -> InstanceMetadata:
        current_port = self.port
        self.stop()
        if current_port:
            self.options = replace(self.options, preferred_port=current_port)
        return self.start(open_chat=open_chat)

    def poll_unexpected_exit(self) -> str | None:
        child = self.child
        if child is None:
            return None
        exit_code = child.poll()
        if exit_code is None:
            return None
        self.last_exit_code = int(exit_code)
        if self._expected_exit:
            self._clear_runtime_metadata()
            return None
        message = f"The local server exited unexpectedly with code {exit_code}."
        self.last_error = message
        self._clear_runtime_metadata(keep_error=True)
        return message

    def _force_stop_child(self) -> None:
        child = self.child
        if child is None or child.poll() is not None:
            return
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            with contextlib.suppress(subprocess.TimeoutExpired):
                child.wait(timeout=5)

    def _clear_runtime_metadata(self, *, keep_error: bool = False) -> None:
        with contextlib.suppress(OSError):
            self.paths.launcher_metadata_file.unlink()
        self.child = None
        self.metadata = None
        self.control_token = None
        self._expected_exit = False
        if not keep_error:
            self.last_error = None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self.stop()
