# Windows packaging and release

Use the production release entry point with the canonical version from `app/version.py`:

```powershell
.\scripts\build_release.ps1 -Version <version> -Channel stable -Clean -MockSmokeTest -GenerateChecksums
```

`-Clean` removes per-build output under `build\release` and `dist`, but it intentionally preserves validated release environments under `build\release-environments`. Each environment is fingerprinted from the exact pinned requirements, Python runtime, Windows platform, architecture, and fingerprint schema. The build verifies the metadata, every applicable pinned distribution, and `pip check` before reuse. Any failed validation causes that environment to be deleted and rebuilt automatically.

Release timing telemetry is written continuously to `build\release\release-timings.json`. A non-secret snapshot is also included in release artifacts as `InferBridge-<version>-release-timings.json`, covered by artifact scanning, checksums, and publication verification.

Portable packaging runs the portable-mode smoke test directly against the already validated PyInstaller distribution after temporarily adding portable-only files. The ZIP is then created directly with the versioned top-level directory and structurally verified without copying or extracting the complete distribution. The installer is compiled first from the unmodified installed-mode distribution.

See:

- `docs/RELEASE_PROCESS.md` for building, signing, verification, and publication
- `docs/VERSIONING.md` for the canonical version and SemVer policy
- `docs/UPGRADE_ROLLBACK.md` for installed and portable data behavior
- `docs/UPDATE_POLICY.md` for optional stable and beta checks
- `docs/COMPATIBILITY_MATRIX.md` for evidence-backed hardware validation
- `docs/KNOWN_ISSUES.md` for structured release limitations
- `docs/THIRD_PARTY_LICENSES.md` for dependency notices

`scripts/build_windows_distribution.ps1` remains a compatibility wrapper and delegates to `build_release.ps1`. Do not use it to bypass release validation.
