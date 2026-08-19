from __future__ import annotations

from .agent_shared import *  # noqa: F401,F403
from .context_builder import _SUMMARY_MAX_CHARS, _bounded_summary_text


class AgentSceneContextMixin:
    _SCENE_DELTA_CHOICE_LIMIT = 50

    @staticmethod
    def _normalized_choice_state(choice: dict[str, Any]) -> str:
        state = str(
            choice.get("choice_state") or choice.get("action") or "selected"
        ).strip().lower()
        return "visible" if state in {"visible", "shown"} else "selected"

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
        reviewed_snapshot = context.get("current_snapshot")
        snapshot = dict(reviewed_snapshot) if isinstance(reviewed_snapshot, dict) else {}
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
            new_stable_lines = [
                dict(line)
                for line in list(context.get("new_stable_lines") or [])
                if isinstance(line, dict)
            ]
            new_choices = [
                dict(choice)
                for choice in list(context.get("new_choices") or [])
                if isinstance(choice, dict)
            ]
            selected_new_choices = [
                choice
                for choice in new_choices
                if self._normalized_choice_state(choice) == "selected"
            ]
            visible_new_choices = [
                choice
                for choice in new_choices
                if self._normalized_choice_state(choice) == "visible"
            ]
            local_progress_summary = self._build_scene_context_fallback(
                scene_id=scene_id,
                route_id=route_id,
                lines=(
                    new_stable_lines
                    if new_stable_lines
                    else list(context.get("stable_lines") or [])
                ),
                selected_choices=(
                    selected_new_choices
                    if new_choices
                    else list(context.get("recent_choices") or [])
                ),
                visible_choices=visible_new_choices,
                snapshot=snapshot,
                key_points=key_points or [],
                line_limit=None if new_stable_lines else 6,
            )
            previous_scene_summary = str(
                context.get("previous_scene_summary") or ""
            ).strip()
            if (
                previous_scene_summary
                and local_progress_summary
                and previous_scene_summary != local_progress_summary
            ):
                separator = " 最新进展："
                latest_reserved_chars = min(600, len(local_progress_summary))
                previous_budget = max(
                    1,
                    _SUMMARY_MAX_CHARS
                    - len(separator)
                    - latest_reserved_chars,
                )
                bounded_previous = _bounded_summary_text(
                    previous_scene_summary,
                    max_chars=previous_budget,
                )
                latest_budget = max(
                    1,
                    _SUMMARY_MAX_CHARS
                    - len(separator)
                    - len(bounded_previous),
                )
                bounded_latest = _bounded_summary_text(
                    local_progress_summary,
                    max_chars=latest_budget,
                )
                summary = f"{bounded_previous}{separator}{bounded_latest}"
            else:
                summary = _bounded_summary_text(
                    previous_scene_summary or local_progress_summary,
                    max_chars=_SUMMARY_MAX_CHARS,
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
    def _format_scene_delta_for_cat(
        cls,
        *,
        new_stable_lines: list[dict[str, Any]],
        new_choices: list[dict[str, Any]],
        continuity_lines: list[dict[str, Any]] | None = None,
        choice_limit: int | None = None,
    ) -> str:
        """Build a bounded, deterministic capsule for the cat-facing queue.

        The signature deliberately cannot accept a cumulative summary, key points,
        focus points, or observed OCR.  Explicitly non-stable lines are rejected as
        a final defensive boundary; lines without a stability field remain valid
        for trusted readers whose history records predate that field.
        """

        def _confirmed_lines(value: Any) -> list[dict[str, Any]]:
            confirmed: list[dict[str, Any]] = []
            for item in list(value or []):
                if not isinstance(item, dict):
                    continue
                if not str(item.get("text") or "").strip():
                    continue
                stability = str(item.get("stability") or "").strip().lower()
                if stability and stability != "stable":
                    continue
                confirmed.append(item)
            return confirmed

        stable_delta = _confirmed_lines(new_stable_lines)
        normalized_choice_limit = max(
            1,
            int(
                cls._SCENE_DELTA_CHOICE_LIMIT
                if choice_limit is None
                else choice_limit
            ),
        )
        choices_delta = [
            item
            for item in list(new_choices or [])
            if isinstance(item, dict)
            and str(item.get("text") or item.get("label") or "").strip()
        ][-normalized_choice_limit:]
        if not stable_delta and not choices_delta:
            return ""

        continuity = _confirmed_lines(continuity_lines)[-2:]
        parts: list[str] = [
            "Galgame 实时剧情增量：",
            "回应约束：只回应“本次回应对象”；连续性背景仅供理解，不要复述或回应。",
        ]
        if continuity:
            parts.extend(("", "连续性背景（最多 2 条，不是回应对象）："))
            preview = [
                cls._format_scene_line(line, index=index)
                for index, line in enumerate(continuity, 1)
            ]
            parts.extend(f"- {line}" for line in preview if line)

        parts.extend(("", "本次回应对象："))
        if stable_delta:
            parts.append("最新稳定台词：")
            latest_line = cls._format_scene_line(stable_delta[-1])
            if latest_line:
                parts.append(f"- {latest_line}")

        selected_choices: list[str] = []
        visible_choices: list[str] = []
        for choice in choices_delta:
            rendered = cls._format_choice_text(
                {**choice, "text": choice.get("text") or choice.get("label")}
            )
            if not rendered:
                continue
            state = cls._normalized_choice_state(choice)
            if state == "visible":
                visible_choices.append(rendered)
            else:
                selected_choices.append(rendered)
        if selected_choices:
            parts.append("玩家刚刚选择：")
            parts.extend(f"- {choice}" for choice in selected_choices)
        if visible_choices:
            parts.append("刚刚出现的可见选项：")
            parts.extend(f"- {choice}" for choice in visible_choices)

        return "\n".join(parts).strip()

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
            and str(item.get("stability") or "").strip().lower() != "stable"
        ]
        choices = [
            item for item in list(context.get("recent_choices") or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        # ``new_*`` is populated by the incremental scene-summary scheduler.  Keep
        # missing-key compatibility for direct/legacy callers: their first summary
        # still treats the available stable context as the initial response target.
        # An explicitly empty list is different -- it means there is no new content
        # and cumulative context must not be presented as something to respond to.
        new_stable_lines_source = (
            context.get("new_stable_lines")
            if "new_stable_lines" in context
            else stable_lines
        )
        new_choices_source = (
            context.get("new_choices")
            if "new_choices" in context
            else choices
        )
        new_stable_lines = [
            item for item in list(new_stable_lines_source or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]
        new_choices = [
            item for item in list(new_choices_source or [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ]

        parts: list[str] = [
            "累计剧情背景（仅供理解，不要复述）：",
            str(summary or "").strip() or "暂时没有足够剧情上下文。",
        ]

        observed_preview = [cls._format_scene_line(line, index=i) for i, line in enumerate(observed_lines[-3:], 1)]
        observed_preview = [line for line in observed_preview if line]
        if observed_preview:
            parts.append("")
            parts.append("待确认候选（仅供观察，不要作为确定事实回应）：")
            parts.extend(f"- {line}（OCR 候选，尚未稳定确认）" for line in observed_preview)

        parts.append("")
        parts.append("关键变化：")
        parts.append("- 以下结构化要点用于理解累计脉络；不要把旧要点当作本次新增内容复述。")
        if key_points:
            for point in key_points[:6]:
                label = cls._KEY_POINT_LABELS.get(str(point.get("type") or ""), "剧情线索")
                text = str(point.get("text") or "").strip()
                if text:
                    parts.append(f"- {label}：{text[:160]}")
        else:
            parts.append("- 暂无额外结构化关键点；请只基于“本次回应对象”自然回应。")

        focus_points = [
            str(point.get("text") or "").strip()
            for point in key_points
            if str(point.get("type") or "") in {"emotion", "decision", "reveal", "objective"}
            and str(point.get("text") or "").strip()
        ][:3]
        parts.append("")
        parts.append("当前可关注点：")
        parts.append("- 仅用于理解剧情；实际回应必须以“本次回应对象”为准。")
        if focus_points:
            parts.extend(f"- {text[:160]}" for text in focus_points)
        elif new_stable_lines or new_choices:
            parts.append("- 可以自然评论本次新增内容中的情绪、选择或处境。")
        else:
            parts.append("- 当前没有新的已确认回应对象。")

        parts.append("")
        parts.append("本次回应对象：")
        parts.append("新增稳定台词：")
        stable_preview = [
            cls._format_scene_line(line, index=i)
            for i, line in enumerate(new_stable_lines[-5:], 1)
        ]
        stable_preview = [line for line in stable_preview if line]
        if stable_preview:
            parts.extend(f"- {line}" for line in stable_preview)
        else:
            parts.append("- 暂无新增稳定台词。")

        selected_choice_preview = [
            cls._format_choice_text(choice)
            for choice in new_choices
            if cls._normalized_choice_state(choice) != "visible"
        ][-3:]
        selected_choice_preview = [
            choice for choice in selected_choice_preview if choice
        ]
        visible_choice_preview = [
            cls._format_choice_text(choice)
            for choice in new_choices
            if cls._normalized_choice_state(choice) == "visible"
        ][-3:]
        visible_choice_preview = [
            choice for choice in visible_choice_preview if choice
        ]
        parts.append("新增选项：")
        if selected_choice_preview:
            parts.append("玩家已选择：")
            parts.extend(f"- {choice}" for choice in selected_choice_preview)
        if visible_choice_preview:
            parts.append("当前可见选项：")
            parts.extend(f"- {choice}" for choice in visible_choice_preview)
        if not selected_choice_preview and not visible_choice_preview:
            parts.append("- 暂无新增选项。")

        return "\n".join(parts).strip()

    @staticmethod
    def _build_scene_context_fallback(
        *,
        scene_id: str,
        route_id: str,
        lines: list[dict[str, Any]],
        selected_choices: list[dict[str, Any]],
        snapshot: dict[str, Any],
        visible_choices: list[dict[str, Any]] | None = None,
        key_points: list[dict[str, Any]] | None = None,
        line_limit: int | None = 6,
    ) -> str:
        recent_parts: list[str] = []
        selected_lines = lines if line_limit is None else lines[-max(1, line_limit) :]
        for line in selected_lines:
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
        if visible_choices:
            choices = [
                str(choice.get("text") or "").strip()
                for choice in visible_choices[-3:]
                if isinstance(choice, dict)
                and str(choice.get("text") or "").strip()
            ]
            if choices:
                parts.append("当前可见选项：" + "；".join(choices))
        return " ".join(parts)

    def _latest_scene_summary_text(
        self,
        snapshot: dict[str, Any],
        *,
        shared: dict[str, Any] | None = None,
    ) -> str:
        scene_id = str((snapshot or {}).get("scene_id") or "")
        route_id = str((snapshot or {}).get("route_id") or "")
        scene_ids = {scene_id}
        if scene_id and isinstance(shared, dict):
            session_id = str(shared.get("active_session_id") or "")
            boundary_key = self._scene_capsule_boundary_key(
                shared,
                session_id=session_id,
            )
            ledger = self._scene_capsule_delivery_ledger.get(boundary_key) or {}
            scene_aliases = dict(
                ledger.get("memory_handoff_scene_aliases") or {}
            )
            aliased_scene_id = str(
                scene_aliases.get(
                    self._scene_tracker.summary_scope_key(scene_id, route_id)
                )
                or ""
            )
            if aliased_scene_id:
                scene_ids.add(aliased_scene_id)
        for entry in reversed(self._scene_memory or []):
            if (
                str(entry.get("scene_id") or "") in scene_ids
                and str(entry.get("route_id") or "") == route_id
            ):
                return str(entry.get("summary") or "")
        if not scene_id and not route_id and self._scene_memory:
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
