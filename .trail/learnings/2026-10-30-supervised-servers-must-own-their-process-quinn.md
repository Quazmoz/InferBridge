---
title: Supervised servers must own their process and fail when the backend exits
author: Quinn
date: 2026-10-30
branch: dev
id: l-ac7981fa
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/linux-distribution.yml"
  - ".github/workflows/linux-smoke.yml"
  - app/desktop_controller.py
  - app/desktop_launcher.py
  - app/tray_runtime.py
  - docs/LINUX.md
  - packaging/linux/linux_desktop.py
  - scripts/validate_linux_bundle.py
  - tests/test_ci_dependency_contract.py
  - tests/test_conversion_cli_preflight.py
---

## Decided

Add an explicit service mode to the existing desktop controller: run in the foreground without a browser or tray, require exclusive data-directory ownership, and exit nonzero after an unexpected backend failure. Let the host supervisor handle bounded restarts. Keep ordinary second desktop launches reconnecting to their existing owner and keep deliberate Quit clean.

## Tried and ruled out

Using the existing headless invocation unchanged is insufficient supervision. A duplicate invocation returns success after activating another owner, and a crashed child previously left the headless owner polling indefinitely. Restarting inside the controller would hide repeated device failures and duplicate the host supervisor's policy. Local IPC must also bypass environment proxies and reject redirects; adding NO_PROXY only in an installer does not fix other launch paths.

## Watch out

systemd command escaping differs from shell and desktop-entry escaping. Disable dollar expansion with the ExecStart colon prefix and escape percent specifiers separately. Use mixed kill mode so SIGTERM reaches the owner first and its existing child cleanup can run. Service registration does not start or enable anything, and removal must preserve edited or foreign units. Default user services follow login; lingering requires separate host administration. A validated unit and a mock subprocess lifecycle do not certify a live user manager, graphical desktop, or Intel inference.

In the code: app/desktop_launcher.py, app/desktop_controller.py, app/tray_runtime.py, packaging/linux/linux_desktop.py, scripts/validate_linux_bundle.py, tests/test_desktop_http.py, tests/test_linux_distribution.py. Run the real-listener tests with local socket access; Linux qualification additionally verifies the unit with systemd-analyze and kills the owned mock backend to assert exit 6 and preserved data.
