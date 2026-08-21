# Privacy-safe diagnostics bundles

InferBridge provides two local, privacy-conscious diagnostics workflows:

```text
Tray icon → Copy → Diagnostics → Paste into the feedback form or a GitHub issue
System Doctor → Copy support report → Paste into the feedback form or a GitHub issue
```

For a larger troubleshooting bundle:

```text
Tray icon → Export Diagnostics → Review the ZIP → Attach it to a feedback report
```

The primary low-friction support page is:

https://consultant.quinnfavo.com/apps/inferbridge#feedback

GitHub Issues remain available for developers, contributors, advanced technical reports, and public engineering discussion.

Do not upload model files, tokens, certificates, source images, prompts, or chat history.

## Copyable diagnostics

The tray **Copy → Diagnostics** action builds a concise support block from the same allowlisted application, hardware, runtime, and configuration data used by the diagnostics collector. It can include the InferBridge version, build/environment mode, Windows/OS information, architecture, CPU, RAM, detected GPU/NPU devices, OpenVINO and OpenVINO GenAI versions, available OpenVINO devices, selected device, current model identifier, model format when available, and mock-mode state.

Unavailable information is reported as unavailable or not detected rather than inferred. Model filesystem paths are not included in the copyable block.

Nothing is transmitted automatically. InferBridge copies the text only after the user explicitly chooses the action.

## Confirmation for ZIP export

Before ZIP export, InferBridge lists the operational categories that will be included and explicitly states that prompts, chat history, API keys, Hugging Face tokens, images, model files, caches, certificates, and browser localStorage are excluded.

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

Review generated diagnostics before sharing them. Report a privacy issue immediately if any personal or confidential information remains.
