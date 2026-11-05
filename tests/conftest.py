"""Make the repo root importable so `import app` / `import runtime` work under pytest."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    # pyproject sets `--basetemp=.tmp/pytest`; pytest creates that leaf dir but
    # not its parent, so a fresh checkout/CI run errors unless `.tmp/` exists.
    (ROOT / ".tmp").mkdir(parents=True, exist_ok=True)


import pytest  # noqa: E402

from runtime.device_check import cleanup_cached_core  # noqa: E402


@pytest.fixture(autouse=True)
def cleanup_ov_core():
    yield
    cleanup_cached_core()


@pytest.fixture(autouse=True)
def isolate_desktop_keyring(monkeypatch):
    # Never read or write a developer's real Secret Service keyring from unit tests.
    monkeypatch.setenv("OV_LLM_DISABLE_SECRET_SERVICE", "1")
