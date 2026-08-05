import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _hook_namespace():
    return runpy.run_path(str(ROOT / "packaging" / "runtime_hook.py"))


def test_pyinstaller_is_windowed_one_directory_and_collects_openvino():
    spec = (ROOT / "packaging" / "openvino_windows_llm.spec").read_text(encoding="utf-8")
    assert "console=False" in spec
    assert "COLLECT(" in spec
    assert 'collect_all("openvino")' not in spec
    assert '("openvino", "openvino_genai", "openvino_tokenizers")' in spec
    assert "models.json" in spec
    assert "web" in spec
    assert "runtime_hook.py" in spec
    assert "OV_LLM_APP_ICON" in spec
    assert "icon=str(app_icon)" in spec
    assert 'find_spec("optimum.commands.register")' in spec
    assert '"optimum/commands/register"' in spec
    assert 'hiddenimports.append(f"optimum.commands.register.{register_file.stem}")' in spec


def test_conversion_packages_ship_python_sources_not_only_bytecode():
    """Frozen conversion needs .py files on disk, not just PyInstaller's archive.

    Importing ``optimum.intel.openvino`` runs Transformers decorators that call
    ``inspect.getsource`` on the module being imported, and ``import torch`` tokenizes its
    own config modules. With bytecode alone, linecache finds no lines and every packaged
    conversion failed with OSError("could not get source code") before downloading
    anything, while the same conversion worked from a source checkout.
    """

    spec = (ROOT / "packaging" / "openvino_windows_llm.spec").read_text(encoding="utf-8")
    assert "datas += collect_data_files(package, include_py_files=True)" in spec
    assert 'datas += collect_data_files("torch", include_py_files=True)' in spec
    assert "inspect.getsource" in spec
    for package in ('"optimum"', '"optimum.intel"', '"nncf"', '"transformers"'):
        assert package in spec
    # pystray is a tray-only dependency and is deliberately not source-collected.
    assert 'collect_data_files("pystray", include_py_files=False)' in spec


def test_native_smoke_validation_allows_for_the_conversion_import_chain():
    scan = (ROOT / "scripts" / "release_scan.py").read_text(encoding="utf-8")
    assert "timeout=300" in scan


def test_packaged_smoke_requires_openvino_cli_registration_module():
    smoke = (ROOT / "scripts" / "smoke_test_packaged.ps1").read_text(encoding="utf-8")
    assert '"optimum\\commands\\register"' in smoke
    assert '"register_openvino.py"' in smoke
    assert "missing Optimum's OpenVINO CLI registration module" in smoke


def test_windowed_runtime_hook_restores_redirected_child_streams():
    hook = (ROOT / "packaging" / "runtime_hook.py").read_text(encoding="utf-8")
    assert "os.dup(descriptor)" in hook
    assert '_restore_output("stdout", 1)' in hook
    assert '_restore_output("stderr", 2)' in hook


def test_windowed_runtime_hook_never_leaves_a_none_standard_stream(monkeypatch):
    """A windowed launcher has no descriptor to duplicate; None would abort startup.

    Packaged third-party code writes to and flushes ``sys.stdout``/``sys.stderr``
    unconditionally. Leaving either as None turned the packaged Optimum validation into
    an ``AttributeError`` and a runtime-failure exit.
    """

    restore_output = _hook_namespace()["_restore_output"]

    def refuse_dup(_descriptor):
        raise OSError("no console descriptor is available")

    monkeypatch.setattr(os, "dup", refuse_dup)
    for name, descriptor in (("stdout", 1), ("stderr", 2)):
        monkeypatch.setattr(sys, name, None)
        restore_output(name, descriptor)
        stream = getattr(sys, name)
        assert stream is not None
        stream.write("packaged output must not raise\n")
        stream.flush()
        stream.close()


def test_windowed_runtime_hook_keeps_a_usable_descriptor(monkeypatch):
    restore_output = _hook_namespace()["_restore_output"]
    monkeypatch.setattr(sys, "stdout", None)
    restore_output("stdout", 1)
    assert sys.stdout is not None
    sys.stdout.flush()


def test_installer_is_per_user_and_preserves_data_by_default():
    script = (ROOT / "packaging" / "installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in script
    assert "{localappdata}\\Programs\\InferBridge" in script
    assert "Create a desktop shortcut" in script
    assert "IDYES" in script
    assert "DelTree" in script
    assert "SetupIconFile={#AppIconPath}" in script


def test_build_script_generates_checksums_unsigned_names_and_brand_assets():
    # The canonical release entrypoint is build_release.ps1; the legacy wrapper delegates to it.
    release = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
    assert "release_tools.py checksums" in release
    assert "release_tools.py verify-checksums" in release
    assert "unsigned artifacts" in release
    assert "OV_LLM_SIGN_CERT_SHA1" in release
    assert '"/tr", $Timestamp, "/td", "SHA256"' in release
    assert '"signtool", "verify", "/pa", "/all"' not in release
    assert "& $SignTool verify /pa /all $Path" in release
    assert "Configure either OV_LLM_SIGN_CERT_SHA1" in release
    assert "PFX signing requires OV_LLM_SIGN_CERTIFICATE_PASSWORD" in release
    assert "Signed releases require both" in release
    assert "generate_brand_assets.py" in release
    assert "OV_LLM_APP_ICON" in release
    assert '"/DAppIconPath=$AppIcon"' in release

    publisher = (ROOT / "scripts" / "publish_release.ps1").read_text(encoding="utf-8")
    assert "verify_release_signing.py" in publisher
    assert (
        'if ($Channel -eq "stable" -and -not $AllowUnsigned) { $SigningGate += "--require-signed" }'
    ) in publisher

    wrapper = (ROOT / "scripts" / "build_windows_distribution.ps1").read_text(encoding="utf-8")
    assert "build_release.ps1" in wrapper
    assert "Unsigned = $true" in wrapper
    assert "GenerateChecksums = $true" in wrapper

    scan = (ROOT / "scripts" / "release_scan.py").read_text(encoding="utf-8")
    assert "hashlib.sha256" in scan
    assert "{sha256_file(path)}  {path.name}" in scan
