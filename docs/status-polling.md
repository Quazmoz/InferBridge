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
telemetry, build hardware-advisor snapshots, or return the activity log. Advisor
recommendations are intentionally omitted from this high-frequency endpoint.

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
- Hardware-advisor model evaluations
- Aggregate request metrics
- Cache metadata

Hardware telemetry and advisor evaluations are coalesced and cached for 5 seconds per
server process. Concurrent requests share the same refresh. Request counters and token
metrics are rebuilt from live in-memory state for every response, so the hardware cache
does not delay usage updates.

A successful response includes:

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

Set `refresh=true` to request a hardware refresh. If a refresh fails after a previous
successful sample, InferBridge returns the prior hardware sample with `stale: true`
instead of failing the entire status surface. Live request metrics are still refreshed.
If no prior sample exists, the endpoint returns HTTP 503.

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
the cached hardware and advisor telemetry, live request metrics, and the current event
list into the historical response shape. Cached advisor evaluations are merged back
into the corresponding model rows.

Existing API clients therefore continue to work, but new clients should use the split
endpoints to avoid unnecessary GPU property queries, advisor collection, and directory
scans.

## WebGUI polling behavior

The bundled WebGUI keeps its existing status consumers compatible through one browser
composition layer:

- Active model lifecycle cache: 800 milliseconds
- Idle model lifecycle cache: 3 seconds
- Telemetry and advisor cache: 5 seconds
- Event polling cache: 10 seconds

Overlapping requests are coalesced. Caches are partitioned by Authorization header so
one API-key context cannot reuse another context's response. Model lifecycle cache is
invalidated immediately after load, conversion, download, cancellation, unload, delete,
or model-library mutations.

A failed lifecycle poll is never hidden behind stale model state. The WebGUI instead
falls back to `/v1/system/status`, allowing the existing connection and retry handling
to surface the failure. Cached telemetry or events may still be reused when only those
slower data sources are temporarily unavailable.

### Progress percentage semantics

The WebGUI does not manufacture an overall operation percentage by assigning fixed
percentage ranges to download, conversion, finalization, or runtime loading phases.

- `progress.percent` is displayed as progress for the current phase only.
- Valid `completed` and `total` counts take precedence and drive the phase progress bar.
- Human-readable `log_tail` text is never parsed to invent a percentage.
- A phase without a server-provided measurement remains explicitly indeterminate.
- A future server-provided `overall_percent` may be displayed as overall progress, but
  the client does not derive that value itself.

This prevents a conversion tool reporting `40%` from being presented as an unsupported
whole-operation percentage such as `72%`.

## Active operation queue

The primary progress dock remains focused on one stable operation. When two or more
operations are active, the expanded dock shows a button such as:

```text
3 operations active
```

Opening it lists every queued, downloading, converting, finalizing, or loading model.
Selecting a row changes the primary operation deliberately. Background updates do not
steal focus, and the queue preserves its expanded state and contents when the progress
detail area is rebuilt.

The queue is keyboard accessible, responsive on narrow browser windows, and renders all
server-provided labels through text nodes.
