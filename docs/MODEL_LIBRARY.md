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

Every manifest redirect is checked **before making the next request**: only HTTPS URLs on the approved GitHub release hosts, with no embedded credentials or nonstandard ports, are permitted. Redirect chains are limited to five hops. The response must stay below 1 MB, use the supported schema, contain at most 50 entries, and pass the SHA-256 checksum of its canonical catalog. Release publication also includes this asset in the versioned SHA-256 checksum file. A valid copy is cached beside the writable model catalog. If it is unavailable or invalid, the bundled manifest remains the offline fallback.

Checksums protect against corruption and inconsistent publication. They are not substitutes for HTTPS, GitHub account security, release signing, or Authenticode verification.

## Conversion health

New conversions and imported OpenVINO IR directories receive a local `.ovllm-conversion.json` marker containing:

- model identifier and source
- backend and weight format
- application version
- OpenVINO and OpenVINO GenAI versions
- recording date

The library reports conversions as compatible, legacy or untracked, stale after a runtime major or minor change, definition-mismatched, incomplete, or metadata-damaged. A warning does not delete or silently reconvert a model.

If `model-library-user.json` is corrupted, catalog mutations fail closed rather than treating user-owned models as unowned. Preserve the file for recovery; restore a valid backup before retrying a refresh or model import.

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

### Qwen3 and sideloading quickstart (Windows)

InferBridge accepts **already converted OpenVINO IR directories**, not raw downloaded
Hugging Face `.safetensors` files as ready-to-load local models.

1. Obtain a model exported in OpenVINO format using a trusted converter and review its
   license. Its directory should include `openvino_model.xml`, `openvino_model.bin`,
   configuration, and the tokenizer resources needed for inference. Exporting a model
   does not establish that a particular GPU/NPU can run it.
2. Open the built-in InferBridge browser UI and select **Import local** beside the model
   picker, or open **Verified Model Library → Import local**.
3. Enter a **unique model ID**, name, absolute directory path, backend, weight format,
   and an initial device (use **CPU** unless the target has been qualified). Select
   **Import and manage copy**. InferBridge checks the directory and copies it into its
   writable managed model storage; it does not load external code from the path.
4. Select the imported model, then load and benchmark it. Check compatibility warnings
   and real OpenVINO device discovery before trying GPU or NPU.

If a valid converted model directory is **already present directly beneath the managed
models directory**, the library can detect it and offer **Review & register** instead
of copying a second model. Do not copy models into the installed application directory;
use the per-user storage path shown in [Data paths](DATA_PATHS.md).

For Qwen3, the runtime catalog includes `qwen3-4b-int4`, `qwen3-8b-int4` and
`qwen3-30b-a3b-int4` candidates. In **Show all registered**, choose one to prepare
through the normal model conversion flow; alternatively import an OpenVINO IR directory
prepared on a higher-memory machine. Qwen3 conversion and support on any particular
NPU, GPU, or driver remain **unverified** until actually tested. A 30B MoE model has
roughly 30B *total* stored parameters even if fewer experts activate per token.

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

## Additional OpenVINO model candidates (dev)

The full runtime catalog (**Show all registered**) also includes newer general, coding, and high-memory configurations. These are **conversion and device-validation candidates**, not InferBridge-certified models; they have no manufactured CPU, GPU, or NPU verification badges. The smaller curated offline library stays unchanged until local qualification produces evidence.

| Model ID | Weight precision | Initial device | Sizing guidance |
|---|---|---|---|
| `qwen2.5-coder-1.5b-int4` | INT4 | CPU | Low-footprint coding |
| `qwen2.5-coder-7b-int4` | INT4 | GPU | Coding; CPU fallback may be slower |
| `qwen3-4b-int4` | INT4 | NPU | NPU candidate, **not** NPU-certified for this PC |
| `qwen3-8b-int4` | INT4 | GPU | General reasoning/instruction following |
| `qwen3-30b-a3b-int4` | INT4 | CPU | ~30B **total** weights, ~3B active/token; conversion remains heavyweight |
| `qwen3-30b-a3b-int8` | INT8 | CPU | **Over 32 GB estimated runtime RAM** |
| `qwen2.5-coder-32b-int8` | INT8 | CPU | **Over 32 GB estimated runtime RAM**; exact variant not officially device-tested |

Existing `qwen2.5-32b-fp16` and `deepseek-r1-distill-qwen-32b-fp16` entries also exceed 32 GB in estimated runtime memory. 64 GB or more installed RAM is a reasonable *starting point* for the new INT8 giants; actual usable memory, disk, paging, export peak memory, device constraints, and context length must be measured. These are not measured minimum requirements. The hardware advisor estimates the IR footprint, **total** parameter memory (including inactive MoE experts), temporary conversion disk use, and KV cache, and warns/blocks insufficient memory before load. The original FP16/BF16 model download and quantization process can require substantially more RAM and disk than an already-converted INT4/INT8 inference session.

**Preparation:** install the repo's conversion dependencies, then convert by exact model ID with the existing tool:

```powershell
python -m pip install -r requirements-convert.txt
python -m runtime.model_converter --id qwen3-30b-a3b-int8
```

Do not attempt a large model on a 32 GB machine merely because its compressed weights appear to fit. Start on CPU for large models, run the existing hardware preflight, convert on a host with enough memory and scratch disk, and benchmark the converted IR on the real target hardware before updating verification metadata. INT4 is lower-footprint, **not** evidence of equivalent model quality; no model automatically becomes NPU-compatible just because Optimum can export it.

Compatibility references: [Intel OpenVINO 2026.2 model verification](https://docs.openvino.ai/2026/documentation/compatibility-and-support/supported-models.html), [OpenVINO GenAI export](https://openvinotoolkit.github.io/openvino.genai/docs/guides/model-preparation/convert-to-openvino/), and [Optimum Intel supported architectures](https://huggingface.co/docs/optimum-intel/en/openvino/models). The Qwen3 4B/8B and 30B-A3B INT4/INT8 architectures appear in Intel's matrix; the Qwen2.5-Coder variants are related-architecture export candidates and still require qualification. Model publisher licences must be checked at download/use time.
