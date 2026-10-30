"""Single-instance packaged entry point and reusable desktop-launch primitives."""

from __future__ import annotations

import argparse
import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO

from app.brand import DISPLAY_NAME

_APP_TITLE = DISPLAY_NAME
_STARTUP_TIMEOUT_SECONDS = 90
_POLL_INTERVAL_SECONDS = 0.35
_MAX_HELPER_FAILURE_DETAIL_CHARS = 2000


@dataclass(frozen=True)
class InstanceMetadata:
    pid: int
    port: int
    nonce: str
    executable: str
    started_at: str

    @classmethod
    def from_json(cls, raw: object) -> InstanceMetadata | None:
        if not isinstance(raw, dict):
            return None
        try:
            pid = raw["pid"]
            port = raw["port"]
            nonce = raw["nonce"]
            executable = raw["executable"]
            started_at = raw["started_at"]
        except KeyError:
            return None
        # This metadata participates in process ownership and stale-instance decisions.
        # Do not use lossy int()/str() coercion: bools, floats, numeric strings, Infinity,
        # or structured values must never become plausible process-control metadata.
        if (
            isinstance(pid, bool)
            or not isinstance(pid, int)
            or isinstance(port, bool)
            or not isinstance(port, int)
            or not isinstance(nonce, str)
            or not isinstance(executable, str)
            or not isinstance(started_at, str)
        ):
            return None
        if (
            pid < 1
            or not 1 <= port <= 65535
            or not nonce
            or len(nonce) > 512
            or any(ord(char) < 32 for char in nonce)
            or len(executable) > 32768
            or len(started_at) > 256
        ):
            return None
        return cls(pid, port, nonce, executable, started_at)


def choose_available_port(preferred: int = 8000) -> int:
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            try:
                sock.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return int(sock.getsockname()[1])
    raise RuntimeError("No local TCP port is available for the application server.")


class _NoDesktopRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Desktop control credentials must never follow a redirect to another listener.
        return None


def _http_json(
    url: str,
    *,
    timeout: float = 1.5,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Bounded, direct loopback IPC, independent of system/network proxy settings."""
    try:
        endpoint = urllib.parse.urlsplit(url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname != "127.0.0.1"
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.port is None
        ):
            return None
        request_headers = {"Accept": "application/json", **dict(headers or {})}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers=request_headers,
            method=method,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoDesktopRedirect())
        with opener.open(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                return None
            data = response.read(512 * 1024 + 1)
            if len(data) > 512 * 1024:
                return None
            expected_length = response.headers.get("Content-Length")
            if expected_length is not None and int(expected_length) != len(data):
                return None
            payload = json.loads(data.decode("utf-8")) if data else {}
            return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, http.client.HTTPException):
        return None


def verify_instance(metadata: InstanceMetadata) -> bool:
    payload = _http_json(f"http://127.0.0.1:{metadata.port}/desktop/instance")
    return bool(payload and payload.get("instance_nonce") == metadata.nonce)


def wait_for_readiness(
    metadata: InstanceMetadata,
    timeout: float = _STARTUP_TIMEOUT_SECONDS,
    *,
    is_alive: Callable[[], bool] | None = None,
) -> bool:
    """Wait for the matching server to become ready while its child stays alive."""

    deadline = time.monotonic() + timeout
    live_seen = False
    while time.monotonic() < deadline:
        if is_alive is not None and not is_alive():
            return False
        instance = _http_json(f"http://127.0.0.1:{metadata.port}/desktop/instance")
        if instance and instance.get("instance_nonce") == metadata.nonce:
            live = _http_json(f"http://127.0.0.1:{metadata.port}/health/live")
            live_seen = live_seen or bool(live and live.get("status") == "ok")
            ready = _http_json(f"http://127.0.0.1:{metadata.port}/health/ready")
            if live_seen and ready and ready.get("status") == "ready":
                return True
        if is_alive is not None and not is_alive():
            return False
        time.sleep(_POLL_INTERVAL_SECONDS)
    return False


def _write_metadata(path: Path, metadata: InstanceMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(asdict(metadata), indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_metadata(path: Path) -> InstanceMetadata | None:
    try:
        return InstanceMetadata.from_json(json.loads(path.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError):
        return None


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: BinaryIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115
        try:
            # Ensure the file has a byte to lock. When another instance already
            # holds the lock, this read/write touches the byte-range the other
            # process has locked and raises OSError on Windows; treat that (and
            # the lock call below) as "already held" instead of letting an
            # unhandled PermissionError crash the launcher on a second launch.
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.handle = handle
        return True

    def release(self) -> None:
        handle = self.handle
        self.handle = None
        if handle is None:
            return
        with contextlib.suppress(OSError):
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    def __enter__(self) -> InstanceLock:
        if not self.acquire():
            raise RuntimeError("The application instance lock is already held.")
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


def _portable_default() -> bool:
    base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd()
    return (base / "portable.flag").exists()


def resolve_desktop_port(cli_port: int | None, env_port: str | None) -> tuple[int, bool]:
    """Use explicit CLI > environment > fallback; distinguish pinned from default."""

    if cli_port is not None:
        return cli_port, True
    if env_port is None:
        return 8000, False
    value = env_port.strip()
    if not value.isascii() or not value.isdecimal():
        raise ValueError("OV_LLM_PORT must be an integer between 1 and 65535.")
    port = int(value)
    if not 1 <= port <= 65535:
        raise ValueError("OV_LLM_PORT must be an integer between 1 and 65535.")
    return port, True


def _child_command(args: argparse.Namespace, metadata: InstanceMetadata) -> list[str]:
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
        ]
    )
    if args.portable:
        command.append("--portable")
    if args.data_dir:
        command.extend(["--data-dir", args.data_dir])
    if args.mock:
        command.append("--mock")
    return command


def _spawn_server(
    args: argparse.Namespace,
    metadata: InstanceMetadata,
    log_path: Path,
) -> subprocess.Popen:
    control_token = str(getattr(args, "control_token", "") or "")
    if not control_token:
        raise RuntimeError("The packaged server control token is unavailable.")

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    stream = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
    try:
        environment = os.environ.copy()
        environment["OV_LLM_DESKTOP_CONTROL_TOKEN"] = control_token
        return subprocess.Popen(
            _child_command(args, metadata),
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


def _server_child(args: argparse.Namespace) -> int:
    from app.desktop_server import run_server

    environment_token = os.environ.pop("OV_LLM_DESKTOP_CONTROL_TOKEN", "")
    control_token = args.control_token or environment_token
    if not control_token:
        raise RuntimeError("The packaged server control token is unavailable.")
    return run_server(
        port=args.port,
        instance_nonce=args.instance_nonce,
        control_token=control_token,
        owner_pid=args.owner_pid,
        owner_created_at=args.owner_created_at,
        portable=args.portable,
        data_dir=args.data_dir,
        mock=args.mock,
    )


def _system_exit_code(value: object) -> int:
    """Convert ``SystemExit.code`` into a predictable process exit code."""

    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and 0 <= value <= 255:
        return value
    return 2


def _bounded_helper_diagnostic(value: object) -> str:
    raw = str(value)
    flattened = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in raw)
    detail = " ".join(flattened.split())
    if len(detail) > _MAX_HELPER_FAILURE_DETAIL_CHARS:
        return detail[: _MAX_HELPER_FAILURE_DETAIL_CHARS - 1].rstrip() + "…"
    return detail


def _failure_detail(error: BaseException) -> str:
    """Describe a frozen helper failure with its type and originating frame.

    The parent server surfaces only the tail of this stream, and a windowed helper has
    nowhere to show a traceback. A bare message such as "could not get source code" names
    neither the exception type nor the module that raised it, so include both. Only the
    file name is reported, never the full bundle path.
    """

    import traceback

    detail = _bounded_helper_diagnostic(error) or error.__class__.__name__
    frames = traceback.extract_tb(error.__traceback__)
    origin = f" at {Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else ""
    return f"{error.__class__.__name__}: {detail}{origin}"


def _run_packaged_converter(arguments: list[str]) -> int:
    """Run the converter and Optimum CLI inside the frozen helper process.

    The frozen launcher is windowed. Any exception that escapes this boundary causes
    PyInstaller to display an interactive "Unhandled exception" dialog and blocks the
    parent preparation job. Convert all helper failures into stderr plus an exit code.
    """

    from runtime import model_converter

    if getattr(sys, "frozen", False):
        original_which = model_converter.shutil.which
        original_run_streaming_command = model_converter._run_streaming_command

        def packaged_which(command: str) -> str | None:
            if command == "optimum-cli":
                return sys.executable
            return original_which(command)

        def run_optimum(
            command: list[str],
            *,
            progress_emitter: Any = None,
        ) -> None:
            from optimum.commands.optimum_cli import main as optimum_main

            if progress_emitter is not None:
                progress_emitter.emit(
                    "converting",
                    "Running packaged OpenVINO conversion…",
                )
            # A packaged conversion runs Optimum in this process, so tqdm and the
            # Transformers loader draw on these streams directly. Route them through the
            # same line emitter the subprocess path uses: carriage-return redraws become
            # whole lines the parent's readline() can consume, stdout stays a pure JSON
            # progress channel, and download progress reaches the packaged UI instead of
            # a single static message for the whole export.
            emitter = model_converter._ProgressLineEmitter(
                progress_emitter,
                human_stream=sys.stderr,
            )
            console = model_converter.ConsoleLineWriter(emitter.emit)
            previous = sys.argv
            previous_stdout, previous_stderr = sys.stdout, sys.stderr
            sys.argv = list(command)
            sys.stdout = sys.stderr = console
            try:
                result = optimum_main()
            except SystemExit as exc:
                code = _system_exit_code(exc.code)
                if exc.code is not None and not isinstance(exc.code, int):
                    print(_bounded_helper_diagnostic(exc.code), file=sys.stderr, flush=True)
                if code:
                    raise subprocess.CalledProcessError(code, command) from exc
                result = 0
            finally:
                # Restore first: the emitter writes to the real stderr it captured, so a
                # failing drain must not leave this process without usable streams.
                sys.stdout, sys.stderr = previous_stdout, previous_stderr
                sys.argv = previous
                console.drain()
            if isinstance(result, int) and result:
                raise subprocess.CalledProcessError(result, command)

        model_converter.shutil.which = packaged_which
        model_converter._run_streaming_command = run_optimum
        try:
            try:
                return model_converter.main(arguments)
            except SystemExit as exc:
                if exc.code is not None and not isinstance(exc.code, int):
                    print(_bounded_helper_diagnostic(exc.code), file=sys.stderr, flush=True)
                return _system_exit_code(exc.code)
            except KeyboardInterrupt:
                print("Packaged model conversion cancelled.", file=sys.stderr, flush=True)
                return 130
            except Exception as exc:  # noqa: BLE001 - frozen helper process boundary
                print(
                    f"Packaged model conversion failed: {_failure_detail(exc)}",
                    file=sys.stderr,
                    flush=True,
                )
                return 2
        finally:
            model_converter.shutil.which = original_which
            model_converter._run_streaming_command = original_run_streaming_command
    return model_converter.main(arguments)


def _native_runtime_smoke() -> int:
    """Verify that the frozen OpenVINO and tokenizer native libraries can load."""

    try:
        from openvino import Core
        from openvino_genai import LLMPipeline

        core = Core()
        # OpenVINO GenAI resolves this extension by DLL name. Using the same lookup
        # here catches missing transitive DLLs and incorrect PyInstaller search paths.
        if sys.platform.startswith("linux"):
            import openvino_tokenizers

            extension = next(
                Path(openvino_tokenizers.__file__).parent.rglob("libopenvino_tokenizers.so")
            )
            core.add_extension(str(extension))
            if "CPU" not in core.available_devices:
                raise RuntimeError("OpenVINO did not expose a CPU device.")
        else:
            core.add_extension("openvino_tokenizers.dll")
        if not callable(LLMPipeline):
            raise RuntimeError("OpenVINO GenAI LLMPipeline binding is unavailable.")
    except Exception as exc:  # noqa: BLE001 - packaged native boundary
        print(
            f"Packaged OpenVINO native smoke test failed: {_bounded_helper_diagnostic(exc)}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    return 0


def _conversion_import_smoke() -> int:
    """Import the model-export chain that every packaged conversion needs.

    ``optimum-cli export openvino`` imports these modules before it downloads a single
    byte, and importing them executes decorators that read their own Python source with
    ``inspect.getsource``. A bundle that ships only bytecode therefore fails every
    conversion with ``OSError("could not get source code")`` while the same code works
    from a source checkout. Import the chain during release validation so that
    packaging regression fails the build instead of the user's first model download.
    """

    import importlib

    for module in ("optimum.exporters.openvino", "optimum.intel.openvino"):
        try:
            importlib.import_module(module)
        except Exception as exc:  # noqa: BLE001 - packaged conversion import boundary
            print(
                f"Packaged model conversion smoke test failed importing {module}: "
                f"{type(exc).__name__}: {_bounded_helper_diagnostic(exc)}",
                file=sys.stderr,
                flush=True,
            )
            return 2
    return 0


def _diagnostic_export(args: argparse.Namespace) -> int:
    from app.desktop_server import prepare_desktop_environment
    from app.diagnostics import DiagnosticsCollector
    from app.paths import ensure_data_root_writable, resolve_runtime_paths

    prepare_desktop_environment(
        portable=args.portable,
        data_dir=args.data_dir,
        mock=args.mock,
    )
    paths = resolve_runtime_paths(portable=args.portable, desktop=True)
    ensure_data_root_writable(paths)
    result = DiagnosticsCollector(
        paths=paths,
        runtime_snapshot={
            "application_version": "unknown",
            "installation_mode": "portable" if paths.portable else "installed",
            "controller_available": False,
            "server_port": None,
            "live": False,
            "ready": False,
            "server_status": "offline diagnostic",
            "events": [],
        },
    ).export()
    from app.desktop_shell import show_dialog

    show_dialog(_APP_TITLE, f"Sanitized diagnostics ZIP written to:\n{result.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:2] == ["-m", "runtime.model_converter"]:
        return _run_packaged_converter(arguments[2:])

    parser = argparse.ArgumentParser(description=f"{DISPLAY_NAME} desktop tray launcher")
    parser.add_argument("--server-child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--convert-model", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--native-smoke", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--check-devices",
        action="store_true",
        help="Print OpenVINO inventory and Linux device-node access",
    )
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--instance-nonce", default="")
    parser.add_argument("--control-token", default="", help=argparse.SUPPRESS)
    parser.add_argument("--owner-pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--owner-created-at", type=float, default=0.0, help=argparse.SUPPRESS)
    parser.add_argument("--portable", action="store_true", default=_portable_default())
    parser.add_argument("--data-dir", default=os.environ.get("OV_LLM_DATA_DIR"))
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--quit", action="store_true", help="Stop this user's desktop application")
    parser.add_argument("--startup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--start-stopped", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--headless", action="store_true", help="Run without a system-tray icon")
    parser.add_argument(
        "--service",
        action="store_true",
        help="Run without a browser or tray, requiring exclusive server ownership",
    )
    parser.add_argument("--headless-seconds", type=float, default=0, help=argparse.SUPPRESS)
    args, remaining = parser.parse_known_args(arguments)
    if args.service:
        args.headless = True
        args.no_browser = True
    if args.convert_model:
        return _run_packaged_converter(remaining)
    if args.native_smoke:
        return _native_runtime_smoke() or _conversion_import_smoke()
    if remaining:
        parser.error(f"unrecognized arguments: {' '.join(remaining)}")
    if args.check_devices:
        from runtime import device_check

        print(
            json.dumps(
                {
                    "available": device_check.available_devices(),
                    "details": device_check.device_details(),
                    "linux_device_nodes": device_check.linux_device_nodes()
                    if sys.platform.startswith("linux")
                    else {},
                    "note": "Device nodes are hints. GPU requires render-node access and Intel drivers; NPU requires accel-node access, a compatible driver and OpenVINO plugin. Only OpenVINO-visible devices can be selected.",
                },
                indent=2,
            )
        )
        return 0
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if args.server_child:
        if args.port is None:
            parser.error("--server-child requires --port")
        if not args.instance_nonce or not (
            args.control_token or os.environ.get("OV_LLM_DESKTOP_CONTROL_TOKEN")
        ):
            parser.error("--server-child requires instance and control tokens")
        return _server_child(args)
    if args.diagnostic:
        return _diagnostic_export(args)

    try:
        args.port, args.fixed_port = resolve_desktop_port(args.port, os.environ.get("OV_LLM_PORT"))
    except ValueError as exc:
        from app.desktop_shell import show_dialog

        show_dialog(_APP_TITLE, str(exc), error=True)
        return 2

    from app.tray_app import run_tray_controller

    try:
        return run_tray_controller(args)
    except RuntimeError as exc:
        from app.desktop_shell import show_dialog

        show_dialog(_APP_TITLE, str(exc)[:300], error=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
