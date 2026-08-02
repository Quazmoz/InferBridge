# Model preparation recovery

InferBridge records a bounded, sanitized recovery summary when model conversion or loading ends in an error or is cancelled. The recovery workflow reuses the existing lifecycle scheduler. It does not run a separate converter or bypass model-operation serialization.

## Recovery screen

When recoverable state is available, the WebGUI displays a recovery screen with:

- whether Hugging Face source files appear reusable
- whether OpenVINO conversion output is complete, incomplete, or missing
- the last completed preparation stage
- the stage that failed or was cancelled
- a recommended recovery action
- sanitized failure details and a bounded converter log tail

Raw tokens, authorization headers, full local paths, prompts, model contents, and unrestricted logs are not returned by the recovery API.

## Actions

### Resume preparation

Keeps reusable Hugging Face cache files, removes incomplete OpenVINO output, and starts the existing conversion scheduler again. If conversion was already complete and loading failed, InferBridge retries loading instead.

### Retry failed stage

Retries loading when a complete OpenVINO model is present. Otherwise it retries conversion while preserving reusable source cache data.

### Restart from download

Removes the incomplete OpenVINO output and the safely identified Hugging Face cache directory for that exact source repository, then starts a fresh download and conversion. The WebGUI requires explicit confirmation.

### Remove incomplete files

Removes only the incomplete OpenVINO model directory. Reusable Hugging Face cache data is retained. Complete OpenVINO models, symbolic links, paths outside the configured model directory, and the model-directory root itself are never removed through recovery actions.

## Persistence

Terminal recovery records are stored atomically under:

```text
<models directory>/.inferbridge-recovery/<model-id>.json
```

Only sanitized state is stored. A successful preparation or normal model deletion clears the corresponding recovery record.

If InferBridge starts with an incomplete model directory but no saved recovery record, it infers an in-memory recovery state. That state receives a stable recovery ID for the server process so status polling cannot invalidate a pending user action. The incomplete directory is detected again after a restart if it still exists.

## API

Retrieve full sanitized recovery details:

```http
GET /v1/models/recovery/{model_id}
```

Apply an action:

```http
POST /v1/models/recovery/action
Content-Type: application/json

{
  "model": "qwen2.5-3b-instruct-int4",
  "recovery_id": "recovery-...",
  "action": "resume",
  "device": "GPU"
}
```

Supported action values:

```text
resume
retry_failed_stage
restart_download
remove_incomplete_files
```

Actions are recovery-ID scoped. A stale recovery ID returns HTTP 409 instead of acting on newer state. The routes use the existing API-key policy and reject unsafe cross-site browser requests.

Compact recovery summaries are included in model status rows. Failure details are available only through the dedicated recovery-details endpoint.

## Limitations

A resumable preparation reuses cached source files, but Optimum and Hugging Face ultimately determine which files can be reused. InferBridge restarts the conversion stage because partial OpenVINO IR output is not treated as trustworthy or appendable.

Real interrupted-download and conversion behavior must still be verified on Windows with representative Hugging Face models, including gated models and large sharded checkpoints.
