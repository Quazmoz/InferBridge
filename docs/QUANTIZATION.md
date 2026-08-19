# Quantization policy

InferBridge treats quantization as a hardware-aware preparation choice, not as a universal promise that lower precision is lossless or faster.

## Default policy

| Workload | Profile / device | Preferred format | Rationale |
| --- | --- | --- | --- |
| Text generation | Intel NPU | INT4 | Uses InferBridge's NPU-safe symmetric INT4 conversion profile: group size 128, ratio 1.0, symmetric weights. |
| Text generation | CPU/GPU, Balanced | INT8 | Conservative compressed middle ground when fidelity and footprint both matter. |
| Text generation | CPU/GPU, Fastest / Lowest memory / Lowest power | INT4 | Minimizes weight footprint. Actual throughput must be benchmarked on the target PC. |
| Text generation | Best quality | FP16 | Fidelity-first compatibility fallback. |
| Embeddings / VLM | Any | FP16 | Keep FP16 until model-specific quality and compatibility evidence supports compression. |

The policy is a ranking and preparation prior. It does not create a certification claim. Existing hardware certifications remain valid only for the exact model, precision, device, runtime, driver, and test conditions recorded in the certification report.

## INT4 NPU safety contract

InferBridge already records an NPU compatibility marker for INT4 conversions. New NPU-oriented INT4 conversions use:

- symmetric quantization
- ratio `1.0`
- group size `128`

A legacy or externally converted INT4 artifact without a verified compatible marker is not allowed to reach the direct NPU compiler. AUTO/composite routing removes NPU when the artifact is not verified and falls back to a safe CPU/GPU target when possible.

## Precision identity

Keep precision variants as distinct catalog identities and model directories. For example:

- `qwen2.5-1.5b-int4`
- `qwen2.5-1.5b-int8`
- `qwen2.5-1.5b-fp16`

Do not describe an artifact as FP16 after replacing its files with INT4 or INT8. Separate identities make storage, recovery, benchmark evidence, and future certification unambiguous.

## Compare formats before promoting a default

Convert the variants first, then run the local comparison tool on the same device and driver:

```powershell
python scripts/compare_quantization.py `
  qwen2.5-1.5b-int4 `
  qwen2.5-1.5b-int8 `
  qwen2.5-1.5b-fp16 `
  --device GPU `
  --reference qwen2.5-1.5b-fp16 `
  --json benchmark-results/qwen-1.5b-quantization-gpu.json
```

For NPU, compare INT4 against the FP16 fallback and keep INT8 out of the default NPU path unless it is explicitly validated on that platform.

The comparison report records converted size, load time, deterministic smoke-task results, generation throughput, and response similarity to the selected reference. The smoke suite is intentionally small. It detects obvious regressions but is not a substitute for model-specific evaluation or the InferBridge hardware certification harness.

## Promotion rule

Promote a compressed variant to a curated default only after:

1. conversion completes transactionally with the expected precision and compatibility marker;
2. load and generation succeed on the intended device;
3. the smoke-quality comparison does not materially regress against the FP16 reference;
4. the measured footprint or performance benefit is meaningful on real hardware;
5. the existing API, streaming, tool-call, model lifecycle, and web UI validation suites still pass;
6. the evidence is retained if the UI or documentation will make a verified compatibility claim.

Until then, label the variant as a candidate and preserve FP16 as the compatibility fallback.
