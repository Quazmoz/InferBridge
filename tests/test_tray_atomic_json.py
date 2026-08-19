from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from app.tray_support import atomic_json


def test_concurrent_atomic_json_writers_use_independent_staging_files(tmp_path):
    path = tmp_path / "tray-command.json"

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [
            executor.submit(atomic_json, path, {"writer": index, "command": "start"})
            for index in range(24)
        ]
        for future in futures:
            future.result()

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["command"] == "start"
    assert payload["writer"] in range(24)
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob(".*.tmp"))
