# Dependency update candidate — 2026-10-30

This is a development-only upgrade; neither the installer nor Microsoft Store
package is approved for release until native testing and the Store certification
incident [#69](https://github.com/Quazmoz/InferBridge/issues/69) are resolved.

## Windows Python release candidate

The following exact versions were selected from published stable PyPI releases
and must pass the pinned release-environment gate and the Windows-native build:

| Dependency | Previous | Candidate | Scope |
|---|---|---|---|
| fastapi | 0.143.0 | 0.162.0 | API request handling and OpenAI compatibility |
| pydantic | 2.14.0 | 2.14.2 | Validation fixes |
| optimum-intel | 2.0.0 | 2.2.0 | Model export and conversion |
| optimum | 2.2.0 | 2.3.0 | Required by optimum-intel 2.2.0 (\`~=2.3.0\`) |
| nncf | 3.2.0 | 3.4.0 | Compression and quantization |
| safetensors | 0.7.0 | 0.8.0 | Model file decoding and conversion |

All are **candidate** dependencies until source tests, bundled import smoke,
and relevant real Intel CPU/GPU/NPU conversion and inference are verified.

## Intentionally retained

OpenVINO / GenAI / tokenizers remain on the hardware-qualified
`2026.2.1` family to avoid silently changing CPU/GPU/NPU behavior.
Torch `2.12.0`, Transformers `5.0.0`, and Hugging Face Hub `1.19.0`
remain pinned as a coupled conversion group; upgrading them should be
a separately validated milestone that also tests the actual frozen PyInstaller
boot path and Optimum Intel conversion API. NumPy is retained at `2.4.6`:
the newest 2.5.x requires Python >=3.12, while manual CI still qualifies
Python 3.11. Pillow 13 is a major upgrade and is deferred. Ruff and
setuptools remain pinned because they participate in source-format and
frozen-bootstrap contracts.

## Linux isolation

The verified Linux x86_64 portable artifact uses `requirements/linux.lock`
(hashes, CPU-only torch), which is unaffected by these Windows-only
candidate upgrades. `requirements/linux.in` now includes
`requirements/linux-baseline.txt`, an exact copy of the previous constraints.
A source contract test verifies the snapshot against the lock.
Never regenerate `linux.lock` against Windows `release.txt` accidentally.

## Test before Microsoft Store resubmission

1. Review Windows source qualification CI for pinned requirements,
   `pip check`, full mock `pytest`, native C# contracts, and WinUI publish.
2. Run the real Windows package builder in Azure DevOps with the
   requested MSIX artifact type; retain hashes, dependency inventory and WACK.
3. Install the resulting signed test MSIX on a clean Windows 11 device and
   run `scripts/smoke_test_msix.ps1`.
4. Check one model download, conversion, CPU load and chat; repeat GPU/NPU
   operations only where the machine actually exposes compatible devices.
5. Do not promote this branch or submit to Partner Center from a CI source
   pass alone.
