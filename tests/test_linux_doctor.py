"""Linux doctor: findings from a fake /sys, /dev and library tree, never the host."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from runtime import linux_doctor

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux doctor")
# os.geteuid does not exist on Windows, where this module is still collected.
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def _tree(root: Path, *, os_id="ubuntu", like="debian") -> Path:
    (root / "etc").mkdir(parents=True)
    (root / "etc/os-release").write_text(
        f'ID={os_id}\nID_LIKE="{like}"\nVERSION_ID="24.04"\nPRETTY_NAME="Test {os_id}"\n'
    )
    return root


def _pci(root: Path, address: str, *, device: str, pci_class: str, driver: str | None) -> None:
    entry = root / "sys/bus/pci/devices" / address
    entry.mkdir(parents=True)
    (entry / "vendor").write_text("0x8086\n")
    (entry / "device").write_text(device + "\n")
    (entry / "class").write_text(pci_class + "\n")
    if driver:
        target = root / "sys/bus/pci/drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (entry / "driver").symlink_to(target)


def _node(root: Path, relative: str, mode: int) -> Path:
    path = root / "dev" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(mode)
    return path


def _lib(root: Path, name: str) -> None:
    directory = root / "usr/lib/x86_64-linux-gnu"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).touch()


def _by_id(report: dict) -> dict[str, dict]:
    return {item["id"]: item for item in report["findings"]}


def _diagnose(root: Path, **overrides):
    values = {
        "root": root,
        "environment": {"PATH": ""},
        "openvino_devices": ["CPU"],
        "session_groups": {"users"},
        "account_groups": {"users"},
        "kernel_release": "6.8.0-45-generic",
        "machine": "x86_64",
    }
    values.update(overrides)
    return linux_doctor.diagnose(**values)


def test_cpu_only_machine_reports_no_accelerators_without_errors(tmp_path):
    report = _diagnose(_tree(tmp_path))
    findings = _by_id(report)
    assert report["errors"] == 0
    assert findings["gpu"]["status"] == "info" and findings["npu"]["status"] == "info"
    assert report["distribution"]["family"] == "debian"


def test_gpu_without_runtime_gives_exact_ubuntu_and_fedora_commands(tmp_path):
    root = _tree(tmp_path)
    _pci(root, "0000:00:02.0", device="0x7d55", pci_class="0x030000", driver="i915")
    _node(root, "dri/renderD128", 0o666)
    findings = _by_id(_diagnose(root))
    runtime = findings["gpu-runtime"]
    assert runtime["status"] == "error"
    assert any("intel-opencl-icd" in command for command in runtime["fix"]["debian"])
    assert any("intel-compute-runtime" in command for command in runtime["fix"]["fedora"])
    assert findings["gpu-access"]["status"] == "ok"
    text = linux_doctor.render(_diagnose(root))
    assert "Ubuntu/Debian:" in text and "Fedora:" not in text


def test_installed_gpu_runtime_that_openvino_does_not_list_is_a_warning(tmp_path):
    root = _tree(tmp_path)
    _pci(root, "0000:00:02.0", device="0x7d55", pci_class="0x030000", driver="xe")
    _node(root, "dri/renderD128", 0o666)
    for name in ("libOpenCL.so.1", "libze_intel_gpu.so.1", "libze_loader.so.1"):
        _lib(root, name)
    (root / "etc/OpenCL/vendors").mkdir(parents=True)
    (root / "etc/OpenCL/vendors/intel.icd").write_text("libigdrcl.so\n")
    findings = _by_id(_diagnose(root))
    assert findings["gpu-runtime"]["status"] == "ok"
    assert findings["gpu-openvino"]["status"] == "warning"
    assert "gpu-openvino" not in _by_id(_diagnose(root, openvino_devices=["CPU", "GPU.0"]))


def test_unbound_gpu_and_old_kernel_point_at_a_kernel_upgrade(tmp_path):
    root = _tree(tmp_path)
    _pci(root, "0000:00:02.0", device="0x7d55", pci_class="0x030000", driver=None)
    findings = _by_id(_diagnose(root, kernel_release="6.5.0-1-generic"))
    assert findings["gpu-kernel-driver"]["status"] == "error"
    assert any("hwe" in line for line in findings["gpu-kernel-driver"]["fix"]["debian"])
    assert findings["gpu-kernel-version"]["status"] == "warning"


@pytest.mark.skipif(IS_ROOT, reason="root bypasses device permissions")
def test_group_added_but_session_not_refreshed_says_log_out(tmp_path):
    root = _tree(tmp_path)
    _pci(root, "0000:00:02.0", device="0x7d55", pci_class="0x030000", driver="i915")
    node = _node(root, "dri/renderD128", 0o000)
    group = linux_doctor._group_name(node.stat().st_gid)
    findings = _by_id(_diagnose(root, session_groups={"users"}, account_groups={"users", group}))
    assert findings["gpu-access"]["status"] == "error"
    assert "predates" in findings["gpu-access"]["title"]
    findings = _by_id(_diagnose(root, session_groups={"users"}, account_groups={"users"}))
    assert any("usermod" in line for line in findings["gpu-access"]["fix"]["any"])


def test_npu_present_without_user_mode_driver_on_fedora(tmp_path):
    root = _tree(tmp_path, os_id="fedora", like="")
    _pci(root, "0000:00:0b.0", device="0x643e", pci_class="0x120000", driver="intel_vpu")
    _node(root, "accel/accel0", 0o666)
    report = _diagnose(root)
    findings = _by_id(report)
    assert report["distribution"]["family"] == "fedora"
    assert findings["npu-kernel-driver"]["status"] == "ok"
    assert "Lunar Lake" in findings["npu-kernel-driver"]["title"]
    assert findings["npu-runtime"]["fix"]["fedora"] == [
        "sudo dnf install intel-npu-driver oneapi-level-zero"
    ]
    _lib(root, "libze_intel_npu.so.1")
    _lib(root, "libze_loader.so.1")
    findings = _by_id(_diagnose(root, openvino_devices=["CPU", "NPU"]))
    assert findings["npu-runtime"]["status"] == "ok" and "npu-openvino" not in findings


@pytest.mark.skipif(IS_ROOT, reason="root bypasses device permissions")
def test_root_only_accel_node_suggests_the_udev_rule(tmp_path):
    root = _tree(tmp_path)
    _pci(root, "0000:00:0b.0", device="0x7d1d", pci_class="0x120000", driver="intel_vpu")
    node = _node(root, "accel/accel0", 0o600)
    if linux_doctor._group_name(node.stat().st_gid) != "root":
        pytest.skip("test files are not root-group owned here")
    fix = _by_id(_diagnose(root))["npu-access"]["fix"]["any"]
    assert any("10-intel-vpu.rules" in line for line in fix)


def test_shared_data_directory_and_missing_keyring_are_reported(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(0o755)
    findings = _by_id(_diagnose(_tree(tmp_path / "root"), data_dir=data))
    assert findings["data-permissions"]["status"] == "warning"
    assert findings["keyring"]["fix"]["debian"] == ["sudo apt install libsecret-tools"]


def test_non_x86_machine_is_an_error(tmp_path):
    assert _by_id(_diagnose(_tree(tmp_path), machine="aarch64"))["platform"]["status"] == "error"


def test_diagnose_tolerates_a_host_without_posix_accounts(monkeypatch, tmp_path):
    # Windows CI fakes sys.platform == "linux" for --check-devices; os.getgroups is absent.
    monkeypatch.delattr(os, "getgroups")
    monkeypatch.delattr(os, "getuid")
    monkeypatch.delattr(os, "confstr")
    report = linux_doctor.diagnose(root=_tree(tmp_path), openvino_devices=["CPU"])
    assert report["findings"]
