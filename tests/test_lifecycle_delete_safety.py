from pathlib import Path
from types import SimpleNamespace

import pytest

from app import model_library_conversion
from app.lifecycle_safety import _reject_reparse_point_delete


def _manager_for(path: Path):
    cfg = SimpleNamespace(abs_path=lambda _base_dir: path)
    return SimpleNamespace(catalog={"demo": cfg})


def test_model_delete_rejects_symbolic_link_or_junction(monkeypatch, tmp_path):
    model_dir = tmp_path / "model"
    manager = _manager_for(model_dir)
    monkeypatch.setattr(model_library_conversion, "is_reparse_point", lambda path: True)

    with pytest.raises(ValueError, match="symbolic link or Windows junction"):
        _reject_reparse_point_delete(manager, "demo")


def test_model_delete_allows_ordinary_directory(monkeypatch, tmp_path):
    model_dir = tmp_path / "model"
    manager = _manager_for(model_dir)
    monkeypatch.setattr(model_library_conversion, "is_reparse_point", lambda path: False)

    _reject_reparse_point_delete(manager, "demo")
