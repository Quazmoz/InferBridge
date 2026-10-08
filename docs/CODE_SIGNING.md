# Code signing

Unsigned local-development builds are supported. Artifact filenames remain deterministic and do not use `signed` or `unsigned` suffixes. Trust state is recorded only in the validated release manifest and summary. Stable GitHub publication requires verified Authenticode signatures for both the installer and the launcher inside the portable ZIP.

## Free signing through SignPath Foundation (CI)

InferBridge is MIT-licensed, so it qualifies for free OSS code signing from [SignPath Foundation](https://signpath.org). The certificate is issued to SignPath Foundation, so Windows shows **SignPath Foundation** as the publisher. Every release needs a maintainer's manual approval in SignPath, and SmartScreen reputation still builds over the first downloads.

`.github/workflows/release.yml` (manual `workflow_dispatch`) runs the build in three phases on a GitHub-hosted runner:

1. `build_release.ps1 -ExternallySigned -Phase Launcher` builds and smoke-tests the PyInstaller layout.
2. `InferBridge.exe` is uploaded and signed through SignPath (artifact configuration `launcher`), then copied back.
3. `-Phase Package` verifies the launcher signature, compiles the installer, and creates the portable ZIP.
4. The installer is signed through SignPath (artifact configuration `installer`) and copied back.
5. `-Phase Finalize` verifies the installer signature and writes the manifest, summary, and checksums with both trust fields set.

The workflow never publishes. Download the release artifact and publish with `publish_release.ps1`, which re-verifies both signatures.

One-time setup:

1. Apply at https://signpath.org/apply with the repository URL.
2. After acceptance, in SignPath: link the predefined *GitHub.com* trusted build system to the project, install the [SignPath GitHub App](https://github.com/apps/signpath) on this repository, and create artifact configurations with the slugs `launcher` and `installer` from `.signpath/artifact-configurations/`.
3. In GitHub repository settings add the secret `SIGNPATH_API_TOKEN` (a submitter's API token) and the variable `SIGNPATH_ORGANIZATION_ID`. Override `SIGNPATH_PROJECT_SLUG` (default `InferBridge`) or `SIGNPATH_SIGNING_POLICY_SLUG` (default `release-signing`) only if SignPath assigned different slugs.
4. Run **Release build** with *sign* checked and approve both signing requests in SignPath.

Locally, the same phases work with any external signer: run each phase with identical arguments in the same checkout and replace the file between phases.

## Code signing policy

Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by [SignPath Foundation](https://signpath.org).

- Committers and reviewers: [Quazmoz](https://github.com/Quazmoz)
- Approvers: [Quazmoz](https://github.com/Quazmoz)

Only release builds produced by `.github/workflows/release.yml` from this repository are signed, and every signing request is approved manually.

Privacy: this program will not transfer any information to other networked systems unless specifically requested by the user or the person installing or operating it. Update checks are off by default, and model downloads from Hugging Face happen only when the user starts them.

## Certificate you hold yourself


Preferred Windows certificate-store signing:

```text
OV_LLM_SIGNTOOL_PATH
OV_LLM_SIGN_CERT_SHA1
OV_LLM_SIGN_TIMESTAMP_URL
```

Certificate-file fallback:

```text
OV_LLM_SIGN_CERTIFICATE
OV_LLM_SIGN_CERTIFICATE_PASSWORD
OV_LLM_SIGN_TIMESTAMP_URL
```

Certificates, private keys, passwords, tokens, and signing secrets must never enter the repository, generated release output, or logs. Prefer a certificate-store thumbprint or secure CI secret injection over a PFX file.

Configure exactly one certificate source. PFX signing requires `OV_LLM_SIGN_CERTIFICATE_PASSWORD`; the build never accepts an interactive password prompt. Do not pass certificate paths, passwords, or thumbprints as script arguments. Restrict access to the signing account and remove any temporary PFX after the secure job completes.

The certificate subject controls the publisher name shown by Windows. A valid trusted certificate removes the `Unknown publisher` state, but Microsoft Defender SmartScreen reputation is separate and may take time to establish for a new certificate or product.

## Build behavior

```powershell
.\scripts\build_release.ps1 -Version 0.6.3 -Channel stable -Clean -Sign -MockSmokeTest -GenerateChecksums
```

The release build:

1. generates the application PNG and multi-resolution ICO assets;
2. embeds the ICO in the packaged launcher and installer;
3. signs the packaged launcher before portable staging;
4. timestamps the signature;
5. verifies the launcher with `signtool verify /pa /all`;
6. compiles the installer;
7. signs, timestamps, and verifies the installer;
8. marks trust fields true only after verification succeeds.

A signing, timestamp, or verification failure blocks a signed release. The ZIP archive itself is not Authenticode-signed. Its manifest records whether the contained launcher signature was verified, and users must still verify the ZIP SHA-256 checksum.

`/tr <url> /td SHA256` applies an RFC 3161 timestamp. Before publishing a release whose metadata claims signatures, `publish_release.ps1` independently runs `signtool verify /pa /all` against the installer and against the launcher extracted from the portable ZIP. A missing SignTool, partial claim, manifest/summary disagreement, missing artifact, malformed ZIP, or nonzero verification result blocks publication.

Stable publication additionally invokes the signing verifier with `--require-signed`. An unsigned artifact set can still be built for local validation, but it cannot pass the stable publication gate.

Signed releases must include both the portable launcher and installer; `-Sign` cannot be combined with `-SkipPortable` or `-SkipInstaller`.

Unsigned validation:

```powershell
.\scripts\build_release.ps1 -Version <new-version> -Unsigned -SkipInstaller -MockSmokeTest
```

Previously published unsigned artifacts must not be replaced or retagged. A later signed build requires a new version.
