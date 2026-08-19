"""Frozen executable process boundary for the packaged InferBridge launcher.

PyInstaller runtime hooks execute before this module. The lightweight
``--bootstrap-smoke`` mode therefore proves that the frozen interpreter, runtime hooks,
and desktop startup import graph can load from the installed directory without starting
the tray, server, browser, or model runtime.
"""

from __future__ import annotations

import sys

_APP_TITLE = "InferBridge"


def _show_startup_failure(error: BaseException) -> None:
    """Report an unexpected frozen-launch failure without exposing a traceback."""

    message = (
        f"{_APP_TITLE} could not start because the packaged runtime raised "
        f"{error.__class__.__name__}.\n\n"
        "Reinstall the latest build over the existing installation. Your downloaded "
        "models and settings are stored separately and are preserved."
    )
    try:
        from app.desktop_shell import show_dialog

        show_dialog(_APP_TITLE, message, error=True)
    except Exception:
        # A failure this early may also prevent the dialog helper from importing. The
        # important invariant is that the exception does not escape to PyInstaller's
        # windowed unhandled-exception boundary.
        pass


def _bootstrap_smoke() -> int:
    """Import the real desktop startup graph without starting owned resources."""

    try:
        from app.desktop_launcher import main
        from app.tray_app import run_tray_controller
    except Exception:  # noqa: BLE001 - installer-owned frozen import boundary
        return 2
    return 0 if callable(main) and callable(run_tray_controller) else 2


def run(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--bootstrap-smoke"]:
        return _bootstrap_smoke()

    try:
        from app.desktop_launcher import main

        return main(arguments)
    except SystemExit:
        # argparse deliberately uses SystemExit for invalid command lines. Preserve that
        # contract rather than converting expected CLI validation into a startup failure.
        raise
    except Exception as exc:  # noqa: BLE001 - frozen top-level process boundary
        _show_startup_failure(exc)
        return 2


if __name__ == "__main__":
    sys.exit(run())
