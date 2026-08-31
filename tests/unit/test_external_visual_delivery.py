import asyncio
import json
import time
from pathlib import Path
from types import MethodType
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.omni_realtime_client import _responses as responses_module
from main_logic.omni_realtime_client import (
    ImageStageResult,
    MultimodalTurnDelivery,
    OmniRealtimeClient,
    TurnDetectionMode,
)



_REAL_FIT_IMAGES = responses_module.fit_images_to_turn_budget

DUMMY_IMAGE_B64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsL"
    "DBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "2wBDAQkJCQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QA"
    "HwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAFBABAAAAAAAAAAAAAA"
    "AAAAAACf/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AE0A/9k="
)


def _make_client(
    api_type: str,
    model: str,
    *,
    turn_admission_lock: asyncio.Lock | None = None,
) -> OmniRealtimeClient:
    return OmniRealtimeClient(
        base_url="wss://test.example.invalid/realtime",
        api_key="test-key",
        model=model,
        api_type=api_type,
        turn_detection_mode=TurnDetectionMode.SERVER_VAD,
        turn_admission_lock=turn_admission_lock,
    )


def _wire_completed_response_transport(client: OmniRealtimeClient) -> list[dict]:
    sent: list[dict] = []

    async def send_event(_self, event, **_kwargs):
        copied = dict(event)
        sent.append(copied)
        arbiter = _self._response_arbiter
        if event["type"] == "conversation.item.create":
            arbiter.notify_item_created(
                {
                    "type": "conversation.item.created",
                    "item": {
                        "id": event["item"]["id"],
                        "type": "message",
                        "role": "user",
                    },
                }
            )
        elif event["type"] == "response.create":
            arbiter.notify_response_created({"type": "response.created"})
            arbiter.notify_response_terminal({"type": "response.done"})
        return True

    client.send_event = MethodType(send_event, client)
    client._response_arbiter._send_event = client.send_event
    return sent


@pytest.mark.parametrize(
    ("api_type", "model", "expected"),
    [
        ("openai", "gpt-4o-realtime", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("gpt", "gpt-4o-realtime", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("gemini", "gemini-2.5-flash-native-audio", MultimodalTurnDelivery.DIRECT_ATOMIC),
        ("qwen", "qwen3-omni-flash-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("glm", "glm-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("step", "step-audio-2-mini", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("grok", "grok-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("free", "free-model", MultimodalTurnDelivery.HANDOFF_REQUIRED),
        ("local", "custom-realtime", MultimodalTurnDelivery.HANDOFF_REQUIRED),
    ],
)
def test_multimodal_turn_delivery_is_provider_adapter_capability(
    api_type,
    model,
    expected,
):
    client = _make_client(api_type, model)

    assert client.get_multimodal_turn_delivery() is expected


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_is_one_atomic_user_item():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)
    client._analyze_image_with_vision_model = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gpt")
    ticket = await client.submit_multimodal_turn(
        "图片里是什么？",
        DUMMY_IMAGE_B64,
        turn_id="turn-gpt",
    )

    assert ticket is not None
    assert [event["type"] for event in sent] == [
        "conversation.item.create",
        "response.create",
    ]
    content = sent[0]["item"]["content"]
    assert content == [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64," + DUMMY_IMAGE_B64,
        },
        {"type": "input_text", "text": "图片里是什么？"},
    ]
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_carries_the_sampled_span_in_one_item():
    """The sampled span shares one user item and still triggers one reply."""
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-span")
    await client.submit_multimodal_turn(
        "这是什么？",
        (DUMMY_IMAGE_B64, DUMMY_IMAGE_B64, DUMMY_IMAGE_B64),
        turn_id="turn-span",
    )

    assert [event["type"] for event in sent] == [
        "conversation.item.create",
        "response.create",
    ]
    content = sent[0]["item"]["content"]
    assert [part["type"] for part in content] == [
        "input_image",
        "input_image",
        "input_image",
        "input_text",
    ]
    await client.close()


@pytest.mark.asyncio
async def test_gpt_multimodal_turn_caps_frames_at_the_per_turn_budget():
    """Provider-side floor: an item written into the conversation is final."""
    from config import MAX_MULTIMODAL_TURN_IMAGES

    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    sent = _wire_completed_response_transport(client)

    await client.prepare_external_voice_turn(turn_id="turn-flood")
    await client.submit_multimodal_turn(
        "这是什么？",
        tuple(DUMMY_IMAGE_B64 for _ in range(12)),
        turn_id="turn-flood",
    )

    content = sent[0]["item"]["content"]
    images = [part for part in content if part["type"] == "input_image"]
    assert len(images) == MAX_MULTIMODAL_TURN_IMAGES
    await client.close()


@pytest.mark.asyncio
async def test_new_external_turn_rejects_superseded_multimodal_ticket():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    _wire_completed_response_transport(client)
    arbiter = client._response_arbiter
    real_enqueue = arbiter.enqueue
    old_ticket_queued = asyncio.Event()
    release_enqueue_return = asyncio.Event()

    async def enqueue_then_pause_return(*args, **kwargs):
        ticket = await real_enqueue(*args, **kwargs)
        old_ticket_queued.set()
        await release_enqueue_return.wait()
        return ticket

    arbiter.enqueue = enqueue_then_pause_return
    await client.prepare_external_voice_turn(turn_id="turn-old")
    old_submit = asyncio.create_task(
        client.submit_multimodal_turn(
            "旧问题",
            DUMMY_IMAGE_B64,
            turn_id="turn-old",
        )
    )
    await old_ticket_queued.wait()

    await client.prepare_external_voice_turn(turn_id="turn-new")
    release_enqueue_return.set()

    with pytest.raises(RuntimeError, match="admission rejected"):
        await old_submit
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_gemini_multimodal_turn_is_one_content_with_image_and_text():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gemini")
    result = await client.submit_multimodal_turn(
        "看一下这张图",
        DUMMY_IMAGE_B64,
        turn_id="turn-gemini",
    )

    assert result is None
    session.send_client_content.assert_awaited_once()
    kwargs = session.send_client_content.await_args.kwargs
    assert kwargs["turn_complete"] is True
    assert len(kwargs["turns"]) == 1
    content = kwargs["turns"][0]
    assert content.role == "user"
    assert len(content.parts) == 2
    assert bytes(content.parts[0].inline_data.data)
    assert content.parts[0].inline_data.mime_type == "image/jpeg"
    assert content.parts[1].text == "看一下这张图"
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_gemini_turn_drops_frames_lost_while_fitting_the_image_budget():
    """Losing visual ownership during compression degrades to text-only.

    Fitting an over-budget turn runs the compressor on a worker thread, which
    is a real yield point. A successor utterance can complete
    _begin_core_multimodal_turn (which synchronously invalidates this record),
    prepare_external_voice_turn and handle_interruption inside that window --
    the two paths do not exclude each other, since neither takes the other's
    lock. Sending the frames anyway hands the provider images that no longer
    belong to this turn, and Core never learns about it, so the existing
    "degrade to text-only" exit is never reached.

    The sentence still goes out: dropping frames may only downgrade a turn to
    text-only, never discard what the user said.
    """
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    owned = [True]

    async def _fit_and_lose_ownership(images, _budget):
        # 压缩这一步是真实让出点：后继发声在这里拿走了视觉所有权。
        owned[0] = False
        return list(images), {
            "original_count": len(images),
            "final_count": len(images),
            "sampled": False,
            "compressed": True,
            "dropped": 0,
        }

    responses_module.fit_images_to_turn_budget = _fit_and_lose_ownership
    try:
        await client.prepare_external_voice_turn(turn_id="turn-lost")
        await client.submit_multimodal_turn(
            "看一下这张图",
            DUMMY_IMAGE_B64,
            turn_id="turn-lost",
            visual_still_owned=lambda: owned[0],
        )
    finally:
        responses_module.fit_images_to_turn_budget = _REAL_FIT_IMAGES

    session.send_client_content.assert_awaited_once()
    content = session.send_client_content.await_args.kwargs["turns"][0]
    # 帧被丢掉了……
    assert all(part.inline_data is None for part in content.parts)
    # ……但用户那句话照送。
    assert any(part.text == "看一下这张图" for part in content.parts)
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_openai_turn_drops_frames_lost_while_shrinking_the_item():
    """Dual of the Gemini path for the WebSocket branch.

    An oversized item is recompressed on a worker thread -- a real yield point.
    The successor's _begin_core_multimodal_turn() invalidates this record
    synchronously, but its prepare_external_voice_turn() may still be blocked on
    the shared turn-admission lock and not yet have updated
    _external_voice_turn_pause_id, so the ticket's admission_check still passes
    on the unchanged pause id and superseded frames reach provider history.

    Images are dropped in place rather than letting admission_check reject the
    whole ticket: that rejection happens AFTER the item is committed and needs
    an unconfirmed compensating delete (issue #2982).
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    owned = [True]
    captured: list = []

    async def _shrink_and_lose_ownership(_fn, item_event, _payload):
        # 重压这一步是真实让出点：后继发声在这里拿走了视觉所有权。
        owned[0] = False
        return "shrunk"

    async def _fake_enqueue(**kwargs):
        event = kwargs["events_before_response"][0]
        # 忠实模拟 arbiter：提交前会调 pre_commit。所有权是在重压那一步丢的，
        # 早于 enqueue 的那次降级还持有，所以这条路要靠 pre_commit 兜住。
        kwargs["pre_commit"](event)
        captured.append(event)
        # 用已完成的 future 而不是 asyncio.sleep(0)：submit_multimodal_turn 只
        # await ticket.sent，`done` 那个协程永远不会被等待，会留一条
        # RuntimeWarning（warnings-as-errors 下直接失败）。
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    _arbiter = SimpleNamespace(
        enqueue=_fake_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda: None,
        cancel_ticket=AsyncMock(),
        cancel_current=AsyncMock(),
    )
    client._ensure_response_arbiter = lambda: _arbiter
    asyncio_to_thread = asyncio.to_thread
    asyncio.to_thread = _shrink_and_lose_ownership
    _real_limit = responses_module.OMNI_WS_FRAME_LIMIT_BYTES
    # 把帧上限压到必然超限，逼它走重压那条路（那才是本用例要覆盖的让出点）。
    responses_module.OMNI_WS_FRAME_LIMIT_BYTES = 8
    try:
        await client.prepare_external_voice_turn(turn_id="turn-ws")
        assert owned[0] is True
        await client.submit_multimodal_turn(
            "看一下这张图",
            [DUMMY_IMAGE_B64] * 3,
            turn_id="turn-ws",
            visual_still_owned=lambda: owned[0],
        )
    finally:
        asyncio.to_thread = asyncio_to_thread
        responses_module.OMNI_WS_FRAME_LIMIT_BYTES = _real_limit

    assert owned[0] is False, "夹具没走到重压那一步"
    assert captured, "item 没被送进 arbiter"
    content = captured[0]["item"]["content"]
    # 帧被摘掉了……
    assert all(part["type"] != "input_image" for part in content)
    # ……但用户那句话照送。
    assert any(part.get("text") == "看一下这张图" for part in content)
    await client.close()


@pytest.mark.asyncio
async def test_lost_ownership_downgrades_before_the_oversize_check_fails_the_turn():
    """Frames already forfeited must not fail the whole turn on size.

    The oversize path deliberately raises RealtimeImagePayloadTooLargeError so
    Core can fail the turn closed rather than silently dropping images. But if
    visual ownership is already gone those frames were never going to be sent,
    and raising there kills a turn that should merely have degraded to
    text-only -- the sentence would be lost, which this PR forbids outright.

    So the downgrade runs before the size check.
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()
    captured: list = []

    async def _fake_enqueue(**kwargs):
        event = kwargs["events_before_response"][0]
        kwargs["pre_commit"](event)
        captured.append(event)
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    _arbiter = SimpleNamespace(
        enqueue=_fake_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda: None,
        cancel_ticket=AsyncMock(),
        cancel_current=AsyncMock(),
    )
    client._ensure_response_arbiter = lambda: _arbiter

    # 固定住"压不下去"这个前提：抛错的那条路要求 _try_shrink_image_payload
    # 返回 None。不固定的话重压可能碰巧成功，用例就走不到要证伪的地方——
    # 第一版正是这样，把降级去掉它也照过。
    client._try_shrink_image_payload = lambda *_a: None
    _real_limit = responses_module.OMNI_WS_FRAME_LIMIT_BYTES
    # 上限取在「带 3 张图必然超、摘掉图之后不超」之间，这样才测得到"先降级、
    # 后判大小"这个顺序。
    responses_module.OMNI_WS_FRAME_LIMIT_BYTES = 3 * len(DUMMY_IMAGE_B64)
    try:
        await client.prepare_external_voice_turn(turn_id="turn-oversize")
        await client.submit_multimodal_turn(
            "看一下这张图",
            [DUMMY_IMAGE_B64] * 3,
            turn_id="turn-oversize",
            visual_still_owned=lambda: False,
        )
    finally:
        responses_module.OMNI_WS_FRAME_LIMIT_BYTES = _real_limit

    assert captured, "整轮被超限判死了——降级应当跑在大小检查之前"
    content = captured[0]["item"]["content"]
    assert all(part["type"] != "input_image" for part in content)
    assert any(part.get("text") == "看一下这张图" for part in content)
    await client.close()


@pytest.mark.asyncio
async def test_send_event_downgrades_inside_the_transport_critical_section():
    """The last await before the write is the send semaphore.

    send_event() serializes the payload only after acquiring _send_semaphore,
    so an item queued behind another transport write can lose visual ownership
    while waiting. Running the downgrade inside that critical section, before
    json.dumps, means the mutation is picked up without re-serializing.

    Asserted on what actually reached the socket, not on the dict we passed in:
    a hook that ran after serialization would leave the images on the wire.
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    written: list = []

    class _Sock:
        async def send(self, payload):
            written.append(payload)

    client.ws = _Sock()

    event = {
        "type": "conversation.item.create",
        "item": {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "data:image/jpeg;base64,x"},
                {"type": "input_text", "text": "look"},
            ],
        },
    }

    def _strip_images(ev):
        item = ev["item"]
        item["content"] = [
            part for part in item["content"] if part.get("type") != "input_image"
        ]

    assert await client.send_event(event, pre_send=_strip_images) is True

    assert written, "什么都没写出去"
    payload = json.loads(written[0])
    content = payload["item"]["content"]
    assert all(part["type"] != "input_image" for part in content)
    assert any(part.get("text") == "look" for part in content)
    await client.close()


@pytest.mark.asyncio
async def test_openai_turn_drops_frames_lost_between_enqueue_and_dispatch():
    """The pre-enqueue check cannot cover the arbiter's own waits.

    Between enqueue() and the transport write the arbiter still waits on an
    active response and on the send semaphore. A successor onset invalidates
    the record synchronously while its prepare_external_voice_turn() is blocked
    on the Core swap/admission lock, so the ticket's admission_check -- which
    reads only _external_voice_turn_pause_id -- still passes and the superseded
    images get committed.

    The pre_commit hook runs immediately before _worker_send, which is the
    actual transport boundary. It downgrades the item rather than rejecting the
    ticket: a rejection happens AFTER the item is committed and needs an
    unconfirmed compensating delete (issue #2982).
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client.handle_interruption = AsyncMock()

    owned = [True]
    captured: list = []

    enqueue_kwargs: list = []

    async def _fake_enqueue(**kwargs):
        # 模拟 arbiter：enqueue 返回之后、真正提交之前还有等待，后继回合就在
        # 这段窗口里拿走了视觉所有权。
        enqueue_kwargs.append(kwargs)
        event = kwargs["events_before_response"][0]
        owned[0] = False
        kwargs["pre_commit"](event)
        captured.append(event)
        loop = asyncio.get_running_loop()
        sent, done = loop.create_future(), loop.create_future()
        sent.set_result(None)
        done.set_result(None)
        return SimpleNamespace(sent=sent, done=done)

    _arbiter = SimpleNamespace(
        enqueue=_fake_enqueue,
        resume_dispatch=lambda: None,
        pause_dispatch=lambda: None,
        cancel_ticket=AsyncMock(),
        cancel_current=AsyncMock(),
    )
    client._ensure_response_arbiter = lambda: _arbiter

    await client.prepare_external_voice_turn(turn_id="turn-late")
    # 前提自证：送进 arbiter 的那一刻仍然持有所有权（否则测的是上一条的窗口）。
    assert owned[0] is True
    await client.submit_multimodal_turn(
        "看一下这张图",
        DUMMY_IMAGE_B64,
        turn_id="turn-late",
        visual_still_owned=lambda: owned[0],
    )

    assert captured, "item 没被送进 arbiter"
    content = captured[0]["item"]["content"]
    assert all(part["type"] != "input_image" for part in content)
    assert any(part.get("text") == "看一下这张图" for part in content)

    # 还要证明**第三道**闸门接上了：ticket 自带的 event_sender 必须把同一个降级
    # 函数带进 send_event 的临界区（那里等信号量，所有权同样可能翻转）。只验
    # pre_commit 的话，这条接线断掉也发现不了。
    sender = enqueue_kwargs[0].get("event_sender")
    assert sender is not None, "ticket 没带 event_sender，传输临界区那道闸门没接上"
    written: list = []

    class _Sock:
        async def send(self, payload):
            written.append(payload)

    client.ws = _Sock()
    await sender({
        "type": "conversation.item.create",
        "item": {
            "role": "user",
            "content": [
                {"type": "input_image", "image_url": "data:image/jpeg;base64,x"},
                {"type": "input_text", "text": "看一下这张图"},
            ],
        },
    })
    assert written, "event_sender 没把事件写出去"
    sent_content = json.loads(written[0])["item"]["content"]
    assert all(part["type"] != "input_image" for part in sent_content)
    await client.close()


@pytest.mark.asyncio
async def test_gemini_turn_drops_frames_lost_during_the_quarantine_wait():
    """Ownership can still flip after the budget fit, inside the quarantine wait.

    _submit_external_gemini_turn() awaits _await_gemini_external_quarantine()
    when a previous turn's token is still outstanding, and a successor can
    invalidate this record inside that wait. Checking only after the budget fit
    therefore misses it -- and checking there at all only ran when the budget
    actually trimmed something. The check belongs immediately before the SDK
    send.

    The sentence still goes out; only the frames are dropped.
    """
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    owned = [True]
    quarantine_waits = [0]

    async def _lose_ownership_on_the_submit_wait():
        # 隔离等待会被调两次：prepare_external_voice_turn 一次，
        # _submit_external_gemini_turn 头部一次。只在**第二次**丢所有权，
        # 否则 submit_multimodal_turn 还没开始就已经失去，这条用例就退化成
        # "复查放哪都能过"，钉不住它要钉的窗口。
        quarantine_waits[0] += 1
        if quarantine_waits[0] >= 2:
            owned[0] = False

    client._await_gemini_external_quarantine = _lose_ownership_on_the_submit_wait

    await client.prepare_external_voice_turn(turn_id="turn-quarantine")
    # 让 _submit_external_gemini_turn 头部走进隔离等待那条分支。
    #
    # ⚠️ 必须挂在 prepare **之后**。prepare 里的
    # _start_gemini_external_submit_quarantine() 会为一个已存在的 token 起后台
    # 隔离任务，那个任务一被调度就把 token settle 成 None，第二次隔离等待的分支
    # 就再也进不去了。原来挂在 prepare 之前也能过，只是因为 submit 那条路上恰好
    # 没有让出点让后台任务跑起来——那是巧合，不是判据：图片预算那一步现在无条件
    # 走一次 asyncio.to_thread（把每张图归一到模型档位），多出的这个 await 就让
    # 后台任务抢先跑完，用例随即翻车。挂在 prepare 之后，前提由用例自己保证，与
    # 被测路径上有几个 await 无关。
    client._gemini_external_outcome_token = object()
    # 前提自证：进入被测函数时仍然持有所有权。
    assert owned[0] is True
    await client.submit_multimodal_turn(
        "看一下这张图",
        DUMMY_IMAGE_B64,
        turn_id="turn-quarantine",
        visual_still_owned=lambda: owned[0],
    )

    # 前提自证：所有权确实是在**第二次**（submit 内部那次）等待里丢的。
    assert quarantine_waits[0] >= 2
    assert owned[0] is False
    session.send_client_content.assert_awaited_once()
    content = session.send_client_content.await_args.kwargs["turns"][0]
    assert all(part.inline_data is None for part in content.parts)
    assert any(part.text == "看一下这张图" for part in content.parts)
    await client.close()


@pytest.mark.asyncio
async def test_gemini_multimodal_turn_carries_the_sampled_span_in_one_content():
    """The sampled span shares one Content and still triggers one reply."""
    from config import MAX_MULTIMODAL_TURN_IMAGES

    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()

    await client.prepare_external_voice_turn(turn_id="turn-gemini-span")
    await client.submit_multimodal_turn(
        "看一下这张图",
        tuple(DUMMY_IMAGE_B64 for _ in range(9)),
        turn_id="turn-gemini-span",
    )

    session.send_client_content.assert_awaited_once()
    kwargs = session.send_client_content.await_args.kwargs
    assert len(kwargs["turns"]) == 1
    parts = kwargs["turns"][0].parts
    # Provider 侧独立兜底，多余的丢弃。
    assert len(parts) == MAX_MULTIMODAL_TURN_IMAGES + 1
    assert all(part.inline_data is not None for part in parts[:-1])
    assert parts[-1].text == "看一下这张图"
    await client.close()


@pytest.mark.parametrize(
    ("api_type", "model"),
    [
        ("qwen", "qwen3-omni-flash-realtime"),
        ("glm", "glm-realtime"),
        ("step", "step-audio-2-mini"),
        ("grok", "grok-realtime"),
        ("free", "free-model"),
        ("local", "custom-realtime"),
    ],
)
@pytest.mark.asyncio
async def test_unsupported_realtime_multimodal_turn_fails_closed_without_send(
    api_type,
    model,
):
    client = _make_client(api_type, model)
    client.ws = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock()

    with pytest.raises(RuntimeError, match="requires VLM handoff"):
        await client.submit_multimodal_turn(
            "不要退化成纯文本",
            DUMMY_IMAGE_B64,
            turn_id="turn-handoff",
        )

    client.ws.send.assert_not_awaited()
    client._analyze_image_with_vision_model.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_multimodal_turn_rejects_invalid_image_before_provider_send():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()

    with pytest.raises(ValueError, match="valid base64"):
        await client.submit_multimodal_turn(
            "这张图坏了",
            "not-base64!",
            turn_id="turn-invalid",
        )

    client.ws.send.assert_not_awaited()
    await client.close()


def test_stage_multimodal_frame_only_updates_raw_cache():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    client._analyze_image_with_vision_model = AsyncMock()

    result = client.stage_multimodal_frame(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="screen-1",
        captured_at=10.0,
    )

    assert result == ImageStageResult(
        accepted=True,
        mode="staged",
        generation=1,
    )
    assert client._latest_image_b64 == DUMMY_IMAGE_B64
    assert client._latest_image_captured_at == 10.0
    assert client._latest_image_source == "screen"
    assert client._latest_image_request_id == "screen-1"
    client._analyze_image_with_vision_model.assert_not_called()


def test_stage_multimodal_frame_rejects_stale_capture():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    first = client.stage_multimodal_frame(DUMMY_IMAGE_B64, captured_at=20.0)
    stale = client.stage_multimodal_frame(DUMMY_IMAGE_B64, captured_at=19.0)

    assert first.accepted is True
    assert stale == ImageStageResult(
        accepted=False,
        mode="staged",
        generation=1,
        rejection_reason="stale_frame",
    )
    assert client._latest_image_captured_at == 20.0


@pytest.mark.parametrize(
    ("api_type", "model"),
    [
        ("openai", "gpt-4o-realtime"),
        ("gemini", "gemini-2.5-flash-native-audio"),
    ],
)
@pytest.mark.asyncio
async def test_callback_owned_image_bypasses_raw_frame_fence_only(
    api_type,
    model,
):
    client = _make_client(api_type, model)
    provider = AsyncMock()
    client.ws = provider
    if api_type == "gemini":
        client._gemini_session = provider
    client.block_raw_visual_delivery()

    ambient = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        bypass_rate_limit=True,
    )
    proactive = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="proactive",
        bypass_rate_limit=True,
        cache_latest=False,
    )
    callback_cached = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        bypass_rate_limit=True,
    )
    callback = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="callback",
        bypass_rate_limit=True,
        cache_latest=False,
    )

    assert ambient.accepted is False
    assert ambient.rejection_reason == "raw_visual_delivery_blocked"
    assert proactive.accepted is False
    assert proactive.rejection_reason == "raw_visual_delivery_blocked"
    assert callback_cached.accepted is False
    assert callback_cached.rejection_reason == "raw_visual_delivery_blocked"
    assert callback.accepted is True
    assert client._latest_image_b64 is None
    if api_type == "gemini":
        provider.send_realtime_input.assert_awaited_once()
    else:
        provider.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_callback_fence_bypass_still_rejects_visual_mode_change_before_send():
    client = _make_client("openai", "gpt-4o-realtime")
    client.ws = AsyncMock()
    client.block_raw_visual_delivery()
    client._send_semaphore = asyncio.Semaphore(1)
    await client._send_semaphore.acquire()
    sending = asyncio.create_task(
        client.stream_image(
            DUMMY_IMAGE_B64,
            source="callback",
            bypass_rate_limit=True,
            cache_latest=False,
        )
    )
    await asyncio.sleep(0)

    client.set_visual_delivery_mode("external_description")
    client._send_semaphore.release()
    result = await sending

    assert result.accepted is False
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_loud_pcm_publishes_activity_before_provider_admission():
    admission_lock = asyncio.Lock()
    client = _make_client(
        "openai",
        "gpt-4o-realtime",
        turn_admission_lock=admission_lock,
    )
    client.ws = AsyncMock()
    client._resample_uplink = lambda audio: audio
    client._user_recent_activity_time = 0.0
    await admission_lock.acquire()
    loud_pcm = (1_000).to_bytes(2, "little", signed=True) * 512

    streaming = asyncio.create_task(client.stream_audio(loud_pcm))
    await asyncio.sleep(0)

    assert client._user_recent_activity_time > 0.0
    assert not streaming.done()
    client.ws.send.assert_not_awaited()

    admission_lock.release()
    await streaming
    client.ws.send.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_step_legacy_one_shot_annotation_remains_outside_asr_routing_scope():
    client = _make_client("step", "step-audio-2-mini")
    client.ws = AsyncMock()
    client._analyze_image_with_vision_model = AsyncMock(
        return_value="legacy Step description"
    )

    result = await client.stream_image(
        DUMMY_IMAGE_B64,
        cache_latest=False,
        bypass_rate_limit=True,
        source="callback",
        request_id="callback-1",
    )

    assert result == ImageStageResult(
        accepted=True,
        mode="external_description",
        generation=0,
        description="legacy Step description",
    )
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64,
        update_turn_state=False,
    )
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_proactive_prompt_ephemeral_keeps_environment_annotation_exception(
    monkeypatch,
):
    client = _make_client("step", "step-audio-2-mini")
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    analyze_image = AsyncMock(return_value="屏幕上显示番茄钟结束提醒")
    monkeypatch.setattr(
        "utils.screenshot_utils.analyze_image_with_vision_model",
        analyze_image,
    )
    real_analyze = client._analyze_image_with_vision_model
    client._analyze_image_with_vision_model = AsyncMock(wraps=real_analyze)
    injected: dict = {}

    async def inject_text(text, **kwargs):
        injected["text"] = text
        injected.update(kwargs)
        kwargs["on_completed"]()
        return object()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)

    staged = await client.stream_image(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="proactive-screen",
    )
    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert staged.accepted is True
    assert delivered is True
    assert injected["text"] == "主动看看屏幕"
    visual_event = injected["events_before_text"][0]
    visual_text = visual_event["item"]["content"][0]["text"]
    assert "屏幕上显示番茄钟结束提醒" in visual_text
    client._analyze_image_with_vision_model.assert_awaited_once_with(
        DUMMY_IMAGE_B64
    )
    analyze_image.assert_awaited_once()
    client.ws.send.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_native_qwen_streaming_remains_audio_buffer_bound():
    client = _make_client("qwen", "qwen3-omni-flash-realtime")
    client.ws = AsyncMock()
    client._audio_in_buffer = True

    result = await client.stream_image(DUMMY_IMAGE_B64)

    assert result.accepted is True
    sent = client.ws.send.await_args.args[0]
    assert "input_image_buffer.append" in sent
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_cancels_multimodal_sdk_send_before_returning():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session
    client.handle_interruption = AsyncMock()
    send_started = asyncio.Event()
    send_cancelled = asyncio.Event()

    async def send_client_content(*_args, **_kwargs):
        send_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            send_cancelled.set()
            raise

    session.send_client_content.side_effect = send_client_content
    await client.prepare_external_voice_turn(turn_id="turn-old")
    old_submit = asyncio.create_task(
        client.submit_multimodal_turn(
            "旧问题",
            DUMMY_IMAGE_B64,
            turn_id="turn-old",
        )
    )
    await send_started.wait()

    await client.prepare_external_voice_turn(turn_id="turn-new")

    assert send_cancelled.is_set()
    with pytest.raises(asyncio.CancelledError):
        await old_submit
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_new_gemini_turn_quarantines_accepted_multimodal_turn():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    old_session = AsyncMock()
    old_context = AsyncMock()
    old_context.__aexit__ = AsyncMock()
    client._gemini_session = old_session
    client._gemini_context_manager = old_context
    client.ws = old_session
    client.instructions = "system prompt"
    client._native_audio = True
    client.handle_interruption = AsyncMock()
    replacement_session = AsyncMock()

    async def reconnect(*_args, **_kwargs):
        client._connection_generation += 1
        client._gemini_session = replacement_session
        client.ws = replacement_session

    client.connect = AsyncMock(side_effect=reconnect)
    await client.prepare_external_voice_turn(turn_id="turn-old")
    await client.submit_multimodal_turn(
        "Gemini 已接受",
        DUMMY_IMAGE_B64,
        turn_id="turn-old",
    )

    assert client._gemini_external_submit_task is None
    assert client._gemini_external_outcome_token is not None

    reconnected = await client.prepare_external_voice_turn(turn_id="turn-new")

    old_context.__aexit__.assert_awaited_once_with(None, None, None)
    client.connect.assert_awaited_once_with("system prompt", native_audio=True)
    assert client._gemini_session is replacement_session
    assert client._gemini_external_outcome_token is None
    assert reconnected is True
    client.abandon_external_voice_turn("turn-new")
    await client.close()


@pytest.mark.asyncio
async def test_retired_gemini_handler_cannot_settle_replacement_external_turn():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    release_old_handler = asyncio.Event()

    class OldSession:
        def receive(self):
            async def responses():
                await release_old_handler.wait()
                raise RuntimeError("closed")
                yield None

            return responses()

    client._gemini_session = OldSession()
    old_handler = asyncio.create_task(client._handle_messages_gemini())
    await asyncio.sleep(0)

    client._connection_generation += 1
    replacement_token = object()
    client._gemini_external_outcome_token = replacement_token
    release_old_handler.set()
    await old_handler

    assert client._gemini_external_outcome_token is replacement_token
    client._gemini_external_outcome_token = None
    await client.close()


@pytest.mark.asyncio
async def test_gemini_text_only_external_asr_stays_text_only():
    client = _make_client("gemini", "gemini-2.5-flash-native-audio")
    session = AsyncMock()
    client._gemini_session = session
    client.ws = session

    await client.submit_external_voice_turn("只有转写", turn_id="turn-text")

    kwargs = session.send_client_content.await_args.kwargs
    content = kwargs["turns"][0]
    assert len(content.parts) == 1
    assert content.parts[0].text == "只有转写"
    await client.close()


@pytest.mark.asyncio
async def test_proactive_nudge_still_speaks_while_raw_frames_are_fenced():
    """A fenced raw route means no visual, not no nudge.

    Independent ASR arms the session's raw-frame fence but keeps the
    latest-frame cache warm for proactive observation. Treating that cached
    frame as deliverable makes every proactive turn fail its native image
    inject and return without sending even its text -- and a screen share
    keeps rearming the cache, so she stays silent for the whole session.
    """
    client = _make_client("openai", "gpt-4o-realtime-preview")
    client.ws = AsyncMock()
    client._ai_recent_activity_time = 0
    client._user_recent_activity_time = 0
    client._client_vad_active = False
    client._client_vad_last_speech_time = 0
    injected: dict = {}

    async def inject_text(text, **kwargs):
        injected["text"] = text
        injected.update(kwargs)
        kwargs["on_completed"]()
        return object()

    client.inject_text_and_request_response = AsyncMock(side_effect=inject_text)
    assert client._supports_native_image is True

    # Core owns the frames while independent ASR runs: the cache is kept warm
    # without the frame ever being allowed onto the provider connection.
    client.block_raw_visual_delivery()
    staged = client.stage_multimodal_frame(
        DUMMY_IMAGE_B64,
        source="screen",
        request_id="independent-screen-1",
    )
    assert staged.accepted is True
    assert client._latest_image_b64 == DUMMY_IMAGE_B64

    delivered = await client.prompt_ephemeral("主动看看屏幕")

    assert delivered is True
    assert injected["text"] == "主动看看屏幕"
    assert not injected.get("events_before_text")
    # 帧没被消费：栅栏解除之后它还能用。
    assert client._proactive_image_consumed is False
    await client.close()


@pytest.mark.asyncio
async def test_live_gemini_external_turn_counts_as_an_active_response():
    """Gemini owns the turn before its first content event arrives.

    Between the SDK send returning and the first model content, ``_is_responding``
    is still false and the arbiter is idle, yet the provider has already accepted
    the external-ASR turn (`_gemini_external_outcome_token` stays live until
    turn_complete/interrupted). A queued proactive callback that passes every
    busy check in that window submits a second, unscoped Gemini turn.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._is_responding = False
    assert client.is_active_response() is False

    client._gemini_external_outcome_token = object()
    assert client.is_active_response() is True

    # 终结边缘落地后重新变空闲。
    client._gemini_external_outcome_token = None
    assert client.is_active_response() is False
    await client.close()


@pytest.mark.asyncio
async def test_successive_gemini_external_turns_quarantine_the_live_predecessor():
    """A second external turn must not silently overwrite a live one.

    An overlapping utterance is prepared before the previous turn's dispatcher
    reaches the SDK send, so the prepare-time quarantine finds no outcome token
    to retire. If this path then mints a fresh token over the live one, two
    Gemini turns coexist: their responses can interleave, and the newer turn's
    ownership can be carried off by the older turn's terminal.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    order = []

    async def send_user_turn(_text, *, images_bytes=()):
        order.append("send")

    async def await_quarantine():
        order.append("await_quarantine")
        client._gemini_external_outcome_token = None

    client._gemini_send_user_turn = AsyncMock(side_effect=send_user_turn)
    client._start_gemini_external_submit_quarantine = MagicMock(
        side_effect=lambda *a, **k: order.append("start_quarantine")
    )
    client._await_gemini_external_quarantine = AsyncMock(
        side_effect=await_quarantine
    )

    # 上一轮还挂着（终结事件未到）。
    stale_token = object()
    client._gemini_external_outcome_token = stale_token

    await client._submit_external_gemini_turn("第二句")

    assert order == ["start_quarantine", "await_quarantine", "send"]
    # 新回合拿到的是自己的 token，不是被覆盖的旧的。
    assert client._gemini_external_outcome_token is not None
    assert client._gemini_external_outcome_token is not stale_token
    await client.close()


@pytest.mark.asyncio
async def test_cancelled_external_submit_keeps_its_token_and_arms_quarantine():
    """Cancellation only ends our await; Gemini may already have taken the turn.

    TranscriptDispatcher.invalidate_all() propagates cancellation down the
    worker to here, long after send was handed to the SDK. Settling the token
    as "never sent" announces that no turn is in flight, so the next prepare
    starts no quarantine and that turn's late transcript/response bleeds into
    its successor.

    Both assertions are needed. Asserting only "the token survives" would also
    pass for an implementation that pins the token forever and wedges the
    session busy (is_active_response() reads exactly that token); asserting
    only "a quarantine was armed" would miss the premature settle.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    send_started = asyncio.Event()
    release = asyncio.Event()

    async def _stalling_send(*_args, **_kwargs):
        send_started.set()
        await release.wait()

    client._gemini_send_user_turn = AsyncMock(side_effect=_stalling_send)
    quarantine_args = []

    async def _fake_quarantine(submit_task, outcome_token):
        quarantine_args.append((submit_task, outcome_token))

    client._quarantine_gemini_external_submit = _fake_quarantine
    client._gemini_external_outcome_token = None

    submit = asyncio.create_task(client._submit_external_gemini_turn("被打断的一句"))
    await asyncio.wait_for(send_started.wait(), timeout=1)
    token_in_flight = client._gemini_external_outcome_token
    assert token_in_flight is not None

    submit.cancel()
    with pytest.raises(asyncio.CancelledError):
        await submit

    # 没有被当成"没送成"结算掉。
    assert client._gemini_external_outcome_token is token_in_flight
    # 隔离已武装，且拿到的是这条 submit 与这张 token。
    assert client._gemini_external_quarantine_task is not None
    await asyncio.wait_for(
        asyncio.shield(client._gemini_external_quarantine_task), timeout=1
    )
    assert quarantine_args == [(submit, token_in_flight)]
    release.set()
    await client.close()


@pytest.mark.asyncio
async def test_synchronous_external_send_failure_still_settles_immediately():
    """Dual: a synchronous refusal really did not send, so settle right away.

    Otherwise a token that can never receive a terminal event pins the session
    busy and she never speaks on her own again.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._gemini_send_user_turn = AsyncMock(
        side_effect=RuntimeError("provider refused")
    )
    client._quarantine_gemini_external_submit = AsyncMock()
    client._gemini_external_outcome_token = None

    with pytest.raises(RuntimeError, match="provider refused"):
        await client._submit_external_gemini_turn("发不出去的一句")

    assert client._gemini_external_outcome_token is None
    client._quarantine_gemini_external_submit.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_first_gemini_external_turn_does_not_pay_for_quarantine():
    """No live predecessor means no connection retirement."""
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._gemini_send_user_turn = AsyncMock()
    client._start_gemini_external_submit_quarantine = MagicMock()
    client._await_gemini_external_quarantine = AsyncMock()
    client._gemini_external_outcome_token = None

    await client._submit_external_gemini_turn("第一句")

    client._start_gemini_external_submit_quarantine.assert_not_called()
    client._await_gemini_external_quarantine.assert_not_awaited()
    client._gemini_send_user_turn.assert_awaited_once()
    await client.close()


@pytest.mark.asyncio
async def test_a_bare_turn_complete_still_settles_the_external_token():
    """A content-free terminal must not strand the token.

    Gemini can end a turn with just ``turn_complete`` -- no ``model_turn``, no
    ``output_transcription`` -- and that path never advances the turn epoch.
    Anything that gates settling on the epoch therefore refuses forever, and
    ``is_active_response()`` reports busy for the rest of the session: she
    stops speaking up on her own. The owed-terminal credit is the only gate.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = False
    # 无内容的终结：epoch 从未推进过。
    client._current_turn_epoch = 0
    client._turn_epoch = 0
    client._is_responding = True

    bare_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=bare_terminal, tool_call=None),
        connection_generation=1,
    )

    assert client._gemini_external_outcome_token is None
    await client.close()

@pytest.mark.asyncio
async def test_late_continuation_terminal_cannot_settle_the_external_token():
    """The epoch alone is not enough: late content advances it too.

    A response cancelled by ``handle_interruption()`` can still emit AI content
    before its terminal, and that content bumps ``_current_turn_epoch`` whether
    or not it is a new turn. The owed-terminal credit binds the terminal to the
    response it belongs to instead of to a clock.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_external_token_epoch = 7
    # 被取消的那一轮欠一个终结。
    client._gemini_cancelled_terminal_pending = True
    # 它的迟到续帧已经把 epoch 推过了铸造刻度 —— 光靠 epoch 会误判。
    client._current_turn_epoch = 9
    client._is_responding = True

    terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )

    # 这条终结欠给旧那一轮，token 必须留着。
    assert client._gemini_external_outcome_token is token
    # 欠账是一次性的：已被这条终结消费掉。
    assert client._gemini_cancelled_terminal_pending is False

    # 外部回合自己的终结现在才结算得掉。
    client._is_responding = True
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )
    assert client._gemini_external_outcome_token is None
    await client.close()


@pytest.mark.asyncio
async def test_handle_interruption_records_the_owed_terminal():
    """The credit is worthless unless the interruption actually records it.

    Asserting the consumption path alone stays green when nothing ever sets the
    flag -- the call-site blind spot this PR keeps hitting.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._is_responding = True
    client._current_response_id = None
    client._gemini_cancelled_terminal_pending = False

    await client.handle_interruption()

    assert client._gemini_cancelled_terminal_pending is True
    await client.close()


@pytest.mark.asyncio
async def test_a_genuine_new_turn_voids_a_stale_owed_terminal():
    """The credit must not outlive the response it was owed by.

    If the cancelled response never emits its terminal, an un-voided credit
    would eat the NEXT legitimate one, leaving the external token settled by
    nobody -- the session would read busy forever and she would stop speaking
    up on her own.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()
    client._gemini_cancelled_terminal_pending = True
    client._is_responding = False
    client._interrupted = False
    # 用户在 AI 最后一帧之后发过声 → 必然是新 turn。
    client._user_recent_activity_time = 200.0
    client._ai_recent_activity_time = 100.0

    content_start = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=content_start, tool_call=None),
        connection_generation=1,
    )

    assert client._gemini_cancelled_terminal_pending is False
    await client.close()


@pytest.mark.asyncio
async def test_external_submit_establishes_a_new_user_turn():
    """An external ASR turn IS a user turn; the client has to be told.

    The audio never reaches the provider, so none of the transport-side points
    that advance ``_user_recent_activity_time`` fire for it. Left stale, the
    turn's first content is judged a late continuation: it stays suppressed by
    ``_interrupted``, the owed-terminal credit is never voided, and that turn's
    own terminal spends the credit instead of settling its token -- which pins
    ``is_active_response()`` busy for the rest of the session.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._gemini_send_user_turn = AsyncMock()
    client._start_gemini_external_submit_quarantine = MagicMock()
    client._await_gemini_external_quarantine = AsyncMock()
    client._gemini_external_outcome_token = None
    # AI 刚说过话；用户活动时间戳停在更早。
    client._ai_recent_activity_time = time.time() - 10.0
    client._user_recent_activity_time = client._ai_recent_activity_time - 10.0

    await client._submit_external_gemini_turn("用户插话")

    # 用户比 AI 更晚发声 → _is_new_turn 会为真，欠账才有机会被作废。
    assert client._user_recent_activity_time > client._ai_recent_activity_time
    await client.close()


@pytest.mark.asyncio
async def test_one_event_with_both_terminal_flags_spends_the_debt_once():
    """The debt is per EVENT, not per branch.

    Gemini can report an interrupted completion with ``turn_complete`` and
    ``interrupted`` both set, and both terminal branches run for that single
    event. Consuming separately, the first takes the debt and the second --
    finding none -- settles the freshly minted token using the OLD response's
    terminal, which is precisely what the debt exists to prevent.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    client._is_responding = True

    both_flags = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=True,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=both_flags, tool_call=None),
        connection_generation=1,
    )

    # 这一条事件只该抵掉一笔欠账，token 必须留着。
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    await client.close()


@pytest.mark.asyncio
async def test_late_content_from_the_cancelled_turn_keeps_the_debt():
    """The cancelled turn's own late content must not void its debt.

    Voiding on ``_is_new_turn`` alone is satisfied by that content: the user
    already spoke after the AI's last frame, which is exactly what the
    cancellation means. The debt then dies before the terminal it was owed,
    and that terminal settles the freshly minted external token -- the turn
    reads idle while it is still in flight.

    The line below this one declares the new turn under the stricter
    ``_is_new_turn and _can_clear_interrupted``; voiding has to use the same
    definition of "the old turn is over".
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    # handle_interruption() 之后的状态：这一轮被叫停，不再 responding。
    client._is_responding = False
    client._interrupted = True
    # 外部回合把用户活动刷到了 AI 最后一帧之后，且仍在 3s 窗口内。
    client._ai_recent_activity_time = time.time()
    client._user_recent_activity_time = client._ai_recent_activity_time + 0.1

    late_content = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=late_content, tool_call=None),
        connection_generation=1,
    )
    # 被取消那一轮的迟到内容不是「新回合开始」的证据。
    assert client._gemini_cancelled_terminal_pending is True

    real_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=real_terminal, tool_call=None),
        connection_generation=1,
    )
    # 那条终结属于被取消的旧回合，不能拿去结算新铸的 token。
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    await client.close()


@pytest.mark.asyncio
async def test_voiding_the_debt_also_drops_its_deadline():
    """The two fields are one piece of state; retiring one orphans the other.

    The consume and connection-replacement paths already clear them together.
    A deadline left behind by the turn-start void is inert only because the one
    arming site always overwrites it -- a second arming site would inherit it.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0
    client._is_responding = False
    # 外部 ASR 送的是文本，provider 不回 input_transcription，所以打断标志会一直
    # 挂着 —— 用生产上的状态，别用 _interrupted=False 把判据绕过去。这里靠的是
    # AI 静默超窗（_can_clear_interrupted 的第三个析取项）。
    client._interrupted = True
    client._gemini_user_transcript_after_interrupt = False
    client._user_recent_activity_time = 200.0
    client._ai_recent_activity_time = 100.0

    content_start = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=content_start, tool_call=None),
        connection_generation=1,
    )

    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_deadline is None
    await client.close()


@pytest.mark.asyncio
async def test_a_slow_handoff_restarts_the_debt_deadline_at_the_gemini_send():
    """The clock starts when the provider is actually interrupted.

    Gemini has no ``response.cancel``: ``handle_interruption`` only marks local
    state, and the provider learns of the barge-in when the successor's content
    lands. Timing the debt from the interruption means a slow ASR handoff or a
    heavy multimodal send burns the whole window before the provider has even
    been told, so the cancelled turn's terminal is judged current and settles
    the successor token -- the regression this change exists to prevent.

    Arming still happens at the interruption so every early return carries a
    deadline; the send re-stamps it, once.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()
    client.note_user_turn_started = MagicMock()

    sent = []
    client._gemini_session = SimpleNamespace(
        send_client_content=AsyncMock(side_effect=lambda **kw: sent.append(kw)),
    )

    # 打断已经发生，但 ASR 交接 + 压图拖过了 TTL：期限已经到点。
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_awaiting_delivery = True
    client._gemini_cancelled_terminal_id = object()
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 1.0

    await client._gemini_send_user_turn("successor")

    assert sent, "the successor content must actually be sent"
    deadline = client._gemini_cancelled_terminal_deadline
    assert deadline is not None and deadline > time.monotonic()
    # 只续一次：后面每次发送都续命的话，一笔没人抵掉的欠账会被无限延寿。
    assert client._gemini_cancelled_terminal_awaiting_delivery is False
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 1.0
    await client._gemini_send_user_turn("another")
    assert client._gemini_cancelled_terminal_deadline < time.monotonic()

    # 行为层：续期之后，被取消那一轮的终结仍然抵的是欠账，不碰后继的 token。
    client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0
    token = object()
    client._gemini_external_outcome_token = token
    client._is_responding = True
    client._interrupted = True
    terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )
    assert client._gemini_external_outcome_token is token
    await client.close()


@pytest.mark.asyncio
async def test_the_debt_does_not_expire_before_the_interrupt_is_delivered():
    """The clock cannot run while the provider has not been interrupted.

    ``send_client_content()`` puts the successor on the wire before it returns,
    and the re-stamp happens after that await. The receive loop can deliver the
    cancelled turn's terminal inside that gap, where the deadline is still the
    one stamped at the interruption and may already be spent. Expiring there
    hands the terminal to a successor whose own turn has not started.

    Until that send lands nothing else has been submitted, so the first
    terminal can only belong to the cancelled turn.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    # 交接拖过了 TTL，而中断还没送达 —— 重打时间戳那一步还没轮到。
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 1.0
    client._gemini_cancelled_terminal_awaiting_delivery = True
    client._is_responding = True
    client._interrupted = True

    terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=terminal, tool_call=None),
        connection_generation=1,
    )

    # 这条终结抵的是欠账，不能去结算后继的 token。
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_awaiting_delivery is False
    await client.close()


@pytest.mark.asyncio
async def test_only_the_send_that_delivered_the_debt_restarts_its_clock():
    """A send restamps the debt it delivered, not whatever is armed on return.

    An earlier send can still be inside ``send_client_content`` when a barge-in
    arms a fresh debt. Reading the flags after that await lets the earlier send
    claim the new debt, lowering awaiting-delivery and starting the TTL while
    the successor's content has not been sent at all -- so a slow handoff
    afterwards spends the window before the provider is interrupted.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client.note_user_turn_started = MagicMock()

    armed = {}

    async def _arm_midflight(**_kw):
        # 这次发送在飞的时候，用户抢话武装了一笔**新**欠账。
        client._gemini_cancelled_terminal_pending = True
        client._gemini_cancelled_terminal_awaiting_delivery = True
        client._gemini_cancelled_terminal_id = object()
        client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0
        armed["id"] = client._gemini_cancelled_terminal_id

    client._gemini_session = SimpleNamespace(
        send_client_content=AsyncMock(side_effect=_arm_midflight),
    )
    # 这次发送开始时没有任何欠账挂着。
    client._gemini_cancelled_terminal_pending = False
    client._gemini_cancelled_terminal_awaiting_delivery = False
    client._gemini_cancelled_terminal_id = None

    await client._gemini_send_user_turn("earlier turn")

    # 那笔欠账不是这次发送送达的：它必须仍然在等自己的送达。
    assert client._gemini_cancelled_terminal_id is armed["id"]
    assert client._gemini_cancelled_terminal_awaiting_delivery is True
    await client.close()


@pytest.mark.asyncio
async def test_an_expired_debt_does_not_eat_a_legitimate_terminal():
    """A debt that outlived its window is spent but not honoured.

    The turn-start void only runs when AI content arrives before the terminal.
    A bare ``turn_complete`` -- which this provider does emit -- skips it, so
    without a deadline a stale debt waits indefinitely and absorbs a terminal
    that belongs to someone else. That leaves an external token nobody settles
    and a session pinned busy, which is the worse of the two failure
    directions.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_deadline = time.monotonic() - 0.001
    client._is_responding = True
    client._interrupted = True

    bare_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=bare_terminal, tool_call=None),
        connection_generation=1,
    )

    # 过期的欠账被花掉但不认账：这条终结照常结算当前回合。
    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_deadline is None
    assert client._gemini_external_outcome_token is None
    await client.close()


@pytest.mark.asyncio
async def test_a_replacement_connection_does_not_inherit_the_debt():
    """The debt belongs to the connection that armed it.

    The replacement's first terminal is its own. Absorbed by the predecessor's
    debt, the turn that produced it never settles its token.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._gemini_cancelled_terminal_pending = True
    client._gemini_cancelled_terminal_deadline = time.monotonic() + 60.0

    client._on_connection_attached()

    assert client._gemini_cancelled_terminal_pending is False
    assert client._gemini_cancelled_terminal_deadline is None
    await client.close()


@pytest.mark.unit
def test_arming_the_cancellation_debt_always_sets_a_deadline():
    """The expiry fails open on a missing deadline, so arming must set one.

    ``_consume_cancelled_terminal`` treats ``deadline is None`` as "never
    expires" so that state built directly by tests keeps the old semantics. A
    production arming site that forgot the deadline would silently opt out of
    the bound.
    """
    source = (
        Path(__file__).resolve().parents[2]
        / "main_logic"
        / "omni_realtime_client"
        / "_transport.py"
    ).read_text(encoding="utf-8").split(chr(10))

    arming = [
        index
        for index, line in enumerate(source)
        if line.strip() == "self._gemini_cancelled_terminal_pending = True"
    ]
    assert arming, "the arming site moved; update this guard"
    for index in arming:
        # 窗口要盖住两条赋值以及它们各自的解释注释。
        window = chr(10).join(source[index : index + 16])
        assert "self._gemini_cancelled_terminal_deadline = (" in window, (
            f"line {index + 1}: arming the debt must also set its deadline, "
            f"got: {window!r}"
        )
        assert (
            "self._gemini_cancelled_terminal_awaiting_delivery = True" in window
        ), (
            f"line {index + 1}: arming the debt must mark it as awaiting "
            f"delivery, or the deadline starts before the provider is "
            f"interrupted; got: {window!r}"
        )


@pytest.mark.asyncio
async def test_non_terminal_content_does_not_spend_the_cancellation_debt():
    """Only a terminal event pays off the debt.

    A cancelled response keeps emitting content before it ends. Spending the
    debt on one of those events leaves nothing to absorb the real terminal that
    follows, so that terminal settles the freshly minted external token and the
    session reads idle while its response is still live.
    """
    client = _make_client("gemini", "gemini-2.0-flash-live-001")
    client._connection_generation = 1
    client._still_owns_connection = lambda _gen: True
    client._read_host_turn_id = lambda: None
    client.on_response_done = None
    client.on_new_message = None
    client.on_text_delta = None
    client._settle_gemini_proactive_inject = MagicMock()

    token = object()
    client._gemini_external_outcome_token = token
    client._gemini_cancelled_terminal_pending = True
    client._is_responding = True
    client._interrupted = True
    # 短静默且用户没再发声 → late continuation，不会被当成新回合（新回合会作废欠账）。
    client._user_recent_activity_time = 100.0
    client._ai_recent_activity_time = time.time()

    late_content = SimpleNamespace(
        model_turn=SimpleNamespace(parts=[]),
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=False,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=late_content, tool_call=None),
        connection_generation=1,
    )
    # 非终结事件不该动欠账。
    assert client._gemini_cancelled_terminal_pending is True

    # 旧回合真正的终结到达：它才该抵掉欠账，并且不能结算新 token。
    real_terminal = SimpleNamespace(
        model_turn=None,
        input_transcription=None,
        output_transcription=None,
        interrupted=False,
        turn_complete=True,
    )
    await client._process_gemini_response(
        SimpleNamespace(server_content=real_terminal, tool_call=None),
        connection_generation=1,
    )
    assert client._gemini_external_outcome_token is token
    assert client._gemini_cancelled_terminal_pending is False
    await client.close()
