import asyncio
from types import SimpleNamespace

import pytest

from main_logic.watch_together import preparation


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
    manager = SimpleNamespace(current_game_speech_audio_runtime_signature=lambda: "voice")
    result = await preparation.prepare("video", manager, "cat")
    await asyncio.gather(*preparation.tasks)
    job = preparation.jobs[result["id"]]
    assert job["status"] == "error"
    assert job["stage"] == "Saving preparation failed"
    assert job["error"] == type(failure).__name__
    assert (tmp_path / "preparations" / result["id"] / "timeline.json").exists()
