"""Deterministic external services for full session lifecycle regressions."""

import asyncio
from queue import Queue

from main_logic.core import LLMSessionManager
from main_logic.core import lifecycle, manager as manager_module


class MemoryConfig:
    def __init__(self):
        self.core = {"AUDIO_API_KEY": "test", "CORE_API_TYPE": "qwen", "DISABLE_TTS": True}
        self.model = {"base_url": "wss://example.invalid", "api_key": "test", "model": "test", "api_type": "qwen"}

    def get_character_data(self):
        return "tester", "cat", {}, {"cat": {}}, {}, {}, {}, {}, {}

    async def aget_character_data(self):
        return self.get_character_data()

    def get_core_config(self):
        return dict(self.core)

    async def aget_core_config(self, **kwargs):
        return self.get_core_config()

    def get_model_api_config(self, tier, **kwargs):
        return dict(self.model)

    async def aget_model_api_config(self, tier, **kwargs):
        return self.get_model_api_config(tier)

    async def aensure_region_resolved(self):
        return True

    def cleanup_invalid_voice_ids(self):
        return 0, []

    def voice_id_exists_in_any_storage(self, voice):
        return False


class ConnectedSocket:
    class State:
        @property
        def CONNECTED(self):
            return self

    client_state = State()

    def __init__(self):
        self.messages = []

    async def send_json(self, data):
        self.messages.append(data)

    async def send_text(self, data):
        import json
        await self.send_json(json.loads(data))


class ProviderClient:
    def __init__(self, **kwargs):
        self.callbacks = kwargs
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.connect_entered = asyncio.Event()
        self.allow_connect = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.allow_close.set()
        self.closed = asyncio.Event()
        self.handler_entered = asyncio.Event()
        self._audio_processor = None
        self.raise_on_connect = None

    async def connect(self, initial_prompt, native_audio=True):
        self.connect_entered.set()
        await self.allow_connect.wait()
        if self.raise_on_connect:
            raise self.raise_on_connect

    async def close(self):
        self.close_entered.set()
        await self.allow_close.wait()
        self.closed.set()

    async def handle_messages(self):
        self.handler_entered.set()
        await asyncio.Event().wait()

    def set_tools(self, tools):
        pass


async def make_full_manager(monkeypatch):
    config = MemoryConfig()
    monkeypatch.setattr(manager_module, "get_config_manager", lambda: config)
    manager = LLMSessionManager(Queue(), "cat", "test prompt")
    manager.user_language = "zh"
    manager.websocket = ConnectedSocket()
    clients = []
    created = asyncio.Queue()

    class Factory(ProviderClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            clients.append(self)
            created.put_nowait(self)

    monkeypatch.setattr(lifecycle, "OmniRealtimeClient", Factory)

    # Only external prompt/memory services and persisted settings are supplied;
    # the actual start, end, prepare, install, activation and callbacks run.
    async def prompt():
        return "test prompt"

    async def memory(*args):
        return ""

    async def settings(*args, **kwargs):
        return {"noiseReductionEnabled": False, "independentAsrEnabled": False}

    async def default_voice(*args, **kwargs):
        return None

    manager._build_initial_prompt = prompt
    manager._start_session_fetch_new_dialog = memory
    monkeypatch.setattr(lifecycle, "ensure_default_yui_voice_for_free_api", default_voice)
    monkeypatch.setattr(lifecycle._core_facade, "aload_global_conversation_settings", settings)
    return manager, created, clients


async def drain_manager(manager, clients, *tasks):
    for client in clients:
        client.allow_connect.set()
        client.allow_close.set()
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await manager.end_session(by_server=True)
    idle = getattr(manager, "_idle_session_reset_task", None)
    if idle is not None:
        idle.cancel()
        await asyncio.gather(idle, return_exceptions=True)
    background = tuple(getattr(manager, "_bg_tasks", ()))
    for task in background:
        task.cancel()
    await asyncio.gather(*background, return_exceptions=True)
