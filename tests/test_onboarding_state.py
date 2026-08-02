import json

from app.onboarding_state import OnboardingStateStore, migrate_state


def test_state_migration_uses_safe_defaults():
    state = migrate_state({"completed": 1, "selected_model": "tiny"})
    assert state["schema_version"] == 1
    assert state["completed"] is True
    assert state["selected_model"] == "tiny"
    assert state["actual_device"] is None


def test_corrupt_state_restarts_without_touching_models(tmp_path):
    path = tmp_path / "onboarding" / "state.json"
    path.parent.mkdir()
    path.write_text("{broken", encoding="utf-8")
    model = tmp_path / "models" / "keep.xml"
    model.parent.mkdir()
    model.write_text("keep", encoding="utf-8")
    store = OnboardingStateStore(path)

    loaded = store.load()

    assert loaded.recovered is True
    assert loaded.state["completed"] is False
    assert model.read_text(encoding="utf-8") == "keep"
    assert path.with_suffix(".json.corrupt").exists()
    assert not path.exists()

    loaded_again = store.load()
    assert loaded_again.recovered is False
    assert loaded_again.state["completed"] is False


def test_state_is_written_atomically(tmp_path):
    path = tmp_path / "state.json"
    store = OnboardingStateStore(path)
    store.update(completed=True, selected_model="tiny", actual_device="MOCK")
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["completed"] is True
    assert not path.with_suffix(".json.tmp").exists()


def test_update_preserves_existing_fields(tmp_path):
    path = tmp_path / "state.json"
    store = OnboardingStateStore(path)
    store.update(selected_model="tiny", selected_device="CPU")

    state = store.update(completed=True)

    assert state["completed"] is True
    assert state["selected_model"] == "tiny"
    assert state["selected_device"] == "CPU"
