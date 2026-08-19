# PyInstaller one-directory build for the Windows desktop tray launcher.

import importlib.util
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

# PyInstaller's pkg_resources runtime hook imports setuptools' vendored jaraco.text
# before the application entry point runs. setuptools 81 reads package data such as
# ``setuptools/_vendor/jaraco/text/Lorem ipsum.txt`` at import time. PyInstaller can
# otherwise collect the Python modules without that data file, which turns a successful
# install into an immediate "Unhandled exception in script" crash on first launch.
# Collect setuptools package data explicitly so the frozen bootstrap is self-contained.
datas += collect_data_files("setuptools", include_py_files=False)

# Optimum 2.x discovers accelerator-specific CLI commands by walking the on-disk
# PEP 420 namespace at optimum.commands.register. PyInstaller normally places Python
# modules in its archive, where pathlib.iterdir() cannot see them. Materialize every
# installed registration module in the frozen filesystem and also mark it as a hidden
# import so `optimum-cli export openvino` is available in installed and portable builds.
optimum_register_spec = importlib.util.find_spec("optimum.commands.register")
if (
    optimum_register_spec is None
    or optimum_register_spec.submodule_search_locations is None
):
    raise RuntimeError(
        "The release environment is missing the optimum.commands.register namespace."
    )
optimum_register_files = sorted(
    {
        register_file
        for location in optimum_register_spec.submodule_search_locations
        for register_file in Path(location).glob("*.py")
        if register_file.name != "__init__.py"
    },
    key=lambda path: str(path).lower(),
)
if not optimum_register_files:
    raise RuntimeError(
        "The release environment contains no Optimum CLI registration modules."
    )
for register_file in optimum_register_files:
    datas.append((str(register_file), "optimum/commands/register"))
    hiddenimports.append(f"optimum.commands.register.{register_file.stem}")

# Collect every OpenVINO native distribution explicitly. openvino-genai loads the
# tokenizer extension by DLL name at runtime, so relying on transitive collection can
# leave openvino_tokenizers.dll without its package-owned companion binaries.
for package in ("openvino", "openvino_genai", "openvino_tokenizers"):
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
#
# These packages must ship their Python sources, not only the bytecode PyInstaller keeps
# in its archive. Modules along the conversion import chain read their own source through
# inspect.getsource while they are being imported: Transformers applies
# add_start_docstrings_to_model_forward to optimum.intel.openvino model classes, and that
# decorator calls inspect.getsource to measure the docstring indentation. linecache finds
# no lines for an archived module, so a bytecode-only bundle raises
# OSError("could not get source code") when `optimum-cli export openvino` imports
# optimum.intel.openvino - before the first model byte is downloaded, which fails every
# conversion in the installed application while the source checkout keeps working.
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
    datas += collect_data_files(package, include_py_files=True)

# torch is pulled in transitively by Optimum rather than collected above, and its config
# modules tokenize their own source in torch.utils._config_module.install_config_module
# during `import torch`. Collect those sources here so the frozen converter cannot depend
# on a third-party hook continuing to do it. Sources a hook already collected are
# deduplicated, so this only guarantees the invariant.
datas += collect_data_files("torch", include_py_files=True)

for distribution in (
    "openvino",
    "openvino-genai",
    "openvino-tokenizers",
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
    [str(root / "app" / "frozen_entrypoint.py")],
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
    name="InferBridge",
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
    name="InferBridge",
)
