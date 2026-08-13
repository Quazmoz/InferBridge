import os
import threading

from app import telemetry


def test_dir_size_bytes_sums_nested_files(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 1000)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 2000)
    assert telemetry.dir_size_bytes(tmp_path) == 3000


def test_dir_size_bytes_missing_path_is_zero(tmp_path):
    assert telemetry.dir_size_bytes(tmp_path / "nope") == 0


def test_cached_dir_size_reuses_recent_scan(monkeypatch, tmp_path):
    telemetry.clear_dir_size_cache()
    (tmp_path / "model.bin").write_bytes(b"z" * 1024)
    original = telemetry.dir_size_bytes
    calls = 0

    def counted(path):
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(telemetry, "dir_size_bytes", counted)
    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=60) == 1024
    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=60) == 1024
    assert calls == 1


def test_clear_dir_size_cache_forces_refresh(tmp_path):
    telemetry.clear_dir_size_cache()
    model = tmp_path / "model.bin"
    model.write_bytes(b"z" * 1024)
    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=60) == 1024

    model.write_bytes(b"z" * 2048)
    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=60) == 1024
    telemetry.clear_dir_size_cache(tmp_path)
    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=60) == 2048


def test_dir_size_cache_is_bounded(tmp_path):
    telemetry.clear_dir_size_cache()
    for index in range(telemetry._DIR_SIZE_CACHE_MAX_ENTRIES + 3):
        path = tmp_path / str(index)
        path.mkdir()
        telemetry.cached_dir_size_bytes(path, cache_seconds=60)
    assert len(telemetry._dir_size_cache) == telemetry._DIR_SIZE_CACHE_MAX_ENTRIES


def test_disk_stats_reports_footprint_and_real_volume(tmp_path):
    telemetry.clear_dir_size_cache(tmp_path)
    (tmp_path / "model.bin").write_bytes(b"z" * (1024 * 1024))  # 1 MiB
    stats = telemetry.disk_stats(tmp_path, cache_seconds=0)
    assert set(stats) == {"models_gb", "total_gb", "free_gb"}
    assert stats["total_gb"] > 0  # real volume size, not just the footprint
    assert stats["free_gb"] >= 0
    assert stats["models_gb"] >= 0


def test_disk_stats_nonexistent_dir_resolves_to_existing_ancestor(tmp_path):
    stats = telemetry.disk_stats(tmp_path / "does" / "not" / "exist", cache_seconds=0)
    assert stats["models_gb"] == 0.0
    assert stats["total_gb"] > 0  # walked up to a real volume


def test_memory_and_cpu_stats_return_dicts():
    assert isinstance(telemetry.memory_stats(), dict)
    assert "percent" in telemetry.cpu_stats()


def test_dir_size_bytes_skips_files_that_disappear_mid_walk(tmp_path, monkeypatch):
    """A conversion writing into the tree can unlink a file between listing and stat."""

    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    (tmp_path / "b.bin").write_bytes(b"y" * 50)

    class _Vanishing(type(tmp_path)):
        def stat(self, *args, **kwargs):
            if self.name == "a.bin":
                raise FileNotFoundError(self)
            return super().stat(*args, **kwargs)

    monkeypatch.setattr(telemetry, "Path", _Vanishing)

    # The vanished file is skipped rather than aborting the whole measurement.
    assert telemetry.dir_size_bytes(tmp_path) == 50


def test_dir_size_bytes_returns_zero_for_an_unreadable_tree(tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(os, "walk", refuse)

    assert telemetry.dir_size_bytes(tmp_path) == 0


def test_a_non_positive_cache_window_always_rescans(tmp_path):
    telemetry.clear_dir_size_cache()
    model = tmp_path / "model.bin"
    model.write_bytes(b"z" * 512)

    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=0) == 512
    model.write_bytes(b"z" * 1024)
    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=0) == 1024
    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=-5) == 1024
    # A bypassed read must not populate the cache for later cached callers.
    assert telemetry._dir_size_cache_key(tmp_path) not in telemetry._dir_size_cache


def test_the_cache_key_is_normalized_across_equivalent_paths(tmp_path):
    telemetry.clear_dir_size_cache()
    (tmp_path / "model.bin").write_bytes(b"z" * 256)

    assert telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=60) == 256
    # A relative-looking spelling of the same directory must hit the same entry.
    assert telemetry.cached_dir_size_bytes(str(tmp_path) + "/.", cache_seconds=60) == 256
    assert len(telemetry._dir_size_cache) == 1


def test_concurrent_status_polls_share_one_directory_scan(tmp_path, monkeypatch):
    """The status panel polls often; simultaneous polls must not duplicate the walk."""

    telemetry.clear_dir_size_cache()
    (tmp_path / "model.bin").write_bytes(b"z" * 2048)
    scans = []

    def slow_walk(path):
        scans.append(path)
        return 2048

    monkeypatch.setattr(telemetry, "dir_size_bytes", slow_walk)
    results = []

    def poll():
        results.append(telemetry.cached_dir_size_bytes(tmp_path, cache_seconds=60))

    threads = [threading.Thread(target=poll) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [2048] * 8
    assert len(scans) == 1


def test_evicting_the_cache_drops_the_least_recently_used_entry(tmp_path):
    telemetry.clear_dir_size_cache()
    directories = []
    for index in range(telemetry._DIR_SIZE_CACHE_MAX_ENTRIES):
        path = tmp_path / str(index)
        path.mkdir()
        directories.append(path)
        telemetry.cached_dir_size_bytes(path, cache_seconds=60)

    # Touch the oldest entry so the next insert evicts the second-oldest instead.
    telemetry.cached_dir_size_bytes(directories[0], cache_seconds=60)
    overflow = tmp_path / "overflow"
    overflow.mkdir()
    telemetry.cached_dir_size_bytes(overflow, cache_seconds=60)

    keys = set(telemetry._dir_size_cache)
    assert telemetry._dir_size_cache_key(directories[0]) in keys
    assert telemetry._dir_size_cache_key(directories[1]) not in keys
    assert telemetry._dir_size_cache_key(overflow) in keys


def test_dir_size_gb_reports_gigabytes_to_two_decimals(tmp_path, monkeypatch):
    monkeypatch.setattr(telemetry, "dir_size_bytes", lambda _path: 1_610_612_736)  # 1.5 GiB

    assert telemetry.dir_size_gb(tmp_path) == 1.5


def test_dir_size_gb_bypasses_the_cache_by_default(tmp_path):
    telemetry.clear_dir_size_cache()
    model = tmp_path / "model.bin"
    model.write_bytes(b"z" * 1024)

    assert telemetry.dir_size_gb(tmp_path) == 0.0  # under the rounding floor
    assert telemetry._dir_size_cache_key(tmp_path) not in telemetry._dir_size_cache


def test_disk_stats_survives_an_unreadable_volume(tmp_path, monkeypatch):
    def refuse(_path):
        raise OSError("volume unavailable")

    monkeypatch.setattr(telemetry.shutil, "disk_usage", refuse)
    stats = telemetry.disk_stats(tmp_path, cache_seconds=0)

    assert stats["total_gb"] == 0.0
    assert stats["free_gb"] == 0.0


def test_gpu_stats_is_absent_without_an_openvino_runtime(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)

    assert telemetry.gpu_stats() is None


def test_gpu_stats_is_absent_when_no_gpu_device_is_present(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.device_check",
        _fake_device_check(devices=["CPU", "NPU"], core=object()),
    )

    assert telemetry.gpu_stats() is None


def test_gpu_stats_summarizes_memory_statistics(monkeypatch):
    class _Core:
        def get_property(self, _device, name):
            if name == "GPU_DEVICE_TOTAL_MEM_SIZE":
                return 8 * 1024**3
            if name == "FULL_DEVICE_NAME":
                return "Intel Arc"
            if name == "GPU_MEMORY_STATISTICS":
                return {"usm_device_used": 2 * 1024**3, "label": "n/a"}
            raise KeyError(name)

    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.device_check",
        _fake_device_check(devices=["CPU", "GPU.0"], core=_Core()),
    )

    stats = telemetry.gpu_stats()

    assert stats["device"] == "GPU.0"
    assert stats["full_name"] == "Intel Arc"
    assert stats["total_gb"] == 8.0
    assert stats["used_gb"] == 2.0
    assert stats["statistics"]["usm_device_used_gb"] == 2.0
    assert stats["statistics"]["label"] == "n/a"  # non-integer values pass through


def test_gpu_stats_returns_none_rather_than_raising(monkeypatch):
    def explode():
        raise RuntimeError("driver crashed")

    monkeypatch.setattr("importlib.util.find_spec", lambda _name: object())
    monkeypatch.setitem(
        __import__("sys").modules,
        "runtime.device_check",
        _fake_device_check(devices=explode, core=object()),
    )

    assert telemetry.gpu_stats() is None


def test_first_stat_gb_prefers_the_earlier_key_and_ignores_non_integers():
    stats = {"free_bytes": "n/a", "available_bytes": 4 * 1024**3, "used_bytes": 1024**3}

    assert telemetry._first_stat_gb(stats, ("free", "available")) == 4.0
    assert telemetry._first_stat_gb(stats, ("missing",)) is None


def _fake_device_check(*, devices, core):
    import types

    module = types.ModuleType("runtime.device_check")
    module.get_core = lambda: core
    module.available_devices = devices if callable(devices) else (lambda: devices)
    return module
