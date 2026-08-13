# Model storage and cache manager

InferBridge desktop builds include a local storage screen for understanding and cleaning the writable data managed by the application. The screen is informational by default. Nothing is removed without an explicit action and confirmation.

## Inventory

The manager reports:

- converted OpenVINO model size by catalog model;
- conversion-health state from the existing compatibility marker;
- whether a model is loaded or currently preparing;
- the last successful generation recorded by this installation;
- reusable Hugging Face source-cache size, deduplicated when multiple definitions share one source repository;
- incomplete model output, transaction staging, and persisted recovery metadata;
- protected transaction-backup size;
- OpenVINO compiled-cache size; and
- the bytes currently reclaimable by each available cleanup action.

The API never returns absolute local paths. Last-use tracking stores only a catalog model identifier and Unix timestamp in `config/storage-usage.json`. It does not store prompts, responses, token content, API keys, Hugging Face credentials, or client information.

## Cleanup actions

### Delete converted model

Uses the existing model lifecycle deletion path. The model must be unloaded and must not be loading or converting. Existing containment checks reject paths outside managed model storage, symbolic links, and Windows junctions.

The catalog definition and reusable Hugging Face source cache remain available. The model must be converted again before it can be loaded.

### Remove reusable Hugging Face cache

Removes only the cache directory derived from a catalog model's validated `owner/repository` source identifier. No arbitrary path is accepted. Cleanup is refused while any catalog model sharing that source is loading or converting.

A future conversion downloads the source files again.

### Remove incomplete preparation data

Removes incomplete live output, transaction staging, and the corresponding recovery record. It reuses the resilient recovery cleanup that handles read-only files and bounded Windows sharing-violation retries.

Transaction backups are deliberately excluded. They may contain the previous working model and remain available to the transactional publication recovery path.

### Clear compiled cache

Removes and recreates the managed OpenVINO compiled-cache directory. Every model must be unloaded and all lifecycle operations must be idle. The next load for a model and device may take longer while OpenVINO compiles again.

## Safety model

All cleanup operations:

1. derive their target from configured managed roots and catalog identifiers;
2. reject symbolic links and Windows junctions before traversal;
3. walk directories without following links;
4. refuse active lifecycle conflicts;
5. block new model lifecycle work while the selected files are being removed;
6. retry transient Windows file-lock and read-only failures with bounded delays;
7. return sanitized errors without local paths; and
8. require the local browser UI header, same-origin safeguards, and any configured API key.

The storage endpoint is desktop-only and excluded from the public OpenAPI schema.

## API

```text
GET  /v1/storage
POST /v1/storage/cleanup
```

Cleanup request examples:

```json
{"action":"delete_converted_model","model_id":"qwen2.5-1.5b-instruct-fp16"}
```

```json
{"action":"remove_huggingface_cache","model_id":"qwen2.5-1.5b-instruct-fp16"}
```

```json
{"action":"remove_incomplete_data","model_id":"qwen2.5-1.5b-instruct-fp16"}
```

```json
{"action":"clear_compiled_cache"}
```
