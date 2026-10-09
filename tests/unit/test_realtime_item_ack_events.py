"""Client item ids and item acknowledgements on OpenAI Realtime GA (#3350).

OpenAI GA rejects an ``item.id`` longer than 32 characters
(``string_above_max_length``), and it acknowledges a created item with
``conversation.item.added`` / ``.done`` instead of ``conversation.item.created``.
"""

import asyncio
import json
import re
from pathlib import Path

import pytest

from main_logic.omni_realtime_client import (
    CLIENT_ITEM_ID_MAX_LENGTH,
    OmniRealtimeClient,
    new_client_item_id,
)
from main_logic.omni_realtime_client._protocol_capabilities import (
    ITEM_ADDED_EVENT_TYPE,
    ITEM_CREATED_EVENT_TYPE,
    resolve_realtime_protocol_capabilities,
)
from main_logic.omni_realtime_client._response_arbiter import (
    RealtimeResponseArbiter,
)


_REPO_ROOT = Path(__file__).resolve().parents[2]


# -- item ids -----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["", "vis", "cbvis"])
def test_client_item_ids_fit_the_openai_limit(kind):
    ids = {new_client_item_id(kind) for _ in range(200)}

    assert len(ids) == 200
    assert CLIENT_ITEM_ID_MAX_LENGTH == 32
    for item_id in ids:
        assert len(item_id) <= CLIENT_ITEM_ID_MAX_LENGTH
        assert re.fullmatch(r"[a-z0-9_]+", item_id)


def test_a_kind_that_would_starve_the_random_part_is_refused():
    with pytest.raises(ValueError):
        new_client_item_id("x" * 20)


def test_no_call_site_hand_builds_an_item_id_from_a_full_uuid():
    # The old shape: ``f"item_neko_..._{uuid4().hex}"`` -- 42 to 58 chars.
    hand_built = re.compile(r"""["']id["']\s*:\s*\(?\s*f["'][^"']*\{uuid""")
    offenders = []
    for path in (_REPO_ROOT / "main_logic").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "item_neko_" in text or hand_built.search(text):
            offenders.append(str(path.relative_to(_REPO_ROOT)))
    assert offenders == []


# -- acknowledgement event names ----------------------------------------------


@pytest.mark.parametrize(
    ("api_type", "realtime_url", "accepts_added"),
    [
        ("openai", "wss://api.openai.com/v1/realtime", True),
        ("gpt", "wss://api.openai.com/v1/realtime", True),
        ("qwen", "wss://dashscope.aliyuncs.com/api-ws/v1/realtime", False),
        ("glm", "wss://open.bigmodel.cn/api/paas/v4/realtime", False),
        ("step", "wss://api.stepfun.com/v1/realtime", False),
        ("free", "wss://www.lanlan.app/core", False),
        ("free", "wss://lanlan.tech/core", False),
    ],
)
def test_item_ack_event_names_follow_the_route(api_type, realtime_url, accepts_added):
    capabilities = resolve_realtime_protocol_capabilities(api_type, realtime_url)

    assert ITEM_CREATED_EVENT_TYPE in capabilities.item_ack_event_types
    assert (ITEM_ADDED_EVENT_TYPE in capabilities.item_ack_event_types) is (
        accepts_added
    )


def test_one_item_acknowledged_under_both_names_counts_once():
    async def send(_event):
        return None

    arbiter = RealtimeResponseArbiter(send)
    item = {"id": "neko_abc", "type": "message", "role": "user"}

    arbiter.notify_item_created({"type": ITEM_CREATED_EVENT_TYPE, "item": item})
    arbiter.notify_item_created({"type": ITEM_ADDED_EVENT_TYPE, "item": item})
    assert arbiter._item_created_serial == 1

    arbiter.notify_item_created(
        {"type": ITEM_ADDED_EVENT_TYPE, "item": {**item, "id": "neko_def"}}
    )
    # An id-less acknowledgement cannot be matched, so it still counts.
    arbiter.notify_item_created({"type": ITEM_ADDED_EVENT_TYPE, "item": {}})
    arbiter.notify_item_created({"type": ITEM_ADDED_EVENT_TYPE, "item": {}})
    assert arbiter._item_created_serial == 4


class _ScriptedSocket:
    """Answers client events with a provider's server-event sequence."""

    def __init__(self, item_ack_frames):
        self._item_ack_frames = item_ack_frames
        self._inbox: asyncio.Queue[str] = asyncio.Queue()
        self.sent: list[tuple[float, dict]] = []

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        while True:
            yield await self._inbox.get()

    async def send(self, payload, *_args, **_kwargs):
        event = json.loads(payload)
        self.sent.append((asyncio.get_running_loop().time(), event))
        if event.get("type") == "conversation.item.create":
            item = {**event["item"], "status": "completed"}
            for frame_type in self._item_ack_frames:
                self._inbox.put_nowait(
                    json.dumps({"type": frame_type, "item": item})
                )
        elif event.get("type") == "response.create":
            response = {"id": "resp_1", "status": "completed", "output": []}
            for frame_type in ("response.created", "response.done"):
                self._inbox.put_nowait(
                    json.dumps({"type": frame_type, "response": response})
                )

    async def close(self, *_args, **_kwargs):
        return None


async def _item_to_response_create_gap(api_type, model, item_ack_frames):
    client = OmniRealtimeClient(
        "wss://test.example.invalid/realtime",
        "test-key",
        model=model,
        api_type=api_type,
    )
    socket = _ScriptedSocket(item_ack_frames)
    client.ws = socket
    client._fatal_error_occurred = False
    receiver = asyncio.create_task(client.handle_messages())
    try:
        ticket = await client.submit_external_text_turn("你好", turn_id="turn-1")
        result = await asyncio.wait_for(ticket.done, 5)
    finally:
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)

    sent = {event["type"]: (at, event) for at, event in socket.sent}
    item_at, item_event = sent["conversation.item.create"]
    response_at, _ = sent["response.create"]
    assert len(item_event["item"]["id"]) <= CLIENT_ITEM_ID_MAX_LENGTH
    return result, response_at - item_at


@pytest.mark.asyncio
async def test_openai_ga_item_added_releases_the_item_ack_without_waiting():
    result, gap = await _item_to_response_create_gap(
        "openai",
        "gpt-realtime-mini-2025-12-15",
        (ITEM_ADDED_EVENT_TYPE, "conversation.item.done"),
    )

    assert result.item_acknowledged is True
    # Before #3350 this was the full 1.5 s item-ack timeout on every turn.
    assert gap < 0.5


@pytest.mark.asyncio
async def test_openai_route_still_accepts_the_legacy_created_name():
    result, gap = await _item_to_response_create_gap(
        "openai",
        "gpt-realtime-mini-2025-12-15",
        (ITEM_CREATED_EVENT_TYPE, ITEM_ADDED_EVENT_TYPE, "conversation.item.done"),
    )

    assert result.item_acknowledged is True
    assert gap < 0.5


@pytest.mark.asyncio
async def test_qwen_route_acknowledges_with_created_as_before():
    result, gap = await _item_to_response_create_gap(
        "qwen",
        "qwen3-omni-flash-realtime",
        (ITEM_CREATED_EVENT_TYPE,),
    )

    assert result.item_acknowledged is True
    assert gap < 0.5
