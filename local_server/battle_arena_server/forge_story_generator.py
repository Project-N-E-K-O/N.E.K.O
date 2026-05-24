# -*- coding: utf-8 -*-
"""Forged card story generation for Neko Brawl.

This module intentionally owns the whole "story lead -> LLM prompt -> card
story" chain. The battle arena server should only call the thin public helper
below, so provider selection, prompt wording, fallback handling, and future
LLM changes stay in one place.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any


MAX_STORY_LEAD_CHARS = 500
MAX_FIELD_CHARS = 160
MAX_STORY_CHARS = 260
FORGE_STORY_TIMEOUT_SECONDS = 25
FORGE_STORY_MAX_TOKENS = 240


class ForgeStoryGenerationError(RuntimeError):
    """Raised when the NEKO core LLM path cannot produce a usable story."""


@dataclass(frozen=True)
class ForgeStoryResult:
    story: str
    provider: str
    model: str
    source_fact_id: str | None = None


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _strip_code_fence(text: str) -> str:
    cleaned = str(text or "").strip()
    match = re.fullmatch(r"```(?:\w+)?\s*(.*?)\s*```", cleaned, flags=re.S)
    if match:
        cleaned = match.group(1).strip()
    return cleaned.strip().strip('"').strip("'").strip()


def _repair_utf8_mojibake(text: str) -> str:
    """Repair common UTF-8-as-Latin-1 mojibake returned by some proxies."""

    source = str(text or "")
    if not source:
        return source
    suspicious = sum(source.count(ch) for ch in ("æ", "å", "ç", "è", "é", "ï¼", "ã€"))
    if suspicious < 3:
        return source
    try:
        repaired = source.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return source
    return repaired if repaired else source


def _clean_story(text: str) -> str:
    cleaned = _strip_code_fence(_repair_utf8_mojibake(text))
    cleaned = re.sub(r"^\s*(?:故事正文|卡牌故事|story)\s*[:：]\s*", "", cleaned, flags=re.I).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if not cleaned:
        raise ForgeStoryGenerationError("empty_story")
    return _clip(cleaned, MAX_STORY_CHARS)


def _card_line(card: dict[str, Any], key: str, label: str) -> str:
    return f"- {label}：{_clip(card.get(key), MAX_FIELD_CHARS) or '未提供'}"


def _configured_llm_targets(config_manager: Any) -> list[tuple[str, dict[str, Any]]]:
    """Return NEKO model configs to try for short text generation.

    The summary tier is the intended lightweight text path, but the bundled
    free Lanlan summary endpoint can reject non-dialogue helper prompts. The
    agent tier is still a NEKO-managed provider config and works as a real
    service fallback without hardcoding a vendor.
    """

    targets: list[tuple[str, dict[str, Any]]] = []
    seen: set[tuple[str, str, str]] = set()
    for name in ("summary", "agent"):
        try:
            cfg = config_manager.get_model_api_config(name) or {}
        except Exception:
            continue
        model = str(cfg.get("model") or "").strip()
        base_url = str(cfg.get("base_url") or "").strip()
        api_key = str(cfg.get("api_key") or "").strip()
        if not (model and base_url):
            continue
        key = (name, model, base_url)
        if key in seen:
            continue
        seen.add(key)
        targets.append((name, {**cfg, "model": model, "base_url": base_url, "api_key": api_key}))
    return targets


def build_forge_story_prompt(payload: dict[str, Any]) -> tuple[str, str]:
    """Build the prompt pair sent through NEKO's configured LLM provider.

    TODO: [真实自身故事系统接入]
    当前 storyLead 来自 active facts.json 或临时事件。后续接入“自身故事”后，
    在调用本函数前把对应的故事引子填入 storyLead 即可。
    """

    story_lead = _clip(payload.get("storyLead"), MAX_STORY_LEAD_CHARS)
    if not story_lead:
        raise ForgeStoryGenerationError("missing_story_lead")

    card = payload.get("card")
    if not isinstance(card, dict):
        card = {}
    lanlan_name = _clip(payload.get("lanlanName") or payload.get("character"), 40)
    master_name = _clip(payload.get("masterName"), 40)
    lanlan_prompt = _clip(payload.get("lanlanPrompt"), 1200)

    system_prompt = (
        "你是“猫娘大乱斗”的卡牌故事写作者。"
        f"当前猫娘是{lanlan_name or '当前猫娘'}，主人是{master_name or '主人'}。"
        "你的任务是把真实记忆事实抽取出的故事引子，改写成一张 Forged 卡牌的专属小故事。"
        "必须保留原有事实关系与情绪基调，不要新增现实中不存在的重大事实。"
        "可以做轻量游戏化演绎，但不要改变事实方向。"
        "故事主体必须以第三人称叙事描写猫娘的动作、情绪和战斗化想象。"
        "最后必须用一句猫娘第一人称台词收束，用引号包住，并贴合卡牌主属性与 Combo 属性。"
    )
    if lanlan_prompt:
        system_prompt += f"\n\n当前猫娘人格/背景摘要：\n{lanlan_prompt}"

    user_prompt = "\n".join(
        [
            "请根据下面的故事引子和卡牌模板，生成一段卡牌专属小故事。",
            "",
            "硬性要求：",
            "1. 只输出故事正文，不要标题，不要 JSON，不要解释规则。",
            "2. 字数控制在 80-140 个中文字符左右。",
            "3. 保留故事引子的情绪基调和关系，不要改成相反含义。",
            "4. 不要编造新的现实履历、地点、人物关系或长期承诺。",
            "5. 可以把卡牌效果轻微融入动作、气氛或战斗画面。",
            "6. 大部分叙事内容使用第三人称，例如“她”“猫娘”“{猫娘名}”，不要全篇用第一人称。",
            "7. 最后一小句必须是猫娘自己的第一人称台词，用中文引号“”包住；台词要表现猫娘性格，并尽量贴合主属性、Combo 属性与卡牌效果。",
            "8. 如果主属性偏热情，台词更主动明亮；偏温柔，台词更体贴安抚；偏高冷，台词更克制清冷；偏天然，台词更轻快直觉。",
            "",
            "故事引子：",
            story_lead,
            "",
            "卡牌信息：",
            _card_line(card, "name", "卡名"),
            _card_line(card, "baseCode", "基础编号"),
            _card_line(card, "type", "类型"),
            _card_line(card, "cost", "费用"),
            _card_line(card, "attrName", "主属性"),
            _card_line(card, "comboAttrName", "Combo 属性"),
            _card_line(card, "mainText", "主效果"),
            _card_line(card, "comboText", "Combo 效果"),
        ]
    )
    return system_prompt, user_prompt


async def generate_forge_card_story(payload: dict[str, Any]) -> ForgeStoryResult:
    """Generate a Forged card story through NEKO's configured core LLM client.

    The active NEKO catgirl is authoritative: forge stories are written from
    the memory context of the catgirl who invited the player, not from a UI
    display name supplied by the battle page.
    """

    from active_neko_context import resolve_active_neko_context
    from utils.config_manager import get_config_manager
    from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm, reset_active_character, set_active_character
    from utils.token_tracker import set_call_type

    config_manager = get_config_manager()
    active_context = await resolve_active_neko_context()
    master_name = active_context.master_name
    lanlan_name = active_context.lanlan_name
    lanlan_prompt = active_context.lanlan_prompt

    prompt_payload = {
        **payload,
        "lanlanName": lanlan_name,
        "character": lanlan_name,
        "masterName": master_name,
        "lanlanPrompt": lanlan_prompt,
    }
    system_prompt, user_prompt = build_forge_story_prompt(prompt_payload)

    targets = _configured_llm_targets(config_manager)
    if not targets:
        raise ForgeStoryGenerationError("llm_model_not_configured")

    last_error = ""
    for tier_name, cfg in targets:
        token = set_active_character(master_name, lanlan_name)
        set_call_type("neko_brawl_forge_story")
        llm = create_chat_llm(
            cfg["model"],
            cfg["base_url"],
            cfg.get("api_key") or "",
            max_completion_tokens=FORGE_STORY_MAX_TOKENS,
            timeout=FORGE_STORY_TIMEOUT_SECONDS,
        )

        try:
            async with llm:
                result = await asyncio.wait_for(
                    llm.ainvoke(
                        [
                            SystemMessage(content=system_prompt),
                            HumanMessage(content=user_prompt),
                        ]
                    ),
                    timeout=FORGE_STORY_TIMEOUT_SECONDS,
                )
            story = _clean_story(getattr(result, "content", "") or "")
            return ForgeStoryResult(
                story=story,
                provider=f"neko-core-{tier_name}",
                model=cfg["model"],
                source_fact_id=payload.get("sourceFactId") or payload.get("factId"),
            )
        except asyncio.TimeoutError:
            last_error = f"{tier_name}:timeout"
        except Exception as exc:
            last_error = f"{tier_name}:{str(exc) or type(exc).__name__}"
        finally:
            reset_active_character(token)

    raise ForgeStoryGenerationError(last_error or "all_llm_targets_failed")
