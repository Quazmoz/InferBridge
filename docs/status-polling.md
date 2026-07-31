# Split status polling

InferBridge separates fast-changing model lifecycle state from comparatively expensive
system telemetry and the bounded activity stream.

## Endpoints

### `GET /v1/models/status`

Use this endpoint for model loading and conversion progress.

It returns:

- Loaded model IDs and count
- Active preparation count
- Per-model lifecycle status
- Structured operation IDs and revisions
- Cancellation capabilities
- Loaded device assignments

It does not query GPU driver properties, scan the model directory, collect CPU or memory
telemetry, or return the activity log.

Suggested cadence:

- About once per second while an operation is active
- Every 3 seconds or slower while idle

Responses use `Cache-Control: no-store` because lifecycle state is transient.

### `GET /v1/system/telemetry`

Use this endpoint for system and operational telemetry.

It returns:

- CPU and memory usage
- GPU identity and memory information when available
- Available OpenVINO devices and suggestions
- Model directory and volume disk usage
- Aggregate request metrics
- Cache metadata

Telemetry is coalesced and cached for 5 seconds per server process. Concurrent requests
share the same refresh. A successful response includes:

```json
{
  "cache": {
    "hit": true,
    "stale": false,
    "ttl_seconds": 5.0,
    "age_seconds": 1.274
  }
}
```

Set `refresh=true` to request a refresh. If a refresh fails after a previous successful
sample, InferBridge returns the prior sample with `stale: true` instead of failing the
entire status surface. If no prior sample exists, the endpoint returns HTTP 503.

### `GET /v1/events`

Use this endpoint to retrieve activity events incrementally.

Query parameters:

- `cursor`: last processed event ID, default `0`
- `limit`: maximum events returned, from `1` to `100`, default `50`

Example response:

```json
{
  "object": "list",
  "data": [
    {
      "id": 41,
      "timestamp": 1785511200,
      "level": "info",
      "message": "Loaded Model One on GPU"
    }
  ],
  "cursor": 40,
  "next_cursor": 41,
  "latest_cursor": 41,
  "has_more": false,
  "reset_required": false
}
```

Event IDs are assigned atomically and remain monotonic across concurrent request and
worker activity. The in-memory event buffer remains bounded. When a cursor predates the
oldest retained event, or belongs to a previous server process, `reset_required` is
true and the response contains the currently retained window.

Suggested cadence is every 10 seconds, or after an operation-changing action.

## Compatibility endpoint

`GET /v1/system/status` remains supported. It composes the lightweight model snapshot,
the cached telemetry sample, and the current event list into the historical response
shape.

Existing API clients therefore continue to work, but new clients should use the split
endpoints to avoid unnecessary GPU property queries and directory scans.

## WebGUI polling behavior

The bundled WebGUI keeps its existing status consumers compatible through one browser
composition layer:

- Active model lifecycle cache: 800 milliseconds
- Idle model lifecycle cache: 3 seconds
- Telemetry cache: 5 seconds
- Event polling cache: 10 seconds

Overlapping requests are coalesced. Caches are partitioned by Authorization header so
one API-key context cannot reuse another context's response. Model lifecycle cache is
invalidated immediately after load, conversion, download, cancellation, unload, delete,
or model-library mutations.

If a split endpoint is unavailable, the WebGUI falls back to `/v1/system/status`.

## Active operation queue

The primary progress dock remains focused on one stable operation. When two or more
operations are active, the expanded dock shows a button such as:

```text
3 operations active
```

Opening it lists every queued, downloading, converting, finalizing, or loading model.
Selecting a row changes the primary operation deliberately. Background updates do not
steal focus, and the queue preserves its expanded state during progress refreshes.

The queue is keyboard accessible, responsive on narrow browser windows, and renders all
server-provided labels through text nodes.
