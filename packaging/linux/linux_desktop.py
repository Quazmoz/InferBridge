"""Linux bundle desktop integration; application behavior stays in app.desktop_launcher."""

from __future__ import annotations

import os
import sys
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
        "Categories=Development;Science;\n"
        f"X-InferBridge-Root={str(root).replace(chr(92), chr(92) * 2)}\n"
    )


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
            directory.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_text(contents, encoding="utf-8")
            temporary.chmod(0o644)
            temporary.replace(target)
    print(
        "Desktop entries removed; application data preserved."
        if remove
        else "Desktop entries installed."
    )
    return 0


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    arguments = sys.argv[1:]
    if arguments in (["--install-desktop"], ["--uninstall-desktop"]):
        return integrate(root, remove=arguments == ["--uninstall-desktop"])
    from app.frozen_entrypoint import run

    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
