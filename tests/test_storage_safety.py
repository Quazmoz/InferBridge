"""Guards that stand between managed cleanup and the rest of the filesystem.

Storage cleanup deletes directory trees, so the refusal paths matter more than the
success path: a symlink or Windows junction inside a managed directory must abort the
removal rather than let ``rmtree`` walk out of the data root.
"""

from __future__ import annotations

import os
import stat

import pytest

from app.storage_safety import (
    StorageConflict,
    _all_lifecycle_idle,
    _lexically_within,
    _measure_tree,
    _model_activity,
    _path_exists,
    _remove_tree,
    cleanup_capability,
)


def _symlink_or_skip(link, target, *, target_is_directory: bool = False) -> None:
    """Create a symlink, or skip when the platform will not allow one.

    Creating a symbolic link on Windows needs Developer Mode or an elevated account, so
    an ordinary developer or release machine cannot exercise these refusal paths. Skip
    rather than fail: the guard under test is real, the privilege to stage it is not.
    """

    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("Symbolic links are unavailable on this platform.")


class _Task:
    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


class _Lock:
    def __init__(self, held: bool) -> None:
        self._held = held

    def locked(self) -> bool:
        return self._held


class _Manager:
    def __init__(self, **kwargs) -> None:
        self.engines = kwargs.get("engines", {})
        self.load_tasks = kwargs.get("load_tasks", {})
        self.convert_tasks = kwargs.get("convert_tasks", {})
        self.status_overrides = kwargs.get("status_overrides", {})
        self._model_recovery_locks = kwargs.get("recovery_locks", {})


# --- measurement -----------------------------------------------------------------


def test_a_managed_tree_reports_its_total_size(tmp_path) -> None:
    root = tmp_path / "models"
    target = root / "tiny"
    (target / "nested").mkdir(parents=True)
    (target / "a.bin").write_bytes(b"x" * 10)
    (target / "nested" / "b.bin").write_bytes(b"y" * 5)

    measurement = _measure_tree(target, root=root)

    assert measurement.present is True
    assert measurement.size_bytes == 15
    assert measurement.unsafe is False
    assert measurement.unreadable is False


def test_a_missing_path_measures_as_absent(tmp_path) -> None:
    measurement = _measure_tree(tmp_path / "models" / "gone", root=tmp_path / "models")

    assert measurement.present is False
    assert measurement.size_bytes == 0


def test_a_symlinked_target_is_flagged_unsafe(tmp_path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.bin").write_bytes(b"important")
    _symlink_or_skip(root / "tiny", outside, target_is_directory=True)

    measurement = _measure_tree(root / "tiny", root=root)

    assert measurement.present is True
    assert measurement.unsafe is True
    assert measurement.size_bytes == 0


def test_a_symlink_nested_inside_the_tree_is_flagged_unsafe(tmp_path) -> None:
    root = tmp_path / "models"
    target = root / "tiny"
    target.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink_or_skip(target / "link", outside, target_is_directory=True)

    assert _measure_tree(target, root=root).unsafe is True


def test_a_symlinked_file_inside_the_tree_is_flagged_unsafe(tmp_path) -> None:
    root = tmp_path / "models"
    target = root / "tiny"
    target.mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"important")
    _symlink_or_skip(target / "link.bin", outside)

    assert _measure_tree(target, root=root).unsafe is True


def test_a_symlinked_root_makes_every_child_unsafe(tmp_path) -> None:
    real_root = tmp_path / "real"
    (real_root / "tiny").mkdir(parents=True)
    linked_root = tmp_path / "models"
    _symlink_or_skip(linked_root, real_root, target_is_directory=True)

    assert _measure_tree(linked_root / "tiny", root=linked_root).unsafe is True


def test_a_path_outside_the_managed_root_is_unsafe(tmp_path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    escape = tmp_path / "elsewhere"
    escape.mkdir()

    assert _measure_tree(escape, root=root).unsafe is True
    assert _measure_tree(root / ".." / "elsewhere", root=root).unsafe is True


def test_a_file_is_unsafe_unless_the_caller_expects_one(tmp_path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    target = root / "catalog.json"
    target.write_bytes(b"{}" * 8)

    assert _measure_tree(target, root=root).unsafe is True
    allowed = _measure_tree(target, root=root, allow_file=True)
    assert allowed.unsafe is False
    assert allowed.size_bytes == 16


def test_the_root_itself_counts_as_within_the_root(tmp_path) -> None:
    assert _lexically_within(tmp_path, tmp_path) is True
    assert _lexically_within(tmp_path / "child", tmp_path) is True
    assert _lexically_within(tmp_path.parent, tmp_path) is False


def test_path_existence_does_not_follow_a_broken_symlink(tmp_path) -> None:
    link = tmp_path / "dangling"
    _symlink_or_skip(link, tmp_path / "never-created")

    assert _path_exists(link) is True
    assert link.exists() is False


# --- removal ---------------------------------------------------------------------


def test_removing_a_managed_tree_returns_the_reclaimed_size(tmp_path) -> None:
    root = tmp_path / "models"
    target = root / "tiny"
    target.mkdir(parents=True)
    (target / "a.bin").write_bytes(b"x" * 32)

    reclaimed = _remove_tree(target, root=root, description="model files")

    assert reclaimed == 32
    assert not target.exists()


def test_removing_a_missing_tree_reclaims_nothing(tmp_path) -> None:
    root = tmp_path / "models"
    root.mkdir()

    assert _remove_tree(root / "gone", root=root, description="model files") == 0


def test_removal_refuses_to_follow_a_symlink_out_of_the_managed_root(tmp_path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.bin").write_bytes(b"important")
    _symlink_or_skip(root / "tiny", outside, target_is_directory=True)

    with pytest.raises(StorageConflict) as conflict:
        _remove_tree(root / "tiny", root=root, description="model files")

    assert conflict.value.code == "unsafe_path"
    # The refusal must leave the linked-to data completely untouched.
    assert (outside / "precious.bin").read_bytes() == b"important"


def test_removal_refuses_a_path_outside_the_managed_root(tmp_path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    escape = tmp_path / "elsewhere"
    escape.mkdir()
    (escape / "keep.bin").write_bytes(b"keep")

    with pytest.raises(StorageConflict) as conflict:
        _remove_tree(escape, root=root, description="model files")

    assert conflict.value.code == "unsafe_path"
    assert (escape / "keep.bin").exists()


def test_removal_reports_an_unreadable_tree_instead_of_deleting_part_of_it(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "models"
    target = root / "tiny"
    target.mkdir(parents=True)
    (target / "a.bin").write_bytes(b"x")

    def refuse(*_args, **_kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(os, "walk", lambda *a, **k: refuse())

    with pytest.raises(StorageConflict) as conflict:
        _remove_tree(target, root=root, description="model files")

    assert conflict.value.code == "storage_unreadable"
    assert target.exists()


def test_removal_clears_a_read_only_file(tmp_path) -> None:
    root = tmp_path / "models"
    target = root / "tiny"
    target.mkdir(parents=True)
    locked = target / "a.bin"
    locked.write_bytes(b"x" * 4)
    os.chmod(locked, stat.S_IRUSR)

    assert _remove_tree(target, root=root, description="model files") == 4
    assert not target.exists()


def test_removal_of_a_single_managed_file_is_allowed_when_requested(tmp_path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    target = root / "catalog.json"
    target.write_bytes(b"{}")

    assert _remove_tree(target, root=root, description="catalog", allow_file=True) == 2
    assert not target.exists()


def test_removal_gives_up_with_a_clear_conflict_when_the_tree_survives(
    tmp_path, monkeypatch
) -> None:
    """Windows file locks are simulated: rmtree keeps failing, so cleanup must not hang."""

    root = tmp_path / "models"
    target = root / "tiny"
    target.mkdir(parents=True)
    (target / "a.bin").write_bytes(b"x")

    import shutil as shutil_module

    from app import storage_safety

    monkeypatch.setattr(storage_safety.time, "sleep", lambda _seconds: None)

    attempts = {"count": 0}

    def refuse(*_args, **_kwargs):
        attempts["count"] += 1
        raise PermissionError("file is in use")

    monkeypatch.setattr(shutil_module, "rmtree", refuse)

    with pytest.raises(StorageConflict) as conflict:
        _remove_tree(target, root=root, description="model files")

    assert conflict.value.code == "cleanup_failed"
    assert isinstance(conflict.value.__cause__, OSError)
    # Retries are bounded, so a permanently locked directory cannot spin forever.
    assert attempts["count"] == 5


def test_removal_succeeds_once_a_transient_lock_clears(tmp_path, monkeypatch) -> None:
    root = tmp_path / "models"
    target = root / "tiny"
    target.mkdir(parents=True)
    (target / "a.bin").write_bytes(b"x" * 8)

    import shutil as shutil_module

    from app import storage_safety

    monkeypatch.setattr(storage_safety.time, "sleep", lambda _seconds: None)
    real_rmtree = shutil_module.rmtree
    attempts = {"count": 0}

    def flaky(path, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError("file is in use")
        return real_rmtree(path, **kwargs)

    monkeypatch.setattr(shutil_module, "rmtree", flaky)

    assert _remove_tree(target, root=root, description="model files") == 8
    assert not target.exists()


# --- lifecycle activity ----------------------------------------------------------


def test_a_loaded_model_is_reported_as_loaded() -> None:
    activity = _model_activity(_Manager(engines={"tiny": object()}), "tiny")

    assert activity == {"loaded": True, "preparing": False}


@pytest.mark.parametrize("status", ["queued", "loading", "queued_convert", "converting"])
def test_every_active_status_counts_as_preparing(status) -> None:
    manager = _Manager(status_overrides={"tiny": {"status": status}})

    assert _model_activity(manager, "tiny")["preparing"] is True


def test_a_finished_status_does_not_count_as_preparing() -> None:
    manager = _Manager(status_overrides={"tiny": {"status": "ready"}})

    assert _model_activity(manager, "tiny")["preparing"] is False


def test_a_running_task_or_held_recovery_lock_counts_as_preparing() -> None:
    assert _model_activity(_Manager(load_tasks={"tiny": _Task(False)}), "tiny")["preparing"]
    assert _model_activity(_Manager(convert_tasks={"tiny": _Task(False)}), "tiny")["preparing"]
    assert _model_activity(_Manager(recovery_locks={"tiny": _Lock(True)}), "tiny")["preparing"]
    assert not _model_activity(_Manager(load_tasks={"tiny": _Task(True)}), "tiny")["preparing"]


def test_activity_of_an_unknown_model_is_idle() -> None:
    assert _model_activity(_Manager(), "never-seen") == {"loaded": False, "preparing": False}


def test_a_manager_with_nothing_in_flight_is_idle() -> None:
    assert _all_lifecycle_idle(_Manager()) is True
    assert _all_lifecycle_idle(_Manager(load_tasks={"a": _Task(True)})) is True
    assert _all_lifecycle_idle(_Manager(status_overrides={"a": {"status": "ready"}})) is True


@pytest.mark.parametrize(
    "manager",
    [
        _Manager(engines={"tiny": object()}),
        _Manager(load_tasks={"tiny": _Task(False)}),
        _Manager(convert_tasks={"tiny": _Task(False)}),
        _Manager(recovery_locks={"tiny": _Lock(True)}),
        _Manager(status_overrides={"tiny": {"status": "converting"}}),
    ],
)
def test_any_in_flight_work_blocks_the_idle_check(manager) -> None:
    assert _all_lifecycle_idle(manager) is False


def test_malformed_status_overrides_do_not_break_the_idle_check() -> None:
    assert _all_lifecycle_idle(_Manager(status_overrides={"tiny": "converting"})) is True


# --- capability reporting --------------------------------------------------------


def test_cleanup_is_offered_when_there_is_something_to_reclaim() -> None:
    assert cleanup_capability(reclaimable_bytes=1024) == {
        "available": True,
        "reclaimable_bytes": 1024,
        "reason": "",
    }


def test_an_empty_target_is_reported_as_nothing_to_reclaim() -> None:
    capability = cleanup_capability(reclaimable_bytes=0)

    assert capability["available"] is False
    assert capability["reason"] == "No reclaimable files were found."


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("protected", "Retained for automatic transaction recovery."),
        ("unsafe", "A symbolic link or Windows junction was detected. Remove it manually."),
        ("unreadable", "Some files could not be inspected safely."),
        ("preparing", "Wait for model preparation to finish."),
        ("loaded", "Unload the model before removing its managed files."),
    ],
)
def test_each_blocking_condition_explains_itself(flag, expected) -> None:
    capability = cleanup_capability(reclaimable_bytes=1024, **{flag: True})

    assert capability["available"] is False
    assert capability["reason"] == expected
    # The size is still reported so the UI can show what is being withheld.
    assert capability["reclaimable_bytes"] == 1024


def test_blocking_conditions_are_reported_in_severity_order() -> None:
    """A protected path is the strongest reason, then unsafe, then the softer states."""

    capability = cleanup_capability(
        reclaimable_bytes=1024,
        protected=True,
        unsafe=True,
        unreadable=True,
        preparing=True,
        loaded=True,
    )

    assert capability["reason"] == "Retained for automatic transaction recovery."


def test_a_whole_root_cleanup_waits_for_every_model_to_go_idle() -> None:
    blocked = cleanup_capability(reclaimable_bytes=1024, require_all_idle=True, all_idle=False)

    assert blocked["available"] is False
    assert blocked["reason"] == "Unload all models and wait for active operations to finish."
    assert cleanup_capability(reclaimable_bytes=1024, require_all_idle=True, all_idle=True)[
        "available"
    ]
