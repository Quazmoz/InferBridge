---
title: "Commands the web UI offers to copy must match the browser's operating system, and Linux updates stay out of the UI"
author: Quinn
date: 2026-11-13
branch: dev
cost: { minutes: 10, tokens: 222913 }
id: l-e852b037
scope: repo
status: active
src: agent
touches:
  - app/connection_hub_ui.py
  - app/release_routes.py
  - app/release_ui.py
  - app/onboarding_hardware.py
  - app/linux_cli.py
  - app/errors.py
  - docs/LINUX.md
---

## Decided

Snippets a user copies from the browser UI (curl examples, the rerank request, Copilot CLI variables) follow the operating system of the browser showing the page, not the server: PowerShell with curl.exe on Windows, POSIX shell on Linux and macOS. Over LAN those two machines differ, and the browser UI is the whole Linux product. Server-side facts (firewall advice, NPU driver link, start-at-login state) follow the server's operating system instead.

The About dialog on a Linux bundle shows a message pointing at `inferbridge update-check` instead of update controls. The route gate that keeps Linux bundles off the Windows release manifest was kept on purpose.

## Tried and ruled out

Removing the Linux gate in the release routes so the UI could check for updates: the docs briefly promised this, but a test pins that a Linux bundle never contacts the Windows manifest feed, and the CLI already lists the Linux assets from the releases API. The docs were corrected instead.

Detecting the client OS inside the pure tool-setup builders: Node 21+ defines a global navigator, so the Node-driven tests would flip to POSIX output on macOS and Linux. The flag is passed in from the hub script and defaults to the Windows form the native app mirrors.

## Watch out

Before the fix a Linux bundle's About dialog offered "Enable Stable Checks", which the server refused with 409. Any new update control must honor `update_message` in the release status. The tray's "Start with Windows" item is hidden off Windows; the browser card reads the autostart entry or the enabled user service by file checks only, because status is polled.

In the code: app/connection_hub_ui.py (POSIX, sh), app/release_routes.py (update_message), app/release_ui.py, app/onboarding_hardware.py (detect_linux_npu_hardware), app/linux_cli.py (starts_at_login), app/errors.py (NPU_DRIVER_DOWNLOAD_URL).
