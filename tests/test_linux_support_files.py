from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_linux_support_scripts_exist_and_use_strict_bash():
    scripts = [
        ROOT / "setup.sh",
        ROOT / "start_server.sh",
        ROOT / "setup" / "install_deps.sh",
        ROOT / "setup" / "check_ubuntu_hardware.sh",
        ROOT / "setup" / "convert_model.sh",
        ROOT / "setup" / "linux" / "install_deps.sh",
        ROOT / "setup" / "linux" / "check_hardware.sh",
        ROOT / "setup" / "linux" / "convert_model.sh",
        ROOT / "setup" / "linux" / "lib.sh",
    ]

    for path in scripts:
        text = path.read_text(encoding="utf-8")
        assert text.startswith("#!/usr/bin/env bash")
        assert "set -euo pipefail" in text


def test_platform_setup_layout_is_explicit():
    setup_text = (ROOT / "setup.sh").read_text(encoding="utf-8")
    setup_readme = (ROOT / "setup" / "README.md").read_text(encoding="utf-8")
    linux_doc = (ROOT / "docs" / "LINUX.md").read_text(encoding="utf-8")
    ubuntu_doc = (ROOT / "docs" / "UBUNTU.md").read_text(encoding="utf-8")
    fedora_doc = (ROOT / "docs" / "FEDORA.md").read_text(encoding="utf-8")
    device_doc = (ROOT / "docs" / "DEVICE_SUPPORT.md").read_text(encoding="utf-8")

    expected = "Linux support is experimental and currently supports Ubuntu and Fedora."
    assert expected in setup_text  # The source-checkout developer path.
    assert expected in ubuntu_doc
    assert expected in fedora_doc
    # The packaged Linux app is supported on CPU; GPU/NPU stay honestly unverified.
    assert "has not yet been verified on physical hardware" in linux_doc
    assert "setup/windows/" in setup_readme
    assert "setup/linux/" in setup_readme
    assert "| Fedora | Supported (CI-verified) |" in device_doc


def test_linux_docs_keep_cpu_as_first_validation_target():
    linux_doc = (ROOT / "docs" / "LINUX.md").read_text(encoding="utf-8")
    ubuntu_doc = (ROOT / "docs" / "UBUNTU.md").read_text(encoding="utf-8")
    fedora_doc = (ROOT / "docs" / "FEDORA.md").read_text(encoding="utf-8")
    device_doc = (ROOT / "docs" / "DEVICE_SUPPORT.md").read_text(encoding="utf-8")

    assert "Pick **CPU** first" in linux_doc
    assert "CPU inference is the recommended first path." in ubuntu_doc
    assert "CPU inference is the recommended first path." in fedora_doc
    assert "not yet hardware-verified" in device_doc
    assert "If OpenVINO does not list a device, the app cannot use it." in device_doc


def test_fedora_setup_hints_are_present():
    lib_text = (ROOT / "setup" / "linux" / "lib.sh").read_text(encoding="utf-8")
    fedora_doc = (ROOT / "docs" / "FEDORA.md").read_text(encoding="utf-8")

    assert "sudo dnf install -y python3 python3-pip python3-devel git" in lib_text
    assert "sudo dnf install -y pciutils" in lib_text
    assert "sudo dnf install -y python3 python3-pip python3-devel git" in fedora_doc
