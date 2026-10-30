---
title: Name large pytest cases to fit Windows environment limits
author: Quinn
date: 2026-10-30
branch: dev
id: l-324274fc
scope: repo
status: active
src: agent
touches:
  - tests/test_desktop_http.py
  - tests/test_linux_distribution.py
---

## Decided

Give large parameterized payloads short explicit pytest case IDs. Decode the service unit's quoted environment value before comparing it with a platform path.

## Tried and ruled out

The default ID for the 512 KiB oversized HTTP response copied the payload into PYTEST_CURRENT_TEST. Windows rejects environment values longer than 32767 characters, so that case failed during setup and teardown before exercising IPC. The raw Windows path assertion also failed because a correct unit contains doubled backslashes.

## Watch out

Keep the oversized-response check enabled on Windows. Short IDs fix the harness without shrinking the test payload or weakening its assertion.

In the code: tests/test_desktop_http.py response payload parametrization; tests/test_linux_distribution.py relative-XDG fallback assertion. Windows qualification run 41124537043 exposed both harness issues; production Linux package checks passed on the same commit.
