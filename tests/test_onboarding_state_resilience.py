from app.onboarding_state import migrate_state


def test_state_migration_normalizes_device_and_sanitizes_text():
    state = migrate_state(
        {
            "completed": True,
            "selected_model": "  tiny\x00model  ",
            "selected_device": " auto:npu, gpu, cpu ",
        }
    )

    assert state["completed"] is True
    assert state["selected_model"] == "tinymodel"
    assert state["selected_device"] == "AUTO:NPU,GPU,CPU"


def test_invalid_selected_device_restarts_onboarding_without_dropping_model():
    state = migrate_state(
        {
            "completed": True,
            "restart_requested": False,
            "selected_model": "tinyllama-1.1b-chat-fp16",
            "selected_device": "NPU;invalid",
            "actual_device": "NPU",
        }
    )

    assert state["completed"] is False
    assert state["restart_requested"] is True
    assert state["selected_device"] is None
    assert state["selected_model"] == "tinyllama-1.1b-chat-fp16"
    assert state["actual_device"] == "NPU"
