# Linux development qualification — 30 October 2026

Application version: `1.1.1-dev.1`; code commit:
`fea8bf35529d34336ce9797954d664524fe866cd`.

[Native distribution CI](https://github.com/Quazmoz/InferBridge/actions/runs/41072708370)
passed the same portable archive on Ubuntu 22.04, Ubuntu 24.04 and a Fedora 44
container running on native x86_64 GitHub runners. Each mock API run passed 13
checks with one warning for optional tool/structured output support and no
failures. The retained packaged reports below come from Ubuntu 22.04.

The separate real CPU probe used the bundle's private interpreter and application
conversion/inference engine on **AMD EPYC 9V45**, Ubuntu **22.04.5**, kernel
**6.8.0-1070-azure**. It converted pinned FP16 random test weights, generated,
streamed, and closed the engine without mock mode. Model:
[`trl-internal-testing/tiny-random-LlamaForCausalLM`](https://huggingface.co/trl-internal-testing/tiny-random-LlamaForCausalLM/tree/2c542e4731d42d3bb68324c4eca16df9f93b8b33),
revision `2c542e4731d42d3bb68324c4eca16df9f93b8b33`.
Only allowlisted JSON/tokenizer files and safetensors were downloaded (6,476,069
bytes); pickle weights and remote code were excluded. Model files are not retained
in this report bundle or the downloadable application.

OpenVINO: `2026.2.1`; GenAI/tokenizers: `2026.2.1.0`; Optimum Intel: `2.0.0`;
CPU PyTorch: `2.12.0+cpu`. Total probe time: 13.16 seconds. Maximum
single-process high-water RSS across the probe and child converter: 603,352 KiB;
this is not simultaneous process-tree memory or a performance benchmark.

This proves native x86_64 runtime execution on that AMD CI host. It does not
certify Intel CPU hardware, a useful chat model, the complete real-inference API,
GPU/NPU execution, GNOME/KDE, Wayland/X11, or DEB/RPM installation. The random
weights do not produce meaningful language. The production Windows certification
matrix is unchanged.

Reports exclude prompts, generated text, credentials, usernames, hostnames and
private filesystem paths. Verify the retained files from this directory:

```bash
sha256sum -c SHA256SUMS.txt
```

- [Real CPU probe](cpu-evidence.json)
- [Packaged mock API contract](packaged-contract.json)
- [Packaged lifecycle result](packaged-checks.json)
- [SHA-256 hashes](SHA256SUMS.txt)
