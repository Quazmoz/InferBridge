# Local application audit — 2026-09-18

Candidate: InferBridge 0.11.0-beta.1. Windows x64, Python 3.12.10, OpenVINO GenAI 2026.2.1.0. Real-device checks use the existing TinyLlama FP16 model and BGE small embeddings.

## Major issues reproduced and fixed

1. After disconnecting an NPU stream, recovery closes and rebuilds the native engine. The following HTTP request previously rendered its prompt against the closed engine before acquiring the generation lock, producing HTTP 500. Prompt workers now acquire the existing current-engine lease, wait for recovery, and select the replacement. Chat, Responses, tool retries, and context-budget inspection use this safeguard. Cancellation retains the lease until the tokenizer worker exits. A concurrent unload returns HTTP 409.
2. GenAI's default generation configuration reapplied the chat template to already rendered prompts. The exact 1,536-token NPU context check became 1,551 tokens inside the native pipeline and failed. The shared generation configuration now disables this second formatting pass. The regression exercises both synchronous generation and streaming.

Also corrected the Benchmark Lab browser test's assumption that every machine exposes CPU alone, removed live Hugging Face access from the embedding-registration regression, and made the Windows certification report tolerate device rows without context results.

## Verification

| Check | Result |
|---|---|
| Complete local unit suite | 1,332 passed, 20 skipped |
| Chromium browser suite | 39 passed |
| Ruff lint and formatting | Passed |
| Injected browser JavaScript | All 37 blocks parse |
| Python dependency consistency | Passed |
| Curated model library manifest | Passed |
| External mock HTTP contract | 12 passed, 1 expected optional-output warning, 1 authentication skip, 0 failed |
| Real CPU HTTP contract with authentication | 14 passed, 0 failed; 384-dimensional embeddings included |
| Real GPU HTTP contract with authentication | 13 passed, 1 embedding skip, 0 failed |
| Real NPU HTTP contract with authentication | 13 passed, 1 embedding skip, 0 failed |
| CPU, GPU, and NPU prompt capacity | Exact 1,536-token generation and rejection beyond the configured prompt limit passed |

Real HTTP contracts exercise model discovery/load, OpenAI chat, SSE streaming, interrupted-stream recovery, tools and strict JSON, metrics, Responses streaming/non-streaming, benchmarking, and unload/reload.

Local evidence is retained under `.tmp/audit-final-hardware2/`, `.tmp/audit-mock-contract/`, and the audit pytest cache directories. These are local outputs and are not shipped with the app.

## Limits

Qualification applies to this machine and these model artifacts. It does not certify every model, precision, Intel device, or driver. The 20 skipped tests are not claimed as passes. Conversion of new model downloads, clean-machine installer upgrade/downgrade/uninstall, signing, and SmartScreen trust are not established by these source and hardware checks. The release remains unsigned.
