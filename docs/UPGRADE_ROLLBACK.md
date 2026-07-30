# Upgrade and rollback

## Installed releases

The installer uses a stable application ID and upgrades the registered installation in place. Before copying a new release, it removes the previous immutable PyInstaller payload from the installation directory. This prevents stale Python modules, DLLs, or native extensions from being mixed with the new build.

Mutable data remains under `%LOCALAPPDATA%\OpenVINOWindowsLLM`, outside the installation directory. Models, configuration, onboarding state, benchmark history, caches, logs, diagnostics, and small configuration backups therefore survive ordinary upgrades. The installer cleanup does not target that data directory.

Inno Setup asks Windows Restart Manager to close and restart the packaged executable. Quit the tray before upgrading when possible. A reboot should occur only when Windows cannot replace a locked file. Review the setup log if an upgrade is partial.

The installer warns before a detected core-version downgrade. It never deletes the user-data directory during an upgrade. Interactive uninstall preserves data by default.

A `psutil_windows` missing-attribute error after installing an older package indicates that Python code and a native extension from different dependency versions were present together. Install the latest build over the existing registered installation. The clean replacement step removes the stale runtime payload while preserving application data.

## Portable releases

Do not replace a running portable directory. Quit the tray, extract the new ZIP into a new directory, then copy or explicitly point `OV_LLM_DATA_DIR` to the desired data directory. Portable mode otherwise uses its own sibling `data` directory and never silently shares installed data.

## Data schema

`data-schema.json` records the persistent schema. Migrations are idempotent and run before catalog or onboarding state is used. Before a future destructive migration, the application copies small configuration records into `config\backups`. Large model directories are not duplicated.

A release refuses to start when the data schema is newer than it understands. Full downgrade compatibility is not promised. The release manifest records minimum, current, and downgrade-compatible schema versions plus compiled-cache invalidation policy.

## Rollback procedure

1. Quit the tray and confirm the local server has stopped.
2. Preserve `%LOCALAPPDATA%\OpenVINOWindowsLLM\config` or the portable `data\config` directory.
3. Review the target release manifest data-schema fields and known issues.
4. Install or extract the prior release.
5. Reuse models only when the target manifest says model cache is compatible.
6. Delete only the compiled OpenVINO cache when the OpenVINO version, driver, device, or compilation properties changed.
7. Restore a compatible small-configuration backup when the older release rejects the current schema.

Do not delete model directories as a routine rollback step.
