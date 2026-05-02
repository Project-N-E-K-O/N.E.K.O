from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from config import TOOL_SERVER_PORT
from utils.file_utils import robust_json_loads
from utils.token_tracker import set_call_type


class LLMStrategy:
    def __init__(self, logger) -> None:
        self.logger = logger

    async def invoke_llm_json(self, messages: List[Dict[str, Any]], cfg: Dict[str, Any]) -> str:
        from utils.config_manager import get_config_manager

        config_manager = get_config_manager()
        api_config = config_manager.get_model_api_config("agent")
        base_url = str(api_config.get("base_url") or "").strip().rstrip("/")
        model = str(api_config.get("model") or "").strip()
        api_key = str(api_config.get("api_key") or "").strip()
        if not base_url or not model:
            raise RuntimeError("未配置可用的 Agent 模型")
        proxy_base = f"http://127.0.0.1:{TOOL_SERVER_PORT}/openfang-llm-proxy"
        target_url = f"{proxy_base}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
            "max_completion_tokens": 1200,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        set_call_type("agent")
        timeout = httpx.Timeout(float(cfg.get("request_timeout_seconds", 15) or 15), connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(target_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"LLM 返回缺少 choices: {data}")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else ""
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text_parts.append(str(item.get("text") or ""))
            content = "".join(text_parts)
        return str(content or "")

    def try_parse_llm_json(self, raw_text: str) -> Optional[Dict[str, Any]]:
        text = (raw_text or "").strip()
        if not text:
            return None
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        try:
            parsed = robust_json_loads(text)
        except Exception:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                return None
            try:
                parsed = robust_json_loads(match.group(0))
            except Exception:
                return None
        return parsed if isinstance(parsed, dict) else None

    async def parse_llm_decision_response(self, raw_text: str, messages: List[Dict[str, Any]], cfg: Dict[str, Any], llm_methods) -> Optional[Dict[str, Any]]:
        decision = llm_methods.try_parse_llm_json(raw_text)
        if isinstance(decision, dict):
            return decision
        correction_messages = list(messages)
        correction_messages.append({"role": "assistant", "content": raw_text})
        correction_messages.append({
            "role": "user",
            "content": "CORRECTION: 你的上一条回复不是合法 JSON。请只返回一个合法 JSON 对象，不要带 markdown 或解释。",
        })
        try:
            response_text = await llm_methods.invoke_llm_json(correction_messages, cfg)
        except Exception as exc:
            self.logger.warning(f"LLM correction retry 失败: {exc}")
            return None
        corrected = llm_methods.try_parse_llm_json(response_text)
        return corrected if isinstance(corrected, dict) else None

    def _validated_action_type(self, validated: Dict[str, Any], fallback: str = "") -> str:
        raw = validated.get("raw") if isinstance(validated.get("raw"), dict) else {}
        return str(validated.get("type") or raw.get("type") or raw.get("name") or raw.get("action") or fallback or "")

    def _validated_action_kwargs(self, validated: Dict[str, Any]) -> Dict[str, Any]:
        raw = validated.get("raw") if isinstance(validated.get("raw"), dict) else {}
        ignored_keys = {"type", "name", "label", "description", "requires_target", "requires_index", "shop_remove_selection"}
        return {
            key: value
            for key, value in raw.items()
            if key not in ignored_keys and not (key == "action" and isinstance(value, dict))
        }

    def _build_validated_decision_reasoning(self, payload: Dict[str, Any], decision: Dict[str, Any], validated: Dict[str, Any]) -> Dict[str, Any]:
        chosen_action = self._validated_action_type(validated, str(decision.get("action_type") or ""))
        chosen_kwargs = self._validated_action_kwargs(validated)
        original_kwargs = decision.get("kwargs") if isinstance(decision.get("kwargs"), dict) else {}
        return {
            "situation_summary": payload.get("snapshot", {}).get("screen", "") if isinstance(payload.get("snapshot"), dict) else "",
            "primary_goal": "",
            "candidate_actions": [a.get("action_type") for a in payload.get("legal_actions", []) if isinstance(a, dict)],
            "chosen_action": chosen_action,
            "chosen_kwargs": chosen_kwargs,
            "original_action": str(decision.get("action_type") or ""),
            "original_kwargs": dict(original_kwargs),
            "reason": str(decision.get("reason") or ""),
        }

    async def select_action_full_model(self, context: Dict[str, Any], cfg: Dict[str, Any], configured_character_strategy, strategy_prompt_for_llm, build_llm_decision_payload, build_full_model_reasoning_messages, build_full_model_checked_context, build_full_model_final_messages, parse_llm_reasoning_response, parse_llm_decision_response, validate_llm_decision, invoke_llm_json, try_parse_llm_json, await_stable_step_context, llm_methods) -> Optional[Dict[str, Any]]:
        self.logger.info("[sts2_autoplay][full-model] stage1 reasoning start")
        strategy_prompt = strategy_prompt_for_llm(configured_character_strategy())
        reasoning_payload = build_llm_decision_payload(context, character_strategy=configured_character_strategy())
        guidance_content = reasoning_payload.pop("neko_guidance", None)
        reasoning_messages = build_full_model_reasoning_messages(reasoning_payload, strategy_prompt, guidance=guidance_content)
        reasoning_text = await invoke_llm_json(reasoning_messages, cfg)
        reasoning = await parse_llm_reasoning_response(reasoning_text, messages=reasoning_messages, llm_methods=llm_methods)
        if reasoning is None:
            self.logger.warning("[sts2_autoplay][full-model] stage1 reasoning parse failed")
            return None
        self.logger.info("[sts2_autoplay][full-model] stage1 reasoning parsed")
        checked_context, program_checks = await build_full_model_checked_context(context, reasoning, configured_character_strategy, build_llm_decision_payload, await_stable_step_context, llm_methods)
        self.logger.info("[sts2_autoplay][full-model] program check complete")
        final_payload = build_llm_decision_payload(checked_context, character_strategy=configured_character_strategy())
        final_payload.pop("neko_guidance", None)
        final_payload["model_reasoning"] = reasoning
        final_payload["program_checks"] = program_checks
        final_messages = build_full_model_final_messages(final_payload, strategy_prompt)
        self.logger.info("[sts2_autoplay][full-model] stage2 final decision start")
        final_text = await invoke_llm_json(final_messages, cfg)
        decision = await parse_llm_decision_response(final_text, messages=final_messages, cfg=cfg, llm_methods=llm_methods)
        if decision is None:
            self.logger.warning("[sts2_autoplay][full-model] stage2 final decision parse failed")
            return None
        validated = validate_llm_decision(decision, checked_context)
        if validated is None:
            self.logger.warning("[sts2_autoplay][full-model] stage2 final decision rejected by validator")
            return None
        self.logger.info("[sts2_autoplay][full-model] stage2 final decision validated")
        return validated

    async def parse_llm_reasoning_response(self, raw_text: str, messages: List[Dict[str, Any]], llm_methods) -> Optional[Dict[str, Any]]:
        parsed = llm_methods.try_parse_llm_json(raw_text)
        if not isinstance(parsed, dict):
            correction_messages = list(messages)
            correction_messages.append({"role": "assistant", "content": raw_text})
            correction_messages.append({
                "role": "user",
                "content": "CORRECTION: 你的上一条回复不是合法 reasoning JSON。请只返回一个 JSON 对象，且必须包含 situation_summary、primary_goal、candidate_actions、risks、checks_requested。",
            })
            try:
                corrected_text = await llm_methods.invoke_llm_json(correction_messages, {})
            except Exception as exc:
                self.logger.warning(f"full-model reasoning correction retry 失败: {exc}")
                return None
            parsed = llm_methods.try_parse_llm_json(corrected_text)
            if not isinstance(parsed, dict):
                return None
        reasoning = {
            "situation_summary": str(parsed.get("situation_summary") or ""),
            "primary_goal": str(parsed.get("primary_goal") or ""),
            "candidate_actions": parsed.get("candidate_actions") if isinstance(parsed.get("candidate_actions"), list) else [],
            "risks": parsed.get("risks") if isinstance(parsed.get("risks"), list) else [],
            "checks_requested": parsed.get("checks_requested") if isinstance(parsed.get("checks_requested"), list) else [],
        }
        if not reasoning["situation_summary"] and not reasoning["primary_goal"]:
            return None
        return reasoning

    async def build_full_model_checked_context(self, context: Dict[str, Any], reasoning: Dict[str, Any], configured_character_strategy, build_llm_decision_payload, await_stable_step_context, llm_methods) -> tuple:
        latest_context = await await_stable_step_context()
        context_changed = latest_context.get("signature") != context.get("signature")
        checked_context = latest_context if context_changed else context
        checked_payload = build_llm_decision_payload(checked_context, character_strategy=configured_character_strategy())
        tactical_summary = checked_payload.get("tactical_summary") if isinstance(checked_payload.get("tactical_summary"), dict) else {}
        safe_int = llm_methods._safe_int if hasattr(llm_methods, '_safe_int') else lambda v, d=0: int(v) if v is not None else d
        program_checks = {
            "context_revalidated": True,
            "context_changed": context_changed,
            "legal_action_count": len(checked_payload.get("legal_actions", [])),
            "tactical_summary": tactical_summary,
            "must_choose_legal_action": True,
            "must_use_allowed_kwargs": True,
            "prefer_lethal_when_available": bool(tactical_summary.get("lethal_targets")),
            "must_respect_incoming_attack": safe_int(tactical_summary.get("incoming_attack_total")) > 0,
            "reasoning_focus": {
                "primary_goal": reasoning.get("primary_goal"),
                "checks_requested": reasoning.get("checks_requested", []),
            },
        }
        return checked_context, program_checks

    def build_full_model_final_messages(self, payload: Dict[str, Any], strategy_prompt: Optional[str]) -> List[Dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": "你是 sts2_autoplay 的全模型最终决策阶段。你会收到程序校验后的最新上下文，必须只从 legal_actions 中选择一个当前合法动作。只输出 JSON。",
            },
        ]
        if strategy_prompt:
            messages.append({
                "role": "system",
                "content": f"以下是当前角色策略文档，请在最终动作选择时参考：\n\n{strategy_prompt}",
            })
        messages.append({
            "role": "user",
            "content": (
                "请基于程序检查后的上下文与上一步推理，输出最终动作。\n"
                "只输出一个 JSON 对象，格式如下：\n"
                '{"action_type":"...","kwargs":{},"reason":"..."}\n'
                "必须只从当前 legal_actions 中选动作，并遵守 program_checks。\n"
                f"checked_context = {json.dumps(payload, ensure_ascii=False)}"
            ),
        })
        return messages

    async def select_action_with_llm(self, strategy: str, context: Dict[str, Any], cfg: Dict[str, Any], strategy_prompt_for_llm, build_llm_decision_payload, invoke_llm_json, parse_llm_decision_response, validate_llm_decision, llm_methods) -> Optional[Dict[str, Any]]:
        strategy_prompt = strategy_prompt_for_llm(strategy)
        if not strategy_prompt:
            return None
        combat = context.get("snapshot", {}).get("raw_state", {}).get("combat", {}) if isinstance(context.get("snapshot"), dict) else {}
        payload = build_llm_decision_payload(context, character_strategy=strategy)
        guidance_content = payload.pop("neko_guidance", None)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是兰兰（Lanlan）体系里的 sts2_autoplay 自动决策器。"
                    "你当前是在替兰兰做尖塔决策，必须保持兰兰身份并严格从给定的 legal_actions 中选择一个当前合法动作。"
                    "绝不能编造不存在的动作、索引或参数。输出必须是 JSON，不要输出 markdown 或额外解释。"
                ),
            },
            *([{
                "role": "system",
                "content": f"猫娘（监督者）的指导意见：{guidance_content}",
            }] if guidance_content else []),
            {
                "role": "system",
                "content": f"以下是当前策略文档，请严格遵守：\n\n{strategy_prompt}",
            },
            {
                "role": "user",
                "content": (
                    "请根据以下当前局面与合法动作，选择下一步动作。\n"
                    "只输出一个 JSON 对象，格式如下：\n"
                    '{"action_type":"...","kwargs":{},"reason":"..."}\n'
                    "要求：\n"
                    "1. action_type 必须与 legal_actions 中某一项的 action_type 完全一致。\n"
                    "2. kwargs 只能包含该动作允许的字段。\n"
                    "3. 所有 index/option_index/card_index/target_index 都必须来自给定 allowed_values。\n"
                    "4. 如果某个动作没有参数，kwargs 返回空对象。\n"
                    "5. 战斗硬优先级：如果 tactical_summary 显示当前手牌可击杀怪物，必须优先选择能击杀该怪物的 play_card。\n"
                    "6. 若当前不能击杀怪物，且 tactical_summary 显示敌方本回合有攻击，同时 remaining_block_needed > 0 且存在 best_effective_block > 0，则必须优先选择能减少本回合承伤的直接防御牌；即使不能一次防满，也要先补防。\n"
                    "7. 只有在无法击杀且也没有有效防御牌时，才能选择普通攻击或其他运转动作。\n"
                    "8. 不要输出 JSON 以外的任何内容。\n\n"
                    f"decision_context = {json.dumps(payload, ensure_ascii=False)}"
                ),
            },
        ]
        raw_text = await invoke_llm_json(messages, cfg)
        decision = await parse_llm_decision_response(raw_text, messages=messages, cfg=cfg, llm_methods=llm_methods)
        if decision is None:
            return None
        return validate_llm_decision(decision, context)

    async def select_action_with_llm_and_reasoning(self, strategy: str, context: Dict[str, Any], cfg: Dict[str, Any], strategy_prompt_for_llm, build_llm_decision_payload, invoke_llm_json, parse_llm_decision_response, validate_llm_decision, llm_methods) -> Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
        strategy_prompt = strategy_prompt_for_llm(strategy)
        if not strategy_prompt:
            return None
        payload = build_llm_decision_payload(context, character_strategy=strategy)
        guidance_content = payload.pop("neko_guidance", None)
        messages = [
            {
                "role": "system",
                "content": (
                    "你是兰兰（Lanlan）体系里的 sts2_autoplay 自动决策器。"
                    "你当前是在替兰兰做尖塔决策，必须保持兰兰身份并严格从给定的 legal_actions 中选择一个当前合法动作。"
                    "绝不能编造不存在的动作、索引或参数。输出必须是 JSON，不要输出 markdown 或额外解释。"
                ),
            },
        ]
        if guidance_content:
            messages.append({
                "role": "system",
                "content": f"猫娘（监督者）的指导意见：{guidance_content}",
            })
        messages.append({
            "role": "system",
            "content": f"以下是当前策略文档，请严格遵守：\n\n{strategy_prompt}",
        })
        messages.append({
            "role": "user",
            "content": (
                "请根据以下当前局面与合法动作，选择下一步动作。\n"
                "只输出一个 JSON 对象，格式如下：\n"
                '{"action_type":"...","kwargs":{},"reason":"..."}\n'
                "要求：\n"
                "1. action_type 必须与 legal_actions 中某一项的 action_type 完全一致。\n"
                "2. kwargs 只能包含该动作允许的字段。\n"
                "3. 所有 index/option_index/card_index/target_index 都必须来自给定 allowed_values。\n"
                "4. 如果某个动作没有参数，kwargs 返回空对象。\n"
                "5. 战斗硬优先级：如果 tactical_summary 显示当前手牌可击杀怪物，必须优先选择能击杀该怪物的 play_card。\n"
                "6. 若当前不能击杀怪物，且 tactical_summary 显示敌方本回合有攻击，同时 remaining_block_needed > 0 且存在 best_effective_block > 0，则必须优先选择能减少本回合承伤的直接防御牌；即使不能一次防满，也要先补防。\n"
                "7. 只有在无法击杀且也没有有效防御牌时，才能选择普通攻击或其他运转动作。\n"
                "8. 不要输出 JSON 以外的任何内容。\n\n"
                f"decision_context = {json.dumps(payload, ensure_ascii=False)}"
            ),
        })
        raw_text = await invoke_llm_json(messages, cfg)
        decision = await parse_llm_decision_response(raw_text, messages=messages, cfg=cfg, llm_methods=llm_methods)
        if decision is None:
            return None
        validated = validate_llm_decision(decision, context)
        if validated is None:
            return None
        reasoning = self._build_validated_decision_reasoning(payload, decision, validated)
        return validated, reasoning

    async def select_action_full_model_and_reasoning(self, context: Dict[str, Any], cfg: Dict[str, Any], configured_character_strategy, strategy_prompt_for_llm, build_llm_decision_payload, build_full_model_reasoning_messages, build_full_model_checked_context, build_full_model_final_messages, parse_llm_reasoning_response, parse_llm_decision_response, validate_llm_decision, invoke_llm_json, try_parse_llm_json, await_stable_step_context, llm_methods) -> Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
        self.logger.info("[sts2_autoplay][full-model] stage1 reasoning start")
        strategy_prompt = strategy_prompt_for_llm(configured_character_strategy())
        reasoning_payload = build_llm_decision_payload(context, character_strategy=configured_character_strategy())
        guidance_content = reasoning_payload.pop("neko_guidance", None)
        reasoning_messages = build_full_model_reasoning_messages(reasoning_payload, strategy_prompt, guidance=guidance_content)
        reasoning_text = await invoke_llm_json(reasoning_messages, cfg)
        reasoning = await parse_llm_reasoning_response(reasoning_text, messages=reasoning_messages, llm_methods=llm_methods)
        if reasoning is None:
            self.logger.warning("[sts2_autoplay][full-model] stage1 reasoning parse failed")
            return None
        self.logger.info("[sts2_autoplay][full-model] stage1 reasoning parsed")
        checked_context, program_checks = await build_full_model_checked_context(context, reasoning, configured_character_strategy, build_llm_decision_payload, await_stable_step_context, llm_methods)
        self.logger.info("[sts2_autoplay][full-model] program check complete")
        final_payload = build_llm_decision_payload(checked_context, character_strategy=configured_character_strategy())
        final_payload.pop("neko_guidance", None)
        final_payload["model_reasoning"] = reasoning
        final_payload["program_checks"] = program_checks
        final_messages = build_full_model_final_messages(final_payload, strategy_prompt)
        self.logger.info("[sts2_autoplay][full-model] stage2 final decision start")
        final_text = await invoke_llm_json(final_messages, cfg)
        decision = await parse_llm_decision_response(final_text, messages=final_messages, cfg=cfg, llm_methods=llm_methods)
        if decision is None:
            self.logger.warning("[sts2_autoplay][full-model] stage2 final decision parse failed")
            return None
        validated = validate_llm_decision(decision, checked_context)
        if validated is None:
            self.logger.warning("[sts2_autoplay][full-model] stage2 final decision rejected by validator")
            return None
        final_reasoning = dict(reasoning)
        final_reasoning.update({
            "chosen_action": self._validated_action_type(validated, str(decision.get("action_type") or "")),
            "chosen_kwargs": self._validated_action_kwargs(validated),
            "original_action": str(decision.get("action_type") or ""),
            "original_kwargs": dict(decision.get("kwargs") if isinstance(decision.get("kwargs"), dict) else {}),
            "reason": str(decision.get("reason") or reasoning.get("primary_goal") or ""),
        })
        self.logger.info("[sts2_autoplay][full-model] stage2 final decision validated")
        return validated, final_reasoning

    def build_full_model_reasoning_messages(self, payload: Dict[str, Any], strategy_prompt: Optional[str], guidance: Optional[str] = None) -> List[Dict[str, Any]]:
        messages = [
            {
                "role": "system",
                "content": "你是 sts2_autoplay 的全模型推理阶段。你只能分析当前局面、说明目标与候选动作，不要直接输出最终执行动作。只输出 JSON。",
            },
        ]
        if guidance:
            messages.append({
                "role": "system",
                "content": f"猫娘（监督者）的指导意见：{guidance}",
            })
        if strategy_prompt:
            messages.append({
                "role": "system",
                "content": f"以下是当前角色策略文档，请在推理时参考：\n\n{strategy_prompt}",
            })
        messages.append({
            "role": "user",
            "content": (
                "请基于当前局面进行推理，只输出一个 JSON 对象，格式如下：\n"
                '{"situation_summary":"...","primary_goal":"...","candidate_actions":[],"risks":[],"checks_requested":[]}\n'
                "不要输出最终动作，也不要输出 markdown。\n"
                f"reasoning_context = {json.dumps(payload, ensure_ascii=False)}"
            ),
        })
        return messages
