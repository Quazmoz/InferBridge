# Desktop onboarding API

The packaged desktop server extends the existing FastAPI application. It does not duplicate model recommendation, conversion, load, or benchmark logic in JavaScript.

All request and response bodies use Pydantic models from `app/onboarding_models`.

## Read routes

| Route | Purpose |
|---|---|
| `GET /v1/onboarding/status` | Versioned completion state and optional rescan recommendation |
| `GET /v1/onboarding/system-scan?refresh=true` | Existing hardware-advisor snapshot presented with ready, warning, unavailable, or unknown statuses |
| `GET /v1/onboarding/npu-readiness?refresh=true` | Explicit NPU readiness classification and safe fallback |
| `GET /v1/onboarding/recommendation?refresh=true` | Conservative compatible starting model and estimates |
| `GET /v1/onboarding/preparation/{job_id}` | Current stage, determinate or indeterminate progress, sanitized details, and benchmark result |
| `GET /v1/onboarding/connection` | Actual-port connection configuration after successful setup |

## State-changing routes

| Route | Purpose |
|---|---|
| `POST /v1/onboarding/prepare` | Validate model, device, path, confirmations, and start one serialized preparation job |
| `POST /v1/onboarding/preparation/{job_id}/cancel` | Request cancellation only during safe stages |
| `POST /v1/onboarding/complete` | Return connection details for a successfully verified job |
| `POST /v1/onboarding/restart` | Restart setup without deleting models or benchmarks |

When `OV_LLM_API_KEY` is configured, **all `/v1/onboarding/*` routes, including reads, require the same bearer-key policy as the rest of `/v1`**. The packaged loopback browser does not expose the API key to JavaScript: `DesktopBrowserAuthBridgeMiddleware` injects it server-side for trusted local UI requests after the browser receives its HttpOnly loopback session cookie.

State-changing browser requests additionally require InferBridge's safe-origin checks. Normal non-browser clients authenticate with `Authorization: Bearer <key>`.

## Desktop process routes

`GET /desktop/instance` is loopback-only and returns the local instance nonce and selected port for launcher identity verification. It is intentionally outside the `/v1` API-key contract.

The tray-owned control plane lives under `/desktop/control/*`. `POST /desktop/control/shutdown`, `GET /desktop/control/status`, and the other control endpoints require the exact high-entropy tray control token in `X-Desktop-Control` and reject non-loopback clients. The control token is passed privately from the tray to its child server and is separate from the user-configured API key.

Browser-accessible desktop operations live under `/v1/desktop/operations/*` and follow the normal `/v1` bearer-key policy when authentication is configured.

The controller never terminates a server process unless the child PID matches the metadata for the process it owns. If graceful shutdown does not complete within the bounded timeout, fallback termination is applied only to that owned child.

## Preparation contract

Normal stage order:

```text
preparing
downloading
converting
validating
compiling
loading
benchmarking
ready
```

The response includes whether progress is determinate. A percentage is omitted when the backend cannot measure it reliably. User-visible logs are bounded and sanitized.

Onboarding completion is persisted only after successful measured or explicitly marked mock generation. The benchmark reports requested and actual devices separately.
