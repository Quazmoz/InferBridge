# PyInstaller one-directory build for the Windows desktop tray launcher.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
version_info = Path(os.environ.get("OV_LLM_VERSION_INFO", ""))
build_info = Path(os.environ.get("OV_LLM_BUILD_INFO", ""))
app_icon = Path(os.environ.get("OV_LLM_APP_ICON", ""))
if not version_info.is_file():
    raise RuntimeError("OV_LLM_VERSION_INFO must point to generated version metadata")
if not build_info.is_file():
    raise RuntimeError("OV_LLM_BUILD_INFO must point to generated build metadata")
if not app_icon.is_file():
    raise RuntimeError("OV_LLM_APP_ICON must point to a generated Windows ICO file")

datas = [
    (str(root / "web"), "web"),
    (str(root / "models.json"), "."),
    (str(root / "model_library_manifest.json"), "."),
    (str(root / "LICENSE"), "."),
    (str(root / "README.md"), "."),
    (str(build_info), "."),
]
third_party = Path(os.environ.get("OV_LLM_THIRD_PARTY_NOTICES", ""))
if third_party.is_file():
    datas.append((str(third_party), "."))

binaries = []
hiddenimports = collect_submodules("app") + collect_submodules("runtime")

for package in ("openvino", "openvino_genai"):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# psutil has a version-coupled Python wrapper and native Windows extension. Collect both
# from the same isolated release environment so a fresh package cannot combine mismatched
# files. The installer separately removes the old _internal directory during upgrades.
psutil_datas, psutil_binaries, psutil_hidden = collect_all("psutil")
datas += psutil_datas
binaries += psutil_binaries
hiddenimports += psutil_hidden

# pystray selects its Windows backend dynamically at runtime.
hiddenimports += collect_submodules("pystray")
datas += collect_data_files("pystray", include_py_files=False)

# Optimum performs dynamic command and exporter discovery. Conversion remains in the
# same frozen directory, so the packaged launcher can dispatch the converter helper.
for package in (
    "optimum",
    "optimum.intel",
    "nncf",
    "transformers",
    "huggingface_hub",
    "tokenizers",
    "safetensors",
    "sentencepiece",
):
    hiddenimports += collect_submodules(package)
    datas += collect_data_files(package, include_py_files=False)

for distribution in (
    "openvino",
    "openvino-genai",
    "optimum",
    "optimum-intel",
    "nncf",
    "transformers",
    "huggingface-hub",
    "psutil",
    "pystray",
):
    try:
        datas += copy_metadata(distribution, recursive=True)
    except Exception:
        pass

analysis = Analysis(
    [str(root / "app" / "desktop_launcher.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(root / "packaging" / "runtime_hook.py")],
    excludes=["tkinter", "matplotlib", "notebook", "jupyter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="OpenVINOWindowsLLM",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(version_info),
    icon=str(app_icon),
)
collection = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="OpenVINOWindowsLLM",
)
