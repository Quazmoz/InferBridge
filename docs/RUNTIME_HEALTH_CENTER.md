# Runtime upgrade and model health center

InferBridge records which application, OpenVINO, and OpenVINO GenAI versions produced each tracked conversion. The runtime health center turns that provenance into a controlled maintenance workflow after an upgrade.

It is designed to answer a specific question: **what, if anything, should happen to each converted model after the local OpenVINO runtime changes?**

## Maintenance policy

| Conversion state | Default action | Why |
|---|---|---|
| `compatible` | Leave unchanged | Conversion metadata still matches the current OpenVINO runtime. |
| `legacy_untracked` | Revalidate | The model predates conversion provenance, so InferBridge load-validates it without rewriting its original files. |
| `stale_runtime` | Rebuild compiled cache, then revalidate | The OpenVINO runtime changed. The converted IR may still be valid, but compiled device artifacts should not be assumed reusable. |
| `incompatible_definition` | Reconvert | The current catalog definition no longer matches the conversion provenance. |
| `invalid_metadata` | Reconvert | The compatibility marker cannot be trusted. |
| `incomplete` | Reconvert | Required converted-model files are missing or incomplete. |
| `not_converted` | Leave unchanged | There is no converted artifact to maintain yet. |

A failed current-runtime revalidation promotes a legacy or stale-runtime model to a reconversion recommendation.

When the Hugging Face source cache is still available, the UI explicitly says **Reconvert from existing HF cache**. InferBridge reuses the normal conversion pipeline, so resumable downloads, transactional staging, recovery metadata, cancellation, and reparse-point protections remain in effect.

## Actions

### Revalidate

Revalidation builds a temporary engine through the same model manager and device-safety path used by normal loads. The engine is closed immediately after a successful load.

A successful validation is stored separately in `runtime-health.json`. InferBridge does **not** rewrite `.ovllm-conversion.json`, so the original conversion provenance remains truthful.

Validation evidence is tied to:

- the exact converted artifact fingerprint
- the current OpenVINO version
- the current OpenVINO GenAI version

An InferBridge application-only patch does not invalidate a successful validation when those inference-runtime versions are unchanged.

### Rebuild compiled cache

InferBridge currently uses one shared OpenVINO compiled-cache root. A rebuild therefore:

1. requires every model and lifecycle operation to be idle
2. clears the shared compiled cache through the existing storage manager safety path
3. load-validates the selected affected models sequentially, warming their cache under the current runtime
4. leaves unselected models to compile again on their next normal load

The UI states this consequence before the action runs. InferBridge does not pretend the current cache is model-isolated when it is not.

### Reconvert

Reconversion stays a **per-model operation**. It is intentionally not available as a batch action because it replaces converted model files and can involve large downloads or long-running quantization.

InferBridge preserves the recorded quantization profile when available, schedules conversion through the normal model lifecycle, and does not add a second conversion implementation.

### Leave unchanged

A compatible or successfully validated model needs no action.

For `legacy_untracked` and `stale_runtime` warnings, the user may also explicitly acknowledge **Leave unchanged** for the current OpenVINO runtime. That acknowledgment is scoped to the conversion fingerprint and runtime. A later OpenVINO change or converted-artifact change makes it no longer current.

## Batch operations

Only operations with existing recovery guarantees and no model-file replacement are batchable:

- **Revalidate eligible** runs sequential load validations while all models are unloaded.
- **Rebuild affected caches** clears the shared compiled cache once, then validates the selected models sequentially.
- **Reconvert** is never offered as a batch operation.

A batch can finish with individual validation failures. Failed models remain visible as needing attention instead of making successful validations disappear.

## UI

The health center is available from both:

- **Storage and cache manager → Model health**
- **Model Library Evidence → Model health**

The summary separates:

- models needing attention
- models needing revalidation
- models needing compiled-cache rebuild
- models needing reconversion

Each model retains its existing conversion-health label, source-cache state, current recommendation, blocking reason, and last local validation result.

## Safety and privacy

The maintenance workflow preserves existing InferBridge guarantees:

- all model paths remain server-side
- browser responses do not expose local filesystem paths
- write routes use the same local-browser-origin and API-key checks as storage management
- compiled-cache deletion reuses the storage manager's reparse-point and Windows lock protections
- revalidation requires all model lifecycle operations to be idle
- reconversion requires the selected model to be unloaded and idle
- native validation failures are logged locally and returned to the browser as sanitized errors
- conversion provenance is never rewritten by validation or acknowledgment

## What this does not prove

A successful local revalidation means that the converted artifact loaded successfully with the current local runtime and selected device. It is not a bundled hardware certification, benchmark result, or guarantee for another machine.

The Verified Model Library continues to keep official certification evidence and local evidence separate.