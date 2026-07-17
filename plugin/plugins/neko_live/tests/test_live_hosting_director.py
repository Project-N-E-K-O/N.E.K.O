from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from typing import Any

from plugin.plugins.neko_live.core.live_hosting_director import LiveHostingDirector


class Audit:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str]] = []

    def record(self, event: str, message: str, **_kwargs: Any) -> None:
        self.rows.append((event, message))


class FakeRuntime:
    def __init__(self) -> None:
        self.config = SimpleNamespace(live_mode="solo_stream")
        self.audit = Audit()
        self.recorded: list[dict[str, Any]] = []
        self._idle_hosting_recent_beat_keys: deque[str] = deque(maxlen=10)
        self._idle_hosting_recent_beat_axes: deque[str] = deque(maxlen=5)
        self._idle_hosting_recent_beat_titles: deque[str] = deque(maxlen=10)
        self._idle_hosting_recent_reply_affordances: deque[str] = deque(maxlen=5)
        self._recent_host_material_families: deque[str] = deque(maxlen=12)
        self._idle_hosting_beat_index = 0
        self._beats = [
            {
                "key": "old-choice",
                "title": "old choice",
                "fun_axis": "choice",
                "shape": "tiny_choice",
                "reply_affordance": "viewer can pick one concrete side",
            },
            {
                "key": "fresh-room",
                "title": "keyboard patrol",
                "fun_axis": "object_scene",
                "shape": "light_tease",
                "reply_affordance": "viewer can answer with one small object",
            },
        ]

    def _idle_hosting_beat_candidates(self) -> list[dict[str, Any]]:
        return list(self._beats)

    @staticmethod
    def _idle_hosting_preferred_stage() -> str:
        return "column"

    @staticmethod
    def _idle_hosting_material_stage(material: dict[str, Any] | None) -> str:
        return LiveHostingDirector.idle_hosting_material_stage(material)

    @staticmethod
    def _idle_hosting_stage_ordered_candidates(candidates: list[dict[str, Any]], _stage: str) -> list[dict[str, Any]]:
        return candidates

    @staticmethod
    def _host_material_family(material: dict[str, Any] | None) -> str:
        return "choice_vote" if material and "choice" in str(material.get("key") or "") else "object_scene"

    @staticmethod
    def _recent_spent_output_families() -> set[str]:
        return set()

    @staticmethod
    def _is_similar_idle_hosting_beat_title(_title: str) -> bool:
        return False

    def record_result(self, result: Any) -> None:
        self.recorded.append(result)


def test_live_hosting_director_rotates_idle_beats_away_from_recent_family():
    runtime = FakeRuntime()
    runtime._recent_host_material_families.append("choice_vote")
    director = LiveHostingDirector(runtime)

    beat = director.next_idle_hosting_beat()

    assert beat["key"] == "fresh-room"
    assert beat["family"] == "object_scene"
    assert beat["idle_stage"] == "column"
    assert list(runtime._idle_hosting_recent_beat_keys) == ["fresh-room"]


def test_live_hosting_director_records_idle_skip_through_runtime():
    runtime = FakeRuntime()
    director = LiveHostingDirector(runtime)
    event = director.idle_hosting_event({"state": "idle"})

    result = director.record_idle_hosting_skip(event, "idle_hosting.not_idle")

    assert result.status == "skipped"
    assert runtime.recorded[-1].reason == "idle_hosting.not_idle"
    assert runtime.audit.rows[-1] == ("idle_hosting_skipped", "idle_hosting.not_idle")
