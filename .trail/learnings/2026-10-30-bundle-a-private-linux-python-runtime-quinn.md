---
title: Bundle a private Linux Python runtime to preserve model conversion discovery
author: Quinn
date: 2026-10-30
branch: dev
id: l-21ebf99a
scope: repo
status: active
src: agent
touches:
  - QUICKSTART.md
  - README.md
  - app/config.py
  - app/desktop_launcher.py
  - app/frozen_entrypoint.py
  - app/release_routes.py
  - app/tray_app.py
  - app/tray_runtime.py
  - docs/DEVICE_SUPPORT.md
  - docs/FEDORA.md
---

## Decided

Use a private relocatable CPython runtime with hash-locked Linux wheels for the first Linux archive because Optimum discovers physical namespace registrations and Transformers/torch inspect their Python source during conversion. Keep the existing desktop controller and browser UI as the application boundary to avoid adding a second frozen conversion implementation.

## Tried and ruled out

Reusing the Windows PyInstaller specification would carry Windows metadata, DLL lookup and native WinUI launch assumptions into Linux. It also requires reproducing special source/data collection throughout the dynamic exporter chain. The Linux private interpreter can run the existing source modules directly while still requiring no system Python or source checkout from users.

## Watch out

The interpreter and Python wheels do not bundle system glibc or Intel kernel/user drivers. Native imports, device enumeration and mock API contracts do not prove real CPU/GPU/NPU generation. Keep release feeds separate because the existing release manifest describes Windows executables. Normal Linux extraction uses XDG data; sibling data is an explicit portable option. Closing a browser tab leaves the owner running, so always retain the Quit application menu entry and CLI command when the tray is missing.

In the code: scripts/build_linux_distribution.py, requirements/linux.lock, packaging/linux/InferBridge, app/desktop_controller.py, app/release_routes.py, .github/workflows/linux-distribution.yml. Build on Linux x86_64 and run scripts/validate_linux_bundle.py against the extracted archive from an unrelated directory.
