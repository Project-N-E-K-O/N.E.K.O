# -*- coding: utf-8 -*-
"""Forged card story generation for Neko Brawl.

This module intentionally owns the whole "story lead -> LLM prompt -> card
story" chain. The battle arena server should only call the thin public helper
below, so provider selection, prompt wording, fallback handling, and future
LLM changes stay in one place.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any


MAX_STORY_LEAD_CHARS = 500
MAX_FIELD_CHARS = 160
MAX_STORY_CHARS = 260
FORGE_STORY_TIMEOUT_SECONDS = 25
FORGE_STORY_MAX_TOKENS = 240
FORGE_STORY_FORBIDDEN_GAME_TERMS = (
    "Combo",
    "combo",
    "连携",
    "费用",
    "行动力",
    "效果",
    "伤害",
    "护盾",
    "防御",
    "防御力场",
    "力场",
    "Boss",
    "boss",
    "抽牌",
    "回合",
    "对局",
    "战斗",
    "攻击",
    "卡牌机制",
)


class ForgeStoryGenerationError(RuntimeError):
    """Raised when the NEKO core LLM path cannot produce a usable story."""


@dataclass(frozen=True)
class ForgeStoryResult:
    story: str
    provider: str
    model: str
    source_fact_id: str | None = None


def _forge_request_id(payload: dict[str, Any]) -> str:
    request_id = str(payload.get("requestId") or payload.get("_requestId") or "").strip()
    return request_id or f"forge-{uuid.uuid4().hex[:10]}"


def _log_value(value: Any, *, limit: int = 4000) -> Any:
    if isinstance(value, dict):
        return {str(k): _log_value(v, limit=limit) for k, v in value.items() if "api_key" not in str(k).lower()}
    if isinstance(value, list):
        return [_log_value(v, limit=limit) for v in value[:20]]
    if isinstance(value, tuple):
        return [_log_value(v, limit=limit) for v in value[:20]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _forge_log(request_id: str, event: str, **fields: Any) -> None:
    """Emit detailed forge diagnostics to the running server console only.

    These logs are intentionally not written to local files. They are for the
    temporary card-forging integration pass and should stay free of API keys.
    """

    payload = {key: _log_value(value) for key, value in fields.items()}
    print(
        f"[forge-card-story][{request_id}][{event}] "
        f"{json.dumps(payload, ensure_ascii=False, default=str)}",
        flush=True,
    )


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
    for term in FORGE_STORY_FORBIDDEN_GAME_TERMS:
        if term in cleaned:
            raise ForgeStoryGenerationError(f"game_rule_term_in_story:{term}")
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
        "你是“猫娘大乱斗”的记忆小故事写作者。"
        f"当前猫娘是{lanlan_name or '当前猫娘'}，主人是{master_name or '主人'}。"
        "你的任务是把真实记忆事实抽取出的故事引子，改写成一段猫娘记忆小故事。"
        "必须保留原有事实关系与情绪基调，不要新增现实中不存在的重大事实。"
        "主属性只表示猫娘推动这个事件时的性格气质，不是卡牌规则。"
        "例如温柔表示她会用体贴、安抚、照顾、认真倾听的方式推动故事引子继续发展。"
        "故事的动作、气氛和最后台词只需要贴合主属性代表的性格气质。"
        "不要参考、复述或迎合卡牌名称、羁绊名称、事件标题、卡牌编号、费用、类型、效果、Combo 属性或 Combo 效果；这些只用于游戏规则和界面展示，不是故事提示词。"
        "不要写任何游戏规则、战斗画面、技能表现、护盾、防御、力场、伤害、攻击、Boss、抽牌、回合、对局或 Combo。"
        "故事正文必须严格分成两个部分：前面是第三人称叙事，最后一句是猫娘第一人称台词。"
        "第三人称叙事必须描写猫娘的日常动作、情绪和关系细节，不能直接照抄故事引子。"
        "最后一句必须单独作为收束台词，用中文引号“”包住，并以猫娘第一人称说话。"
    )
    if lanlan_prompt:
        system_prompt += f"\n\n当前猫娘人格/背景摘要：\n{lanlan_prompt}"

    user_prompt = "\n".join(
        [
            "请根据下面的故事引子和本次 Roll 出的主属性，生成一段猫娘记忆小故事。",
            "",
            "硬性要求：",
            "1. 只输出故事正文，不要标题，不要 JSON，不要解释规则。",
            "2. 字数控制在 80-140 个中文字符左右。",
            "3. 保留故事引子的情绪基调和关系，但不要原样粘贴故事引子；请改写成自然的第三人称叙事。",
            "4. 不要编造新的现实履历、地点、人物关系或长期承诺。",
            "5. 只能写日常动作、情绪反应和关系细节，不要写战斗化想象，也不要写成卡牌机制、费用、编号或效果说明。",
            "6. 前面所有叙事句必须使用第三人称，例如“她”“猫娘”“{猫娘名}”；前面叙事句不要用“我”“我们”作为叙事主语。",
            "7. 最后一小句必须是猫娘自己的第一人称台词，必须用中文引号“”包住；台词要表现猫娘性格，并贴合主属性。",
            "8. 主属性是本次 Roll 出来的固定性格气质，故事必须围绕它推动引子发展：热情要主动明亮，温柔要体贴安抚，高冷要克制清冷，天然要轻快直觉。",
            "9. 故事和游戏规则无关，不要写 Combo、连携、费用、效果、伤害、护盾、防御、力场、攻击、Boss、抽牌、回合、对局等规则或战斗内容。",
            "10. 不要因为卡名、羁绊名、事件标题、编号、费用、类型、效果或 Combo 属性改变故事方向；本请求不会提供这些信息，即使模型猜到也不要使用。",
            "11. 输出格式必须是：第三人称叙事一到三句 + 最后一小句中文引号内第一人称台词。不要在台词后再追加叙事。",
            "",
            "故事引子：",
            story_lead,
            "",
            "本次 Roll 主属性：",
            _card_line(card, "attrName", "主属性"),
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

    request_id = _forge_request_id(payload)
    started_at = time.perf_counter()
    raw_card = payload.get("card") if isinstance(payload.get("card"), dict) else {}
    _forge_log(
        request_id,
        "generator.start",
        sourceFactId=payload.get("sourceFactId") or payload.get("factId"),
        storyLead=payload.get("storyLead"),
        requestedLanlanName=payload.get("lanlanName") or payload.get("character"),
        requestedMasterName=payload.get("masterName"),
        card={
            "attrName": raw_card.get("attrName"),
        },
    )

    config_manager = get_config_manager()
    runtime_hint = (
        str(payload.get("runtimeCharacterHint") or payload.get("runtime_character_hint") or payload.get("character") or "")
        .strip()
    )
    active_context = await resolve_active_neko_context(runtime_character_hint=runtime_hint or None)
    master_name = active_context.master_name
    lanlan_name = active_context.lanlan_name
    lanlan_prompt = active_context.lanlan_prompt
    _forge_log(
        request_id,
        "active_context.resolved",
        lanlanName=lanlan_name,
        masterName=master_name,
        source=getattr(active_context, "source", None),
        factsPath=str(getattr(active_context, "facts_path", "") or ""),
        lanlanPromptChars=len(str(lanlan_prompt or "")),
        lanlanPromptPreview=lanlan_prompt,
    )

    prompt_payload = {
        **payload,
        "lanlanName": lanlan_name,
        "character": lanlan_name,
        "masterName": master_name,
        "lanlanPrompt": lanlan_prompt,
    }
    system_prompt, user_prompt = build_forge_story_prompt(prompt_payload)
    _forge_log(
        request_id,
        "prompt.built",
        systemPromptChars=len(system_prompt),
        userPromptChars=len(user_prompt),
        systemPrompt=system_prompt,
        userPrompt=user_prompt,
    )

    targets = _configured_llm_targets(config_manager)
    if not targets:
        _forge_log(request_id, "llm.targets.empty", elapsedMs=round((time.perf_counter() - started_at) * 1000, 1))
        raise ForgeStoryGenerationError("llm_model_not_configured")
    _forge_log(
        request_id,
        "llm.targets.ready",
        targets=[
            {
                "tier": tier_name,
                "model": cfg.get("model"),
                "baseUrl": cfg.get("base_url"),
                "hasApiKey": bool(cfg.get("api_key")),
            }
            for tier_name, cfg in targets
        ],
    )

    last_error = ""
    for tier_name, cfg in targets:
        token = set_active_character(master_name, lanlan_name)
        set_call_type("neko_brawl_forge_story")
        attempt_started_at = time.perf_counter()
        _forge_log(
            request_id,
            "llm.attempt.start",
            tier=tier_name,
            model=cfg.get("model"),
            baseUrl=cfg.get("base_url"),
            timeoutSeconds=FORGE_STORY_TIMEOUT_SECONDS,
            maxTokens=FORGE_STORY_MAX_TOKENS,
        )
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
            raw_content = getattr(result, "content", "") or ""
            _forge_log(
                request_id,
                "llm.attempt.raw_response",
                tier=tier_name,
                elapsedMs=round((time.perf_counter() - attempt_started_at) * 1000, 1),
                rawChars=len(str(raw_content)),
                rawContent=raw_content,
            )
            story = _clean_story(raw_content)
            total_elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
            _forge_log(
                request_id,
                "generator.success",
                provider=f"neko-core-{tier_name}",
                model=cfg["model"],
                sourceFactId=payload.get("sourceFactId") or payload.get("factId"),
                storyChars=len(story),
                story=story,
                elapsedMs=total_elapsed_ms,
            )
            return ForgeStoryResult(
                story=story,
                provider=f"neko-core-{tier_name}",
                model=cfg["model"],
                source_fact_id=payload.get("sourceFactId") or payload.get("factId"),
            )
        except asyncio.TimeoutError:
            last_error = f"{tier_name}:timeout"
            _forge_log(
                request_id,
                "llm.attempt.timeout",
                tier=tier_name,
                elapsedMs=round((time.perf_counter() - attempt_started_at) * 1000, 1),
            )
        except Exception as exc:
            last_error = f"{tier_name}:{str(exc) or type(exc).__name__}"
            _forge_log(
                request_id,
                "llm.attempt.failed",
                tier=tier_name,
                error=last_error,
                elapsedMs=round((time.perf_counter() - attempt_started_at) * 1000, 1),
            )
        finally:
            reset_active_character(token)

    _forge_log(
        request_id,
        "generator.failed",
        error=last_error or "all_llm_targets_failed",
        elapsedMs=round((time.perf_counter() - started_at) * 1000, 1),
    )
    raise ForgeStoryGenerationError(last_error or "all_llm_targets_failed")


# ── 探险终点结算故事 ────────────────────────────────────────────────────────
# 复用与卡牌故事相同的 LLM 调用骨架（_configured_llm_targets + create_chat_llm +
# wait_for + _clean_story），但 prompt 改成"按探险历程写一段总结小故事"。
# 前端在后端不可用/失败时会回退到自己的模板（buildAdventureEndingStory），所以
# 这里失败直接抛错即可，路由层转成 {success:false}。

ADVENTURE_ENDING_MAX_TOKENS = 420
ADVENTURE_ENDING_TIMEOUT_SECONDS = 30


def _summarize_adventure_log(log: Any) -> dict[str, Any]:
    items = log if isinstance(log, list) else []
    events = [l for l in items if isinstance(l, dict) and l.get("type") == "event"]
    successes = sum(1 for l in events if l.get("success"))
    titles = [str(l.get("title")) for l in events if l.get("title")]
    return {
        "successes": successes,
        "fails": len(events) - successes,
        "sides": sum(1 for l in items if isinstance(l, dict) and l.get("type") == "encounter" and l.get("choice") == "enter"),
        "rests": sum(1 for l in items if isinstance(l, dict) and l.get("type") == "rest"),
        "titles": titles,
    }


def build_adventure_ending_prompt(payload: dict[str, Any], lanlan_name: str, master_name: str, lanlan_prompt: str) -> tuple[str, str]:
    stats = _summarize_adventure_log(payload.get("log"))
    system_prompt = (
        "你是“猫娘大乱斗”探险模式的结算讲述者。"
        f"当前猫娘是{lanlan_name or '猫娘'}，主人是{master_name or '主人'}。"
        "请把一次卡牌探险的完整历程，写成一段温暖、轻盈的总结性小故事。"
        "故事是这只猫娘和她的队友猫娘一起走完这趟探险后的回顾。"
        "只写旅途见闻、情绪与两人之间的相处，不要出现卡牌、数值、Boss、战斗、回合、行动力、护盾等任何游戏术语。"
        "结尾用猫娘第一人称台词收束，并用中文引号“”包住。"
    )
    if lanlan_prompt:
        system_prompt += f"\n\n当前猫娘人格/背景摘要：\n{_clip(lanlan_prompt, 1200)}"
    lines = [
        "这趟探险的历程：",
        f"- 沿途完成的事件：顺利 {stats['successes']} 次，不太顺利 {stats['fails']} 次",
        f"- 一起拐进支线小路：{stats['sides']} 次",
        f"- 停下休息：{stats['rests']} 次",
    ]
    if stats["titles"]:
        lines.append(f"- 经历过的片段：{'、'.join(stats['titles'][:8])}")
    lines += [
        "",
        "硬性要求：",
        "1. 只输出故事正文，不要标题、不要解释、不要 JSON。",
        "2. 中文，120-200 字左右。",
        "3. 前面用第三人称叙事，最后一小句是猫娘第一人称台词，用中文引号“”包住。",
        "4. 不要写任何游戏机制、数值或战斗内容。",
    ]
    return system_prompt, "\n".join(lines)


async def generate_adventure_ending_story(payload: dict[str, Any]) -> ForgeStoryResult:
    """按探险历程用 NEKO 核心 LLM 生成终点结算小故事。当前猫娘为叙事主体。"""
    from active_neko_context import resolve_active_neko_context
    from utils.config_manager import get_config_manager
    from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm, reset_active_character, set_active_character
    from utils.token_tracker import set_call_type

    request_id = _forge_request_id(payload)
    started_at = time.perf_counter()
    config_manager = get_config_manager()
    active = await resolve_active_neko_context()
    master_name = active.master_name
    lanlan_name = active.lanlan_name
    system_prompt, user_prompt = build_adventure_ending_prompt(payload, lanlan_name, master_name, active.lanlan_prompt)
    _forge_log(request_id, "ending.prompt.built", systemPromptChars=len(system_prompt), userPromptChars=len(user_prompt))

    targets = _configured_llm_targets(config_manager)
    if not targets:
        raise ForgeStoryGenerationError("llm_model_not_configured")

    last_error = ""
    for tier_name, cfg in targets:
        token = set_active_character(master_name, lanlan_name)
        set_call_type("neko_brawl_adventure_ending")
        llm = create_chat_llm(
            cfg["model"], cfg["base_url"], cfg.get("api_key") or "",
            max_completion_tokens=ADVENTURE_ENDING_MAX_TOKENS,
            timeout=ADVENTURE_ENDING_TIMEOUT_SECONDS,
        )
        try:
            async with llm:
                result = await asyncio.wait_for(
                    llm.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]),
                    timeout=ADVENTURE_ENDING_TIMEOUT_SECONDS,
                )
            story = _clean_story(getattr(result, "content", "") or "")
            _forge_log(request_id, "ending.success", provider=f"neko-core-{tier_name}", storyChars=len(story),
                       elapsedMs=round((time.perf_counter() - started_at) * 1000, 1))
            return ForgeStoryResult(story=story, provider=f"neko-core-{tier_name}", model=cfg["model"])
        except asyncio.TimeoutError:
            last_error = f"{tier_name}:timeout"
        except Exception as exc:
            last_error = f"{tier_name}:{str(exc) or type(exc).__name__}"
        finally:
            reset_active_character(token)

    _forge_log(request_id, "ending.failed", error=last_error or "all_llm_targets_failed")
    raise ForgeStoryGenerationError(last_error or "all_llm_targets_failed")
