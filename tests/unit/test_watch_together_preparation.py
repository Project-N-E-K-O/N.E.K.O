import asyncio
from types import SimpleNamespace

import pytest

from main_logic.watch_together import preparation


@pytest.mark.asyncio
async def test_preparation_freezes_render_locale_for_director_and_tts(tmp_path, monkeypatch):
    from main_logic.core.game_speech_audio_cache import GAME_SPEECH_AUDIO_CACHE
    calls = []

    def identity(text, *, render_language):
        calls.append(render_language)
        return "audio-key", "voice"

    async def preload(lines, *, render_language):
        calls.append(render_language)
        return {"ok": True}

    manager = SimpleNamespace(user_language="en", _conversation_render_language="ja",
                              game_speech_audio_cache_identity=identity, preload_game_speech_audio=preload)

    class Engine:
        def __init__(self, root, synthesize, character, *, language):
            assert language == "ja"
            self.root, self.synthesize = root, synthesize

        async def prepare(self, job, *_args, **_kwargs):
            folder = self.root / job["id"]
            folder.mkdir(parents=True)
            manager._conversation_render_language = "en"
            await self.synthesize("test", folder / "audio.wav")
            job["status"] = "ready"

    monkeypatch.setattr(GAME_SPEECH_AUDIO_CACHE, "get", lambda key: [b'\x00\x00'])
    monkeypatch.setattr(preparation, "Engine", Engine)
    monkeypatch.setattr(preparation, "application_library", lambda: SimpleNamespace(root=tmp_path, import_sources=lambda *a, **kw: None))
    monkeypatch.setattr(preparation, "jobs", {})
    monkeypatch.setattr(preparation, "tasks", set())
    result = await preparation.prepare("video", manager, "cat")
    await asyncio.gather(*preparation.tasks)
    assert preparation.jobs[result["id"]]["status"] == "ready"
    assert calls == ["ja"] * 4


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError("disk error"), RuntimeError("database locked")])
async def test_import_failure_is_terminal_and_preserves_staging(tmp_path, monkeypatch, failure):
    def fail_import(_sources, **kwargs):
        assert kwargs["only_job"]
        assert kwargs["write_report"] is False
        raise failure

    class Engine:
        def __init__(self, root, *_args, **_kwargs):
            self.root = root

        async def prepare(self, job, *_args, **_kwargs):
            (self.root / job["id"]).mkdir(parents=True)
            job.update(status="ready", stage="Ready")

    monkeypatch.setattr(preparation, "Engine", Engine)
    monkeypatch.setattr(preparation, "application_library", lambda: SimpleNamespace(root=tmp_path, import_sources=fail_import))
    monkeypatch.setattr(preparation, "jobs", {})
    monkeypatch.setattr(preparation, "tasks", set())
    manager = SimpleNamespace(game_speech_audio_cache_identity=lambda *args, **kwargs: ("key", "voice"))
    result = await preparation.prepare("video", manager, "cat")
    await asyncio.gather(*preparation.tasks)
    job = preparation.jobs[result["id"]]
    assert job["status"] == "error"
    assert job["stage"] == "Saving preparation failed"
    assert job["error"] == type(failure).__name__
    assert (tmp_path / "preparations" / result["id"] / "timeline.json").exists()
