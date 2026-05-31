from __future__ import annotations

import asyncio
import inspect
from typing import Any

from plugin.sdk.plugin import Err, Ok, SdkError

from .constants import MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING
from .entry_common import _plugin_lock


_NEKO_COMMAND_TOPIC = "neko.study_command"

_NEKO_COMMAND_HANDLERS: dict[str, str] = {
    "explain_current": "_handle_neko_explain_current",
    "quiz_me": "_handle_neko_quiz_me",
    "show_progress": "_handle_neko_show_progress",
    "start_review": "_handle_neko_start_review",
    "change_mode": "_handle_neko_change_mode",
}

_INTERRUPT_COMMANDS: frozenset[str] = frozenset(
    {
        "explain_current",
        "start_review",
        "change_mode",
    }
)

_QUEUE_COMMANDS: frozenset[str] = frozenset(
    {
        "quiz_me",
        "show_progress",
    }
)


def _fmt_explain_current_for_neko(*, text: str, explanation: str) -> str:
    return f"[伴学·概念解释]\n原文: {text}\n\n{explanation}"


def _fmt_quiz_for_neko(*, topic: str, question: str) -> str:
    header = f"[伴学·随堂测验] 主题: {topic}" if topic else "[伴学·随堂测验]"
    return f"{header}\n\n{question}"


def _fmt_progress_for_neko(
    *, items: list[dict[str, Any]], due_count: int, session_questions: int
) -> str:
    if not items:
        lines = ["[伴学·学习进度] 暂无掌握度数据。"]
        if due_count > 0:
            lines.append(f"待复习卡片: {due_count} 张")
        return "\n".join(lines)
    lines = [f"[伴学·学习进度] 本次已答 {session_questions} 题"]
    for item in items:
        lines.append(f"  {item['topic']}: {float(item['mastery']):.0%}")
    if due_count > 0:
        lines.append(f"待复习卡片: {due_count} 张")
    return "\n".join(lines)


def _fmt_review_start_for_neko(
    *, due_items: list[dict[str, Any]], deck_id: str, due_count: int
) -> str:
    deck_info = f"（牌组: {deck_id}）" if deck_id else ""
    top_topics: list[str] = []
    for item in due_items[:5]:
        deck = item.get("deck") if isinstance(item.get("deck"), dict) else {}
        name = str(deck.get("name") or "")
        if name and name not in top_topics:
            top_topics.append(name)
    topic_list = "、".join(top_topics[:5]) if top_topics else "综合"
    limit_note = (
        f"\n先展示前 {len(due_items)} 张。" if due_count > len(due_items) else ""
    )
    return (
        f"[伴学·复习提醒] {due_count} 张卡片待复习{deck_info}{limit_note}\n"
        f"涉及: {topic_list}\n"
        f"现在开始复习吗？"
    )


def _fmt_mode_changed_for_neko(*, new_mode: str, transition_phrase: str) -> str:
    mode_labels = {
        MODE_COMPANION: "伴读模式",
        MODE_INTERACTIVE: "互动模式",
        MODE_TEACHING: "教学模式",
    }
    label = mode_labels.get(new_mode, new_mode)
    phrase = f"\n{transition_phrase}" if transition_phrase else ""
    return f"[伴学·模式切换] 已切换至「{label}」{phrase}"


class _NekoCommandsMixin:
    async def _subscribe_neko_commands(self) -> None:
        await self._unsubscribe_neko_commands()
        if await self._subscribe_neko_command_transport():
            return
        if await self._subscribe_neko_command_bus():
            return
        self.logger.warning(
            "startup: message-plane transport unavailable for {}",
            _NEKO_COMMAND_TOPIC,
        )

    async def _subscribe_neko_command_transport(self) -> bool:
        transport = None
        for ctx in (
            self.ctx,
            getattr(self.ctx, "_host_ctx", None),
            getattr(self, "_host_ctx", None),
        ):
            transport = getattr(ctx, "transport", None)
            if transport is not None:
                break
        subscribe = getattr(transport, "subscribe", None)
        if not callable(subscribe):
            return False
        handler = self._on_neko_command
        result = subscribe(_NEKO_COMMAND_TOPIC, handler)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Err):
            self.logger.warning(
                "startup: failed to subscribe {}: {}",
                _NEKO_COMMAND_TOPIC,
                result.error,
            )
            return False
        self._neko_command_transport = transport
        self._neko_command_handler = handler
        return True

    async def _subscribe_neko_command_bus(self) -> bool:
        bus = getattr(getattr(self.ctx, "bus", None), "messages", None)
        get_messages = getattr(bus, "get", None)
        if not callable(get_messages):
            return False
        try:
            messages = get_messages(max_count=100)
            if inspect.isawaitable(messages):
                messages = await messages
            watch = getattr(messages, "watch", None)
            if not callable(watch):
                return False
            watcher = watch(self.ctx, bus="messages", debounce_ms=0)
            loop = asyncio.get_running_loop()

            def _on_messages_added(delta: Any) -> None:
                self._dispatch_neko_command_messages(delta, loop)

            watcher.subscribe(on="add")(_on_messages_added)
            watcher.start()
        except Exception as exc:
            self.logger.warning(
                "startup: failed to subscribe {} via messages bus: {}",
                _NEKO_COMMAND_TOPIC,
                exc,
            )
            return False
        self._neko_command_watcher = watcher
        return True

    def _dispatch_neko_command_messages(self, delta: Any, loop: asyncio.AbstractEventLoop) -> None:
        for record in getattr(delta, "added", ()) or ():
            payload = self._extract_neko_command_payload(record)
            if payload is None:
                continue

            def _create_task(command_payload: dict[str, Any] = payload) -> None:
                asyncio.create_task(self._on_neko_command(command_payload))

            try:
                loop.call_soon_threadsafe(_create_task)
            except RuntimeError:
                self.logger.warning(
                    "neko command bus callback ignored after event loop closed"
                )

    @staticmethod
    def _extract_neko_command_payload(record: Any) -> dict[str, Any] | None:
        metadata = getattr(record, "metadata", None)
        description = getattr(record, "description", "")
        raw = getattr(record, "raw", None)
        candidates: list[tuple[Any, Any]] = []
        if isinstance(metadata, dict):
            candidates.append((metadata.get("topic") or description, metadata.get("payload")))
        if isinstance(raw, dict):
            raw_metadata = raw.get("metadata")
            if isinstance(raw_metadata, dict):
                candidates.append(
                    (
                        raw_metadata.get("topic") or raw.get("description"),
                        raw_metadata.get("payload"),
                    )
                )
            raw_payload = raw.get("payload")
            if isinstance(raw_payload, dict):
                payload_metadata = raw_payload.get("metadata")
                if isinstance(payload_metadata, dict):
                    candidates.append(
                        (
                            payload_metadata.get("topic")
                            or raw_payload.get("description"),
                            payload_metadata.get("payload"),
                        )
                    )
        for topic, payload in candidates:
            if str(topic or "").strip() == _NEKO_COMMAND_TOPIC and isinstance(
                payload, dict
            ):
                return dict(payload)
        return None

    async def _unsubscribe_neko_commands(self) -> None:
        watcher = getattr(self, "_neko_command_watcher", None)
        self._neko_command_watcher = None
        if watcher is not None:
            stop = getattr(watcher, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:
                    self.logger.warning(
                        "shutdown: failed to stop {} messages bus watcher: {}",
                        _NEKO_COMMAND_TOPIC,
                        exc,
                    )

        transport = getattr(self, "_neko_command_transport", None)
        handler = getattr(self, "_neko_command_handler", None)
        self._neko_command_transport = None
        self._neko_command_handler = None
        if transport is None or handler is None:
            return
        unsubscribe = getattr(transport, "unsubscribe", None)
        if not callable(unsubscribe):
            self.logger.warning(
                "shutdown: message-plane transport cannot unsubscribe {}",
                _NEKO_COMMAND_TOPIC,
            )
            return
        try:
            result = unsubscribe(_NEKO_COMMAND_TOPIC, handler)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            self.logger.warning(
                "shutdown: failed to unsubscribe {}: {}",
                _NEKO_COMMAND_TOPIC,
                exc,
            )
            return
        if isinstance(result, Err):
            self.logger.warning(
                "shutdown: failed to unsubscribe {}: {}",
                _NEKO_COMMAND_TOPIC,
                result.error,
            )

    async def _on_neko_command(self, payload: dict[str, Any]):
        if self._event_bus is None:
            return Err(SdkError("Neko communication is not enabled"))
        if not isinstance(payload, dict):
            self.logger.warning("_on_neko_command received invalid payload")
            return Err(SdkError("invalid command payload"))
        cmd = str(payload.get("command") or "").strip()
        if not cmd:
            self.logger.warning("_on_neko_command received empty command")
            return Err(SdkError("empty command"))

        handler_name = _NEKO_COMMAND_HANDLERS.get(cmd)
        if handler_name is None:
            self.logger.warning("_on_neko_command unknown command: {}", cmd)
            return Err(SdkError(f"unknown command: {cmd}"))

        handler = getattr(self, handler_name, None)
        if handler is None:
            self.logger.error("_on_neko_command handler not found: {}", handler_name)
            return Err(SdkError(f"handler not found: {handler_name}"))

        if cmd in _INTERRUPT_COMMANDS:
            current = self._interruptible_task
            self._interruptible_task = None
            if current is not None and not current.done():
                current.cancel()

            async def _run_interruptible() -> None:
                try:
                    await handler(payload)
                except asyncio.CancelledError:
                    pass

            self._interruptible_task = asyncio.create_task(_run_interruptible())
            self._interruptible_task.add_done_callback(self._on_command_task_done)
            return Ok(None)

        if cmd not in _QUEUE_COMMANDS:
            self.logger.warning("_on_neko_command unclassified command: {}", cmd)
            return Err(SdkError(f"unclassified command: {cmd}"))

        if self._command_worker_task is None or self._command_worker_task.done():
            self.logger.warning("_on_neko_command: worker not running, restarting")
            self._start_command_worker()
        await self._command_queue.put((cmd, payload))
        return Ok(None)

    async def _handle_neko_explain_current(self, payload: dict[str, Any]) -> None:
        text = ""
        if self._ocr_pipeline is not None:
            try:
                snapshot = await asyncio.to_thread(self._ocr_pipeline.capture_snapshot)
                text = str(getattr(snapshot, "text", "") or "").strip()
            except Exception:
                self.logger.exception("explain_current: OCR snapshot failed")

        if not text:
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=5,
                text="[伴学] 当前屏幕无可识别的文字内容。",
            )
            return

        try:
            result = await self.study_explain_text(text=text[:2000])
        except Exception:
            self.logger.exception("explain_current: study_explain_text failed")
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=5,
                text="[伴学] 抱歉，概念解释请求处理失败，请稍后再试。",
            )
            return

        if isinstance(result, Ok):
            reply = result.value if isinstance(result.value, dict) else {}
            explanation = str(
                reply.get("explanation") or reply.get("reply") or result.value or ""
            )
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=5,
                text=_fmt_explain_current_for_neko(
                    text=text[:300], explanation=explanation
                ),
            )
            return

        self.logger.warning(
            "explain_current: study_explain_text failed: {}",
            getattr(result, "error", result),
        )
        await self._push_neko_command_message(
            visibility=[],
            ai_behavior="respond",
            priority=5,
            text="[伴学] 抱歉，概念解释请求处理失败，请稍后再试。",
        )

    async def _handle_neko_quiz_me(self, payload: dict[str, Any]) -> None:
        topic = str(payload.get("topic") or "").strip()
        text = str(payload.get("text") or "").strip()
        async with _plugin_lock(self._lock):
            has_cached_ocr = bool(self._state.last_ocr_text.strip())
        if not topic and not text and not has_cached_ocr:
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=5,
                text="[伴学] 请指定题目主题或当前屏幕有可识别内容。",
            )
            return

        question_text = text or topic
        try:
            result = await self.study_generate_question(text=question_text, topic=topic)
        except Exception:
            self.logger.exception("quiz_me: study_generate_question failed")
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=5,
                text="[伴学] 抱歉，题目生成请求处理失败，请稍后再试。",
            )
            return

        if isinstance(result, Ok):
            reply = result.value if isinstance(result.value, dict) else {}
            question = str(
                reply.get("question") or reply.get("reply") or result.value or ""
            )
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=5,
                text=_fmt_quiz_for_neko(topic=topic, question=question),
            )
            return

        self.logger.warning(
            "quiz_me: study_generate_question failed: {}",
            getattr(result, "error", result),
        )
        await self._push_neko_command_message(
            visibility=[],
            ai_behavior="respond",
            priority=5,
            text="[伴学] 抱歉，题目生成请求处理失败，请稍后再试。",
        )

    async def _handle_neko_show_progress(self, payload: dict[str, Any]) -> None:
        topic = str(payload.get("topic") or "").strip()
        progress_items: list[dict[str, Any]] = []
        if topic:
            topic_row = await asyncio.to_thread(self._store.get_topic, topic)
            if topic_row is None:
                topic_row = await asyncio.to_thread(
                    self._store.find_topic_by_name, topic
                )
            topic_id = str((topic_row or {}).get("id") or topic).strip()
            mastery = await asyncio.to_thread(
                self._knowledge_tracker.get_mastery, topic_id
            )
            if topic_row is not None or mastery > 0:
                progress_items.append(
                    {
                        "topic": str((topic_row or {}).get("name") or topic),
                        "mastery": mastery,
                    }
                )
        else:
            overview = await asyncio.to_thread(
                self._store.list_mastery_overview, limit=10
            )
            for entry in overview or []:
                progress_items.append(
                    {
                        "topic": entry.get("topic_name") or entry.get("topic_id") or "",
                        "mastery": entry.get("mastery") or 0.0,
                    }
                )

        due_count = 0
        if self._memory_deck_store is not None:
            try:
                due_count = await asyncio.to_thread(
                    self._memory_deck_store.count_due_reviews
                )
            except Exception:
                self.logger.exception("show_progress: count_due_reviews failed")

        progress_items = [
            item for item in progress_items if str(item.get("topic") or "").strip()
        ]
        progress_items.sort(
            key=lambda item: float(item.get("mastery") or 0.0), reverse=True
        )
        session_questions = await self._read_neko_session_answer_count()
        await self._push_neko_command_message(
            visibility=[],
            ai_behavior="read",
            priority=2,
            text=_fmt_progress_for_neko(
                items=progress_items,
                due_count=int(due_count or 0),
                session_questions=session_questions,
            ),
        )

    async def _handle_neko_start_review(self, payload: dict[str, Any]) -> None:
        deck_id = str(payload.get("deck_id") or "").strip()
        if self._memory_deck_store is None:
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=3,
                text="[伴学] 记忆卡片系统未就绪。",
            )
            return

        try:
            due_count = await asyncio.to_thread(
                self._memory_deck_store.count_due_reviews, deck_id=deck_id
            )
            due_items = await asyncio.to_thread(
                self._memory_deck_store.due_reviews, deck_id=deck_id, limit=20
            )
        except Exception:
            self.logger.exception("start_review: due_reviews failed")
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=3,
                text="[伴学] 抱歉，复习卡片查询失败，请稍后再试。",
            )
            return

        if not due_items:
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=3,
                text="[伴学] 太棒了，当前没有到期卡片需要复习！",
            )
            return

        await self._push_neko_command_message(
            visibility=[],
            ai_behavior="respond",
            priority=3,
            text=_fmt_review_start_for_neko(
                due_items=due_items,
                deck_id=deck_id,
                due_count=int(due_count or len(due_items)),
            ),
        )

    async def _handle_neko_change_mode(self, payload: dict[str, Any]) -> None:
        mode = str(payload.get("mode") or "").strip()
        if mode not in (MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING):
            self.logger.warning("change_mode: invalid mode: {}", mode)
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="read",
                priority=1,
                text=f"[伴学] 不支持的模式: {mode}",
            )
            return

        try:
            result = await self.study_set_mode(mode=mode, reason="neko_command")
        except Exception as exc:
            self.logger.exception("change_mode: study_set_mode failed")
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="respond",
                priority=1,
                text=f"[伴学] 抱歉，模式切换失败：{exc}",
            )
            raise

        if isinstance(result, Ok):
            reply = result.value if isinstance(result.value, dict) else {}
            await self._push_neko_command_message(
                visibility=[],
                ai_behavior="read",
                priority=1,
                text=_fmt_mode_changed_for_neko(
                    new_mode=mode,
                    transition_phrase=str(reply.get("transition_phrase") or ""),
                ),
            )
            return

        self.logger.warning(
            "change_mode: study_set_mode failed: {}",
            getattr(result, "error", result),
        )
        error = getattr(result, "error", result)
        await self._push_neko_command_message(
            visibility=[],
            ai_behavior="respond",
            priority=1,
            text=f"[伴学] 抱歉，模式切换失败：{error}",
        )
        raise RuntimeError(f"study_set_mode failed: {error}")

    async def _read_neko_session_answer_count(self) -> int:
        lock = getattr(self, "_lock", None)
        if hasattr(lock, "__aenter__"):
            async with lock:
                return self._neko_session_answer_count_unlocked()
        if hasattr(lock, "__enter__"):
            return await asyncio.to_thread(self._read_neko_session_answer_count_sync)
        return self._neko_session_answer_count_unlocked()

    def _read_neko_session_answer_count_sync(self) -> int:
        with self._lock:
            return self._neko_session_answer_count_unlocked()

    def _neko_session_answer_count_unlocked(self) -> int:
        try:
            return int(self._state.session_summary_seed.get("answer_count") or 0)
        except (TypeError, ValueError):
            return 0

    async def _push_neko_command_message(
        self,
        *,
        visibility: list[str],
        ai_behavior: str,
        priority: int,
        text: str,
    ) -> None:
        result = self.ctx.push_message(
            visibility=visibility,
            ai_behavior=ai_behavior,
            priority=priority,
            parts=[{"type": "text", "text": text}],
            source="study_companion",
        )
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Err):
            raise RuntimeError(f"neko command push_message failed: {result.error}")
        if isinstance(result, dict) and result.get("ok") is False:
            raise RuntimeError(
                f"neko command push_message failed: {result.get('error') or result}"
            )


__all__ = [
    "_INTERRUPT_COMMANDS",
    "_NEKO_COMMAND_HANDLERS",
    "_NekoCommandsMixin",
    "_QUEUE_COMMANDS",
]
