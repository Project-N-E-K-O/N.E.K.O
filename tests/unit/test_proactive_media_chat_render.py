"""Unit tests for the chat-visible proactive media channel.

Covers four segments of the chain:
1. ``character_runtime._persist_proactive_media_image`` — media_part
   persistence into the host media dir (mime whitelist / magic sniffing /
   uuid filenames / strict b64 / oversize cap / off-loop via to_thread /
   failure never raises).
2. ``main_server._handle_agent_event`` wiring — a visibility=["chat"]
   event image is persisted and the ``proactive_media`` WS frame is sent
   DIRECTLY at event ingestion (decoupled from LLM delivery: image-only,
   direct_reply and blind events all render; callback requeue cannot
   double-send), without regressing the existing media_images (AI vision)
   path; no "chat" in visibility skips persistence entirely.
3. ``LLMSessionManager.send_proactive_media`` — WS frame structure / host
   URL whitelist / disconnect / send-failure swallowed (the image bubble
   is an enhancement channel and must never interrupt delivery).
4. ``StorageRootsMixin.prune_proactive_media`` — age drop / total-size cap
   oldest-first (with filename order deliberately OPPOSED to mtime order
   so a path-sorting implementation cannot pass) / missing dir no-raise,
   plus a static wiring check that web_app ensures + prunes + mounts the
   dir and keeps a daily background prune task.

Frontend behaviour (app-websocket.js proactive_media branch /
app-proactive.js flush) is covered by tests/frontend source-assertions,
out of unit-test scope.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from main_logic.core import LLMSessionManager  # noqa: E402

# ──────────────────────────────────────────────────────────────────────
# 测试样本
# ──────────────────────────────────────────────────────────────────────

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff\xe0\x00\x10JFIF"
_PNG_B64 = base64.b64encode(_PNG_MAGIC + b"fake-png-body").decode()
_JPEG_B64 = base64.b64encode(_JPEG_MAGIC + b"fake-jpeg-body").decode()
_GARBAGE_B64 = base64.b64encode(b"not an image at all").decode()

_UUID_EXT_RE = re.compile(r"^/user_proactive_media/[0-9a-f]{32}\.(png|jpg|gif|webp)$")
_GOOD_URL = f"/user_proactive_media/{'0' * 32}.png"


class _FakeConfigManager:
    def __init__(self, media_dir):
        self.proactive_media_dir = media_dir
        self.prune_calls = 0

    def prune_proactive_media(self, **kwargs):
        self.prune_calls += 1


def _patch_media_dir(monkeypatch, tmp_path):
    """Point get_config_manager at tmp_path (where the helper's lazy import binds)."""
    import utils.config_manager as cm_mod

    fake = _FakeConfigManager(tmp_path / "proactive_media")
    monkeypatch.setattr(cm_mod, "get_config_manager", lambda: fake)
    return fake


# ──────────────────────────────────────────────────────────────────────
# 1. _persist_proactive_media_image
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_persist_with_png_mime_writes_file_and_returns_url(monkeypatch, tmp_path):
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    url = await _persist_proactive_media_image(_PNG_B64, "image/png")

    assert url is not None
    assert _UUID_EXT_RE.match(url), url
    assert url.endswith(".png")
    fname = url.rsplit("/", 1)[-1]
    saved = (tmp_path / "proactive_media" / fname).read_bytes()
    assert saved == _PNG_MAGIC + b"fake-png-body"


@pytest.mark.unit
async def test_persist_mime_takes_priority_over_magic(monkeypatch, tmp_path):
    """Declared mime=image/jpeg wins even when the bytes have a PNG header (trust the claim)."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    url = await _persist_proactive_media_image(_PNG_B64, "image/jpeg")

    assert url is not None and url.endswith(".jpg")


@pytest.mark.unit
async def test_persist_sniffs_jpeg_magic_when_mime_missing(monkeypatch, tmp_path):
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    url = await _persist_proactive_media_image(_JPEG_B64, "")

    assert url is not None and url.endswith(".jpg")
    fname = url.rsplit("/", 1)[-1]
    assert (tmp_path / "proactive_media" / fname).exists()


@pytest.mark.unit
async def test_persist_rejects_unknown_bytes(monkeypatch, tmp_path):
    """No mime and unrecognized magic → None, and the dir stays empty."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    assert await _persist_proactive_media_image(_GARBAGE_B64, "") is None
    media_dir = tmp_path / "proactive_media"
    assert not any(media_dir.iterdir()) if media_dir.exists() else True


@pytest.mark.unit
async def test_persist_rejects_malformed_base64(monkeypatch, tmp_path):
    """Non-alphabet payload (e.g. urlsafe/garbled) must fail loudly, not
    decode into corrupted bytes via validate=False discarding."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    assert await _persist_proactive_media_image("%%%", "image/png") is None
    # urlsafe 字母表（-_）同样拒绝：validate=False 会静默丢字符解出坏字节
    assert await _persist_proactive_media_image(_PNG_B64[:20] + "-_-_", "image/png") is None
    media_dir = tmp_path / "proactive_media"
    assert not media_dir.exists() or not any(media_dir.iterdir())


@pytest.mark.unit
async def test_persist_tolerates_whitespace_wrapped_b64(monkeypatch, tmp_path):
    """MIME-style line wrapping is stripped before the strict decode."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    wrapped = "\r\n".join(_PNG_B64[i : i + 16] for i in range(0, len(_PNG_B64), 16))
    url = await _persist_proactive_media_image(wrapped, "image/png")

    assert url is not None and url.endswith(".png")


@pytest.mark.unit
async def test_persist_cap_measures_stripped_length_not_raw(monkeypatch, tmp_path):
    """Greptile #2905: the ~10MB cap bounds decoded (on-disk) bytes, so MIME
    folding whitespace must not push a near-limit payload over the cap — a
    valid image whose raw string estimate exceeds the cap but whose stripped
    estimate fits is persisted, not dropped."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    # ~14M 个折行空白：原始串估算 >10MB，去空白后只有几十字节
    padded = _PNG_B64 + "\r\n" * 14_000_000
    assert len(padded) * 3 // 4 > 10 * 1024 * 1024
    url = await _persist_proactive_media_image(padded, "image/png")

    assert url is not None and url.endswith(".png")
    fname = url.rsplit("/", 1)[-1]
    assert (tmp_path / "proactive_media" / fname).read_bytes() == (
        _PNG_MAGIC + b"fake-png-body"
    )


@pytest.mark.unit
async def test_persist_normalizes_non_string_inputs(monkeypatch, tmp_path):
    """Codex #2905 P2: a non-string mime (e.g. JSON number) must degrade to
    sniffing instead of raising through to_thread and aborting the whole
    event; a non-string b64 is rejected without raising."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    # mime=123 → 归一为空 → 魔数嗅探落盘
    url = await _persist_proactive_media_image(_PNG_B64, 123)
    assert url is not None and url.endswith(".png")
    # b64 非字符串：warning + None，不抛
    assert await _persist_proactive_media_image(12345, "image/png") is None


@pytest.mark.unit
async def test_persist_accepts_image_exactly_at_cap(monkeypatch, tmp_path):
    """Greptile round 2: '=' padding must not inflate the estimate — an image
    decoding to exactly the ~10MB cap is accepted; one byte over is dropped."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    exact = base64.b64encode(
        _PNG_MAGIC + b"x" * (10 * 1024 * 1024 - len(_PNG_MAGIC))
    ).decode()
    assert len(base64.b64decode(exact)) == 10 * 1024 * 1024
    url = await _persist_proactive_media_image(exact, "image/png")
    assert url is not None and url.endswith(".png")

    over = base64.b64encode(
        _PNG_MAGIC + b"x" * (10 * 1024 * 1024 - len(_PNG_MAGIC) + 1)
    ).decode()
    assert await _persist_proactive_media_image(over, "image/png") is None


@pytest.mark.unit
async def test_write_path_trips_opportunistic_prune(monkeypatch, tmp_path):
    """Codex #2905 P1: the 256MB total cap must not wait for the daily
    worker — once accumulated writes trip the threshold, the persist worker
    prunes in-thread; below the threshold no prune runs."""
    import app.main_server.character_runtime as cr_mod
    from app.main_server.character_runtime import _persist_proactive_media_image

    fake = _patch_media_dir(monkeypatch, tmp_path)

    # 未到阈值：不触发
    monkeypatch.setattr(cr_mod, "_proactive_media_unpruned_bytes", 0)
    url = await _persist_proactive_media_image(_PNG_B64, "image/png")
    assert url is not None
    assert fake.prune_calls == 0
    assert cr_mod._proactive_media_unpruned_bytes > 0

    # 距阈值仅差 1 字节：本次写入必然跨过 → 就地剪裁一次并清零
    monkeypatch.setattr(
        cr_mod,
        "_proactive_media_unpruned_bytes",
        cr_mod._PROACTIVE_MEDIA_WRITE_TRIP_BYTES - 1,
    )
    url = await _persist_proactive_media_image(_PNG_B64, "image/png")
    assert url is not None
    assert fake.prune_calls == 1
    assert cr_mod._proactive_media_unpruned_bytes == 0


@pytest.mark.unit
async def test_persist_rejects_oversized_image(monkeypatch, tmp_path):
    """Decoded size over the ~10MB cap → dropped before any disk write."""
    from app.main_server.character_runtime import _persist_proactive_media_image

    _patch_media_dir(monkeypatch, tmp_path)

    huge = base64.b64encode(_PNG_MAGIC + b"x" * (11 * 1024 * 1024)).decode()
    assert await _persist_proactive_media_image(huge, "image/png") is None
    media_dir = tmp_path / "proactive_media"
    assert not media_dir.exists() or not any(media_dir.iterdir())


@pytest.mark.unit
async def test_persist_disk_failure_returns_none_not_raises(monkeypatch, tmp_path):
    from app.main_server.character_runtime import _persist_proactive_media_image

    def _boom():
        raise IOError("disk on fire")

    import utils.config_manager as cm_mod

    monkeypatch.setattr(cm_mod, "get_config_manager", _boom)

    assert await _persist_proactive_media_image(_PNG_B64, "image/png") is None


@pytest.mark.unit
async def test_persist_runs_blocking_worker_via_to_thread(monkeypatch, tmp_path):
    """The zero-event-loop-blocking contract: persist must go through
    asyncio.to_thread — a regression to a direct sync call fails here."""
    from app.main_server.character_runtime import (
        _persist_proactive_media_image,
        _persist_proactive_media_image_blocking,
    )

    _patch_media_dir(monkeypatch, tmp_path)

    called_with = []
    real_to_thread = asyncio.to_thread

    async def _spy(func, *args, **kwargs):
        called_with.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _spy)

    url = await _persist_proactive_media_image(_PNG_B64, "image/png")

    assert url is not None
    assert called_with == [_persist_proactive_media_image_blocking]


# ──────────────────────────────────────────────────────────────────────
# 2. _handle_agent_event 接线（visibility=["chat"] → 落盘 + 入口直发帧）
# ──────────────────────────────────────────────────────────────────────


def _make_event(**overrides):
    event = {
        "event_type": "proactive_message",
        "lanlan_name": "Test",
        "text": "[画图完成] 图好了",
        "channel": "plugin:test-source",
        "task_id": "task-1",
        "ai_behavior": "respond",
        "visibility": ["chat"],
        "media_parts": [
            {"type": "image", "binary_base64": _PNG_B64, "mime": "image/png"},
        ],
    }
    event.update(overrides)
    return event


def _make_fake_mgr():
    mgr = MagicMock()
    mgr.passthrough_to_chat_bubble = AsyncMock(return_value=True)
    mgr.enqueue_agent_callback = MagicMock()
    mgr.trigger_agent_callbacks = AsyncMock()
    mgr.submit_proactive_callback = MagicMock()
    mgr.send_proactive_media = AsyncMock(return_value=True)
    mgr.send_lanlan_response = AsyncMock(return_value=True)
    mgr.handle_proactive_complete = AsyncMock()
    mgr.session = None
    mgr.websocket = None
    mgr._pending_agent_callback_task = None
    return mgr


def _patch_event_env(monkeypatch, fake_mgr):
    monkeypatch.setattr(
        "app.main_server.character_runtime._get_session_manager",
        lambda name: fake_mgr,
    )
    monkeypatch.setattr(
        "app.main_server.character_runtime._is_websocket_connected",
        lambda ws: False,
    )


class _LogCollector:
    """Attach a handler straight to the target logger.

    The project logger hierarchy sets ``propagate=False`` in some paths, so
    ``caplog`` cannot reliably observe these records (same workaround as
    tests/unit/test_callback_instruction_origin.py).
    """

    def __init__(self):
        import app.main_server.character_runtime as cr

        self.records: list[str] = []
        self._logger = cr.logger
        self._handler = logging.Handler()
        self._handler.setLevel(logging.WARNING)
        self._handler.emit = lambda r: self.records.append(r.getMessage())
        self._prior_level = self._logger.level

    def __enter__(self):
        self._logger.addHandler(self._handler)
        if self._prior_level > logging.WARNING or self._prior_level == logging.NOTSET:
            self._logger.setLevel(logging.WARNING)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._prior_level)
        return False

    @property
    def text(self):
        return "\n".join(self.records)


def _sent_images(fake_mgr):
    assert fake_mgr.send_proactive_media.await_count == 1
    kwargs = fake_mgr.send_proactive_media.await_args.kwargs
    assert isinstance(kwargs["images"], list) and kwargs["images"]
    assert isinstance(kwargs["turn_id"], str) and kwargs["turn_id"]
    return kwargs["images"]


@pytest.mark.unit
async def test_chat_visible_image_persisted_and_frame_sent_at_ingestion(
    monkeypatch, tmp_path
):
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_make_event())

    # 新通路：入口直发 WS 帧（不挂 callback——callback 不再携带 URL，
    # requeue 重试不可能二次发帧）
    urls = _sent_images(fake_mgr)
    assert len(urls) == 1
    assert _UUID_EXT_RE.match(urls[0]), urls
    # 文件真实存在且内容完整
    fname = urls[0].rsplit("/", 1)[-1]
    assert (tmp_path / "proactive_media" / fname).read_bytes() == (
        _PNG_MAGIC + b"fake-png-body"
    )
    # callback 正常提交且不再带 media_image_urls
    fake_mgr.submit_proactive_callback.assert_called_once()
    callback = fake_mgr.submit_proactive_callback.call_args.args[0]
    assert "media_image_urls" not in callback
    # 既有 AI 视觉通路不回归：deferred b64 照旧在 media_images
    assert callback["media_images"] == [_PNG_B64]


@pytest.mark.unit
async def test_non_string_mime_does_not_abort_event(monkeypatch, tmp_path):
    """Codex #2905 P2 (event-level regression): a malformed mime drops only
    the image — it must not abort the whole event's text delivery and
    callback submission."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    event = _make_event(
        media_parts=[{"type": "image", "binary_base64": _PNG_B64, "mime": 123}]
    )
    await main_server._handle_agent_event(event)

    # 文本照常走主动回复通路
    fake_mgr.submit_proactive_callback.assert_called_once()
    callback = fake_mgr.submit_proactive_callback.call_args.args[0]
    assert callback["media_images"] == [_PNG_B64]
    # mime 归一为空 → 魔数嗅探仍落盘，入口帧照发
    urls = _sent_images(fake_mgr)
    assert len(urls) == 1
    assert _UUID_EXT_RE.match(urls[0]), urls


@pytest.mark.unit
async def test_no_chat_visibility_skips_persistence_and_frame(monkeypatch, tmp_path):
    """visibility without "chat" (e.g. empty list) → no persistence, no frame."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_make_event(visibility=[]))

    fake_mgr.send_proactive_media.assert_not_awaited()
    callback = fake_mgr.submit_proactive_callback.call_args.args[0]
    # AI 视觉通路不受影响
    assert callback["media_images"] == [_PNG_B64]
    media_dir = tmp_path / "proactive_media"
    assert not media_dir.exists() or not any(media_dir.iterdir())


@pytest.mark.unit
async def test_persist_failure_does_not_break_delivery(monkeypatch, tmp_path):
    """Persistence raising → no frame, but the callback still submits; the event chain survives."""
    from app import main_server

    def _boom():
        raise IOError("disk on fire")

    import utils.config_manager as cm_mod

    monkeypatch.setattr(cm_mod, "get_config_manager", _boom)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_make_event())

    fake_mgr.send_proactive_media.assert_not_awaited()
    callback = fake_mgr.submit_proactive_callback.call_args.args[0]
    assert callback["media_images"] == [_PNG_B64]


@pytest.mark.unit
async def test_frame_send_failure_is_logged_and_non_fatal(monkeypatch, tmp_path):
    """send_proactive_media returning False (display ws down) must not
    disturb the event chain, and must leave an actionable warning."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    fake_mgr.send_proactive_media = AsyncMock(return_value=False)
    _patch_event_env(monkeypatch, fake_mgr)

    with _LogCollector() as logs:
        await main_server._handle_agent_event(_make_event())

    fake_mgr.submit_proactive_callback.assert_called_once()
    assert "proactive media frame not delivered" in logs.text


@pytest.mark.unit
async def test_event_caps_persisted_media_count(monkeypatch, tmp_path):
    """Per-event persist cap: 6 chat-visible parts → only the first 4 URLs,
    while the AI-vision path (media_images) still carries all 6; the cap
    log line is asserted so the warning branch cannot be silently deleted."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    event = _make_event()
    event["media_parts"] = [
        {"type": "image", "binary_base64": _PNG_B64, "mime": "image/png"}
    ] * 6
    with _LogCollector() as logs:
        await main_server._handle_agent_event(event)

    urls = _sent_images(fake_mgr)
    assert len(urls) == 4
    assert all(_UUID_EXT_RE.match(u) for u in urls)
    callback = fake_mgr.submit_proactive_callback.call_args.args[0]
    assert len(callback["media_images"]) == 6
    assert "per-event persist cap" in logs.text


@pytest.mark.unit
async def test_image_only_event_still_sends_frame(monkeypatch, tmp_path):
    """No text → no callback is built, but the chat-visible image must
    still render (the frame is decoupled from LLM delivery)."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_make_event(text=""))

    urls = _sent_images(fake_mgr)
    assert len(urls) == 1 and _UUID_EXT_RE.match(urls[0])
    fake_mgr.submit_proactive_callback.assert_not_called()
    fake_mgr.enqueue_agent_callback.assert_not_called()


@pytest.mark.unit
async def test_direct_reply_event_still_sends_frame(monkeypatch, tmp_path):
    """direct_reply bypasses the LLM callback entirely — the image frame
    still goes out at ingestion, before the verbatim reply."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_make_event(direct_reply=True))

    urls = _sent_images(fake_mgr)
    assert len(urls) == 1 and _UUID_EXT_RE.match(urls[0])
    fake_mgr.submit_proactive_callback.assert_not_called()
    fake_mgr.send_lanlan_response.assert_awaited_once()


@pytest.mark.unit
async def test_blind_event_renders_media_without_llm_injection(
    monkeypatch, tmp_path
):
    """blind + visibility=["chat"]: no LLM injection (media_images stays
    empty) and no callback enqueue, but persist + frame + verbatim text
    passthrough all happen."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(_make_event(ai_behavior="blind"))

    urls = _sent_images(fake_mgr)
    assert len(urls) == 1 and _UUID_EXT_RE.match(urls[0])
    fake_mgr.submit_proactive_callback.assert_not_called()
    fake_mgr.enqueue_agent_callback.assert_not_called()
    fake_mgr.passthrough_to_chat_bubble.assert_awaited_once()
    fake_mgr.handle_proactive_complete.assert_awaited()


@pytest.mark.unit
async def test_blind_media_without_chat_visibility_is_dropped(
    monkeypatch, tmp_path
):
    """blind + visibility without "chat": no sink at all — nothing persisted,
    nothing sent, and no verbatim text passthrough either (v2: no "chat"
    means no verbatim render)."""
    from app import main_server

    _patch_media_dir(monkeypatch, tmp_path)
    fake_mgr = _make_fake_mgr()
    _patch_event_env(monkeypatch, fake_mgr)

    await main_server._handle_agent_event(
        _make_event(ai_behavior="blind", visibility=[])
    )

    fake_mgr.send_proactive_media.assert_not_awaited()
    media_dir = tmp_path / "proactive_media"
    assert not media_dir.exists() or not any(media_dir.iterdir())
    fake_mgr.passthrough_to_chat_bubble.assert_not_awaited()


# ──────────────────────────────────────────────────────────────────────
# 3. send_proactive_media（notify.py WS 帧）
# ──────────────────────────────────────────────────────────────────────


class _ClientState:
    def __init__(self, name):
        self._name = name

    @property
    def CONNECTED(self):
        return _ClientState._connected_singleton

    def __eq__(self, other):
        return isinstance(other, _ClientState) and other._name == self._name

    def __hash__(self):
        return hash(self._name)


_ClientState._connected_singleton = _ClientState("CONNECTED")
_DISCONNECTED = _ClientState("DISCONNECTED")


class _FakeWebsocket:
    def __init__(self, connected=True):
        self.client_state = (
            _ClientState._connected_singleton if connected else _DISCONNECTED
        )
        self.send_json = AsyncMock()


def _make_mgr(websocket=None):
    mgr = LLMSessionManager.__new__(LLMSessionManager)
    mgr.lanlan_name = "Test"
    mgr.websocket = websocket
    return mgr


@pytest.mark.unit
async def test_send_proactive_media_frame_structure():
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    ok = await mgr.send_proactive_media(
        turn_id="sid-1", images=[_GOOD_URL]
    )

    assert ok is True
    assert ws.send_json.await_count == 1
    payload = ws.send_json.await_args.args[0]
    assert payload["type"] == "proactive_media"
    assert payload["turn_id"] == "sid-1"
    assert payload["images"] == [_GOOD_URL]


@pytest.mark.unit
async def test_send_proactive_media_filters_non_host_urls():
    """Only host-generated media URLs may reach the frontend img/openExternal
    sinks — arbitrary schemes from a buggy caller are dropped with a warning."""
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    ok = await mgr.send_proactive_media(
        turn_id="sid-f",
        images=[
            _GOOD_URL,
            "http://evil.example/x.png",
            "file:///etc/passwd",
            "javascript:alert(1)",
            123,
            # CodeRabbit #2905：前缀匹配挡不住的穿越/畸形变体——完整形状
            # 匹配必须全部拒绝（new URL 规范化后穿越串可达任意同源路径）
            "/user_proactive_media/../../../etc/passwd",
            f"/user_proactive_media/{'A' * 32}.png",
            f"/user_proactive_media/{'0' * 31}.png",
            f"/user_proactive_media/{'0' * 32}.exe",
        ],
    )

    assert ok is True
    payload = ws.send_json.await_args.args[0]
    assert payload["images"] == [_GOOD_URL]


@pytest.mark.unit
async def test_send_proactive_media_all_filtered_returns_false():
    ws = _FakeWebsocket(connected=True)
    mgr = _make_mgr(websocket=ws)

    ok = await mgr.send_proactive_media(turn_id="sid-g", images=["http://x/y.png"])

    assert ok is False
    ws.send_json.assert_not_awaited()


@pytest.mark.unit
async def test_send_proactive_media_disconnected_returns_false():
    ws = _FakeWebsocket(connected=False)
    mgr = _make_mgr(websocket=ws)

    ok = await mgr.send_proactive_media(
        turn_id="sid-2", images=[_GOOD_URL]
    )

    assert ok is False
    ws.send_json.assert_not_awaited()


@pytest.mark.unit
async def test_send_proactive_media_send_error_swallowed():
    ws = _FakeWebsocket(connected=True)
    ws.send_json.side_effect = RuntimeError("boom")
    mgr = _make_mgr(websocket=ws)

    ok = await mgr.send_proactive_media(
        turn_id="sid-3", images=[_GOOD_URL]
    )

    assert ok is False


# ──────────────────────────────────────────────────────────────────────
# 4. prune_proactive_media（storage_roots 清理）+ web_app 接线
# ──────────────────────────────────────────────────────────────────────


def _make_roots_stub(tmp_path):
    from utils.config_manager.storage_roots import StorageRootsMixin

    stub = StorageRootsMixin.__new__(StorageRootsMixin)
    stub.proactive_media_dir = tmp_path / "proactive_media"
    stub.proactive_media_dir.mkdir(parents=True)
    return stub


def _touch(path, age_seconds):
    path.write_bytes(b"x")
    stamp = time.time() - age_seconds
    os.utime(path, (stamp, stamp))


@pytest.mark.unit
def test_prune_drops_files_older_than_max_age(tmp_path):
    stub = _make_roots_stub(tmp_path)
    # 文件名顺序刻意与 mtime 顺序相反：按名字排序的错误实现会先删 a.png
    oldest = stub.proactive_media_dir / "z.png"
    newest = stub.proactive_media_dir / "a.png"
    _touch(oldest, 20 * 86400)
    _touch(newest, 3600)

    stub.prune_proactive_media(max_age_days=14)

    assert not oldest.exists()
    assert newest.exists()


@pytest.mark.unit
def test_prune_enforces_total_size_cap_oldest_first(tmp_path):
    stub = _make_roots_stub(tmp_path)
    # 同上：z 最旧、a 最新，字母序与 mtime 序相反，钉死"按 mtime 从旧到新"
    oldest = stub.proactive_media_dir / "z.png"
    middle = stub.proactive_media_dir / "m.png"
    newest = stub.proactive_media_dir / "a.png"
    for path, age in ((oldest, 5000), (middle, 3000), (newest, 1000)):
        path.write_bytes(b"x" * (1024 * 1024))
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))

    stub.prune_proactive_media(
        max_age_days=14, max_total_bytes=int(2.5 * 1024 * 1024)
    )

    assert not oldest.exists()  # 超总量时从最旧开始删
    assert middle.exists()
    assert newest.exists()


@pytest.mark.unit
def test_prune_cap_pass_continues_past_locked_oldest(tmp_path, monkeypatch):
    """Codex round 2: a locked oldest file (viewer holding it open on Windows)
    must not stall the whole cap pass — pruning continues with the next-oldest
    survivor, so one permanently-locked file cannot disable the cap forever."""
    from pathlib import Path

    stub = _make_roots_stub(tmp_path)
    oldest = stub.proactive_media_dir / "z.png"
    middle = stub.proactive_media_dir / "m.png"
    newest = stub.proactive_media_dir / "a.png"
    for path, age in ((oldest, 5000), (middle, 3000), (newest, 1000)):
        path.write_bytes(b"x" * (1024 * 1024))
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))

    real_unlink = Path.unlink

    def _locked_unlink(self, missing_ok=False):
        if self.name == "z.png":
            raise PermissionError("file is locked by an external viewer")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _locked_unlink)

    stub.prune_proactive_media(
        max_age_days=14, max_total_bytes=int(2.5 * 1024 * 1024)
    )

    # 最旧的被占用：跳过它继续删下一个最旧（middle），帽子在有界超帽内收敛
    assert oldest.exists()
    assert not middle.exists()
    assert newest.exists()


@pytest.mark.unit
def test_prune_never_raises_on_missing_dir(tmp_path):
    stub = _make_roots_stub(tmp_path)

    stub.prune_proactive_media()  # 目录存在但为空
    stub.proactive_media_dir.rmdir()
    stub.prune_proactive_media()  # 目录不存在也不抛


@pytest.mark.unit
def test_web_app_wires_proactive_media_dir():
    """Static wiring lock: web_app must ensure+prune the dir in the main
    process BEFORE conditionally mounting /user_proactive_media (a reorder
    breaks serving on a fresh install), and keep the daily background prune."""
    src = (
        Path(__file__).resolve().parents[2]
        / "app"
        / "main_server"
        / "web_app.py"
    ).read_text(encoding="utf-8")
    assert 'name="user_proactive_media"' in src
    assert (
        src.index("ensure_proactive_media_directory()")
        < src.index('"/user_proactive_media"')
    )
    # 周期清理：常驻进程内的上限维持（每日 to_thread，不占事件循环）
    assert "to_thread(_config_manager.prune_proactive_media)" in src
