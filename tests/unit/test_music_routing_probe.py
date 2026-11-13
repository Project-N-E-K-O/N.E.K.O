"""The live probe must report failed cases without losing the rest of a run."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import probe_music_routing as probe


@pytest.mark.parametrize("track", [" | ", "Song | ", " | Artist", "No separator", "x" * 99 + " | Band"])
def test_probe_rejects_curated_tracks_that_player_would_drop(track):
    case = probe.case("curate", "Play some music", "curate")
    decision = {
        "has_task": True, "can_execute": True,
        "plugin_id": "netease_music", "entry_id": "playlist",
        "plugin_args": {"action": "curate", "tracks": [track] * 6},
    }
    assert probe.check(case, decision, probe.load_plugins(Path(__file__).resolve().parents[2]))


@pytest.mark.asyncio
async def test_case_exception_still_closes_clients_and_writes_complete_summary(tmp_path, monkeypatch):
    from brain import task_executor
    from utils import config_manager

    closed = []

    class Executor:
        def __init__(self):
            self._cleanup_tasks = set()

        def _get_llm(self, **kwargs):
            raise AssertionError("The test must never call a model")

        def _format_messages(self, messages):
            return messages[-1]["text"]

        async def _assess_user_plugin(self, conversation, plugins, **kwargs):
            if conversation == "bad":
                raise UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed")
            return task_executor.UserPluginDecision(
                has_task=False, can_execute=False, task_description="",
                plugin_id=None, plugin_args=None, reason="\ud800",
            )

        def _close_all_llms(self):
            closed.append(self)

    monkeypatch.setattr(task_executor, "DirectTaskExecutor", Executor)
    monkeypatch.setattr(config_manager, "get_config_manager", lambda: SimpleNamespace(
        get_model_api_config=lambda tier: {"model": "fake-offline-model"},
    ))
    monkeypatch.setattr(probe, "CASES", [
        probe.case("raises", "bad", plugin=None),
        probe.case("surrogate_result", "good", plugin=None),
    ])
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo)
    output = tmp_path / "report"
    options = SimpleNamespace(
        repo=repo, output=output, repeat=1, concurrency=2, tier="summary",
        only="", pipeline=False, lang="zh",
    )

    assert await probe.run(options) == 1

    assert len(closed) == 2
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    results = [json.loads(line) for line in (output / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    assert (summary["total"], summary["passed"]) == (2, 1)
    assert summary["failed"] == [{"id": "raises", "repeat": 1, "errors": ["Case raised UnicodeEncodeError"]}]
    assert len(results) == 2
    good = next(result for result in results if result["case"]["id"] == "surrogate_result")
    assert good["decision"]["reason"] == "\ud800"
    assert good["errors"] == []
