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

"""What a visit runtime needs from the local character's ``SessionManager``.

The runtime never touches ``mgr.session`` or the manager's internals directly:
everything goes through :class:`VisitHost`, so the runtime can be tested
with a fake host and PR-09b only has to fill the two gaps the manager does
not have yet (``open_mirror_speech_stream`` and the ``visit_bind`` filter of
the display socket). :class:`ManagerHost` is the production adapter.

Display-socket frames (``visit_*`` / ``status``) go out through
:meth:`VisitHost.send_frame` / :meth:`VisitHost.send_status`; PR-09b routes
them to bound connections only (design §4.5 ``visit_bind``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Optional, Protocol

from main_routers.visit_router.line_speaker import SpeechStream
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Main")

TAKEOVER_OWNER = "neko_visit"
"""Takeover owner of every visit (OD-24)."""

_FRAME_TIMEOUT_S = 2.0
_TURN_IDLE_POLL_S = 0.2


class VisitHost(Protocol):
    """Session-manager capabilities used by one visit runtime."""

    lanlan_name: str

    def is_current(self) -> bool:
        """False once the character's manager was replaced (``manager_replaced``)."""

    def precondition_failure(self) -> Optional[str]:
        """Why a visit cannot start now (``voice_session_active`` / ``goodbye_silent`` / ``busy``), or None."""

    async def interrupt_main_turn(self, timeout: float) -> bool:
        """Stop a reply the ordinary session is producing; False when it did not settle in time."""

    async def send_frame(self, payload: dict) -> bool:
        """One display-socket frame (``visit_*``); False when it could not be written."""

    async def send_status(self, code: str, details: Optional[dict] = None) -> bool:
        """``{type:'status', message:{code, details}}`` (§4.5); never carries text or tickets."""

    def acquire_takeover(self, dispatcher: Callable[..., Awaitable[bool]], sink: Callable[[dict], bool]) -> Any:
        """Take over the session for ``neko_visit``; raises when another owner holds it."""

    def release_takeover(self, token: Any) -> bool:
        """Release exactly ``token``."""

    async def interrupt_ordinary_speech(self) -> None:
        """Cut ordinary speech right after acquiring the takeover."""

    def hold_callbacks(self, sink: Callable[[dict], bool]) -> Any:
        """Park respond-type callbacks in ``sink`` (independent of the takeover)."""

    def release_callback_hold(self, token: Any) -> bool:
        """Stop parking callbacks."""

    def resubmit_callbacks(self, callbacks: Iterable[dict]) -> None:
        """Hand parked callbacks back to ordinary proactive delivery (declined when impossible)."""

    def open_speech_stream(
        self, *, metadata: dict, request_id: str, on_enqueued: Callable[[int], None],
    ) -> Optional[SpeechStream]:
        """One streaming mirror speech (``SessionManager.open_mirror_speech_stream``, PR-09b)."""

    async def mirror_user_input(self, text: str, *, metadata: dict, request_id: Optional[str]) -> None:
        """Record the family's visit line in the sync stream (never in private chat history)."""

    async def mirror_assistant_output(self, text: str, *, metadata: dict, request_id: str) -> None:
        """Show an assistant line without speaking it (not in private chat history)."""

    async def render_chat_blocks(self, blocks: list[dict], *, request_id: str, source_name: str) -> bool:
        """Render a system message with blocks (debrief chips)."""

    def park_proactive(self) -> None:
        """``_park_proactive_for_goodbye`` at activation."""

    def last_user_input(self) -> float:
        """Wall time of the latest ordinary user input (0 when none)."""

    async def wait_turn_idle(self, timeout: float, *, start_window: float = 0.0) -> None:
        """Wait until the ordinary session is not producing a reply (bounded).

        ``start_window`` > 0: first wait up to that long for a reply to start
        (input that was just ingressed may not have started one yet).
        """

    async def ack_text_session(self, request_id: Optional[str]) -> None:
        """Acknowledge a text ``start_session`` without starting an ordinary text session."""

    async def fail_session(self, mode: str, request_id: Optional[str]) -> None:
        """Answer a ``start_session`` the visit refuses (voice while visiting)."""


class ManagerHost:
    """:class:`VisitHost` over the character's ``LLMSessionManager``."""

    def __init__(self, lanlan_name: str, mgr: Any) -> None:
        self.lanlan_name = lanlan_name
        self._mgr = mgr

    @classmethod
    def for_character(cls, lanlan_name: str) -> Optional["ManagerHost"]:
        """The host of ``lanlan_name``'s current manager, or None when it has none."""
        from main_routers.shared_state import get_session_manager

        mgr = get_session_manager().get(lanlan_name)
        return cls(lanlan_name, mgr) if mgr is not None else None

    def is_current(self) -> bool:
        from main_routers.shared_state import get_session_manager

        try:
            return get_session_manager().get(self.lanlan_name) is self._mgr
        except Exception:  # noqa: BLE001 - 读不到当作已被替换：fail closed
            return False

    def precondition_failure(self) -> Optional[str]:
        mgr = self._mgr
        try:
            if mgr._is_voice_session_active_or_starting():
                return "voice_session_active"
        except Exception:  # noqa: BLE001
            return "busy"
        if getattr(mgr, "is_goodbye_silent", None) and mgr.is_goodbye_silent():
            return "goodbye_silent"
        if getattr(mgr, "is_hot_swap_imminent", False) or getattr(mgr, "_starting_session_count", 0) > 0:
            return "busy"
        return None

    async def interrupt_main_turn(self, timeout: float) -> bool:
        session = getattr(self._mgr, "session", None)
        if session is None or not getattr(session, "_is_responding", False):
            return True
        # 打断与等 turn end 共用一个期限。不用 wait_for：它到点后还会等被取消的协程真正结束，
        # 不肯停的打断会把发凭证（以及占着的串门路由）一直挂住
        deadline = time.monotonic() + timeout
        interrupting = asyncio.ensure_future(session.handle_interruption())
        interrupting.add_done_callback(lambda t: t.cancelled() or t.exception())
        await asyncio.wait([interrupting], timeout=timeout)
        if not interrupting.done():
            interrupting.cancel()
            logger.warning("visit: main turn interruption did not finish in time")
            return False
        if interrupting.cancelled() or interrupting.exception() is not None:
            # 打断失败按 busy 拒绝
            logger.warning("visit: main turn interruption failed: %s",
                           "cancelled" if interrupting.cancelled() else type(interrupting.exception()).__name__)
            return False
        while getattr(session, "_is_responding", False) and time.monotonic() < deadline:
            await asyncio.sleep(_TURN_IDLE_POLL_S)
        return not getattr(session, "_is_responding", False)

    async def send_frame(self, payload: dict) -> bool:
        ws = getattr(self._mgr, "websocket", None)
        if ws is None or not hasattr(ws, "send_json"):
            return False
        state = getattr(ws, "client_state", None)
        if state is not None and state != state.CONNECTED:
            return False
        try:
            await asyncio.wait_for(ws.send_json(payload), _FRAME_TIMEOUT_S)
            return True
        except Exception as exc:  # noqa: BLE001 - 页面不在 / 卡住：串门照常进行
            logger.debug("visit: display frame %s not written: %s", payload.get("type"), type(exc).__name__)
            return False

    async def send_status(self, code: str, details: Optional[dict] = None) -> bool:
        message = json.dumps({"code": code, "details": dict(details or {})}, ensure_ascii=False)
        try:
            # 与 send_frame 一样有界：页面卡住不能把收尾流程（封存、注销）一起卡住
            return bool(await asyncio.wait_for(self._mgr.send_status(message), _FRAME_TIMEOUT_S))
        except Exception as exc:  # noqa: BLE001
            logger.debug("visit: status %s not written: %s", code, type(exc).__name__)
            return False

    def acquire_takeover(self, dispatcher: Callable[..., Awaitable[bool]], sink: Callable[[dict], bool]) -> Any:
        return self._mgr.acquire_takeover(TAKEOVER_OWNER, dispatcher, callback_sink=sink)

    def release_takeover(self, token: Any) -> bool:
        return bool(self._mgr.release_takeover(token))

    async def interrupt_ordinary_speech(self) -> None:
        await self._mgr.interrupt_ordinary_speech_for_takeover()

    def hold_callbacks(self, sink: Callable[[dict], bool]) -> Any:
        return self._mgr.hold_callbacks(sink, owner=TAKEOVER_OWNER)

    def release_callback_hold(self, token: Any) -> bool:
        return bool(self._mgr.release_callback_hold(token))

    def resubmit_callbacks(self, callbacks: Iterable[dict]) -> None:
        from main_logic.proactive_delivery import resolve_callback_delivery_ack

        submit = getattr(self._mgr, "submit_proactive_callback", None)
        for callback in callbacks:
            if callable(submit):
                try:
                    submit(callback, priority=callback.get("priority", 0),
                           coalesce_key=callback.get("coalesce_key") or None)
                    continue
                except Exception as exc:  # noqa: BLE001
                    logger.warning("visit inbox handoff failed: %s", type(exc).__name__)
            resolve_callback_delivery_ack(callback, False)

    def open_speech_stream(
        self, *, metadata: dict, request_id: str, on_enqueued: Callable[[int], None],
    ) -> Optional[SpeechStream]:
        opener = getattr(self._mgr, "open_mirror_speech_stream", None)
        if not callable(opener):
            # PR-09b 才提供：没有时本场按估时放字幕（与 TTS 未就绪同一条路径）
            return None
        return opener(metadata=metadata, request_id=request_id, on_enqueued=on_enqueued)

    async def mirror_user_input(self, text: str, *, metadata: dict, request_id: Optional[str]) -> None:
        await self._mgr.mirror_user_input(text, metadata=metadata, request_id=request_id, send_to_frontend=False)

    async def mirror_assistant_output(self, text: str, *, metadata: dict, request_id: str) -> None:
        # 有界：收尾流程在它之后才封存文件、注销，页面卡住不能把这些一起卡住
        try:
            await asyncio.wait_for(
                self._mgr.mirror_assistant_output(text, metadata=metadata, request_id=request_id), _FRAME_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("visit: assistant mirror timed out")

    async def render_chat_blocks(self, blocks: list[dict], *, request_id: str, source_name: str) -> bool:
        try:
            return bool(await asyncio.wait_for(self._mgr.render_chat_blocks(
                blocks, request_id=request_id, source="system", source_name=source_name,
            ), _FRAME_TIMEOUT_S))
        except asyncio.TimeoutError:
            logger.warning("visit: chat blocks timed out")
            return False

    def park_proactive(self) -> None:
        park = getattr(self._mgr, "_park_proactive_for_goodbye", None)
        if callable(park):
            park()

    def last_user_input(self) -> float:
        # 只认真实用户输入（非空、过了回声抑制）；last_user_activity_time 会被回声 / 空转写 / start_session 刷新
        value = getattr(self._mgr, "last_user_message_time", None)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0

    async def wait_turn_idle(self, timeout: float, *, start_window: float = 0.0) -> None:
        deadline = time.monotonic() + timeout
        start_by = time.monotonic() + max(0.0, min(start_window, timeout))
        started = False
        while time.monotonic() < deadline:
            session = getattr(self._mgr, "session", None)
            responding = session is not None and getattr(session, "_is_responding", False)
            if responding:
                started = True
            elif started or time.monotonic() >= start_by:
                return
            await asyncio.sleep(_TURN_IDLE_POLL_S)

    async def ack_text_session(self, request_id: Optional[str]) -> None:
        # 与其它页面写入一样有界：外部路由分派在等它，页面不收也不能把这条请求卡死
        try:
            await asyncio.wait_for(self._mgr.send_session_started("text", request_id=request_id), _FRAME_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("visit: session ack timed out")

    async def fail_session(self, mode: str, request_id: Optional[str]) -> None:
        if request_id:
            try:
                await asyncio.wait_for(self._mgr.send_session_failed(mode, request_id=request_id), _FRAME_TIMEOUT_S)
            except asyncio.TimeoutError:
                logger.warning("visit: session failure notice timed out")
