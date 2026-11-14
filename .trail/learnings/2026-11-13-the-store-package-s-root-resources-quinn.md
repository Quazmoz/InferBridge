---
title: "The Store package's root resources.pri must include the native app's own resources, or the app crashes at launch"
author: Quinn
date: 2026-11-13
branch: dev
cost: { minutes: 44, tokens: 340727 }
id: l-76bb8caf
scope: repo
status: active
src: agent
touches:
  - docs/KNOWN_ISSUES.md
  - docs/MICROSOFT_STORE.md
  - docs/releases/1.1.1-dev.1.md
  - ".github/workflows/python312-windows.yml"
  - scripts/msix_package.py
  - tests/test_msix_package.py
  - tests/test_store_release_readiness.py
  - windows-native/InferBridge.Native/App.xaml.cs
  - scripts/smoke_test_msix.ps1
  - AGENTS.md
---

## Decided

The MSIX root `resources.pri` is built from the logos plus a copy of the native client's `InferBridge.Native.pri` (MakePri's PRI indexer merges it). The client also hands XAML its own PRI through `Application.ResourceManagerRequested` when it has package identity, so WinUI's file resources (acrylic noise) resolve under `native\`.

Why: in a process with package identity, MRT Core's default `ResourceManager` reads `<package root>\resources.pri` whenever that file exists and only falls back to `[exe name].pri` when it does not. A logo-only root PRI therefore hides every WinUI resource. This was the 2026-10-30 Store rejection (crash at launch, 10.1.2.10).

## Tried and ruled out

- Upgrading Windows App SDK 2.5.1 to 2.6.4 and blaming microsoft-ui-xaml#12169 / Windows 26H2: the same crash reproduces on windows-2022 with 2.6.4. `0xC000027B` in `Microsoft.UI.Xaml.dll` is the generic report for an unhandled failure on the XAML thread, not a signature of that issue.
- Only the `ResourceManagerRequested` hook: XAML then loads, but `NavigationView.set_SelectedItem` throws `0x80073B1F` "ResourceMap Not Found", because WinUI's controls create their own default `ResourceManager` for their strings and that one still reads the root PRI.

## Watch out

- Source checks and a structural pack cannot see this. Only an installed, package-identity launch does. The Windows qualification workflow now runs `smoke_test_msix.ps1 -WindowOnly` on the structural MSIX; keep it.
- The CI stand-in engine must be a real executable. A text file named `InferBridge.exe` makes Windows show a blocking "Unsupported 16-Bit Application" dialog when the client starts its engine.
- To get the inner exception of a packaged launch crash, log `AppDomain.FirstChanceException` and `Application.UnhandledException` to a file under `%USERPROFILE%` on a throwaway branch; the event log only has the outer `0xC000027B`.
- The merged root PRI keeps only en-US strings (the manifest declares only en-us).

In the code: scripts/msix_package.py (CLIENT_PRI, stage), windows-native/InferBridge.Native/App.xaml.cs, scripts/smoke_test_msix.ps1 (-WindowOnly), .github/workflows/python312-windows.yml, docs/MICROSOFT_STORE.md.
