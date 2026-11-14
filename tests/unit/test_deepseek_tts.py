import asyncio
import json
import threading
from queue import Queue
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

import main_logic.tts_client as tts_client
from main_logic.core import LLMSessionManager
from main_logic.tts_client._infra import TTS_AUDIO_DONE_SENTINEL, TTS_SHUTDOWN_SENTINEL
from main_logic.tts_client.workers import deepseek
from utils.config_manager import get_config_manager
from utils.config_manager.voice_storage import VoiceStorageMixin
from utils.tts import native_voice_registry, provider_registry
from utils.tts.providers.deepseek import normalize_deepseek_tts_voice


@pytest.fixture
def manager(monkeypatch):
    cm = Mock()
    cm.get_core_config.return_value = {"assistApi": "deepseek"}
    cm.get_model_api_config.return_value = {}
    cm.get_voices_for_current_api.return_value = {}
    cm.voice_id_exists_in_any_storage.return_value = False
    cm.get_tts_api_key.side_effect = lambda provider: VoiceStorageMixin.get_tts_api_key(cm, provider)
    monkeypatch.setattr(tts_client, "get_config_manager", lambda: cm)
    return cm


def test_catalog_is_hosted_and_normalizes_aliases(manager):
    assert native_voice_registry.get_provider("deepseek") is None
    provider = provider_registry.get("deepseek")
    assert provider.kind == "hosted"
    assert provider.capabilities == {"preset"}
    catalog = provider_registry.preset_catalog_for_ui("deepseek")
    assert set(catalog) == {"stella", "tide"}
    assert catalog["stella"]["provider_label"] == "DeepSeek"
    assert normalize_deepseek_tts_voice(" TiDe ") == ("tide", True)
    assert normalize_deepseek_tts_voice("男") == ("tide", True)
    assert normalize_deepseek_tts_voice("FEMALE") == ("stella", True)
    assert normalize_deepseek_tts_voice(None) == ("stella", False)
    assert provider_registry.selected_preset_provider_key({}, manager, "stella") == "deepseek"
    assert provider_registry.selected_preset_provider_key({}, manager, "female") is None


def test_assist_only_does_not_route_to_deepseek(manager):
    worker, _, provider = tts_client.get_tts_worker("qwen")
    assert (worker, provider) == (tts_client.qwen_realtime_tts_worker, "qwen")


def test_config_manager_resolves_key_and_saves_the_preset(clean_user_data_dir, tmp_path, monkeypatch):
    cm = get_config_manager("N.E.K.O")
    config = {
        "coreApi": "qwen",
        "coreApiKey": "qwen-key",
        "assistApi": "qwen",
        "assistApiKeyDeepseek": "ds-key",
        "enableCustomApi": True,
        "ttsModelProvider": "deepseek",
        "ttsProvider": "deepseek",
        "ttsModelUrl": "wss://api.deepseek.com/v1/tools/tts",
        "ttsModelId": "stale-model",
        "ttsModelApiKey": "stale-key",
        "ttsVoiceId": "tide",
    }
    config_path = tmp_path / "core_config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    get_config_path = cm.get_config_path
    monkeypatch.setattr(
        cm, "get_config_path",
        lambda filename: config_path if filename == "core_config.json" else get_config_path(filename),
    )
    assert cm.get_tts_api_key("deepseek") == "ds-key"
    assert cm.get_core_config()["TTS_VOICE_ID"] == "tide"
    assert provider_registry.selected_provider_key(cm.get_core_config(), cm) == "deepseek"
    assert cm.get_model_api_config("tts_custom")["api_key"] == "qwen-key"
    assert cm.validate_voice_id("tide")
    assert cm.voice_id_to_storage_value("tide") == {"source": "preset", "provider": "deepseek", "ref": "tide"}


@pytest.mark.parametrize("config, voice", [
    ({"TTS_PROVIDER": "deepseek"}, ""),
    ({"TTS_PROVIDER": "deepseek", "assistApi": "mimo"}, "female"),
    ({"assistApi": "mimo"}, "TIDE"),
])
def test_dispatch_selects_explicit_provider_or_canonical_voice(manager, config, voice):
    config["ASSIST_API_KEY_DEEPSEEK"] = " ds-key "
    manager.get_core_config.return_value = config
    assert tts_client.get_tts_worker("qwen", bool(voice), voice) == (
        deepseek.deepseek_tts_worker, "ds-key", "deepseek",
    )
    assert tts_client.selected_realtime_output_tts_provider_key(config, manager, voice) == "deepseek"


def test_explicit_selection_enables_realtime_output_without_character_voice(manager):
    config = {"TTS_PROVIDER": "deepseek", "ASSIST_API_KEY_DEEPSEEK": "ds-key"}
    manager.get_core_config.return_value = config
    session = object.__new__(LLMSessionManager)
    session._config_manager = manager
    session.core_api_type = "qwen"
    session.voice_id = ""
    session._is_free_preset_voice = False
    assert session._resolve_session_use_tts("audio", {}, config)
    assert session._resolve_tts_worker_spec()[:4] == (deepseek.deepseek_tts_worker, "ds-key", "", "deepseek")


def test_explicit_other_hosted_provider_keeps_priority(manager):
    manager.get_core_config.return_value = {"TTS_PROVIDER": "mimo", "ASSIST_API_KEY_MIMO": "mimo-key"}
    _, key, provider = tts_client.get_tts_worker("qwen", False, "tide")
    assert (key, provider) == ("mimo-key", "mimo")


@pytest.mark.parametrize("deepseek_key", [None, "   ", "***MASKED***"])
def test_selected_without_deepseek_key_uses_dummy(manager, deepseek_key):
    manager.get_core_config.return_value = {
        "TTS_PROVIDER": "deepseek", "ASSIST_API_KEY_DEEPSEEK": deepseek_key, "CORE_API_KEY": "qwen-key",
    }
    assert tts_client.get_tts_worker("qwen") == (tts_client.dummy_tts_worker, None, None)


@pytest.mark.parametrize("in_current_bucket", [True, False])
def test_same_name_clone_keeps_its_route(manager, in_current_bucket):
    manager.get_core_config.return_value = {"TTS_PROVIDER": "deepseek", "ASSIST_API_KEY_DEEPSEEK": "ds-key"}
    if in_current_bucket:
        manager.get_voices_for_current_api.return_value = {"tide": {"provider": "cosyvoice"}}
    else:
        manager.voice_id_exists_in_any_storage.return_value = True
    worker, _, provider = tts_client.get_tts_worker("qwen", True, "tide")
    assert (worker, provider) == (tts_client.cosyvoice_vc_tts_worker, "cosyvoice")


class FakeSocket:
    def __init__(self, on_finish):
        self.on_finish = on_finish
        self.events = Queue()
        self.incoming = asyncio.Queue()
        self.closed = threading.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed.set()

    async def send(self, message):
        event = json.loads(message)
        self.events.put(event)
        if event["event"] == "finish_text":
            for reply in self.on_finish:
                self.incoming.put_nowait(reply)

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self.incoming.get()
        if item is None:
            raise StopAsyncIteration
        return item


@pytest.fixture
def worker(monkeypatch):
    requests, replies, connections, scripted_replies = Queue(), Queue(), Queue(), Queue()

    def connect(url, **kwargs):
        ws = FakeSocket(scripted_replies.get_nowait())
        connections.put((ws, url, kwargs))
        return ws

    monkeypatch.setattr(deepseek.websockets, "connect", connect)
    monkeypatch.setattr(deepseek, "_record_tts_telemetry", lambda *_: None)
    thread = threading.Thread(
        target=deepseek.deepseek_tts_worker, args=(requests, replies, "ds-key", "男"), daemon=True,
    )
    thread.start()
    try:
        assert replies.get(timeout=3) == ("__ready__", True)
        assert connections.empty()
        yield SimpleNamespace(requests=requests, replies=replies, connections=connections, script=scripted_replies)
    finally:
        requests.put((TTS_SHUTDOWN_SENTINEL, None))
        thread.join(timeout=4)
        assert not thread.is_alive()


def test_streams_text_and_finishes_after_all_48khz_audio(worker):
    pcm = b"\x10\x00" * 240
    worker.script.put([pcm[:101], pcm[101:], '{"event":"finish","code":"success"}'])
    worker.requests.put(("speech", "你好"))
    socket, url, kwargs = worker.connections.get(timeout=3)
    parsed = urlsplit(url)
    assert (parsed.scheme, parsed.netloc, parsed.path) == ("wss", "api.deepseek.com", "/v1/tools/tts")
    assert parse_qs(parsed.query) == {"voice_id": ["tide"], "format": ["pcm"]}
    assert kwargs["additional_headers"] == {"Authorization": "Bearer ds-key"}
    assert socket.events.get(timeout=3) == {"event": "text", "text": "你好"}
    worker.requests.put(("speech", " there"))
    worker.requests.put((None, None))
    assert socket.events.get(timeout=3) == {"event": "text", "text": " there"}
    assert socket.events.get(timeout=3) == {"event": "finish_text"}
    kind, speech_id, audio = worker.replies.get(timeout=3)
    assert (kind, speech_id, len(audio)) == ("__audio__", "speech", len(pcm) * 2)
    assert worker.replies.get(timeout=3) == (TTS_AUDIO_DONE_SENTINEL, "speech")
    assert socket.closed.is_set()


@pytest.mark.parametrize("reply, code, detail", [
    ('{"event":"finish","code":"content_filter","message":"Rejected"}', "API_POLICY_VIOLATION", "Rejected"),
    (None, "TTS_CONNECTION_FAILED", "closed before sending a finish event"),
])
def test_failure_reports_the_speech_and_next_speech_can_succeed(worker, reply, code, detail):
    worker.script.put([reply])
    worker.requests.put(("failed", "hello"))
    worker.requests.put((None, None))
    assert worker.replies.get(timeout=3) == ("__tts_sentence_failed__", "failed", "")
    kind, payload = worker.replies.get(timeout=3)
    assert kind == "__error__"
    assert json.loads(payload)["code"] == code
    assert detail in json.loads(payload)["message"]
    worker.script.put(['{"event":"finish","code":"success"}'])
    worker.requests.put(("next", "again"))
    worker.requests.put((None, None))
    assert worker.replies.get(timeout=3) == (TTS_AUDIO_DONE_SENTINEL, "next")


def test_interrupt_closes_old_session_before_starting_the_next(worker):
    worker.script.put([])
    worker.requests.put(("old", "old text"))
    old, _, _ = worker.connections.get(timeout=3)
    assert old.events.get(timeout=3)["event"] == "text"
    worker.requests.put(("__interrupt__", None))
    worker.script.put(['{"event":"finish","code":"success"}'])
    worker.requests.put(("new", "new text"))
    worker.requests.put((None, None))
    new, _, _ = worker.connections.get(timeout=3)
    assert old.closed.is_set()
    assert new.events.get(timeout=3) == {"event": "text", "text": "new text"}
    assert worker.replies.get(timeout=3) == (TTS_AUDIO_DONE_SENTINEL, "new")


def test_text_limit_counts_han_characters_twice(worker):
    worker.script.put([])
    worker.requests.put(("long", "字" * 5_000))
    socket, _, _ = worker.connections.get(timeout=3)
    assert socket.events.get(timeout=3)["event"] == "text"
    worker.requests.put(("long", "a"))
    assert worker.replies.get(timeout=3) == ("__tts_sentence_failed__", "long", "")
    kind, payload = worker.replies.get(timeout=3)
    assert kind == "__error__"
    assert json.loads(payload)["code"] == "TTS_CONFIG_INVALID"
    assert socket.events.empty()
