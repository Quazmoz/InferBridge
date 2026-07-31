# Model preparation cancellation

InferBridge exposes operation-scoped cancellation for model preparation:

```http
POST /v1/models/cancel
Content-Type: application/json

{
  "model": "tinyllama-1.1b-chat-fp16",
  "operation_id": "convert-0123456789abcdef0123456789abcdef"
}
```

The `operation_id` must be copied from the current model entry returned by
`GET /v1/system/status`. InferBridge rejects an older operation ID so a delayed browser
control or API retry cannot cancel a newer conversion or load attempt.

## Cancellable work

- A queued conversion can be cancelled.
- A running converter subprocess can be cancelled. InferBridge terminates the converter
  process tree and records a terminal `cancelled` state.
- A queued model load can be cancelled before native compilation starts.
- A load that is performing an automatic download or conversion can be cancelled before
  it enters native OpenVINO compilation.

## Native load limitation

OpenVINO model compilation executes in a worker thread. Python task cancellation cannot
reliably stop that native call once it has started. InferBridge therefore returns HTTP
`409` rather than claiming that the load was cancelled. Let the load finish and then use
`POST /v1/models/unload` if the model is no longer needed.

## Status fields

Active model entries may include:

```json
{
  "can_cancel": true,
  "cancel_mode": "conversion",
  "cancel_reason": null,
  "progress": {
    "operation_id": "convert-0123456789abcdef0123456789abcdef",
    "revision": 12
  }
}
```

`cancel_mode` is either `conversion`, `preparation`, or `null`. When cancellation is not
safe, `cancel_reason` contains user-facing guidance.

## Responses

Successful cancellation returns HTTP `200`, the exact operation ID, the final model
entry, and `already_cancelled`. Repeating the same request after cancellation is
idempotent and also returns HTTP `200`.

Conflicts return HTTP `409` with a structured `detail` object:

```json
{
  "detail": {
    "code": "stale_operation",
    "message": "The requested operation is no longer current. Refresh model status before retrying.",
    "current_operation_id": "convert-fedcba9876543210fedcba9876543210"
  }
}
```

Possible codes include:

- `no_active_operation`
- `stale_operation`
- `native_load_in_progress`
- `not_cancellable`
- `task_finished`
- `operation_replaced`
- `cancellation_incomplete`

The route uses the same API-key policy as other protected endpoints and rejects
cross-site browser mutations.
