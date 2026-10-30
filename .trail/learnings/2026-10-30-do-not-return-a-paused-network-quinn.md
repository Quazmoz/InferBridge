---
title: Do not return a paused network promise from a browser test
author: Quinn
date: 2026-10-30
branch: dev
id: l-e26a9e72
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/linux-distribution.yml"
  - app/desktop_launcher.py
  - app/onboarding_hardware.py
  - app/onboarding_ui.py
  - app/tray_runtime.py
  - browser_tests/test_context_budget_browser.py
  - browser_tests/test_conversation_management_browser.py
  - docs/LINUX.md
  - runtime/device_check.py
  - scripts/validate_linux_bundle.py
---

## Decided

Start a deliberately paused browser request inside a function that returns no promise because Playwright awaits any promise returned by page.evaluate. Keep the request promise on window for the later completion assertion, then fulfill the intercepted route after checking the busy state.

## Tried and ruled out

Returning the assignment expression for a fetch promise deadlocks the test: Playwright waits for that promise before Python can reach the route.fulfill call that resolves it. Increasing navigation or assertion timeouts cannot resolve this ordering cycle. The page and backend do not need a workaround.

## Watch out

Tests opening scrollable settings panels must scroll the desired control into view before asserting viewport visibility. A control can be below the fold in an open panel, especially as settings sections grow. Preserve the viewport and interaction assertions rather than removing them.

In the code: browser_tests/test_context_budget_browser.py test_context_apply_shows_rebuild_status_and_holds_sends; browser_tests/test_conversation_management_browser.py _open_settings. Run the browser_tests suite with its own --basetemp when unit and browser checks run concurrently.
