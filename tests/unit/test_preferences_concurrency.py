import json
import threading

import pytest
from fastapi import Response

from main_routers.config_router import preferences as preferences_router
from utils import preferences
from utils import token_tracker


class _FakeConfigManager:
    def __init__(self, path):
        self.path = path

    def ensure_config_directory(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def get_runtime_config_path(self, _name):
        return self.path

    def get_config_path(self, _name):
        return self.path


def _use_preferences_file(monkeypatch, tmp_path, initial=None):
    path = tmp_path / "user_preferences.json"
    path.write_text(json.dumps(initial or []), encoding="utf-8")
    manager = _FakeConfigManager(path)
    monkeypatch.setattr(preferences, "_config_manager", manager)
    monkeypatch.setattr(preferences, "PREFERENCES_FILE", str(path))
    monkeypatch.setattr(
        preferences,
        "assert_cloudsave_writable",
        lambda *_args, **_kwargs: None,
    )
    return path


def test_versioned_save_rejects_stale_revision_and_asr_decision(monkeypatch, tmp_path):
    _use_preferences_file(monkeypatch, tmp_path)

    newer = {
        "writeId": 20,
        "writerId": "window-b",
        "value": True,
    }
    first = preferences.save_global_conversation_settings_versioned(
        {"independentAsrEnabled": True},
        expected_revision=0,
        asr_decision=newer,
    )
    assert first.success is True
    assert first.snapshot.revision == 1

    stale_revision = preferences.save_global_conversation_settings_versioned(
        {"focusModeEnabled": True},
        expected_revision=0,
    )
    assert stale_revision.conflict is True
    assert stale_revision.snapshot.settings["independentAsrEnabled"] is True

    stale_decision = preferences.save_global_conversation_settings_versioned(
        {"independentAsrEnabled": False},
        expected_revision=1,
        asr_decision={
            "writeId": 10,
            "writerId": "window-a",
            "value": False,
        },
    )
    assert stale_decision.conflict is True
    assert stale_decision.snapshot.settings["independentAsrEnabled"] is True
    assert stale_decision.snapshot.asr_decision == newer


def test_locked_partial_writes_preserve_both_concurrent_changes(monkeypatch, tmp_path):
    path = _use_preferences_file(
        monkeypatch,
        tmp_path,
        [
            {
                "model_path": preferences.GLOBAL_CONVERSATION_KEY,
                "focusModeEnabled": False,
                "subtitleEnabled": False,
            }
        ],
    )
    original_atomic_write = preferences.atomic_write_json
    first_at_write = threading.Event()
    allow_first_write = threading.Event()
    second_at_write = threading.Event()

    def controlled_atomic_write(target, data, **kwargs):
        if threading.current_thread().name == "first-writer":
            first_at_write.set()
            assert allow_first_write.wait(5)
        else:
            second_at_write.set()
        original_atomic_write(target, data, **kwargs)

    monkeypatch.setattr(preferences, "atomic_write_json", controlled_atomic_write)
    results = []

    first = threading.Thread(
        name="first-writer",
        target=lambda: results.append(
            preferences.save_global_conversation_settings({"focusModeEnabled": True})
        ),
    )
    second = threading.Thread(
        name="second-writer",
        target=lambda: results.append(
            preferences.save_global_conversation_settings({"subtitleEnabled": True})
        ),
    )
    first.start()
    assert first_at_write.wait(5)
    second.start()
    assert second_at_write.wait(0.1) is False
    allow_first_write.set()
    first.join(5)
    second.join(5)

    assert sorted(results) == [True, True]
    saved = json.loads(path.read_text(encoding="utf-8"))
    global_entry = next(
        entry
        for entry in saved
        if entry.get("model_path") == preferences.GLOBAL_CONVERSATION_KEY
    )
    assert global_entry["focusModeEnabled"] is True
    assert global_entry["subtitleEnabled"] is True
    assert global_entry["_conversation_settings_revision"] == 2


def test_model_update_and_conversation_write_share_one_rmw_lock(monkeypatch, tmp_path):
    path = _use_preferences_file(
        monkeypatch,
        tmp_path,
        [
            {
                "model_path": "model-a",
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
            },
            {
                "model_path": preferences.GLOBAL_CONVERSATION_KEY,
                "focusModeEnabled": False,
            },
        ],
    )
    original_atomic_write = preferences.atomic_write_json
    model_at_write = threading.Event()
    allow_model_write = threading.Event()
    conversation_at_write = threading.Event()

    def controlled_atomic_write(target, data, **kwargs):
        if threading.current_thread().name == "model-writer":
            model_at_write.set()
            assert allow_model_write.wait(5)
        else:
            conversation_at_write.set()
        original_atomic_write(target, data, **kwargs)

    monkeypatch.setattr(preferences, "atomic_write_json", controlled_atomic_write)
    results = []
    model_writer = threading.Thread(
        name="model-writer",
        target=lambda: results.append(
            preferences.update_model_preferences(
                "model-a",
                {"x": 5, "y": 6},
                {"x": 2, "y": 2},
            )
        ),
    )
    conversation_writer = threading.Thread(
        name="conversation-writer",
        target=lambda: results.append(
            preferences.save_global_conversation_settings({"focusModeEnabled": True})
        ),
    )
    model_writer.start()
    assert model_at_write.wait(5)
    conversation_writer.start()
    assert conversation_at_write.wait(0.1) is False
    allow_model_write.set()
    model_writer.join(5)
    conversation_writer.join(5)

    assert sorted(results) == [True, True]
    saved = json.loads(path.read_text(encoding="utf-8"))
    model_entry = next(entry for entry in saved if entry.get("model_path") == "model-a")
    global_entry = next(
        entry
        for entry in saved
        if entry.get("model_path") == preferences.GLOBAL_CONVERSATION_KEY
    )
    assert model_entry["position"] == {"x": 5, "y": 6}
    assert global_entry["focusModeEnabled"] is True


class _Request:
    def __init__(
        self,
        body,
        if_match=None,
        asr_decision=None,
        raw_asr_decision=None,
    ):
        self._body = body
        self.headers = {}
        if if_match is not None:
            self.headers["if-match"] = if_match
        if raw_asr_decision is not None:
            self.headers["x-conversation-settings-asr-decision"] = raw_asr_decision
        elif asr_decision is not None:
            self.headers["x-conversation-settings-asr-decision"] = json.dumps(
                asr_decision
            )

    async def json(self):
        return dict(self._body) if isinstance(self._body, dict) else self._body


@pytest.mark.asyncio
async def test_conversation_settings_route_returns_etag_and_412_snapshot(
    monkeypatch,
    tmp_path,
):
    _use_preferences_file(monkeypatch, tmp_path)
    first_response = await preferences_router.save_conversation_settings(
        _Request(
            {"independentAsrEnabled": True},
            if_match='"conversation-settings-0"',
            asr_decision={
                "writeId": 20,
                "writerId": "window-b",
                "value": True,
            },
        )
    )
    assert first_response.status_code == 200
    assert first_response.headers["etag"] == '"conversation-settings-1"'
    assert first_response.headers["cache-control"] == "no-store"

    monkeypatch.setattr(token_tracker, "get_telemetry_branch", lambda: "main")
    get_response = Response()
    get_payload = await preferences_router.get_conversation_settings(get_response)
    assert get_response.headers["etag"] == '"conversation-settings-1"'
    assert get_response.headers["cache-control"] == "no-store"
    assert get_payload["revision"] == 1
    assert get_payload["settings"]["independentAsrEnabled"] is True

    conflict_response = await preferences_router.save_conversation_settings(
        _Request(
            {"independentAsrEnabled": False},
            if_match='"conversation-settings-0"',
        )
    )
    assert conflict_response.status_code == 412
    assert conflict_response.headers["etag"] == '"conversation-settings-1"'
    payload = json.loads(conflict_response.body)
    assert payload["settings"]["independentAsrEnabled"] is True
    assert payload["decisions"]["independentAsrEnabled"]["writeId"] == 20


@pytest.mark.asyncio
async def test_set_preferred_model_offloads_locked_write(monkeypatch):
    calls = []

    def fake_move_model_to_top(model_path):
        calls.append(("move", model_path))
        return True

    async def fake_to_thread(func, *args):
        calls.append(("to_thread", func, args))
        return func(*args)

    monkeypatch.setattr(preferences_router, "move_model_to_top", fake_move_model_to_top)
    monkeypatch.setattr(preferences_router.asyncio, "to_thread", fake_to_thread)

    result = await preferences_router.set_preferred_model(
        _Request({"model_path": "model-a"})
    )

    assert result["success"] is True
    assert calls[0] == ("to_thread", fake_move_model_to_top, ("model-a",))
    assert calls[1] == ("move", "model-a")


@pytest.mark.asyncio
async def test_conversation_settings_route_validates_contract_and_keeps_legacy_write(
    monkeypatch,
    tmp_path,
):
    _use_preferences_file(monkeypatch, tmp_path)

    non_object = await preferences_router.save_conversation_settings(
        _Request(["not", "an", "object"])
    )
    assert non_object.status_code == 400

    malformed_if_match = await preferences_router.save_conversation_settings(
        _Request({"focusModeEnabled": True}, if_match='"wrong-etag"')
    )
    assert malformed_if_match.status_code == 400

    malformed_decision = await preferences_router.save_conversation_settings(
        _Request(
            {"independentAsrEnabled": True},
            raw_asr_decision="{not-json",
        )
    )
    assert malformed_decision.status_code == 400

    legacy_write = await preferences_router.save_conversation_settings(
        _Request({"focusModeEnabled": True})
    )
    assert legacy_write.status_code == 200
    payload = json.loads(legacy_write.body)
    assert payload["settings"]["focusModeEnabled"] is True
