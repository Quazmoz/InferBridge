# Code signing

Unsigned local-development builds are supported. Artifact filenames remain deterministic and do not use `signed` or `unsigned` suffixes. Trust state is recorded only in the validated release manifest and summary. Stable GitHub publication requires verified Authenticode signatures for both the installer and the launcher inside the portable ZIP.

## Secure configuration

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
