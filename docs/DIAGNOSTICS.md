# Privacy-safe diagnostics bundles

The InferBridge tray and browser desktop-operations panel can create a fully local support ZIP.

```text
Tray icon → Export Diagnostics → Review the ZIP → Attach it to a GitHub issue
```

Do not upload model files, tokens, certificates, source images, prompts, or chat history.

## Confirmation

Before export, InferBridge lists the operational categories that will be included and explicitly states that prompts, chat history, API keys, Hugging Face tokens, images, model files, caches, certificates, and browser localStorage are excluded.

The output is created under the writable diagnostics directory with a timestamped InferBridge filename:

```text
inferbridge-diagnostics-YYYYMMDD-HHMMSS.zip
```

The application never uploads the bundle.

## Included categories

Best-effort collection may include application and packaging metadata, Windows and hardware information, OpenVINO versions and visible devices, NPU readiness, hardware fingerprint, model and preparation state, bounded sanitized events and logs, benchmark summaries, non-secret configuration, redacted storage paths, liveness, readiness, controller state, certification summaries, and a machine-readable manifest.

## Exclusions

The collector never traverses model directories or cache trees and never includes API keys, Authorization headers, Hugging Face tokens, signing credentials, certificates, private keys, prompts, chat history, raw request bodies, browser localStorage, source images, model weights, OpenVINO IR, compiled cache contents, arbitrary configured files, unbounded logs, remote uploads, or telemetry.

## Privacy controls

- Fixed allowlists for fields and filenames.
- Secret-name and common token-pattern redaction.
- Windows and POSIX home-directory user components replaced with `<redacted-user>`.
- Email address redaction.
- Byte- and line-bounded log collection.
- Whole-line replacement for apparent prompts, request bodies, messages, chat history, or source-image data.
- ZIP entry validation against absolute paths and `..` traversal.
- Symlink and path-escape rejection.
- Size-bounded certification summaries.
- Best-effort collection with sanitized errors recorded in the manifest.

## Manifest

Every ZIP includes `manifest.json` with schema version, application version, creation time, installation mode, included files, redactions, and collection errors.

Review the generated ZIP before sharing it. Report a privacy issue immediately if any personal or confidential information remains.
