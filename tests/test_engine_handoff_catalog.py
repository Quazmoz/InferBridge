from app.engine_handoff_safety import _apply_switching_capabilities


def test_loaded_model_switch_reports_loading_and_disables_unload():
    entry = {
        "is_loaded": True,
        "is_loading": False,
        "can_unload": True,
        "status_label": "Loaded",
        "progress": {"message": "Compiling Demo for NPU…"},
    }

    result = _apply_switching_capabilities(entry, switching=True)

    assert result["is_loaded"] is True
    assert result["is_loading"] is True
    assert result["can_unload"] is False
    assert result["status_label"] == "Compiling Demo for NPU…"


def test_inactive_loaded_model_capabilities_are_unchanged():
    entry = {
        "is_loaded": True,
        "is_loading": False,
        "can_unload": True,
        "status_label": "Loaded",
    }

    result = _apply_switching_capabilities(entry, switching=False)

    assert result == entry
