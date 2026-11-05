"""Linux bundle entry point: ``inferbridge`` subcommands, legacy flags, then the app.

Integration logic lives in ``app.linux_cli`` and application behavior in
``app.desktop_launcher``. This file stays tiny so the bundle layout is easy to audit.
"""

from __future__ import annotations

import sys
from pathlib import Path

from app.linux_cli import (  # noqa: F401 - re-exported for the bundle and its tests
    COMMANDS,
    desktop_entry,
    desktop_quote,
    integrate,
    integrate_service,
    main as cli_main,
    service_entry,
    unit_quote,
    write_entry,
)

SUBCOMMANDS = frozenset({*COMMANDS, "desktop", "version"})
LEGACY_FLAGS = {
    "--install-desktop": ["desktop", "install"],
    "--uninstall-desktop": ["desktop", "uninstall"],
}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    arguments = sys.argv[1:]
    if arguments and arguments[0] in SUBCOMMANDS:
        return cli_main(arguments, root)
    if len(arguments) == 1 and arguments[0] in LEGACY_FLAGS:
        return cli_main(LEGACY_FLAGS[arguments[0]], root)
    if arguments in (["--install-service"], ["--uninstall-service"]):
        return integrate_service(root, remove=arguments == ["--uninstall-service"])
    if arguments in (["-h"], ["--help"]):
        print(
            "Usage: inferbridge [COMMAND]  (no command launches the app)\n\n"
            "Commands: start, stop, status, open, logs, doctor, service, autostart,\n"
            "  desktop, install, uninstall, update-check, version.\n"
            "  Run `inferbridge COMMAND --help` for details.\n\n"
            "Legacy integration flags:\n"
            "  --install-desktop / --uninstall-desktop  Application menu entries\n"
            "  --install-service / --uninstall-service  Optional systemd user unit\n"
        )
    from app.frozen_entrypoint import run

    return run(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
