from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.plugins.neko_live.core.contracts import LiveEvent
from plugin.plugins.neko_live.core.event_bus import EventBus
from plugin.plugins.neko_live.modules.live_audience_session import LiveAudienceSessionModule
from plugin.plugins.neko_live.stores.audit_store import AuditStore


@pytest.mark.asyncio
async def test_live_audience_session_counts_provider_events_and_pushed_outputs() -> None:
    bus = EventBus(AuditStore())
    module = LiveAudienceSessionModule()
    module._now = lambda: 1_700_000_000.0
    await module.setup(SimpleNamespace(event_bus=bus))
    module.start_session()

    bus.publish(
        "danmaku",
        LiveEvent(
            type="danmaku",
            uid="42",
            payload={"nickname": "viewer", "text": "raw message must not be projected"},
            ts=1_700_000_001.0,
        ),
    )
    bus.publish(
        "gift",
        LiveEvent(type="gift", uid="42", payload={"nickname": "viewer"}, ts=1_700_000_002.0),
    )
    bus.emit("result", {"status": "pushed", "identity": {"uid": "42"}})
    bus.emit("result", {"status": "skipped", "identity": {"uid": "42"}})

    snapshot = module.snapshot()
    assert snapshot["active"] is True
    assert snapshot["interaction_viewer_count"] == 1
    assert snapshot["danmaku_count"] == 1
    assert snapshot["support_event_count"] == 1
    assert snapshot["neko_output_count"] == 1
    assert snapshot["viewers"] == [
        {
            "viewer_key": snapshot["viewers"][0]["viewer_key"],
            "nickname": "viewer",
            "interaction_count": 2,
            "danmaku_count": 1,
            "support_event_count": 1,
            "neko_reply_count": 1,
            "last_event_type": "gift",
            "last_interaction_at": "2023-11-14T22:13:22+00:00",
        }
    ]
    public_text = repr(snapshot)
    assert "raw message must not be projected" not in public_text
    assert "uid" not in snapshot["viewers"][0]

    await module.teardown()


def test_live_audience_session_retains_finished_summary_and_resets_on_next_start() -> None:
    module = LiveAudienceSessionModule()
    now = iter((1_700_000_000.0, 1_700_000_030.0, 1_700_000_060.0))
    module._now = lambda: next(now)
    module.start_session()
    module._on_live_event(
        LiveEvent(type="danmaku", uid="1", payload={"nickname": "one"}, ts=1_700_000_010.0)
    )
    module.finish_session()

    finished = module.snapshot()
    assert finished["active"] is False
    assert finished["has_session"] is True
    assert finished["danmaku_count"] == 1
    assert finished["ended_at"] == "2023-11-14T22:13:50+00:00"

    module.start_session()
    restarted = module.snapshot()
    assert restarted["active"] is True
    assert restarted["danmaku_count"] == 0
    assert restarted["viewers"] == []
    assert restarted["ended_at"] == ""


def test_live_audience_session_caps_public_rows_and_unique_viewer_memory() -> None:
    module = LiveAudienceSessionModule()
    module.start_session()

    for index in range(5_010):
        module._on_live_event(
            LiveEvent(type="danmaku", uid=str(index + 1), payload={"nickname": f"viewer-{index + 1}"})
        )

    snapshot = module.snapshot()
    assert snapshot["interaction_viewer_count"] == 5_000
    assert snapshot["interaction_viewer_count_capped"] is True
    assert len(snapshot["viewers"]) == 30
    assert len(module._viewers) == 100
