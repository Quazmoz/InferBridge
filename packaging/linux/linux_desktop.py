"""Linux bundle desktop integration; application behavior stays in app.desktop_launcher."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def desktop_quote(value: str) -> str:
    if any(character in value for character in "\r\n\0"):
        raise ValueError("Desktop paths cannot contain newlines or NUL.")
    # Exec quoting is parsed after desktop-value escape processing.
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    return '"' + value.replace("\\", "\\\\").replace("%", "%%") + '"'


def desktop_entry(root: Path, *, quit: bool = False) -> str:
    executable = desktop_quote(str(root / "InferBridge"))
    icon = str(root / "resources" / "InferBridge.png")
    if any(character in str(root) for character in "\r\n\0"):
        raise ValueError("Desktop paths cannot contain newlines or NUL.")
    # General string values have a separate escape rule from Exec arguments.
    icon = icon.replace("\\", "\\\\")
    return (
        "[Desktop Entry]\nType=Application\n"
        f"Name={'Quit InferBridge' if quit else 'InferBridge'}\n"
        "Comment=Local OpenVINO AI server and model manager\n"
        f"Exec={executable}{' --quit' if quit else ''}\n"
        f"Icon={icon}\nTerminal=false\nStartupNotify=false\n"
        "Categories=Development;\n"
        f"X-InferBridge-Root={str(root).replace(chr(92), chr(92) * 2)}\n"
    )


def write_entry(target: Path, contents: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, prefix=f".{target.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(contents)
        temporary.chmod(0o644)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def integrate(root: Path, *, remove: bool = False) -> int:
    configured = Path(os.environ.get("XDG_DATA_HOME", ""))
    data_home = configured if configured.is_absolute() else Path.home() / ".local" / "share"
    directory = data_home / "applications"
    for quit in (False, True):
        target = directory / ("inferbridge-quit.desktop" if quit else "inferbridge.desktop")
        contents = desktop_entry(root, quit=quit)
        if remove:
            if target.is_file() and target.read_text(encoding="utf-8") == contents:
                target.unlink()
        else:
            write_entry(target, contents)
    print(
        "Desktop entries removed; application data preserved."
        if remove
        else "Desktop entries installed."
    )
    return 0


def unit_quote(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Service paths cannot contain control characters.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%") + '"'


def service_entry(root: Path) -> str:
    # ':' disables environment substitution in ExecStart; '%' still needs escaping.
    # systemd syntax differs from both shell and .desktop quoting.
    executable = unit_quote(str(root / "InferBridge"))
    configured = Path(os.environ.get("XDG_DATA_HOME", ""))
    data_home = configured if configured.is_absolute() else Path.home() / ".local" / "share"
    data_dir = os.environ.get("OV_LLM_DATA_DIR")
    data_option = (
        " --data-dir " + unit_quote(str(Path(data_dir).expanduser().resolve())) if data_dir else ""
    )
    return (
        "[Unit]\nDescription=InferBridge local AI server\n"
        "StartLimitIntervalSec=120\nStartLimitBurst=3\n\n"
        "[Service]\nType=exec\n"
        f"ExecStart=:{executable} --service{data_option}\n"
        f"Environment={unit_quote('XDG_DATA_HOME=' + str(data_home))}\n"
        "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=60\n"
        "KillMode=mixed\nUMask=0077\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


def integrate_service(root: Path, *, remove: bool = False) -> int:
    configured = Path(os.environ.get("XDG_CONFIG_HOME", ""))
    config_home = configured if configured.is_absolute() else Path.home() / ".config"
    target = config_home / "systemd" / "user" / "inferbridge.service"
    contents = service_entry(root)
    if remove:
        if target.is_file() and target.read_text(encoding="utf-8") == contents:
            target.unlink()
            print(
                "Service unit removed; application data preserved. Run systemctl --user daemon-reload."
            )
        else:
            print("No matching service unit removed; a different or edited unit is preserved.")
    else:
        if target.exists() and target.read_text(encoding="utf-8") != contents:
            print(
                "An existing service unit differs. Stop and uninstall it with its owning "
                "launcher, or back up and remove the unit before installing this version.",
                file=sys.stderr,
            )
            return 2
        write_entry(target, contents)
        print(
            "User service installed (not started). Quit any running InferBridge instance, then run:\n"
            "  systemctl --user daemon-reload\n"
            "  systemctl --user enable --now inferbridge.service\n"
            "Stop and disable it before uninstalling:\n"
            "  systemctl --user disable --now inferbridge.service"
        )
    return 0


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    arguments = sys.argv[1:]
    if arguments in (["--install-desktop"], ["--uninstall-desktop"]):
        return integrate(root, remove=arguments == ["--uninstall-desktop"])
    if arguments in (["--install-service"], ["--uninstall-service"]):
        return integrate_service(root, remove=arguments == ["--uninstall-service"])
    if arguments in (["-h"], ["--help"]):
        print(
            "Linux bundle integration:\n"
            "  --install-desktop / --uninstall-desktop  Application menu entries\n"
            "  --install-service / --uninstall-service  Optional systemd user unit\n"
        )
    from app.frozen_entrypoint import run

    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
