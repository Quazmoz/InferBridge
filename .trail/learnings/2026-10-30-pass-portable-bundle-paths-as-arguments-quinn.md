---
title: Pass portable bundle paths as arguments in systemd units
author: Quinn
date: 2026-10-30
branch: dev
id: l-a443e6b4
scope: repo
status: active
src: agent
touches:
  - packaging/linux/linux_desktop.py
  - tests/test_linux_distribution.py
---

## Decided

Invoke /bin/bash directly in the user unit and pass the portable launcher path as a quoted argument after --. Retain the colon prefix to suppress environment expansion and double percent specifiers. Bash is already required by the bundled launcher.

## Tried and ruled out

Using the bundle path as ExecStart's executable failed native Ubuntu 22.04 and 24.04 systemd-analyze verification when the installation directory contained quotes or backslashes, despite correct unit quoting. systemd imposes extra restrictions on executable names. Passing a literal argument through Bash avoids those restrictions without evaluating shell code; do not use bash -c.

## Watch out

Keep the Linux parser regression covering spaces, Unicode, percent, dollar, quote and backslash characters. macOS unit tests cannot exercise systemd's parser.

In the code: packaging/linux/linux_desktop.py service_entry; tests/test_linux_distribution.py test_service_unit_passes_systemd_parser. Initial Linux CI run 41124084957 exposed the restriction.
