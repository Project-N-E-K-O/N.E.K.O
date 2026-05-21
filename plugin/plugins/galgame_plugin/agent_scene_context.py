from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .agent_prompt import _bounded_choice_instruction_text, _context_line_count
from .agent_message_router import AgentMessageRouter
from .agent_scene_tracker import AgentSceneTracker


class AgentSceneContextMixin:
    async def _summarize_scene_for_cat(
        self,
        shared: dict[str, Any],
        *,
        scene_id: str,
        route_id: str,
        snapshot: dict[str, Any],
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        context = build_summarize_context(
            shared,
            scene_id=scene_id,
            config=self._context_config,
        )
        # Fallback: if current scene has no lines yet, include previous scene
        # if the scene change was recent (within 10 seconds)
        if not list(context.get("stable_lines") or []):
            previous_scene_id = str(self._scene_state.get("previous_scene_id") or "").strip()
            last_change = float(self._scene_state.get("last_scene_change_at") or 0.0)
            if previous_scene_id and time.monotonic() - last_change < 10.0:
                context = build_summarize_context(
                    shared,
                    scene_id=scene_id,
                    merge_from_scene_ids=[previous_scene_id],
                    config=self._context_config,
                )
        summary, meta = await self._summarize_scene_context_for_cat(
            context,
            scene_id=scene_id,
            route_id=route_id,
            snapshot=snapshot,
        )
        return summary, context, meta

    async def _summarize_scene_context_for_cat(
        self,
        context: dict[str, Any],
        *,
        scene_id: str,
        route_id: str,
        snapshot: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        pov_context = self._fixed_character_pov_context(
            context, applied_to="scene_summary"
        )
        if pov_context:
            context = {**dict(context), **pov_context}
        summary = ""
        key_points: list[dict[str, Any]] = []
        meta: dict[str, Any] = {"summary_source": "local_context"}
        if self._llm_gateway is not None:
            try:
                payload = await asyncio.wait_for(
                    self._llm_gateway.summarize_scene(context),
                    timeout=self._OBSERVE_SUMMARY_TIMEOUT_SECONDS,
                )
                payload_degraded = bool(payload.get("degraded"))
                summary = "" if payload_degraded else str(payload.get("summary") or "").strip()
                if not payload_degraded:
                    key_points = self._normalize_scene_key_points(payload.get("key_points"))
                meta = {
                    "summary_source": "local_context" if payload_degraded else "llm",
                    "summary_degraded": payload_degraded,
                    "summary_diagnostic": str(payload.get("diagnostic") or ""),
                }
            except Exception as exc:
                meta = {
                    "summary_source": "local_context",
                    "summary_degraded": True,
                    "summary_diagnostic": str(exc),
                }
        if not summary:
            summary = self._build_scene_context_fallback(
                scene_id=scene_id,
                route_id=route_id,
                lines=list(context.get("stable_lines") or []),
                selected_choices=list(context.get("recent_choices") or []),
                snapshot=snapshot,
                key_points=key_points or [],
            )
        formatted = self._format_scene_context_for_cat(
            summary=summary,
            key_points=key_points,
            context=context,
            snapshot=snapshot,
        )
        meta["scene_summary"] = summary
        meta["key_points"] = json_copy(key_points)
        return formatted, meta

    @classmethod
    def _normalize_scene_key_points(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or "").strip()
            text = str(item.get("text") or "").strip()
            if item_type not in cls._KEY_POINT_LABELS or not text:
                continue
            normalized.append(
                {
                    "type": item_type,
                    "text": text,
                    "line_id": str(item.get("line_id") or ""),
                    "speaker": str(item.get("speaker") or ""),
                    "scene_id": str(item.get("scene_id") or ""),
                    "route_id": str(item.get("route_id") or ""),
                }
            )
        return normalized[:8]

    @staticmethod
    def _format_scene_line(line: dict[str, Any], *, index: int | None = None) -> str:
        speaker = str(line.get("speaker") or "旁白").strip() or "旁白"
        text = str(line.get("text") or "").strip()
        if not text:
            return ""
        prefix = f"{index}. " if index is not None else ""
        return f"{prefix}{speaker}：「{text[:120]}」"

    @staticmethod
    def _format_choice_text(choice: dict[str, Any]) -> str:
        text = str(choice.get("text") or "").strip()
        if not text:
            return ""
        return text[:120]

    @classmethod
    def _format_scene_context_for_cat(
        cls,
        *,
        summary: str,
        key_points: list[dict[str, Any]],
        context: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> str:
        stable_lines = [
            item for item in list(context.get("stable_lines") or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        observed_lines = [
            item for item in list(context.get("observed_lines") or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        choices = [
            item for item in list(context.get("recent_choices") or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]

        parts: list[str] = ["当前场景：", str(summary or "").strip() or "暂时没有足够剧情上下文。"]

        parts.append("")
        parts.append("最近关键台词：")
        stable_preview = [cls._format_scene_line(line, index=i) for i, line in enumerate(stable_lines[-5:], 1)]
        stable_preview = [line for line in stable_preview if line]
        if stable_preview:
            parts.extend(f"- {line}" for line in stable_preview)
        else:
            current_text = str(snapshot.get("text") or "").strip()
            if current_text and not observed_lines:
                speaker = str(snapshot.get("speaker") or "旁白").strip() or "旁白"
                parts.append(f"- {speaker}：「{current_text[:120]}」")
            else:
                parts.append("- 台词仍在确认中，暂不作为确定剧情事实。")

        observed_preview = [cls._format_scene_line(line, index=i) for i, line in enumerate(observed_lines[-3:], 1)]
        observed_preview = [line for line in observed_preview if line]
        if observed_preview:
            parts.append("")
            parts.append("待确认候选：")
            parts.extend(f"- {line}（OCR 候选，尚未稳定确认）" for line in observed_preview)

        parts.append("")
        parts.append("最近选项：")
        choice_preview = [cls._format_choice_text(choice) for choice in choices[-3:]]
        choice_preview = [choice for choice in choice_preview if choice]
        if choice_preview:
            parts.extend(f"- {choice}" for choice in choice_preview)
        else:
            parts.append("- 暂无已确认选项。")

        parts.append("")
        parts.append("关键变化：")
        if key_points:
            for point in key_points[:6]:
                label = cls._KEY_POINT_LABELS.get(str(point.get("type") or ""), "剧情线索")
                text = str(point.get("text") or "").strip()
                if text:
                    parts.append(f"- {label}：{text[:160]}")
        else:
            parts.append("- 暂无额外结构化关键点；请基于当前场景和稳定台词自然回应。")

        focus_points = [
            str(point.get("text") or "").strip()
            for point in key_points
            if str(point.get("type") or "") in {"emotion", "decision", "reveal", "objective"}
            and str(point.get("text") or "").strip()
        ][:3]
        parts.append("")
        parts.append("当前可关注点：")
        if focus_points:
            parts.extend(f"- {text[:160]}" for text in focus_points)
        elif stable_preview:
            parts.append("- 可以自然评论角色当前的情绪、选择或处境。")
        else:
            parts.append("- 可以说明台词仍在确认中，先轻描淡写地陪伴观察。")

        return "\n".join(parts).strip()

    @staticmethod
    def _build_scene_context_fallback(
        *,
        scene_id: str,
        route_id: str,
        lines: list[dict[str, Any]],
        selected_choices: list[dict[str, Any]],
        snapshot: dict[str, Any],
        key_points: list[dict[str, Any]] | None = None,
    ) -> str:
        recent_parts: list[str] = []
        for line in lines[-6:]:
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            speaker = str(line.get("speaker") or "旁白").strip() or "旁白"
            recent_parts.append(f"{speaker}：{text}")
        if not recent_parts:
            current_text = str(snapshot.get("text") or "").strip()
            if current_text:
                speaker = str(snapshot.get("speaker") or "旁白").strip() or "旁白"
                recent_parts.append(f"{speaker}：{current_text}")
        prefix = f"场景 {scene_id or '(unknown)'}"
        if route_id:
            prefix += f" / 路线 {route_id}"
        parts: list[str] = [prefix]
        if key_points:
            point_texts = [
                str(point.get("text") or "").strip()
                for point in key_points
                if isinstance(point, dict) and str(point.get("text") or "").strip()
            ]
            if point_texts:
                parts.append("关键信息：" + "；".join(point_texts[:6]))
        if recent_parts:
            parts.append("近期上下文：" + "；".join(recent_parts))
        else:
            parts.append("暂时没有足够台词上下文。")
        if selected_choices:
            choices = [
                str(choice.get("text") or "").strip()
                for choice in selected_choices[-3:]
                if isinstance(choice, dict) and str(choice.get("text") or "").strip()
            ]
            if choices:
                parts.append("最近确认的选项：" + "；".join(choices))
        return " ".join(parts)

    def _latest_scene_summary_text(self, snapshot: dict[str, Any]) -> str:
        scene_id = str((snapshot or {}).get("scene_id") or "")
        for entry in reversed(self._scene_memory or []):
            if str(entry.get("scene_id") or "") == scene_id:
                return str(entry.get("summary") or "")
        if self._scene_memory:
            return str(self._scene_memory[-1].get("summary") or "")
        return ""

    @staticmethod
    def _latest_recent_line_texts(
        shared: dict[str, Any], *, limit: int = 5
    ) -> tuple[str, ...]:
        history = shared.get("history_lines") if isinstance(shared, dict) else None
        if not isinstance(history, list):
            return ()
        lines: list[str] = []
        for entry in history[-limit:]:
            if not isinstance(entry, dict):
                continue
            speaker = str(entry.get("speaker") or "").strip()
            text = str(entry.get("text") or "").strip()
            if not text:
                continue
            lines.append(f"{speaker}：{text}" if speaker else text)
        return tuple(lines)
