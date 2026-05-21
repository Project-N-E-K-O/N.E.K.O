from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403


class AgentSyncMixin:
    @property
    def _recent_pushes(self) -> list[dict[str, Any]]:
        return self._message_router.recent_push_records()

    @_recent_pushes.setter
    def _recent_pushes(self, value: list[dict[str, Any]]) -> None:
        self._scene_tracker.recent_pushes = value

    @property
    def _inbound_messages(self) -> list[dict[str, Any]]:
        return self._message_router.inbound_messages

    @_inbound_messages.setter
    def _inbound_messages(self, value: list[dict[str, Any]]) -> None:
        self._message_router.inbound_messages = value

    @property
    def _outbound_messages(self) -> list[dict[str, Any]]:
        return self._message_router.outbound_messages

    @_outbound_messages.setter
    def _outbound_messages(self, value: list[dict[str, Any]]) -> None:
        self._message_router.outbound_messages = value

    @property
    def _last_interruption(self) -> dict[str, Any]:
        return self._message_router.last_interruption

    @_last_interruption.setter
    def _last_interruption(self, value: dict[str, Any]) -> None:
        self._message_router.last_interruption = dict(value or {})

    async def _maybe_push_focus_lost_notification(self, shared: dict[str, Any]) -> None:
        if self._focus_failure_count != self._FOCUS_FAILURE_PUSH_THRESHOLD:
            return
        if not self._should_push_scene(shared):
            return
        diagnostic = self._target_window_focus_diagnostic(shared)
        if not diagnostic:
            return
        snapshot = sanitize_snapshot_state(shared.get("latest_snapshot", {}))
        await self._push_agent_message(
            shared,
            kind="focus_lost",
            content=diagnostic,
            scene_id=str(snapshot.get("scene_id") or ""),
            route_id=str(snapshot.get("route_id") or ""),
            priority=8,
        )

    def _pending_cat_consultation_message(
        self,
        *,
        message_id: str = "",
    ) -> dict[str, Any] | None:
        target_id = str(message_id or "").strip()
        for outbound in reversed(self._outbound_messages):
            if str(outbound.get("kind") or "") != "cat_consultation":
                continue
            if target_id and str(outbound.get("message_id") or "") != target_id:
                continue
            if str(outbound.get("acked_at") or ""):
                continue
            if str(outbound.get("status") or "") not in {"delivered", "completed"}:
                continue
            return outbound
        return None

    def _new_message_id(self, *, direction: str, kind: str) -> str:
        return self._message_router.new_message_id(direction=direction, kind=kind)

    @staticmethod
    def _utc_now_iso() -> str:
        return str(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

    def _interruptible_activity_id(self) -> str:
        if self._actuation is not None:
            task_id = str(self._actuation.get("task_id") or "")
            strategy_id = str(self._actuation.get("strategy_id") or "")
            kind = str(self._actuation.get("kind") or "actuation")
            return task_id or (f"{kind}:{strategy_id}" if strategy_id else kind)
        if self._planning_task is not None:
            return "planning:choice"
        if self._pending_strategy is not None:
            strategy_id = str(self._pending_strategy.get("strategy_id") or "")
            kind = str(self._pending_strategy.get("kind") or "retry")
            return f"{kind}:{strategy_id}" if strategy_id else kind
        return ""

    def _enqueue_inbound_message(
        self,
        *,
        kind: str,
        content: str,
        priority: int = 5,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._message_router.enqueue_inbound(
            kind=kind,
            content=content,
            priority=priority,
            metadata=metadata,
        )

    def _mark_message(
        self,
        message: dict[str, Any],
        *,
        status: str,
        delivered: bool = False,
        acked: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._message_router.mark_message(
            message,
            status=status,
            delivered=delivered,
            acked=acked,
            metadata=metadata,
        )

    async def _interrupt_for_inbound_message(self, message: dict[str, Any]) -> None:
        interrupted_message_id = self._interruptible_activity_id()
        await self._interrupt_current()
        if interrupted_message_id:
            metadata = message.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["interrupted_message_id"] = interrupted_message_id
            message["metadata"] = metadata
            self._last_interruption = {
                "message_id": str(message.get("message_id") or ""),
                "kind": str(message.get("kind") or ""),
                "interrupted_message_id": interrupted_message_id,
                "ts": self._utc_now_iso(),
            }

    def _enqueue_outbound_message(
        self,
        *,
        kind: str,
        content: str,
        scene_id: str,
        route_id: str,
        priority: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._message_router.enqueue_outbound(
            kind=kind,
            content=content,
            scene_id=scene_id,
            route_id=route_id,
            priority=priority,
            metadata=metadata,
        )

    def _recent_push_records(self) -> list[dict[str, Any]]:
        return self._message_router.recent_push_records()

    def _message_queue_snapshot(
        self,
        *,
        direction: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        return self._message_router.snapshot(direction=direction, limit=limit)

    async def list_messages(
        self,
        shared: dict[str, Any],
        *,
        direction: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        self._ensure_loop_affinity()
        await self._observe(shared, allow_agent_side_effects=False)
        return {
            "action": "list_messages",
            **self._message_queue_snapshot(direction=direction, limit=limit),
        }

    async def ack_message(self, shared: dict[str, Any], *, message_id: str) -> dict[str, Any]:
        self._ensure_loop_affinity()
        await self._observe(shared)
        target_id = str(message_id or "").strip()
        message = self._message_router.ack_message(target_id)
        if message is not None:
            return {
                "action": "ack_message",
                "message": json_copy(message),
                **self._message_queue_snapshot(limit=20),
            }
        return {
            "action": "ack_message",
            "message": None,
            "diagnostic": f"unknown message_id: {target_id}",
            **self._message_queue_snapshot(limit=20),
        }

    def _handle_low_frequency_control_message(
        self,
        shared: dict[str, Any],
        *,
        message: str,
    ) -> dict[str, Any] | None:
        normalized = str(message or "").strip()
        if not normalized:
            return None
        if any(token in normalized for token in ("暂停剧情", "暂停推进", "先暂停", "暂停游戏")):
            self._explicit_standby = True
            status = self._compute_status(shared)
            self._last_status = status
            return {
                "action": "send_message",
                "result": "已按猫娘消息暂停游戏 LLM 自动推进。",
                "status": status,
                "degraded": False,
                "diagnostic": "",
                "input_source": self._current_input_source(shared),
            }
        if any(token in normalized for token in ("继续推动剧情", "继续推进", "继续剧情", "恢复推进")):
            self._explicit_standby = False
            self._next_actuation_at = 0.0
            status = self._compute_status(shared)
            self._last_status = status
            return {
                "action": "send_message",
                "result": "已按猫娘消息恢复游戏 LLM，可在允许模式下继续推进。",
                "status": status,
                "degraded": False,
                "diagnostic": "",
                "input_source": self._current_input_source(shared),
            }
        if any(token in normalized for token in ("保存存档", "存档", "保存游戏")):
            status = self._compute_status(shared)
            self._last_status = status
            return {
                "action": "send_message",
                "result": "已收到保存存档请求，但通用空存档自动保存尚未接入。",
                "status": status,
                "degraded": True,
                "diagnostic": (
                    "save_not_available: 需要游戏专用存档 skill 或用户确认空存档位，"
                    "当前不会静默假装已保存。"
                ),
                "input_source": self._current_input_source(shared),
            }
        return None

    async def send_message(
        self,
        shared: dict[str, Any],
        *,
        message: str,
        reply_to_message_id: str = "",
        sender_role: str = "",
        consultation_reply: bool = False,
    ) -> dict[str, Any]:
        self._ensure_loop_affinity()
        inbound: dict[str, Any]
        reply_context: dict[str, Any] | None = None
        status_snapshot: str | None = None
        input_source_snapshot: str | None = None
        await self._observe(shared)
        inbound = self._enqueue_inbound_message(
            kind="send_message",
            content=message,
            priority=8,
            metadata={
                "reply_to_message_id": str(reply_to_message_id or "").strip(),
                "sender_role": str(sender_role or "").strip(),
                "consultation_reply": bool(consultation_reply),
            },
        )
        self._mark_message(inbound, status="processing")
        try:
            await self._interrupt_for_inbound_message(inbound)
            self._recover_retryable_error_if_ready(time.monotonic())
            pending_consultation = self._is_explicit_cat_consultation_reply(inbound)
            if pending_consultation is not None:
                consultation_payload = self._apply_pending_cat_consultation_reply(
                    shared,
                    message=message,
                    pending=pending_consultation,
                )
                if consultation_payload is not None:
                    self._mark_message(inbound, status="completed", delivered=True)
                    consultation_payload["message"] = json_copy(inbound)
                    return consultation_payload

            control_payload = self._handle_low_frequency_control_message(shared, message=message)
            if control_payload is not None:
                self._mark_message(inbound, status="completed", delivered=True)
                control_payload["message"] = json_copy(inbound)
                return control_payload

            choice_payload = await self._apply_pending_choice_advice(shared, message=message)
            if choice_payload is not None:
                self._mark_message(inbound, status="completed", delivered=True)
                choice_payload["message"] = json_copy(inbound)
                return choice_payload

            reply_context = self._build_agent_reply_context(shared, prompt=message)
            status_snapshot = self._compute_status(shared)
            input_source_snapshot = self._current_input_source(shared)
        except Exception as exc:
            self._mark_message(
                inbound,
                status="failed",
                metadata={"error": str(exc)},
            )
            raise
        try:
            if (
                reply_context is None
                or status_snapshot is None
                or input_source_snapshot is None
            ):
                raise RuntimeError("send_message reached LLM call without a reply context")
            async with self._agent_reply_lock:
                payload = await self._llm_gateway.agent_reply(reply_context)
        except Exception as exc:
            self._mark_message(
                inbound,
                status="failed",
                metadata={"error": str(exc)},
            )
            raise
        self._last_status = status_snapshot
        self._mark_message(inbound, status="completed", delivered=True)
        return {
            "action": "send_message",
            "result": str(payload.get("reply") or ""),
            "status": status_snapshot,
            "degraded": bool(payload.get("degraded")),
            "diagnostic": str(payload.get("diagnostic") or ""),
            "input_source": input_source_snapshot,
            "message": json_copy(inbound),
        }

    async def _push_agent_message(
        self,
        shared: dict[str, Any],
        *,
        kind: str,
        content: str,
        scene_id: str,
        route_id: str,
        metadata: dict[str, Any] | None = None,
        priority: int = 6,
    ) -> bool:
        if not content:
            return False
        # host-play-mode plan, step 12: when fixed character mode is on, prepend
        # the catgirl-facing character anchor so every push is self-contained
        # (catgirl never needs prior pushes to make sense of the current one).
        content = self._maybe_augment_with_cross_scene_memory(
            shared, content, kind=kind
        )
        content = self._maybe_augment_with_character_anchor(
            shared, content, kind=kind
        )
        outbound = self._enqueue_outbound_message(
            kind=kind,
            content=content,
            scene_id=scene_id,
            route_id=route_id,
            priority=priority,
            metadata=metadata,
        )
        outbound_metadata = dict(outbound.get("metadata") or {})
        if bool(outbound_metadata.pop("suppress_delivery", False)):
            outbound_metadata["suppressed"] = True
            outbound["metadata"] = outbound_metadata
            self._mark_message(outbound, status="completed", delivered=False)
            self._recent_pushes = self._recent_push_records()
            return False
        delivered = False
        try:
            # push_message is synchronous in the plugin SDK; keep this call inline
            # so delivery failures can be caught and retried below.
            self._plugin.push_message(
                source=str(getattr(self._plugin, "plugin_id", "") or "galgame_plugin"),
                message_type="proactive_notification",
                description=f"Galgame Agent | {kind}",
                priority=priority,
                content=content,
                metadata=outbound_metadata,
            )
            self._mark_message(outbound, status="delivered", delivered=True)
            delivered = True
        except Exception as exc:
            self._logger.warning("galgame outbound message delivery failed (will retry): {}", exc)
            try:
                await asyncio.sleep(1.0)
                # push_message is synchronous in the plugin SDK; retry inline.
                self._plugin.push_message(
                    source=str(getattr(self._plugin, "plugin_id", "") or "galgame_plugin"),
                    message_type="proactive_notification",
                    description=f"Galgame Agent | {kind}",
                    priority=priority,
                    content=content,
                    metadata=outbound_metadata,
                )
                self._mark_message(
                    outbound,
                    status="delivered",
                    delivered=True,
                    metadata={"retried": True, "initial_error": str(exc)},
                )
                delivered = True
            except Exception as retry_exc:
                self._mark_message(outbound, status="failed", metadata={
                    "error": str(retry_exc), "initial_error": str(exc), "retried": True,
                })
                self._logger.warning("galgame outbound message retry also failed: {}", retry_exc)
        self._recent_pushes = self._recent_push_records()
        # host-play-mode plan, step 19 / G4: record minimal push metadata for the
        # `galgame_get_push_history` query entry. Never store original line text
        # here — it goes to the privacy log path only.
        self._record_push_history(
            outbound,
            kind=kind,
            scene_id=scene_id,
            content_len=len(content),
        )
        return delivered
