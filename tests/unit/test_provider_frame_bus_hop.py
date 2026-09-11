# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The main_server -> agent_server hop for provider frames.

main_server holds the session but cannot write to the message plane: the
ingest credential is minted inside the plugin-server process, and the runner's
port fallback only lands in agent_server's own environment. So a frame the
provider already received rides the EXISTING session PUB channel and
``_on_session_event`` forwards it into the ``frames`` store.

These tests pin that there is exactly one event type and one dispatch branch,
that the branch actually runs (a helper nothing routes to is the failure mode
a source-level check would miss), and that the forward is skipped when no
plugin can read the result.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from app.agent_server import api_runtime as a
from main_logic import agent_event_bus as bus


# -- main_server side ---------------------------------------------------


def test_frame_rides_the_existing_session_pub_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """No fourth ZMQ port: the frame goes out on the session PUB socket."""
    sent: List[Dict[str, Any]] = []

    async def _capture(event: Dict[str, Any]) -> bool:
        sent.append(event)
        return True

    monkeypatch.setattr(bus, "publish_session_event_threadsafe", _capture)

    ok = asyncio.run(
        bus.publish_provider_frame_observed_best_effort(
            "lan",
            image_base64="SGVsbG8=",
            source="screen",
            captured_at=99.5,
            turn_id="turn-1",
            generation=0,
            mime="image/jpeg",
        )
    )

    assert ok is True
    assert len(sent) == 1
    event = sent[0]
    assert event["event_type"] == bus.PROVIDER_FRAME_OBSERVED_EVENT == "provider_frame_observed"
    assert event["image_base64"] == "SGVsbG8="
    assert event["source"] == "screen"
    assert event["captured_at"] == 99.5
    assert event["turn_id"] == "turn-1"
    # 0 is a real generation and must survive; a truthiness test would drop it.
    assert event["generation"] == 0
    assert event["lanlan_name"] == "lan"
    # One base64 copy on the wire, no raw-bytes twin.
    assert not any(isinstance(v, (bytes, bytearray)) for v in event.values())


def test_an_empty_frame_is_not_published(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: List[Dict[str, Any]] = []

    async def _capture(event: Dict[str, Any]) -> bool:
        sent.append(event)
        return True

    monkeypatch.setattr(bus, "publish_session_event_threadsafe", _capture)

    ok = asyncio.run(
        bus.publish_provider_frame_observed_best_effort(
            "lan", image_base64="", source="screen"
        )
    )

    assert ok is False
    assert sent == []


# -- agent_server side --------------------------------------------------


def _patch_publish_frame(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    from plugin.server.messaging import plane_bridge

    captured: List[Dict[str, Any]] = []

    def _capture(record: Dict[str, Any]) -> bool:
        captured.append(record)
        return True

    monkeypatch.setattr(plane_bridge, "publish_frame", _capture)
    return captured


def _enable_user_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    flags = dict(a.Modules.agent_flags or {})
    flags["user_plugin_enabled"] = True
    monkeypatch.setattr(a.Modules, "agent_flags", flags)


def _event(**overrides: Any) -> Dict[str, Any]:
    event: Dict[str, Any] = {
        "event_type": "provider_frame_observed",
        "event_id": "frame-1",
        "lanlan_name": "lan",
        "source": "screen",
        "image_base64": "SGVsbG8=",
        "mime": "image/jpeg",
        "captured_at": 42.0,
        "turn_id": "turn-1",
        "generation": 5,
    }
    event.update(overrides)
    return event


def test_forward_writes_the_frame_into_the_frames_store(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_frame(monkeypatch)

    assert a._forward_provider_frame(_event()) is True
    assert len(captured) == 1
    record = captured[0]
    assert record["image_base64"] == "SGVsbG8="
    assert record["source"] == "screen"
    assert record["captured_at"] == 42.0
    assert record["turn_id"] == "turn-1"
    assert record["generation"] == 5
    # The publisher's event_id is the frame identity end to end.
    assert record["id"] == "frame-1"


def test_forward_is_skipped_when_no_plugin_can_read_it(monkeypatch: pytest.MonkeyPatch) -> None:
    """With user plugins off there is no reader; do not retain screen frames."""
    flags = dict(a.Modules.agent_flags or {})
    flags["user_plugin_enabled"] = False
    monkeypatch.setattr(a.Modules, "agent_flags", flags)
    captured = _patch_publish_frame(monkeypatch)

    assert a._forward_provider_frame(_event()) is False
    assert captured == []


def test_forward_ignores_an_event_without_an_image(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_frame(monkeypatch)

    assert a._forward_provider_frame(_event(image_base64="")) is False
    assert a._forward_provider_frame(_event(image_base64=None)) is False
    assert captured == []


def test_session_event_dispatch_routes_provider_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """The branch must be wired: a helper nothing routes to forwards nothing."""
    _enable_user_plugins(monkeypatch)
    captured = _patch_publish_frame(monkeypatch)

    asyncio.run(a._on_session_event(_event()))

    assert len(captured) == 1
    assert captured[0]["image_base64"] == "SGVsbG8="
