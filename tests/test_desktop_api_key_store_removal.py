from pathlib import Path

import pytest

from app.desktop_network import DesktopApiKeyStore

_VALID_KEY = "ib_abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"


def test_remove_clears_memory_when_no_persisted_blob_exists(tmp_path):
    store = DesktopApiKeyStore(tmp_path)
    store._memory_key = _VALID_KEY

    assert store.remove() is True
    assert store.get_key() is None


def test_remove_reports_missing_credential(tmp_path):
    store = DesktopApiKeyStore(tmp_path)

    assert store.remove() is False
    assert store.get_key() is None


def test_remove_preserves_memory_and_surfaces_persisted_blob_failure(monkeypatch, tmp_path):
    store = DesktopApiKeyStore(tmp_path)
    store._memory_key = _VALID_KEY
    store.key_path.write_bytes(b"locked-dpapi-blob")
    original_unlink = Path.unlink

    def fail_target_unlink(path: Path, *args, **kwargs):
        if path == store.key_path:
            raise PermissionError("credential file is locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_target_unlink)

    with pytest.raises(PermissionError, match="credential file is locked"):
        store.remove()

    assert store._memory_key == _VALID_KEY
    assert store.key_path.exists()
