from app.engine_handoff_safety import (
    _accurate_load_message,
    _apply_switching_capabilities,
)


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


def test_initial_load_copy_does_not_claim_an_existing_model_is_available():
    message = "Compiling Demo for CPU. The currently loaded model remains available…"

    result = _accurate_load_message(message, has_loaded_engine=False)

    assert result == "Compiling Demo for CPU. First load can take several minutes…"


def test_device_switch_copy_preserves_continuity_message():
    message = "Compiling Demo for NPU. The currently loaded model remains available…"

    result = _accurate_load_message(message, has_loaded_engine=True)

    assert result == message
