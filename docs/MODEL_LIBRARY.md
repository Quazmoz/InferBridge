# Verified Model Library

The Verified Model Library is a curated browser layered over the mutable InferBridge runtime catalog. It is intentionally not a public marketplace.

## Evidence states

Each CPU, Intel GPU, and Intel NPU badge has one of three meanings:

- **Verified on DEVICE** means the official manifest contains a retained certification record with an OpenVINO version, certification date, measurements, and hardware or driver evidence.
- **Verified on this PC** means this installation completed a local benchmark for the model and requested device. It is local evidence, not a project-wide compatibility claim.
- **Expected on DEVICE, unverified** is an informed expectation only.

The bundled offline manifest contains no fabricated certifications. Official verification records can be added only after the Windows certification procedure has been executed and retained.

## Curated recommendations

The bundled browser defaults to a small maintained set instead of presenting every catalog entry as equally recommended. The available filters are:

- Fastest
- Balanced
- Best quality
- Lowest memory

Rankings prefer local benchmark evidence when it exists and otherwise use conservative maintained profile scores plus hardware-advisor estimates. The existing full runtime catalog remains available through **Show all registered**.

## Official manifest

InferBridge downloads only the fixed canonical project release asset:

```text
https://github.com/Quazmoz/InferBridge/releases/latest/download/model-library-manifest.json
```

For compatibility with installations and releases from before the rename, the updater may also attempt the legacy repository location. Users cannot supply an arbitrary manifest URL.

The response must remain on an approved GitHub release host, stay below 1 MB, use the supported schema, contain at most 50 entries, and pass the SHA-256 checksum of its canonical catalog. Release publication also includes this asset in the versioned SHA-256 checksum file. A valid copy is cached beside the writable model catalog. If it is unavailable or invalid, the bundled manifest remains the offline fallback.

Checksums protect against corruption and inconsistent publication. They are not substitutes for HTTPS, GitHub account security, release signing, or Authenticode verification.

## Conversion health

New conversions and imported OpenVINO IR directories receive a local `.ovllm-conversion.json` marker containing:

- model identifier and source
- backend and weight format
- application version
- OpenVINO and OpenVINO GenAI versions
- recording date

The library reports conversions as compatible, legacy or untracked, stale after a runtime major or minor change, definition-mismatched, incomplete, or metadata-damaged. A warning does not delete or silently reconvert a model.

## Import and export

### Definitions

**Export definitions** produces a JSON file containing the maintained and user-imported model definitions. **Import definitions** validates at most 50 filesystem-safe entries and refuses conflicting replacements unless overwrite was explicitly requested through the API.

Definitions do not contain API keys, Hugging Face tokens, prompts, chats, benchmark hardware fingerprints, or local model paths.

### Already-converted OpenVINO models

The browser accepts an absolute local directory containing OpenVINO IR. The server:

1. validates required IR markers;
2. rejects symbolic links inside the source directory;
3. checks free disk space;
4. copies into the managed model directory through a temporary path;
5. atomically moves the completed copy into place;
6. records compatibility metadata; and
7. registers the model definition.

A loaded, loading, or converting model cannot be replaced. In-place replacement of a managed converted model is intentionally disabled. Unload and delete the managed copy first, or import the new directory under a different model ID.

## API

```text
GET  /v1/model-library
GET  /v1/model-library/export
POST /v1/model-library/refresh
POST /v1/model-library/import-definitions
POST /v1/model-library/import-converted
```

`profile` accepts `fastest`, `balanced`, `best_quality`, or `lowest_memory`. `include_all=true` adds every registered runtime model.

State-changing browser requests enforce the existing same-origin safeguard and API-key policy. The refresh route does not accept an arbitrary URL. Official definitions cannot enable `trust_remote_code` through the model-library refresh path.
