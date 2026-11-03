"""Protect explicit persona saves from concurrent automatic profile application."""

import asyncio
import copy
import importlib

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """These synthetic route calls never need a real memory server."""
    yield


class SyntheticConfig:
    """Keep every character read/write observable and entirely in memory."""

    def __init__(self):
        self.events = []
        self.data = {"猫娘": {"Synthetic": {
            "设定": "initial synthetic settings",
            "_reserved": {"persona_override": {"preset_id": "old", "profile": {"性格": "old style"}}},
        }}}

    async def aload_characters(self):
        self.events.append("load")
        return copy.deepcopy(self.data)

    async def asave_characters(self, data):
        self.events.append("save")
        self.data = copy.deepcopy(data)


class SyntheticRequest:
    """Supply a known preset without any real runtime or request credentials."""

    async def json(self):
        return {"preset_id": "synthetic", "source": ""}


@pytest.fixture
def persona_routes(monkeypatch):
    """Inject only saved fixtures and the exact lifecycle lock used by sync."""
    persona = importlib.import_module("main_routers.characters_router.persona")
    memory = importlib.import_module("utils.character_memory")
    lock = asyncio.Lock()
    config = SyntheticConfig()

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(memory, "character_config_mutation_lock", lock)
    monkeypatch.setattr(persona, "get_config_manager", lambda: config)
    monkeypatch.setattr(persona, "get_session_manager", lambda: {})
    monkeypatch.setattr(persona, "get_init_one_catgirl", lambda: noop)
    monkeypatch.setattr(persona, "_clear_character_recent_history", noop)
    monkeypatch.setattr(persona, "get_persona_preset", lambda _preset: {"preset_id": "synthetic"})
    monkeypatch.setattr(persona, "_get_persona_payload_request_language", lambda *_args: None)
    monkeypatch.setattr(persona, "build_persona_override_payload", lambda *_args, **_kwargs: {
        "preset_id": "synthetic", "prompt_guidance": "synthetic style", "profile": {"性格": "new style"},
    })
    return persona, config, lock


async def invoke(persona, action):
    """Exercise the actual public route rather than a lock-shaped test helper."""
    if action == "put":
        return await persona.update_character_persona_selection("Synthetic", SyntheticRequest())
    return await persona.clear_character_persona_selection("Synthetic")


@pytest.mark.parametrize("action", ["put", "delete"])
async def test_persona_edit_reads_latest_configuration_after_profile_lock(persona_routes, action):
    """An admitted remote apply finishes before a local edit takes its snapshot."""
    persona, config, lock = persona_routes
    task = None
    try:
        async with lock:
            task = asyncio.create_task(invoke(persona, action))
            await asyncio.sleep(0)
            assert config.events == []
            assert not task.done()
            # This models the remote application that already owns the lock.
            config.data["猫娘"]["Synthetic"]["设定"] = "new synthetic remote setting"
        result = await task
        assert result["success"] is True
        raw = config.data["猫娘"]["Synthetic"]
        assert raw["设定"] == "new synthetic remote setting"
        if action == "put":
            assert raw["_reserved"]["persona_override"]["preset_id"] == "synthetic"
        else:
            assert "persona_override" not in raw.get("_reserved", {})
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("action", ["put", "delete"])
async def test_failed_persona_edit_rolls_back_before_next_profile_writer(persona_routes, monkeypatch, action):
    """A failed recent reset cannot restore its old snapshot over the next writer."""
    persona, config, lock = persona_routes
    before = copy.deepcopy(config.data)
    clearing, release, acquired = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def fail_recent(*_args):
        clearing.set()
        await release.wait()
        raise RuntimeError("synthetic_recent_failure")

    async def next_writer():
        async with lock:
            acquired.set()
            latest = await config.aload_characters()
            latest["猫娘"]["Synthetic"]["设定"] = "next synthetic remote setting"
            await config.asave_characters(latest)

    monkeypatch.setattr(persona, "_clear_character_recent_history", fail_recent)
    edit = asyncio.create_task(invoke(persona, action))
    writer = None
    try:
        await asyncio.wait_for(clearing.wait(), timeout=1)
        writer = asyncio.create_task(next_writer())
        await asyncio.sleep(0)
        assert not acquired.is_set()
        release.set()
        with pytest.raises(RuntimeError, match="synthetic_recent_failure"):
            await edit
        await writer
        expected = copy.deepcopy(before)
        expected["猫娘"]["Synthetic"]["设定"] = "next synthetic remote setting"
        assert config.data == expected
    finally:
        release.set()
        pending = [task for task in (edit, writer) if task is not None and not task.done()]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
