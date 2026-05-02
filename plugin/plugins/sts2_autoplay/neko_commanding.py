from __future__ import annotations

import asyncio
import json
import random
import re
import time
from typing import Any, Awaitable, Dict, Optional


class NekoCommandingMixin:

    async def neko_command(self, command: str, scope: str = "auto", confirm: bool = False) -> Dict[str, Any]:
        raw_command = str(command or "").strip()
        normalized_scope = self._normalize_neko_command_scope(scope)
        if not raw_command:
            return self._wrap_neko_command_result(
                intent="unknown",
                action="clarify",
                result={"status": "clarify", "message": "请告诉我你想让我看局面、给建议，还是实际操作。"},
                executed=False,
                needs_confirmation=True,
            )
        text = self._normalize_neko_command_text(raw_command)
        explicit_single_card_intent = self._is_neko_play_one_card_text(text) or self._is_neko_generic_play_card_text(text) or self._is_neko_short_play_one_card_text(text)
        explicit_autoplay_intent = self._is_neko_autoplay_text(text)
        observation_only = self._is_neko_observation_only_text(text)
        advice_intent = self._is_neko_advice_text(text) or observation_only
        guidance_intent = self._is_neko_guidance_text(text)
        review_intent = self._is_neko_review_text(text)
        question_intent = self._is_neko_autoplay_question_text(text)
        step_once_intent = self._is_neko_step_once_text(text)

        if self._neko_text_has_any(text, ["停了吧", "别打了", "停止", "结束托管", "停止托管", "终止", "stop"]):
            return self._wrap_neko_command_result("stop", "stop_autoplay", await self.stop_autoplay(), executed=False)
        # resume 必须在 pause 之前判断：'取消暂停' 含 '暂停'，否则会被 pause 分支提前吞掉。
        if self._neko_text_has_any(text, ["取消暂停", "继续托管", "恢复托管", "继续自动打", "恢复自动打", "继续代打", "接着托管", "resume"]):
            return self._wrap_neko_command_result("resume", "resume_autoplay", await self.resume_autoplay(), executed=False)
        if self._neko_text_has_any(text, ["暂停", "先停", "等一下", "别动", "pause"]):
            return self._wrap_neko_command_result("pause", "pause_autoplay", await self.pause_autoplay(), executed=False)

        if self._neko_text_has_any(text, ["健康", "连上", "连接", "health"]):
            return self._wrap_neko_command_result("health", "health_check", await self.health_check(), executed=False)
        if self._neko_text_has_any(text, ["刷新"]):
            return self._wrap_neko_command_result("refresh_state", "refresh_state", await self.refresh_state(), executed=False)
        if self._neko_text_has_any(text, ["状态", "情况", "局面", "快照", "合法动作", "现在什么"]):
            return self._wrap_neko_command_result("snapshot", "get_snapshot", await self.get_snapshot(), executed=False)

        if explicit_single_card_intent and not observation_only:
            result = await self.play_one_card_by_neko(objective=raw_command)
            return self._wrap_neko_command_result("play_one_card", "play_one_card_by_neko", result, executed=bool(result.get("executed", False)) if isinstance(result, dict) else False)

        if guidance_intent:
            if self._autoplay_state in {"running", "paused"}:
                result = await self.send_neko_guidance({"content": raw_command, "step": self._step_count, "type": "soft_guidance"})
                return self._wrap_neko_command_result("guidance", "send_neko_guidance", result, executed=False)
            return self._wrap_neko_command_result(
                "guidance",
                "clarify",
                {
                    "status": "clarify",
                    "message": "软指导会在自动游玩运行时生效。你可以先让我开始自动游玩，或改成'这回合怎么打'来要建议。",
                },
                executed=False,
                needs_confirmation=True,
            )

        if review_intent:
            result = await self.review_recent_play_by_neko(objective=raw_command)
            return self._wrap_neko_command_result("review", "review_recent_play_by_neko", result, executed=False)

        if self._autoplay_state in {"running", "paused"} and question_intent:
            result = await self.answer_autoplay_question_by_neko(question=raw_command)
            return self._wrap_neko_command_result("autoplay_question", "answer_autoplay_question_by_neko", result, executed=False)

        if advice_intent:
            result = await self.recommend_one_card_by_neko(objective=raw_command)
            return self._wrap_neko_command_result("advice", "recommend_one_card_by_neko", result, executed=False)

        if step_once_intent:
            result = await self.step_once()
            return self._wrap_neko_command_result("step_once", "step_once", result, executed=bool(result.get("executed", result.get("status") == "ok")) if isinstance(result, dict) else False)

        if explicit_autoplay_intent:
            stop_condition = self._infer_neko_stop_condition(text)
            if stop_condition == "manual" and not confirm:
                return self._wrap_neko_command_result(
                    intent="manual_autoplay_confirmation",
                    action="clarify",
                    result={"status": "confirm_required", "message": "持续托管需要确认。你可以说“确认持续托管”，或改成只帮你打一层/打一场。"},
                    executed=False,
                    needs_confirmation=True,
                )
            result = await self.start_autoplay(objective=raw_command, stop_condition=stop_condition)
            return self._wrap_neko_command_result("start_autoplay", "start_autoplay", result, executed=bool(result.get("action_executed", False)) if isinstance(result, dict) else False)

        if normalized_scope == "autoplay":
            return self._wrap_neko_command_result(
                intent="autoplay_scope_rejected",
                action="clarify",
                result={
                    "status": "confirm_required",
                    "message": "我检测到请求没有明确自动游玩范围。为了避免把单次出牌误升级为托管，请明确说“打完这场战斗”“帮我打一层”或“持续托管”。",
                },
                executed=False,
                needs_confirmation=True,
            )
        if normalized_scope == "one_card":
            return self._wrap_neko_command_result(
                intent="single_card_confirmation",
                action="clarify",
                result={"status": "confirm_required", "message": "实际出牌需要用户原话明确授权，例如“帮我打一张牌”或“帮我出一张”。"},
                executed=False,
                needs_confirmation=True,
            )
        if normalized_scope == "one_action":
            return self._wrap_neko_command_result(
                intent="one_action_confirmation",
                action="clarify",
                result={"status": "confirm_required", "message": "执行游戏动作需要用户原话明确授权，例如“执行一步”或“操作一下”。"},
                executed=False,
                needs_confirmation=True,
            )
        if normalized_scope == "guidance":
            return self._wrap_neko_command_result(
                "guidance",
                "clarify",
                {"status": "clarify", "message": "请明确告诉我想给自动游玩什么指导，例如“先防一下”或“别贪”。"},
                executed=False,
                needs_confirmation=True,
            )
        if normalized_scope == "review":
            result = await self.review_recent_play_by_neko(objective=raw_command)
            return self._wrap_neko_command_result("review", "review_recent_play_by_neko", result, executed=False)
        if normalized_scope in {"question", "chat"}:
            result = await self.answer_autoplay_question_by_neko(question=raw_command)
            return self._wrap_neko_command_result("autoplay_question", "answer_autoplay_question_by_neko", result, executed=False)
        if normalized_scope == "advice":
            result = await self.recommend_one_card_by_neko(objective=raw_command)
            return self._wrap_neko_command_result("advice", "recommend_one_card_by_neko", result, executed=False)

        return self._wrap_neko_command_result(
            intent="unknown",
            action="clarify",
            result={"status": "clarify", "message": "我不确定你是想只要建议，还是要我实际操作。为了安全，我先不动牌。"},
            executed=False,
            needs_confirmation=True,
        )

    def _wrap_neko_command_result(self, intent: str, action: str, result: Dict[str, Any], *, executed: bool, needs_confirmation: bool = False) -> Dict[str, Any]:
        summary = str(result.get("summary") or result.get("message") or "") if isinstance(result, dict) else ""
        observation_only = bool(result.get("observation_only", False)) if isinstance(result, dict) else False
        effective_executed = bool(result.get("executed", executed)) if isinstance(result, dict) else executed
        return {
            "status": result.get("status", "ok") if isinstance(result, dict) else "ok",
            "intent": intent,
            "action": action,
            "executed": effective_executed,
            "needs_confirmation": needs_confirmation,
            "observation_only": observation_only,
            "message": summary,
            "summary": summary,
            "result": result,
        }

    def _normalize_neko_command_text(self, command: str) -> str:
        return re.sub(r"\s+", "", str(command or "").lower())

    def _normalize_neko_command_scope(self, scope: str) -> str:
        raw_scope = re.sub(r"[\s\-]+", "_", str(scope or "auto").strip().lower()) or "auto"
        aliases = {
            "game": "auto",
            "play": "auto",
            "card": "one_card",
            "play_card": "one_card",
            "single_card": "one_card",
            "one_card": "one_card",
            "action": "one_action",
            "step": "one_action",
            "one_action": "one_action",
            "status": "status",
            "snapshot": "status",
            "state": "status",
            "advice": "advice",
            "recommend": "advice",
            "suggestion": "advice",
            "autoplay": "autoplay",
            "auto_play": "autoplay",
            "control": "control",
            "guidance": "guidance",
            "guide": "guidance",
            "review": "review",
            "question": "question",
            "chat": "chat",
            "auto": "auto",
        }
        return aliases.get(raw_scope, "auto")

    def _neko_text_has_any(self, text: str, needles: list[str]) -> bool:
        return any(self._normalize_neko_command_text(needle) in text for needle in needles)

    def _is_neko_guidance_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["先防", "防一下", "保命", "别贪", "优先输出", "能斩就斩", "斩杀", "省资源", "别乱花", "稳一点"])

    def _is_neko_autoplay_question_text(self, text: str) -> bool:
        if self._is_neko_guidance_text(text):
            return False
        return self._neko_text_has_any(text, ["不怎么样", "打得", "打的", "为什么", "为啥", "你在干嘛", "什么思路", "解释", "说说", "看起来", "是不是", "行不行", "能不能", "靠谱吗", "吐槽"])

    def _is_neko_advice_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["怎么打", "打哪张", "哪张牌", "哪张牌好", "建议", "看看", "分析", "怎么办", "怎么出"])

    def _is_neko_observation_only_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["别动", "不要打", "先别操作", "只建议", "只分析", "别直接出", "不要操作", "别出牌"])

    def _is_neko_review_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["打得怎么样", "牌打得怎么样", "打牌怎么样", "出牌怎么样", "牌感", "复盘", "评价一下", "点评", "吐槽一下", "刚才这手", "刚才的出牌"])

    def _is_neko_play_one_card_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["打一张牌", "打出一张牌", "帮我打出一张牌", "出一张", "选一张牌打出去", "帮我打一张", "帮我出一张", "替我打一张", "直接出一张", "直接打出去", "你来打一张"])

    def _is_neko_short_play_one_card_text(self, text: str) -> bool:
        return text in {"帮我打", "替我打", "帮我出", "替我出", "你来打", "直接打"}

    def _is_neko_step_once_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["打一步", "执行一步", "操作一下", "走一步"])

    def _is_neko_generic_play_card_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["帮我打牌", "替我打牌", "帮我出牌", "替我出牌"])

    def _is_neko_autoplay_text(self, text: str) -> bool:
        return self._neko_text_has_any(text, ["打这一关", "打一关", "打一层", "打完这场", "自动打", "托管", "代打"])

    def _infer_neko_stop_condition(self, text: str) -> str:
        if self._neko_text_has_any(text, ["这场战斗", "当前战斗", "本场战斗", "打完这场", "只打这场", "这一场"]):
            return "current_combat"
        if self._neko_text_has_any(text, ["一直", "持续", "无限", "手动停止"]):
            return "manual"
        return "current_floor"

    def _card_actionability_failure(self, context: Dict[str, Any], *, purpose: str) -> Optional[Dict[str, Any]]:
        snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
        actions = context.get("actions") if isinstance(context.get("actions"), list) else []
        self._refresh_runtime_state_from_snapshot(snapshot)
        if self._transport_state != "connected":
            message = f"STS2-Agent 当前未连接，不能{purpose}。最近错误：{self._poll_last_error or self._last_error or '未知'}"
            return {"status": "error", "reason_code": "transport_unavailable", "message": message, "summary": message, "snapshot": snapshot, "executed": False}
        if self._game_state == "unknown":
            message = f"已连接 STS2-Agent，但未识别到可操作的尖塔局面，不能{purpose}。请确认游戏已进入一局 run 或战斗界面。"
            return {"status": "idle", "reason_code": "game_state_unknown", "message": message, "summary": message, "snapshot": snapshot, "executed": False}
        if self._game_state != "combat_active" and not bool(snapshot.get("in_combat", False)):
            message = f"当前不在战斗中，不能{purpose}。当前界面：{snapshot.get('screen', 'unknown')}。"
            return {"status": "idle", "reason_code": "not_in_combat", "message": message, "summary": message, "snapshot": snapshot, "executed": False}
        has_play_card = any(self._action_type_from_snapshot_action(action) == "play_card" for action in actions if isinstance(action, dict))
        if not has_play_card:
            message = f"当前没有可用的出牌动作，不能{purpose}。"
            return {"status": "idle", "reason_code": "no_play_card_action", "message": message, "summary": message, "snapshot": snapshot, "executed": False}
        return None

    async def recommend_one_card_by_neko(self, objective: Optional[str] = None) -> Dict[str, Any]:
        async with self._step_lock:
            context = await self._await_stable_step_context()
            snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
            gate_failure = self._card_actionability_failure(context, purpose="推荐出牌")
            if gate_failure is not None:
                await self._notify_neko_card_task_event("failed", objective=objective, snapshot=snapshot, reason=gate_failure["message"])
                return gate_failure
            play_card_actions = []
            for action in (context.get("actions") if isinstance(context.get("actions"), list) else []):
                if not isinstance(action, dict):
                    continue
                raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
                action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
                if action_type == "play_card":
                    play_card_actions.append(action)
            if not play_card_actions:
                await self._notify_neko_card_task_event(
                    "failed",
                    objective=objective,
                    snapshot=snapshot,
                    reason="当前没有可推荐的出牌动作",
                )
                return {"status": "idle", "message": "当前没有可推荐的出牌动作", "summary": "当前没有可推荐的出牌动作", "snapshot": snapshot}
            card_context = dict(context)
            card_context["actions"] = play_card_actions
            guidance = (
                f"用户只是想要出牌建议，不是授权自动出牌。用户目标：{objective or '请根据当前玩家、手牌和敌人状态推荐最合适的一张牌'}。"
                "本次只能推荐一个 play_card，不要选择结束回合、奖励、地图或其他非出牌动作。"
                "只给建议和理由，禁止执行任何动作；请根据玩家血量/格挡/能量、手牌、敌人血量和意图说明为什么推荐这张牌。"
            )
            action, llm_reasoning = await self._select_action_with_reasoning({**card_context, "neko_guidance": guidance})
            self._last_llm_reasoning = llm_reasoning
            prepared = self._prepare_action_request(action, card_context)
            if prepared.get("action_type") != "play_card":
                return {"status": "idle", "message": "决策结果不是出牌动作，已取消", "summary": "决策结果不是出牌动作，已取消", "snapshot": snapshot}
            card_name = self._card_name_for_prepared_action(prepared)
            reason = self._reason_for_card_action(llm_reasoning, card_name)
            await self._notify_neko_card_task_event(
                "recommended",
                objective=objective,
                snapshot=snapshot,
                prepared=prepared,
                reasoning=llm_reasoning,
                card_name=card_name,
                reason=reason,
            )
            message = f"建议打出 {card_name}。理由：{reason}"
            return {
                "status": "recommended",
                "message": message,
                "summary": message,
                "card_name": card_name,
                "reason": reason,
                "snapshot": snapshot,
                "executed": False,
                "action": prepared,
            }

    async def play_one_card_by_neko(self, objective: Optional[str] = None) -> Dict[str, Any]:
        async with self._step_lock:
            context = await self._await_stable_step_context()
            snapshot = context.get("snapshot") if isinstance(context.get("snapshot"), dict) else {}
            gate_failure = self._card_actionability_failure(context, purpose="打出一张牌")
            if gate_failure is not None:
                await self._notify_neko_card_task_event("failed", objective=objective, snapshot=snapshot, reason=gate_failure["message"])
                return gate_failure
            play_card_actions = []
            for action in (context.get("actions") if isinstance(context.get("actions"), list) else []):
                if not isinstance(action, dict):
                    continue
                raw = action.get("raw") if isinstance(action.get("raw"), dict) else {}
                action_type = str(action.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or "")
                if action_type == "play_card":
                    play_card_actions.append(action)
            if not play_card_actions:
                await self._notify_neko_card_task_event(
                    "failed",
                    objective=objective,
                    snapshot=snapshot,
                    reason="当前没有可打出的卡牌动作",
                )
                return {"status": "idle", "message": "当前没有可打出的卡牌", "summary": "当前没有可打出的卡牌", "snapshot": snapshot}
            card_context = dict(context)
            card_context["actions"] = play_card_actions
            guidance = (
                f"用户明确授权猫娘选择一张卡牌打出。用户目标：{objective or '请根据当前玩家、手牌和敌人状态选择最合适的一张牌并打出'}。"
                "本次只能选择 play_card，不要选择结束回合、奖励、地图或其他非出牌动作。"
                "请根据玩家血量/格挡/能量、手牌、敌人血量和意图说明为什么要打出这张牌。"
            )
            action, llm_reasoning = await self._select_action_with_reasoning({**card_context, "neko_guidance": guidance})
            self._last_llm_reasoning = llm_reasoning
            prepared = self._prepare_action_request(action, card_context)
            if prepared.get("action_type") != "play_card":
                return {"status": "idle", "message": "决策结果不是出牌动作，已取消", "summary": "决策结果不是出牌动作，已取消", "snapshot": snapshot}
            card_name = self._card_name_for_prepared_action(prepared)
            reason = self._reason_for_card_action(llm_reasoning, card_name)
            await self._notify_neko_card_task_event(
                "planned",
                objective=objective,
                snapshot=snapshot,
                prepared=prepared,
                reasoning=llm_reasoning,
                card_name=card_name,
                reason=reason,
            )
            revalidated = await self._revalidate_prepared_action(prepared, card_context)
            if revalidated is None:
                await self._notify_neko_card_task_event(
                    "failed",
                    objective=objective,
                    snapshot=snapshot,
                    prepared=prepared,
                    card_name=card_name,
                    reason="准备执行前局面变化，原卡牌动作已不可用",
                )
                return {"status": "stale", "message": "局面已变化，原卡牌动作不可执行", "summary": "局面已变化，原卡牌动作不可执行", "snapshot": snapshot}
            prepared = revalidated
            card_name = self._card_name_for_prepared_action(prepared)
            reason = self._reason_for_card_action(llm_reasoning, card_name)
            result = await self._execute_action(prepared)
            if result.get("status") != "ok":
                failure_reason = str(result.get("message") or result.get("error") or "动作执行失败")
                await self._notify_neko_card_task_event(
                    "failed",
                    objective=objective,
                    snapshot=snapshot,
                    prepared=prepared,
                    reasoning=llm_reasoning,
                    card_name=card_name,
                    reason=failure_reason,
                )
                return {**result, "message": failure_reason, "summary": failure_reason, "card_name": card_name, "reason": reason, "snapshot": snapshot, "executed": False}
            await self._await_action_interval()
            settled_context = await self._await_post_action_settle(card_context, prepared)
            self._publish_snapshot(settled_context["snapshot"], record_history=True)
            await self._notify_neko_card_task_event(
                "completed",
                objective=objective,
                snapshot=settled_context["snapshot"],
                prepared=prepared,
                reasoning=llm_reasoning,
                card_name=card_name,
                reason=reason,
            )
            message = f"已按猫娘选择打出 {card_name}。理由：{reason}"
            return {**result, "message": message, "summary": message, "card_name": card_name, "reason": reason, "snapshot": settled_context["snapshot"], "executed": True}

    async def answer_autoplay_question_by_neko(self, question: str) -> Dict[str, Any]:
        snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
        if not snapshot:
            try:
                await self.refresh_state()
                snapshot = self._snapshot if isinstance(self._snapshot, dict) else {}
            except Exception as exc:
                message = f"我现在读不到尖塔局面，没法可靠回答这句：{question}。错误：{exc}。"
                return {"status": "error", "message": message, "summary": message, "executed": False, "observation_only": True}
        report = self._report_full_step({"snapshot": snapshot}, decision_result=self._last_llm_reasoning)
        hand = "、".join(str(card.get("name") or "?") for card in report.get("hand", []) if isinstance(card, dict)) or "暂无手牌"
        enemies = []
        for enemy in report.get("enemies", []):
            if isinstance(enemy, dict):
                enemies.append(f"{enemy.get('name', '?')} {enemy.get('hp', '?')}/{enemy.get('max_hp', '?')} 意图{enemy.get('intent', '?')}{enemy.get('intent_value') or ''}")
        enemies_text = "；".join(enemies) if enemies else "暂无敌人信息"
        reasoning = report.get("llm_reasoning") if isinstance(report.get("llm_reasoning"), dict) else {}
        chosen_action = str(reasoning.get("chosen_action") or report.get("last_action") or "继续观察")
        reason = str(reasoning.get("reason") or reasoning.get("primary_goal") or "当前没有完整模型理由记录")
        task = self._semi_auto_task if isinstance(self._semi_auto_task, dict) else {}
        objective = str(task.get("objective") or "当前尖塔任务")
        guidance_hint = ""
        if any(token in self._normalize_neko_command_text(question) for token in ["不怎么样", "不太行", "乱打", "吐槽"]):
            guidance_hint = "如果你希望我调整打法，可以直接说‘先防一下/别贪/优先输出’，我会把它作为下一轮决策指导。"
        message = (
            f"我在看着当前局面：{objective}；Act{report.get('act')}F{report.get('floor')} {report.get('screen')}，"
            f"血量 {report.get('player_hp')}/{report.get('max_hp')}，格挡 {report.get('block')}，能量 {report.get('energy')}。"
            f"手牌有：{hand}。敌人：{enemies_text}。"
            f"刚才/当前思路是 {chosen_action}，理由：{reason}。{guidance_hint}"
        )
        return {
            "status": "answered",
            "message": message,
            "summary": message,
            "executed": False,
            "observation_only": True,
            "question": question,
            "report": {
                "act": report.get("act"),
                "floor": report.get("floor"),
                "screen": report.get("screen"),
                "hp": [report.get("player_hp"), report.get("max_hp")],
                "block": report.get("block"),
                "energy": report.get("energy"),
                "hand": report.get("hand", []),
                "enemies": report.get("enemies", []),
                "chosen_action": chosen_action,
                "reason": reason,
            },
        }

    async def review_recent_play_by_neko(self, objective: Optional[str] = None) -> Dict[str, Any]:
        if not self._recent_snapshot_log and not self._snapshot:
            try:
                await self.refresh_state()
            except Exception as exc:
                message = f"轻量牌感观察：猫娘暂时无法读取最近局面，不能可靠点评刚才的出牌。错误：{exc}。建议主程序请用户稍后刷新状态后再试。"
                return {"status": "error", "message": message, "summary": message, "executed": False, "observation_only": True}

        snapshots = list(self._recent_snapshot_log)[: max(1, self._safe_int(self._cfg.get("neko_review_recent_snapshot_count"), 8))]
        if not snapshots and self._snapshot:
            snapshots = [self._build_review_snapshot_summary(self._snapshot, timestamp=self._last_poll_at or time.time())]
        observation = self._build_neko_review_observation(snapshots)
        message = observation["message"]
        return {
            "status": observation["status"],
            "message": message,
            "summary": message,
            "executed": False,
            "observation_only": True,
            "intent": "review",
            "objective": objective,
            "review": observation,
            "recent_snapshots": snapshots,
        }

    def _build_review_snapshot_summary(self, snapshot: Dict[str, Any], *, timestamp: float) -> Dict[str, Any]:
        raw_state = snapshot.get("raw_state") if isinstance(snapshot.get("raw_state"), dict) else {}
        combat = raw_state.get("combat") if isinstance(raw_state.get("combat"), dict) else {}
        player = combat.get("player") if isinstance(combat.get("player"), dict) else {}
        run = raw_state.get("run") if isinstance(raw_state.get("run"), dict) else {}
        enemies = combat.get("enemies") if isinstance(combat.get("enemies"), list) else []
        hand = combat.get("hand") if isinstance(combat.get("hand"), list) else []
        enemy_summaries = []
        for enemy in enemies[:4]:
            if not isinstance(enemy, dict):
                continue
            raw_intent = enemy.get("intent")
            intent = raw_intent if isinstance(raw_intent, dict) else {}
            intent_type = str(intent.get("type") or raw_intent or "")
            enemy_summaries.append({
                "name": str(enemy.get("name") or ""),
                "hp": self._safe_int(enemy.get("hp")),
                "max_hp": self._safe_int(enemy.get("max_hp")),
                "block": self._safe_int(enemy.get("block")),
                "intent": intent_type,
                "intent_value": self._safe_int(intent.get("value") if intent.get("value") is not None else enemy.get("intent_value")),
                "attack_value": self._enemy_intent_attack_total(enemy),
            })
        return {
            "time": timestamp,
            "step": self._step_count,
            "screen": snapshot.get("screen", "unknown"),
            "floor": snapshot.get("floor", 0),
            "act": snapshot.get("act", 0),
            "in_combat": bool(snapshot.get("in_combat", False)),
            "turn": self._safe_int(self._first_present(combat.get("turn"), raw_state.get("turn")), 0),
            "hp": self._safe_int(self._first_present(player.get("hp"), raw_state.get("current_hp"), run.get("current_hp"), run.get("hp")), 0),
            "max_hp": self._safe_int(self._first_present(player.get("max_hp"), raw_state.get("max_hp"), run.get("max_hp")), 0),
            "block": self._combat_player_block(combat),
            "energy": self._safe_int(self._first_present(player.get("energy") if player else None, combat.get("player_energy")), 0),
            "hand_names": [str(card.get("name") or card.get("card_name") or "") for card in hand[:10] if isinstance(card, dict)],
            "enemy_count": len(enemy_summaries),
            "enemies": enemy_summaries,
            "available_actions": snapshot.get("available_action_count", 0),
        }

    def _build_neko_review_observation(self, snapshots: list[Dict[str, Any]]) -> Dict[str, Any]:
        usable = [s for s in snapshots if isinstance(s, dict)]
        if len(usable) < 2:
            message = "轻量牌感观察：最近连续局面不足，无法可靠判断完整牌序。只能粗略看出当前局面尚可；如果敌人来袭伤害偏高，优先补防会更稳。建议主程序向用户说明猫娘刚开始观察，还需要再多看几手，不要给出确定性复盘结论。"
            return {"status": "insufficient_log", "confidence": "low", "message": message, "signals": {"snapshot_count": len(usable)}}

        latest = usable[0]
        oldest = usable[-1]
        hp_delta = self._safe_int(latest.get("hp"), 0) - self._safe_int(oldest.get("hp"), 0)
        block = self._safe_int(latest.get("block"), 0)
        incoming_attack = sum(max(0, self._review_enemy_attack_value(enemy)) for enemy in (latest.get("enemies") if isinstance(latest.get("enemies"), list) else []))
        enemy_hp_old = sum(max(0, self._safe_int(enemy.get("hp"), 0)) for enemy in (oldest.get("enemies") if isinstance(oldest.get("enemies"), list) else []))
        enemy_hp_new = sum(max(0, self._safe_int(enemy.get("hp"), 0)) for enemy in (latest.get("enemies") if isinstance(latest.get("enemies"), list) else []))
        enemy_hp_delta = enemy_hp_new - enemy_hp_old
        hand_changed_fast = len({tuple(s.get("hand_names") or []) for s in usable[: min(4, len(usable))]}) >= min(3, len(usable))
        aggressive = enemy_hp_delta < 0 and (incoming_attack > block or hp_delta < 0)
        visible_fact = self._build_neko_review_visible_fact(latest)
        card_praise = self._build_neko_review_card_praise(usable, enemy_hp_delta=enemy_hp_delta, incoming_attack=incoming_attack, block=block)
        detail_hint = "".join(part for part in [visible_fact, card_praise] if part)

        if hand_changed_fast and len(usable) < 4:
            message = f"轻量牌感观察：刚才手牌、血量或状态变化较快，猫娘没有稳定看清每一张牌。{detail_hint}只能按血量、格挡和敌人状态推测：本轮节奏偏激进，压血线效果不错，但防守可能需要更稳。建议主程序把该结论包装成轻量参考，可以偶尔点名夸一张确实看到过的牌，而不是严格牌序审计。"
            status = "uncertain"
            confidence = "low"
        elif aggressive:
            message = f"轻量牌感观察：我只能根据最近看到的局面粗略判断。玩家这几手整体偏进攻，敌人血线压得不错，节奏感较好；{detail_hint}但如果敌人本回合仍有较高来袭伤害，当前防御余量可能偏薄。建议主程序用陪玩口吻反馈：先肯定压血线做得好，可以偶尔带一句猫娘实际看到的血量、格挡、敌人意图或具体卡牌表现；再温和提醒下次先估算防御缺口，再决定是否全力打伤害。"
            status = "ok"
            confidence = "medium"
        elif incoming_attack > block:
            message = f"轻量牌感观察：最近局面显示敌人仍有来袭伤害，当前格挡可能覆盖不足。{detail_hint}没有看到明显错误牌序，但建议主程序温和提醒用户优先核对防御缺口，再考虑输出。"
            status = "ok"
            confidence = "medium"
        else:
            message = f"轻量牌感观察：最近几手没有暴露明显危险信号，血量和防御压力看起来可控。{detail_hint}建议主程序先肯定整体节奏，可以顺手夸一句具体牌打得不错，再说明这只是基于最近快照的轻量参考，不是严格复盘。"
            status = "ok"
            confidence = "medium"
        return {
            "status": status,
            "confidence": confidence,
            "message": message,
            "signals": {
                "snapshot_count": len(usable),
                "hp_delta": hp_delta,
                "enemy_hp_delta": enemy_hp_delta,
                "incoming_attack": incoming_attack,
                "block": block,
                "hand_changed_fast": hand_changed_fast,
                "visible_fact": visible_fact,
                "card_praise": card_praise,
            },
        }

    def _build_neko_review_visible_fact(self, latest: Dict[str, Any]) -> str:
        hp = self._safe_int(latest.get("hp"), 0)
        max_hp = self._safe_int(latest.get("max_hp"), 0)
        block = self._safe_int(latest.get("block"), 0)
        energy = self._safe_int(latest.get("energy"), 0)
        enemies = latest.get("enemies") if isinstance(latest.get("enemies"), list) else []
        enemy = next((item for item in enemies if isinstance(item, dict) and str(item.get("name") or "").strip()), None)
        parts = []
        if hp > 0 and max_hp > 0:
            parts.append(f"玩家血量约{hp}/{max_hp}")
        if block > 0:
            parts.append(f"当前有{block}点格挡")
        if energy > 0:
            parts.append(f"还剩{energy}点能量")
        if enemy:
            enemy_name = str(enemy.get("name") or "敌人")
            enemy_hp = self._safe_int(enemy.get("hp"), 0)
            enemy_max_hp = self._safe_int(enemy.get("max_hp"), 0)
            attack_value = self._review_enemy_attack_value(enemy)
            if enemy_hp > 0 and enemy_max_hp > 0:
                parts.append(f"{enemy_name}血量约{enemy_hp}/{enemy_max_hp}")
            if attack_value > 0:
                parts.append(f"敌人来袭约{attack_value}点")
        if not parts:
            return ""
        return "猫娘看到" + "，".join(parts[:3]) + "。"

    def _review_enemy_attack_value(self, enemy: Any) -> int:
        if not isinstance(enemy, dict):
            return 0
        if "attack_value" in enemy:
            return self._safe_int(enemy.get("attack_value"), 0)
        intent = str(enemy.get("intent") or "").strip().lower()
        if any(token in intent for token in {"attack", "attacking", "damage", "打", "攻击", "来袭"}):
            return self._safe_int(enemy.get("intent_value"), 0)
        return 0

    def _build_neko_review_card_praise(self, snapshots: list[Dict[str, Any]], *, enemy_hp_delta: int, incoming_attack: int, block: int) -> str:
        card_name = ""
        for snapshot in snapshots:
            played_cards = snapshot.get("played_cards") if isinstance(snapshot.get("played_cards"), list) else []
            hand_names = snapshot.get("hand_names") if isinstance(snapshot.get("hand_names"), list) else []
            for name in [*played_cards, *hand_names]:
                normalized = str(name or "").strip()
                if normalized:
                    card_name = normalized
                    break
            if card_name:
                break
        if not card_name:
            if enemy_hp_delta < 0:
                return "可以泛泛夸一句：刚才这波打得不错，确实把敌人血线往下压了。"
            if block >= incoming_attack and incoming_attack > 0:
                return "可以泛泛夸一句：防御衔接得不错，压力处理得比较稳。"
            return ""
        if enemy_hp_delta < 0:
            return f"可以点名夸一句：你这个【{card_name}】打得不错，刚才确实把敌人血线往下压了。"
        if block >= incoming_attack and incoming_attack > 0:
            return f"可以点名夸一句：你这个【{card_name}】衔接得不错，防御压力处理得比较稳。"
        return ""
