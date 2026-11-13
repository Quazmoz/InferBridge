"""The ``inferbridge`` command for Linux installations.

One entry point for the portable tarball, the user install and the .deb/.rpm packages::

    inferbridge                     launch (controller + browser UI)
    inferbridge start|stop|status|open|logs|doctor|version
    inferbridge service install|uninstall|enable|disable|start|stop|status
    inferbridge autostart enable|disable|status
    inferbridge desktop install|uninstall
    inferbridge install             register a portable bundle for this user
    inferbridge uninstall [--purge] [--remove-app]
    inferbridge update-check [--channel stable|beta]

It never needs root. Application behavior stays in ``app.desktop_launcher``; this module
only manages the user's integration files and talks to a running instance through the
same instance metadata and lock the controller owns. Files written here carry a
``# Managed by InferBridge sha256=…`` line, so an unmodified file from an older version can
be replaced on upgrade while a user-edited file is always preserved.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from app.version import __version__

SERVICE_NAME = "inferbridge.service"
DESKTOP_FILE = "inferbridge.desktop"
QUIT_DESKTOP_FILE = "inferbridge-quit.desktop"
ICON_NAME = "inferbridge"
SYSTEM_PREFIXES = ("/opt/", "/usr/")
_MARKER = "# Managed by InferBridge sha256="
_RELEASES_API = "https://api.github.com/repos/Quazmoz/InferBridge/releases?per_page=30"
_START_TIMEOUT_SECONDS = 150.0


# --------------------------------------------------------------------------- paths


def _xdg(variable: str, fallback: str) -> Path:
    configured = Path(os.environ.get(variable, ""))
    return configured if configured.is_absolute() else Path.home() / fallback


def data_home() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share")


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config")


def user_bin() -> Path:
    return Path.home() / ".local" / "bin"


def user_unit_path() -> Path:
    return config_home() / "systemd" / "user" / SERVICE_NAME


def system_unit_paths() -> tuple[Path, ...]:
    return (Path("/usr/lib/systemd/user") / SERVICE_NAME, Path("/lib/systemd/user") / SERVICE_NAME)


def install_kind(root: Path) -> str:
    """system (package under /opt or /usr), user (linked into ~/.local/bin) or portable."""
    resolved = str(root.resolve()) + "/"
    if resolved.startswith(SYSTEM_PREFIXES) and not os.access(root, os.W_OK):
        return "system"
    link = user_bin() / "inferbridge"
    with contextlib.suppress(OSError):
        if link.is_symlink() and link.resolve() == (root / "InferBridge").resolve():
            return "user"
    return "portable"


# ----------------------------------------------------------------- managed files


def _seal(contents: str) -> str:
    digest = hashlib.sha256(contents.encode("utf-8")).hexdigest()
    return contents + f"{_MARKER}{digest}\n"


def managed_unmodified(text: str) -> bool:
    """True for a file InferBridge wrote and nobody has edited since."""
    body, separator, tail = text.rpartition(_MARKER)
    if not separator or not tail.endswith("\n") or "\n" in tail[:-1]:
        return False
    return hashlib.sha256(body.encode("utf-8")).hexdigest() == tail.strip()


def write_entry(target: Path, contents: str, mode: int = 0o644) -> None:
    """Atomic write; a failure leaves the previous file and no staging file behind."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        temporary.chmod(mode)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _remove_if(target: Path, *expected: str) -> bool:
    """Remove ``target`` only when it is one of ``expected`` or an unmodified managed file."""
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return False
    if text in expected or managed_unmodified(text):
        target.unlink()
        return True
    return False


# --------------------------------------------------------------- desktop entries


def desktop_quote(value: str) -> str:
    if any(character in value for character in "\r\n\0"):
        raise ValueError("Desktop paths cannot contain newlines or NUL.")
    # Exec quoting is parsed after desktop-value escape processing.
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    return '"' + value.replace("\\", "\\\\").replace("%", "%%") + '"'


def desktop_entry(root: Path, *, quit: bool = False, autostart: bool = False) -> str:
    executable = desktop_quote(str(root / "InferBridge"))
    if any(character in str(root) for character in "\r\n\0"):
        raise ValueError("Desktop paths cannot contain newlines or NUL.")
    arguments = " --quit" if quit else (" --startup --no-browser" if autostart else "")
    name = "Quit InferBridge" if quit else "InferBridge"
    entry = (
        "[Desktop Entry]\nType=Application\n"
        f"Name={name}\n"
        "Comment=Local OpenVINO AI server and model manager\n"
        f"Exec={executable}{arguments}\n"
        f"Icon={ICON_NAME}\nTerminal=false\nStartupNotify=false\n"
        "Categories=Development;\n"
        "Keywords=AI;LLM;OpenVINO;OpenAI;chat;\n"
        f"X-InferBridge-Root={str(root).replace(chr(92), chr(92) * 2)}\n"
    )
    if autostart:
        entry += "X-GNOME-Autostart-enabled=true\n"
    return entry


def _legacy_desktop_entry(root: Path, *, quit: bool = False) -> str:
    """Byte-exact entry written by 1.1.1-dev builds before themed icons; removable."""
    icon = str(root / "resources" / "InferBridge.png").replace("\\", "\\\\")
    return (
        "[Desktop Entry]\nType=Application\n"
        f"Name={'Quit InferBridge' if quit else 'InferBridge'}\n"
        "Comment=Local OpenVINO AI server and model manager\n"
        f"Exec={desktop_quote(str(root / 'InferBridge'))}{' --quit' if quit else ''}\n"
        f"Icon={icon}\nTerminal=false\nStartupNotify=false\n"
        "Categories=Development;\n"
        f"X-InferBridge-Root={str(root).replace(chr(92), chr(92) * 2)}\n"
    )


def icon_target() -> Path:
    return data_home() / "icons" / "hicolor" / "256x256" / "apps" / f"{ICON_NAME}.png"


def integrate(root: Path, *, remove: bool = False) -> int:
    directory = data_home() / "applications"
    source_icon = root / "resources" / "InferBridge.png"
    for quit in (False, True):
        target = directory / (QUIT_DESKTOP_FILE if quit else DESKTOP_FILE)
        contents = desktop_entry(root, quit=quit)
        if remove:
            _remove_if(target, contents, _legacy_desktop_entry(root, quit=quit))
        else:
            write_entry(target, contents)
    icon = icon_target()
    if remove:
        # The icon is shared by every bundle version; keep it while any entry remains.
        if not any(directory.glob("inferbridge*.desktop")):
            with contextlib.suppress(OSError):
                if source_icon.is_file() and icon.read_bytes() == source_icon.read_bytes():
                    icon.unlink()
    elif source_icon.is_file():
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_icon, icon)
    print(
        "Desktop entries removed; application data preserved."
        if remove
        else "Desktop entries installed."
    )
    return 0


# --------------------------------------------------------------- systemd user unit


def unit_quote(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Service paths cannot contain control characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_entry(root: Path) -> str:
    # ':' disables environment substitution in ExecStart; '%' still needs escaping.
    # systemd syntax differs from both shell and .desktop quoting.
    launcher = unit_quote(str(root / "InferBridge"))
    data_dir = os.environ.get("OV_LLM_DATA_DIR")
    data_option = (
        " --data-dir " + unit_quote(str(Path(data_dir).expanduser().resolve())) if data_dir else ""
    )
    return (
        "[Unit]\nDescription=InferBridge local AI server\n"
        "Documentation=https://github.com/Quazmoz/InferBridge/blob/main/docs/LINUX.md\n"
        "StartLimitIntervalSec=120\nStartLimitBurst=3\n\n"
        "[Service]\nType=exec\n"
        # systemd restricts executable names; the bundle path is a literal Bash argument.
        f"ExecStart=:/bin/bash -- {launcher} --service{data_option}\n"
        f"Environment={unit_quote('XDG_DATA_HOME=' + str(data_home()))}\n"
        "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=60\n"
        "KillMode=mixed\nUMask=0077\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def integrate_service(root: Path, *, remove: bool = False, replace_managed: bool = False) -> int:
    target = user_unit_path()
    contents = service_entry(root)
    if remove:
        if _remove_if(target, contents):
            print(
                "Service unit removed; application data preserved. Run systemctl --user daemon-reload."
            )
        else:
            print("No matching service unit removed; a different or edited unit is preserved.")
        return 0
    sealed = _seal(contents)
    if target.exists():
        current = target.read_text(encoding="utf-8")
        if current in (contents, sealed):
            return 0
        if not (replace_managed and managed_unmodified(current)):
            print(
                "An existing service unit differs. Stop and uninstall it with its owning "
                "launcher, or back up and remove the unit before installing this version.",
                file=sys.stderr,
            )
            return 2
    write_entry(target, sealed if replace_managed else contents)
    print(
        "User service installed (not started). Quit any running InferBridge instance, then run:\n"
        "  inferbridge service enable\n"
        "or: systemctl --user daemon-reload && systemctl --user enable --now inferbridge.service"
    )
    return 0


def _systemctl(*arguments: str, check: bool = False) -> subprocess.CompletedProcess | None:
    from app.desktop_shell import host_environment

    if not shutil.which("systemctl"):
        return None
    try:
        return subprocess.run(
            ["systemctl", "--user", *arguments],
            capture_output=True,
            text=True,
            timeout=60,
            check=check,
            env=host_environment(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def service_state() -> dict[str, str]:
    active = _systemctl("is-active", SERVICE_NAME)
    enabled = _systemctl("is-enabled", SERVICE_NAME)
    return {
        "active": active.stdout.strip() if active else "unavailable",
        "enabled": enabled.stdout.strip() if enabled else "unavailable",
        "unit": next(
            (str(path) for path in (user_unit_path(), *system_unit_paths()) if path.exists()), ""
        ),
    }


# --------------------------------------------------------------- running instance


def runtime_paths(args: argparse.Namespace):
    from app.desktop_server import prepare_desktop_environment
    from app.paths import resolve_runtime_paths

    prepare_desktop_environment(portable=args.portable, data_dir=args.data_dir, mock=False)
    return resolve_runtime_paths(portable=args.portable, desktop=True)


def instance(paths) -> dict | None:
    from app.desktop_launcher import _http_json, _read_metadata, verify_instance

    metadata = _read_metadata(paths.launcher_metadata_file)
    if metadata is None or not verify_instance(metadata):
        return None
    origin = f"http://127.0.0.1:{metadata.port}"
    ready = _http_json(f"{origin}/health/ready") or {}
    return {
        "url": f"{origin}/",
        "api": f"{origin}/v1",
        "port": metadata.port,
        "server_pid": metadata.pid,
        "ready": ready.get("status") == "ready",
    }


def _launcher(root: Path) -> list[str]:
    return [str(root / "InferBridge")]


def _passthrough(args: argparse.Namespace) -> list[str]:
    options = ["--portable"] if args.portable else []
    return options + ([f"--data-dir={args.data_dir}"] if args.data_dir else [])


def command_start(root: Path, args: argparse.Namespace, *, quiet: bool = False) -> int:
    paths = runtime_paths(args)
    current = instance(paths)
    if current is None:
        logs = paths.logs_dir
        logs.mkdir(parents=True, exist_ok=True)
        command = [*_launcher(root), "--headless", "--no-browser", *_passthrough(args)]
        if getattr(args, "mock", False):
            command.append("--mock")
        with (logs / "cli-start.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        deadline = time.monotonic() + _START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            current = instance(paths)
            if current and current["ready"]:
                break
            if process.poll() is not None:
                current = instance(paths)  # Another launcher may have won the lock.
                if current:
                    break
                print(
                    f"InferBridge did not start (exit code {process.returncode}). "
                    f"See {logs / 'cli-start.log'} and `inferbridge logs`.",
                    file=sys.stderr,
                )
                return 1
            time.sleep(0.3)
        else:
            print(
                "InferBridge did not become ready in time; see `inferbridge logs`.", file=sys.stderr
            )
            return 1
    if not quiet:
        print(f"InferBridge is running at {current['url']} (OpenAI API: {current['api']})")
    return 0


def command_stop(root: Path, args: argparse.Namespace) -> int:
    paths = runtime_paths(args)
    state = service_state()
    if state["active"] == "active" and instance(paths):
        print("InferBridge runs as a user service; stopping it with systemctl.")
        result = _systemctl("stop", SERVICE_NAME)
        return 0 if result and result.returncode == 0 else 1
    return subprocess.run([*_launcher(root), "--quit", *_passthrough(args)], check=False).returncode


def command_status(root: Path, args: argparse.Namespace) -> int:
    paths = runtime_paths(args)
    current = instance(paths)
    report = {
        "version": __version__,
        "install": install_kind(root),
        "application_root": str(root),
        "data_dir": str(paths.data_root),
        "logs_dir": str(paths.logs_dir),
        "running": current is not None,
        **(current or {}),
        "service": service_state(),
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"InferBridge {__version__} ({report['install']} install at {root})")
        if current:
            readiness = "ready" if current["ready"] else "starting"
            print(f"Running: {current['url']} ({readiness}); OpenAI API {current['api']}")
        else:
            print("Not running. Start it with `inferbridge start` or from the application menu.")
        service = report["service"]
        print(f"User service: {service['active']} / {service['enabled']}")
        print(f"Data: {paths.data_root}")
    return 0 if current else 3


def _chromium_app_window(url: str) -> bool:
    from app.desktop_shell import _spawn_detached

    for browser in ("chromium", "chromium-browser", "google-chrome", "microsoft-edge", "brave"):
        if shutil.which(browser):
            return _spawn_detached([browser, f"--app={url}"])
    return False


def command_open(root: Path, args: argparse.Namespace) -> int:
    paths = runtime_paths(args)
    if instance(paths) is None and command_start(root, args, quiet=True):
        return 1
    current = instance(paths)
    if current is None:
        print("InferBridge stopped before the browser could open.", file=sys.stderr)
        return 1
    from app.desktop_shell import open_browser

    if (args.app_window and _chromium_app_window(current["url"])) or open_browser(current["url"]):
        print(f"Opened {current['url']}")
        return 0
    print(f"Open {current['url']} in a browser.")
    return 0


def command_logs(root: Path, args: argparse.Namespace) -> int:
    paths = runtime_paths(args)
    logs = sorted(paths.logs_dir.glob("*.log"))
    if not logs:
        print(f"No logs yet in {paths.logs_dir}.")
        return 0
    if args.path:
        print(paths.logs_dir)
        return 0
    for log in logs:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-args.lines :]
        print(f"==> {log} <==")
        print("\n".join(lines))
    if service_state()["unit"]:
        print(f"\nService output: journalctl --user -u {SERVICE_NAME}")
    if args.follow:
        offsets = {log: log.stat().st_size for log in logs}
        with contextlib.suppress(KeyboardInterrupt):
            while True:
                for log in logs:
                    with contextlib.suppress(OSError), log.open(encoding="utf-8") as stream:
                        stream.seek(offsets[log])
                        chunk = stream.read()
                        offsets[log] = stream.tell()
                        if chunk:
                            sys.stdout.write(chunk)
                            sys.stdout.flush()
                time.sleep(0.5)
    return 0


def command_doctor(root: Path, args: argparse.Namespace) -> int:
    from runtime import device_check, linux_doctor

    paths = runtime_paths(args)
    report = linux_doctor.diagnose(
        openvino_devices=device_check.available_devices(),
        data_dir=paths.data_root if paths.data_root.exists() else None,
    )
    report["openvino_details"] = device_check.device_details()
    report["install"] = {"kind": install_kind(root), "root": str(root), "version": __version__}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"InferBridge {__version__} doctor ({install_kind(root)} install)\n")
        print(linux_doctor.render(report, all_distros=args.all_distros))
    return 1 if args.strict and report["errors"] else 0


# ------------------------------------------------------------------- service CLI


def command_service(root: Path, args: argparse.Namespace) -> int:
    action = args.action
    system_unit = next((path for path in system_unit_paths() if path.exists()), None)
    packaged = install_kind(root) == "system" and system_unit is not None
    if action == "status":
        result = _systemctl("status", "--no-pager", SERVICE_NAME)
        print(result.stdout if result else "systemd is not available for this user session.")
        return 0
    if action in {"install", "enable"}:
        if packaged:
            print(f"Using the packaged user unit {system_unit}.")
        else:
            code = integrate_service(root, replace_managed=True)
            if code:
                return code
            _systemctl("daemon-reload")
        if action == "install":
            return 0
        if instance(runtime_paths(args)) and service_state()["active"] != "active":
            print(
                "A desktop InferBridge instance is running. Run `inferbridge stop` first: the "
                "service needs exclusive use of the data directory.",
                file=sys.stderr,
            )
            return 2
        result = _systemctl("enable", "--now", SERVICE_NAME)
        if result is None or result.returncode:
            detail = result.stderr.strip() if result else "systemctl is unavailable."
            print(detail, file=sys.stderr)
            return 1
        print("User service enabled and started; it starts at login. URL: `inferbridge status`.")
        return 0
    commands = {
        "start": ("start",),
        "stop": ("stop",),
        "disable": ("disable", "--now"),
        "uninstall": ("disable", "--now"),
    }[action]
    result = _systemctl(*commands, SERVICE_NAME)
    if action == "uninstall":
        if not packaged:
            integrate_service(root, remove=True)
        _systemctl("daemon-reload")
        _systemctl("reset-failed", SERVICE_NAME)
        return 0
    ok = result is not None and result.returncode == 0
    if not ok:
        print((result.stderr.strip() if result else "systemctl is unavailable."), file=sys.stderr)
    return 0 if ok else 1


def autostart_path() -> Path:
    return config_home() / "autostart" / DESKTOP_FILE


def command_autostart(root: Path, args: argparse.Namespace) -> int:
    target = autostart_path()
    if args.action == "enable":
        if target.exists() and not managed_unmodified(target.read_text(encoding="utf-8")):
            print(f"{target} was edited; it is preserved. Remove it to re-enable.", file=sys.stderr)
            return 2
        write_entry(target, _seal(desktop_entry(root, autostart=True)))
        print(
            "InferBridge will start at login (no browser window). Disable: inferbridge autostart disable"
        )
    elif args.action == "disable":
        removed = _remove_if(target)
        print(
            "Login autostart disabled."
            if removed
            else "Login autostart was not enabled by InferBridge."
        )
    else:
        print("enabled" if target.exists() else "disabled")
    return 0


def starts_at_login() -> bool:
    """Autostart entry or enabled user service. File checks only: cheap enough to poll."""
    wants = config_home() / "systemd" / "user" / "default.target.wants" / SERVICE_NAME
    return autostart_path().exists() or wants.is_symlink()


# ------------------------------------------------------------- install/uninstall


def command_install(root: Path, args: argparse.Namespace) -> int:
    if install_kind(root) == "system":
        print("This installation is managed by your package manager; nothing to register.")
        return 0
    link = user_bin() / "inferbridge"
    launcher = root / "InferBridge"
    if link.exists() or link.is_symlink():
        foreign = not link.is_symlink() or link.resolve().name != "InferBridge"
        if foreign:
            print(
                f"{link} exists and is not an InferBridge link; it is preserved.", file=sys.stderr
            )
            return 2
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(launcher)
    integrate(root)
    if user_unit_path().exists():
        # Upgrade: point an unmodified managed unit at this bundle and restart it if running.
        was_active = service_state()["active"] == "active"
        if integrate_service(root, replace_managed=True) == 0:
            _systemctl("daemon-reload")
            if was_active:
                _systemctl("restart", SERVICE_NAME)
    if autostart_path().exists() and managed_unmodified(autostart_path().read_text("utf-8")):
        write_entry(autostart_path(), _seal(desktop_entry(root, autostart=True)))
    on_path = str(link.parent) in os.environ.get("PATH", "").split(os.pathsep)
    print(f"Installed InferBridge {__version__} for this user: {link} -> {launcher}")
    if not on_path:
        print(f"Add {link.parent} to PATH (most distributions do this at your next login).")
    return 0


def _data_root_is_safe_to_purge(path: Path) -> bool:
    """Only a directory InferBridge created (it has config/) and never a home or XDG root."""
    resolved = path.resolve()
    forbidden = {Path("/"), Path.home().resolve(), data_home().resolve(), config_home().resolve()}
    return resolved not in forbidden and (resolved / "config").is_dir()


def command_uninstall(root: Path, args: argparse.Namespace) -> int:
    kind = install_kind(root)
    paths = runtime_paths(args)
    if instance(paths):
        command_stop(root, args)
    command_service(root, argparse.Namespace(**{**vars(args), "action": "uninstall"}))
    _remove_if(autostart_path())
    if kind != "system":
        integrate(root, remove=True)
    link = user_bin() / "inferbridge"
    with contextlib.suppress(OSError):
        if link.is_symlink() and link.resolve() == (root / "InferBridge").resolve():
            link.unlink()
    if args.purge:
        if not args.yes:
            print(f"Refusing to delete {paths.data_root} without --yes.", file=sys.stderr)
            return 2
        if not _data_root_is_safe_to_purge(paths.data_root):
            print(
                f"{paths.data_root} does not look like an InferBridge data directory.",
                file=sys.stderr,
            )
            return 2
        shutil.rmtree(paths.data_root, ignore_errors=False)
        print(f"Deleted application data: {paths.data_root}")
    else:
        print(f"Application data preserved: {paths.data_root} (use --purge --yes to delete it)")
    if kind == "system":
        print(
            "Remove the application with: sudo apt remove inferbridge  or  sudo dnf remove inferbridge"
        )
    elif args.remove_app:
        if not ((root / "InferBridge").is_file() and (root / "resources").is_dir()):
            print(f"{root} does not look like an InferBridge bundle; not deleted.", file=sys.stderr)
            return 2
        shutil.rmtree(root)
        print(f"Deleted application files: {root}")
    else:
        print(f"Delete {root} to remove the application files (or rerun with --remove-app).")
    return 0


# ------------------------------------------------------------------- update check


def latest_release(releases: list[dict], channel: str) -> dict | None:
    from app.release_models import SemanticVersion, channel_accepts

    best = None
    for release in releases:
        if release.get("draft"):
            continue
        version = str(release.get("tag_name") or "").removeprefix("v")
        try:
            parsed = SemanticVersion.parse(version)
        except ValueError:
            continue
        if not channel_accepts(channel, version):
            continue
        if best is None or parsed > best[0]:
            best = (parsed, version, release)
    if best is None:
        return None
    _parsed, version, release = best
    linux = [
        {"name": asset["name"], "url": asset["browser_download_url"]}
        for asset in release.get("assets", [])
        if "linux" in asset.get("name", "") or asset.get("name", "").endswith((".deb", ".rpm"))
    ]
    return {"version": version, "url": release.get("html_url"), "linux_assets": linux}


def command_update_check(root: Path, args: argparse.Namespace) -> int:
    from app.release_models import SemanticVersion

    current = SemanticVersion.parse(__version__)
    channel = args.channel or ("beta" if current.prerelease else "stable")
    request = urllib.request.Request(
        _RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"InferBridge/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed URL
            releases = json.loads(response.read(4 * 1024 * 1024))
    except (OSError, ValueError) as exc:
        print(f"Update check unavailable: {exc.__class__.__name__}", file=sys.stderr)
        return 1
    latest = latest_release(releases if isinstance(releases, list) else [], channel)
    newer = bool(latest and SemanticVersion.parse(latest["version"]) > current)
    report = {
        "current": __version__,
        "channel": channel,
        "update_available": newer,
        "latest": latest,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    elif newer:
        print(f"InferBridge {latest['version']} is available ({channel}): {latest['url']}")
        for asset in latest["linux_assets"]:
            print(f"  {asset['name']}")
        if not latest["linux_assets"]:
            print("  This release has no Linux downloads.")
        print("Verify downloads with the release's linux-SHA256SUMS.txt before installing.")
    else:
        print(f"InferBridge {__version__} is current for the {channel} channel.")
    return 0


# --------------------------------------------------------------------------- main

COMMANDS = {
    "start": command_start,
    "stop": command_stop,
    "status": command_status,
    "open": command_open,
    "logs": command_logs,
    "doctor": command_doctor,
    "service": command_service,
    "autostart": command_autostart,
    "install": command_install,
    "uninstall": command_uninstall,
    "update-check": command_update_check,
}


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", default=os.environ.get("OV_LLM_DATA_DIR"))
    common.add_argument("--portable", action="store_true")
    parser = argparse.ArgumentParser(
        prog="inferbridge",
        description="InferBridge for Linux. Run without a command to launch the app.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", parents=[common], help="Start in the background")
    start.add_argument("--mock", action="store_true", help="Deterministic mock engine (testing)")
    sub.add_parser("stop", parents=[common], help="Stop the running instance")
    sub.add_parser("status", parents=[common], help="Show URL, readiness and service state")
    sub.choices["status"].add_argument("--json", action="store_true")
    opener = sub.add_parser("open", parents=[common], help="Open the browser UI (starts if needed)")
    opener.add_argument("--app-window", action="store_true", help="Chromium-style app window")
    opener.add_argument("--mock", action="store_true", help=argparse.SUPPRESS)
    logs = sub.add_parser("logs", parents=[common], help="Show application logs")
    logs.add_argument("-n", "--lines", type=int, default=60)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.add_argument("--path", action="store_true", help="Print the log directory")
    doctor = sub.add_parser("doctor", parents=[common], help="Check drivers, devices and setup")
    doctor.add_argument("--json", action="store_true")
    doctor.add_argument("--all-distros", action="store_true", help="Show fixes for every distro")
    doctor.add_argument("--strict", action="store_true", help="Exit 1 when a check fails")
    service = sub.add_parser("service", parents=[common], help="systemd user service")
    service.add_argument(
        "action", choices=["install", "uninstall", "enable", "disable", "start", "stop", "status"]
    )
    autostart = sub.add_parser("autostart", parents=[common], help="Start at login (tray mode)")
    autostart.add_argument("action", choices=["enable", "disable", "status"])
    desktop = sub.add_parser("desktop", parents=[common], help="Application menu entries")
    desktop.add_argument("action", choices=["install", "uninstall"])
    sub.add_parser("install", parents=[common], help="Register this bundle for the current user")
    uninstall = sub.add_parser("uninstall", parents=[common], help="Remove user integration")
    uninstall.add_argument("--purge", action="store_true", help="Also delete models and settings")
    uninstall.add_argument("--yes", action="store_true", help="Confirm --purge")
    uninstall.add_argument("--remove-app", action="store_true", help="Delete a portable bundle")
    update = sub.add_parser("update-check", parents=[common], help="Look for a newer release")
    update.add_argument("--channel", choices=["stable", "beta", "nightly"])
    update.add_argument("--json", action="store_true")
    sub.add_parser("version", help="Print the version")
    return parser


def main(argv: list[str], root: Path) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(f"InferBridge {__version__} ({install_kind(root)} install at {root})")
        return 0
    if args.command == "desktop":
        if install_kind(root) == "system":
            print("The package already provides the application menu entry.")
            return 0
        return integrate(root, remove=args.action == "uninstall")
    return COMMANDS[args.command](root, args)
