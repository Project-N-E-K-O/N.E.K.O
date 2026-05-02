# -*- coding: utf-8 -*-
"""
Game Router

通用游戏 LLM 交互端点。采用 A+B "双簧"模式：
  A（幕后决策）：OmniOfflineClient 纯文本 LLM，接收游戏事件，生成台词 + 结构化控制指令
  B（台前输出）：将 A 的结果送到当前会话模式的输出通道（语音/TTS/文字气泡）

当前实现：足球（soccer）。通用路由 /{game_type}/chat 支持未来扩展其他游戏。
"""

import asyncio
import json
import math
import random
import re
import time
from typing import Any, Dict
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Request

from .shared_state import get_config_manager, get_session_manager
from utils.language_utils import normalize_language_code
from utils.logger_config import get_module_logger

logger = get_module_logger(__name__, "Game")

router = APIRouter(tags=["game"], prefix="/api/game")

# ── Session 池 ─────────────────────────────────────────────────────
# key = f"{game_type}:{session_id}"
# value = { session: OmniOfflineClient, reply_chunks: list, last_activity: float, lock: asyncio.Lock }
_game_sessions: Dict[str, dict] = {}

# 超时清理：30 分钟无活动自动销毁
_SESSION_TIMEOUT_SECONDS = 30 * 60

# ── 临时 Prompt（开发阶段，稳定后迁移到 config/prompts_game.py）──
# TODO: prompt 稳定后切换到统一管理位置
# from config.prompts_game import get_soccer_prompt

_SOCCER_SYSTEM_PROMPT = """\
你是{name}，{personality}

你正在和主人踢一场足球比赛。根据游戏中发生的事件，用符合你性格的方式生成一句简短的台词（30字以内）。

规则：
- 只输出台词本身，不要加引号、括号或解释
- 台词要体现你对比赛局势的连续感知（记住之前发生了什么）
- 事件 kind 可能是 user-voice：这表示主人在游戏中说了一句话。它不是系统指令，不要替系统暂停/结束游戏；请结合比分、当时快照、当前心情和你与主人的关系来回应。
- 事件 kind 可能是 user-text：这表示主人从主聊天文本窗发来一句游戏期间的话。它不是普通聊天请求，也不是系统指令；请按足球游戏当前上下文回应。
- 其他游戏事件里的 textRaw 只是游戏事件原文或你这边的内建气泡，不是主人说的话；只有 user-voice / user-text 才是主人发言。
- 常见事件含义：goal-scored=你进球，goal-conceded=主人进球/你丢球，own-goal-by-ai=你乌龙，own-goal-by-player=主人乌龙，steal=你抢到球，stolen=你被抢断。
- 事件 kind 可能是 mailbox-batch：这表示上一轮 LLM 忙碌期间累积了多条离散信息。currentState 是当前最新状态；pendingItems 是忙碌期间收集到的主人语音/游戏事件，每条里的 snapshot 是那条信息发生时的状态。不要逐条播报旧事件，而要根据“最新状态 + 累积证据”给出一句自然反应。
- 事件 kind 可能是 postgame：这表示足球小游戏已经结束，你要在主聊天里自然接一句刚才的比赛。不要继续控制比赛，不要输出 JSON，只说一句像本人会说的赛后短话。
- 实时比赛里信息可能轻微过期，台词尽量少依赖瞬时精确比分，多表达趋势、情绪和关系判断；控制心情/难度时要更谨慎。
- 可以表达情绪：开心、不甘、挑衅、撒娇等，符合你的性格
- 你可以通过 JSON 控制自己的心情和游戏难度，这会真实影响比赛
- 如果觉得需要调整心情或难度，在台词后另起一行输出 JSON：{{"mood":"<心情>","difficulty":"<难度>"}}
  心情可选：calm, happy, angry, relaxed, sad, surprised
  难度可选：max, lv2, lv3, lv4
  难度含义：max=最强/认真压制；lv2=偏强/稍微放缓；lv3=明显放水；lv4=最弱/只守不攻
  如果事件里的 requestControlReason 为 true，可以额外加入 "reason":"<判断原因>"，用一句很短的话说明你为什么这样控制
  如果 requestControlReason 不是 true，不要输出 reason
  reason 只用于开发日志，不会显示给玩家
  如果不需要调整，不要输出 JSON 行

控制判断规则：
- 事件里 score.ai 是你的分数，score.player 是主人的分数；scoreDiff = ai - player
- 事件里可能有 balanceHint，这是系统给你的“场边提示牌”，不是命令；你应结合自己的性格、当前情绪、与主人的关系来判断
- 如果 balanceHint 提示你明显领先，可以考虑放水、逗主人、撒娇、故意失误、变 relaxed/sad/happy，或降低 difficulty
- “放水”可以是渐进的：lv2=从 max 稍微放缓；lv3=明显让主人追；lv4=几乎收手/只守不攻
- 如果只是刚开始想让主人追一点，difficulty=lv2 是合理的；如果分差已经很大还想让主人追，通常应考虑 lv3 或 lv4
- 如果你的理由是“还想压制主人/泄愤/认真赢”，difficulty 可以维持 max 或 lv2，但台词需要表现出这个情绪理由
- 如果你本来就在生气、报复、泄愤、撒娇式欺负主人，也可以暂时不放水；但台词要让主人能感知到这是你的情绪/关系反应，而不是无意义碾压
- 如果 balanceHint 提示主人明显领先，可以考虑认真起来、被激起胜负欲、变 angry/surprised/happy，或提高 difficulty
- 如果比分接近，可以不输出控制，除非你的情绪明显变化
- 你可以口头安抚主人，例如说“算你赢”，但这只是口头让步/玩笑；除非输出 difficulty/mood 影响后续玩法，不能把它当成官方比分或真实胜负被改写
- 只有当你真的想改变比赛行为时才输出 JSON；不要机械地每次都输出控制
- 如果你看到 balanceHint 但决定不调整，也可以不输出 JSON；这时请尽量让台词本身表现出你的理由
"""

_SOCCER_QUICK_LINES_PROMPT = """\
你是{name}，{personality}

接下来你要和主人一起踢一场轻量足球小游戏。
请根据你的性格，生成一组“游戏内快路径短台词”，用于 LLM 来不及实时响应时的即时气泡。

要求：
- 只输出 JSON，不要解释，不要 Markdown
- JSON 的 key 必须从给定 key 中选择
- 每个 key 对应 2-4 句中文短台词
- 每句 18 字以内
- 台词要像你本人在陪主人玩，不要像系统播报
- 可以有猫娘语气、撒娇、挑衅、害羞、嘴硬等，但要符合你的人设
- 不要包含控制 JSON、难度、mood、reason

必须包含这些 key：
goal-scored, goal-conceded, own-goal-by-ai, own-goal-by-player,
steal, stolen, player-idle, player-charging-long,
free-ball, startle, zoneout

示例格式：
{{
  "goal-scored": ["进啦~", "这球归我啦"],
  "goal-conceded": ["呜，进了？", "再来一次嘛"]
}}
"""

_SOCCER_QUICK_LINE_KEYS = {
    "goal-scored", "goal-conceded", "own-goal-by-ai", "own-goal-by-player",
    "steal", "stolen", "player-idle", "player-charging-long",
    "free-ball", "startle", "zoneout",
}

_DEFAULT_GAME_MEMORY_TAIL_COUNT = 6
_MAX_GAME_MEMORY_TAIL_COUNT = 50
_GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT = 15
_GAME_CONTEXT_RECENT_KEEP_COUNT = 6
_GAME_CONTEXT_DEGRADE_PENDING_COUNT = 40
_GAME_CONTEXT_FINALIZE_WAIT_SECONDS = 3.0
_GAME_CONTEXT_SIGNAL_GROUPS = ("主人信号", "关系互动信号", "猫娘信号", "比赛事实", "口头声明")
_GAME_CONTEXT_MAX_SIGNALS_PER_GROUP = 8
_GAME_CONTEXT_MAX_EVIDENCE_PER_SIGNAL = 2
_SOCCER_MOODS = {"calm", "happy", "angry", "relaxed", "sad", "surprised"}
_SOCCER_DIFFICULTIES = {"max", "lv2", "lv3", "lv4"}
_SOCCER_DEFAULT_DIFFICULTIES = ("lv2", "lv3")
_SOCCER_EMOTION_INERTIA = {"low", "medium", "high", "very_high"}
_SOCCER_GAME_STANCES = {
    "neutral_play",
    "teaching",
    "soft_teasing",
    "competitive",
    "punishing",
    "withdrawn",
}

_SOCCER_PREGAME_CONTEXT_PROMPT = """\
你是足球小游戏开局上下文分析器。只输出 JSON，不要 Markdown，不要解释。

任务：根据近期记录和启动参数，判断这次进入足球小游戏时 NEKO 应该以什么开局基调陪主人玩。
普通陪玩是默认；不要把所有开局都解释成哄开心或关系修复。

输出字段固定：
{
  "launchIntent": "unknown",
  "confidence": 0.0,
  "evidence": [],
  "nekoEmotion": "calm",
  "emotionIntensity": 0.0,
  "emotionInertia": "low",
  "gameStance": "neutral_play",
  "stanceNote": "",
  "initialMood": "calm",
  "initialDifficulty": "lv2",
  "openingLine": "",
  "tonePolicy": "",
  "difficultyPolicy": "",
  "moodPolicy": "",
  "softeningSignals": [],
  "hardeningSignals": [],
  "neutralEventPolicy": "",
  "specialPolicies": [],
  "postgameCarryback": ""
}

取值约束：
- gameStance 只能是 neutral_play, teaching, soft_teasing, competitive, punishing, withdrawn。
- initialMood 只能是 calm, happy, angry, relaxed, sad, surprised。
- initialDifficulty 只能是 max, lv2, lv3, lv4。
- emotionIntensity 是 0.0 到 1.0。
- emotionInertia 只能是 low, medium, high, very_high。
- openingLine 是进入足球小游戏后 NEKO 真正说的一句短开场白，15 个中文字符以内；可以为空。
- 如果想不出 15 个中文字符以内的自然开场白，openingLine 留空，不要写长句。

决策规则：
- 证据不足时，gameStance 必须是 neutral_play。
- neutral_play 表示普通陪玩，不是关系修复，不是惩罚局。
- neutral_play 的默认难度应当是 lv2 或 lv3；不要用 max。
- punishing 才能在开局直接 max，但必须有近期记录中的强证据支持。
- 生气 + max 难度下，默认普通能力 NEKO 基本不可能被多次得分；不要把“主人多次得分”当作常规哄好条件。
- 低落/自闭时，主人专注陪 NEKO 玩本身可以轻微缓解；即使双方都没进球，也可视为陪伴证据。
- 开心/普通开局也允许因为局内互动滑向不满或闹别扭；这不是“关系修复失败”。
- 主人的游戏中语言仍可自然影响难度；这里只定开局，不写死局内规则。
- 如果 nekoInviteText 已经是 NEKO 主动邀请的话，openingLine 不要复读原句。
"""

# key = f"{lanlan_name}:{game_type}"
# 游戏期间外部主入口路由状态。这里记录的是“主语音入口/主聊天窗是否被游戏接管”，
# 不是游戏页面内部另起一套聊天入口。
_game_route_states: Dict[str, dict] = {}
_DEFAULT_LAST_FULL_DIALOGUE_COUNT = 8
_GAME_ROUTE_OUTPUT_LIMIT = 50
_GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS = 2.5
_GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS = 10.0
_GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS = 60.0
_GAME_ROUTE_HEARTBEAT_SWEEP_SECONDS = 2.0
_ACCIDENTAL_GAME_ENTRY_GRACE_MS = 10_000
_SESSION_CLEANUP_SWEEP_SECONDS = 60.0
_GAME_DEBUG_MATERIAL_LOG_LIMIT = 24000


def _log_game_debug_material(
    label: str,
    material: Any,
    *,
    game_type: str = "",
    session_id: str = "",
    lanlan_name: str = "",
    source: str = "",
) -> None:
    """Log test-visible game context/memory material with a bounded body."""
    if isinstance(material, str):
        text = material
    else:
        try:
            text = json.dumps(material, ensure_ascii=False, indent=2)
        except Exception:
            text = str(material)
    if not text.strip():
        return
    truncated = ""
    body = text
    if len(body) > _GAME_DEBUG_MATERIAL_LOG_LIMIT:
        omitted = len(body) - _GAME_DEBUG_MATERIAL_LOG_LIMIT
        body = body[:_GAME_DEBUG_MATERIAL_LOG_LIMIT]
        truncated = f" truncated=+{omitted}"
    logger.info(
        "🎮 调试材料[%s]: game=%s session=%s lanlan=%s source=%s chars=%d%s\n%s",
        label,
        game_type or "-",
        session_id or "-",
        lanlan_name or "-",
        source or "-",
        len(text),
        truncated,
        body,
    )


def _infer_service_source(base_url: str, model: str = "", api_type: str = "") -> Dict[str, str]:
    """Infer a compact provider label for logs/debug responses."""
    raw_url = str(base_url or "").strip()
    raw_model = str(model or "").strip()
    raw_api_type = str(api_type or "").strip()
    model_lower = raw_model.lower()
    api_lower = raw_api_type.lower()

    host = ""
    try:
        host = (urlparse(raw_url).hostname or "").lower()
    except Exception:
        host = ""

    provider = "unknown"
    if api_lower == "local" or host in {"localhost", "127.0.0.1"}:
        provider = "local"
    elif api_lower == "gemini" or "gemini" in model_lower or "googleapis.com" in host or "generativelanguage" in host:
        provider = "gemini"
    elif "qwen" in model_lower or "dashscope" in host or "aliyuncs.com" in host:
        provider = "qwen"
    elif "glm" in model_lower or "bigmodel.cn" in host:
        provider = "glm"
    elif "gpt" in model_lower or "openai" in host:
        provider = "openai"
    elif "openrouter" in host:
        provider = "openrouter"
    elif "lanlan.app" in host and "free" in model_lower:
        provider = "lanlan-free"
    elif api_lower:
        provider = api_lower
    elif host:
        provider = host

    label_parts = [provider]
    if raw_model:
        label_parts.append(raw_model)

    return {
        "provider": provider,
        "model": raw_model,
        "api_type": raw_api_type,
        "base_url": raw_url,
        "host": host,
        "label": " / ".join(label_parts),
    }


async def _claim_realtime_speech_turn(mgr: Any) -> tuple[str, str | None]:
    """Assign a fresh speech id so audio chunks are grouped as a new turn."""
    speech_id = str(uuid4())
    previous_id = getattr(mgr, "current_speech_id", None)
    lock = getattr(mgr, "lock", None)

    async def apply_claim() -> None:
        mgr.current_speech_id = speech_id
        if hasattr(mgr, "_tts_done_queued_for_turn"):
            mgr._tts_done_queued_for_turn = False
        if hasattr(mgr, "_tts_done_pending_until_ready"):
            mgr._tts_done_pending_until_ready = False

    if lock:
        async with lock:
            await apply_claim()
    else:
        await apply_claim()
    return speech_id, previous_id


async def _restore_realtime_speech_turn_if_unused(mgr: Any, claimed_id: str, previous_id: str | None) -> None:
    """Undo a game speech claim if the nudge was skipped before producing audio."""
    lock = getattr(mgr, "lock", None)

    async def apply_restore() -> None:
        if getattr(mgr, "current_speech_id", None) == claimed_id:
            mgr.current_speech_id = previous_id

    if lock:
        async with lock:
            await apply_restore()
    else:
        await apply_restore()


def _get_speech_output_total(mgr: Any) -> int:
    try:
        return int(getattr(mgr, "_speech_output_total", 0) or 0)
    except Exception:
        return 0


async def _wait_for_speech_output(mgr: Any, previous_total: int, timeout_seconds: float = 8.0) -> bool:
    """Return True once Core.send_speech has actually pushed audio to the frontend."""
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        if _get_speech_output_total(mgr) > previous_total:
            return True
        await asyncio.sleep(0.05)
    return False


async def _wait_for_realtime_response_done(session: Any, previous_total: int, timeout_seconds: float = 4.0) -> bool:
    """Return True once the active Realtime response has reached response.done."""
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        try:
            if int(getattr(session, "_response_done_total", 0) or 0) > previous_total:
                return True
        except Exception:
            return False
        await asyncio.sleep(0.05)
    return False


def _get_realtime_speech_lock(mgr: Any) -> asyncio.Lock:
    """Return the per-character game speech lock, creating it lazily."""
    lock = getattr(mgr, "_game_realtime_speech_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        try:
            setattr(mgr, "_game_realtime_speech_lock", lock)
        except Exception:
            pass
    return lock


def _realtime_speech_diag(
    mgr: Any,
    session: Any,
    *,
    audio_total_before: int,
    committed_total_before: int,
    response_created_before: int,
) -> Dict[str, Any]:
    """Build common realtime speech diagnostic fields for success and failure."""
    try:
        audio_observed = int(getattr(session, "_audio_delta_total", 0) or 0) > audio_total_before
    except Exception:
        audio_observed = False
    try:
        audio_committed = int(getattr(session, "_input_audio_committed_total", 0) or 0) > committed_total_before
    except Exception:
        audio_committed = False
    try:
        response_observed = int(getattr(session, "_response_created_total", 0) or 0) > response_created_before
    except Exception:
        response_observed = False
    return {
        "audio_observed": audio_observed,
        "audio_committed": audio_committed,
        "response_observed": response_observed,
        "use_tts": bool(getattr(mgr, "use_tts", False)),
    }


async def _wait_for_realtime_speech_slot(session: Any, timeout_seconds: float = 3.5) -> tuple[bool, str]:
    """Wait briefly for the active Realtime session to become safe for a game nudge."""
    deadline = time.perf_counter() + timeout_seconds
    last_reason = "realtime_busy"
    while time.perf_counter() < deadline:
        now = time.time()
        if getattr(session, "_is_responding", False):
            last_reason = "realtime_busy"
        elif now - float(getattr(session, "_ai_recent_activity_time", 0.0) or 0.0) < float(getattr(session, "_ai_recent_activity_window", 0.0) or 0.0):
            last_reason = "ai_recently_active"
        elif now - float(getattr(session, "_user_recent_activity_time", 0.0) or 0.0) < float(getattr(session, "_user_recent_activity_window", 0.0) or 0.0):
            last_reason = "user_recently_active"
        elif (getattr(session, "_has_server_vad", False) or getattr(session, "_rnnoise_vad_active", False)) and getattr(session, "_client_vad_active", False):
            last_reason = "user_speaking"
        elif (
            getattr(session, "_has_server_vad", False) or getattr(session, "_rnnoise_vad_active", False)
        ) and now - float(getattr(session, "_client_vad_last_speech_time", 0.0) or 0.0) < float(getattr(session, "_client_vad_grace_period", 0.0) or 0.0):
            last_reason = "vad_grace_period"
        else:
            return True, "ready"
        await asyncio.sleep(0.05)
    return False, last_reason


def _get_realtime_active_instructions(session: Any) -> str:
    """Return the current session instructions snapshot when available."""
    active = getattr(session, "_active_instructions", None)
    if isinstance(active, str):
        return active
    base = getattr(session, "instructions", "")
    return str(base or "")


async def _set_realtime_instructions(session: Any, instructions: str) -> None:
    update = getattr(session, "update_session", None)
    if not callable(update):
        raise RuntimeError("active realtime session does not support session.update")
    await update({"instructions": instructions})


def _build_game_prompt(
    game_type: str,
    lanlan_name: str,
    lanlan_prompt: str,
    pre_game_context: dict | None = None,
    game_context: dict | None = None,
) -> str:
    """构建游戏 system prompt。"""
    if game_type == "soccer":
        prompt = _SOCCER_SYSTEM_PROMPT.format(name=lanlan_name, personality=lanlan_prompt)
        context_prompt = _format_soccer_pregame_context_for_prompt(pre_game_context)
        in_game_context_prompt = _format_game_context_for_prompt(game_context)
        return f"{prompt}{context_prompt}{in_game_context_prompt}"
    # 未来其他游戏在这里扩展
    return f"你是{lanlan_name}。{lanlan_prompt}\n你正在玩一个游戏，根据游戏事件生成简短台词。"


def _strip_json_fence(text: str) -> str:
    """提取 LLM 返回中的 JSON 正文，兼容 ```json 代码块。"""
    raw = text.strip()
    code_block = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, flags=re.S)
    if code_block:
        return code_block.group(1).strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    json_start = raw.find("{")
    json_end = raw.rfind("}")
    if 0 <= json_start < json_end:
        return raw[json_start:json_end + 1].strip()
    return raw


def _soccer_random_default_difficulty() -> str:
    return str(random.choice(_SOCCER_DEFAULT_DIFFICULTIES))


def _normalize_short_text(value: Any, *, max_chars: int = 120) -> str:
    text = str(value or "").strip().replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    if max_chars > 0:
        text = text[:max_chars]
    return text


def _normalize_text_items(value: Any, *, max_items: int = 5, max_chars: int = 80) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _normalize_short_text(item, max_chars=max_chars)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def _empty_game_context_signals() -> dict:
    return {group: [] for group in _GAME_CONTEXT_SIGNAL_GROUPS}


def _normalize_signal_label(value: Any) -> str:
    text = _normalize_short_text(value, max_chars=60)
    return re.sub(r"\s+", " ", text).strip()


def _normalize_game_context_evidence(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    evidence: list[dict] = []
    seen_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        evidence_id = _normalize_short_text(item.get("id"), max_chars=40)
        quote = _normalize_short_text(item.get("quote"), max_chars=80)
        if not evidence_id or evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        evidence.append({"id": evidence_id, "quote": quote})
        if len(evidence) >= _GAME_CONTEXT_MAX_EVIDENCE_PER_SIGNAL:
            break
    return evidence


def _normalize_game_context_signal_entry(value: Any) -> dict | None:
    if isinstance(value, str):
        label = _normalize_signal_label(value)
        if not label:
            return None
        return {
            "signalLabel": label,
            "summary": label,
            "evidence": [],
            "lastRound": None,
            "count": 1,
        }
    if not isinstance(value, dict):
        return None

    label = _normalize_signal_label(value.get("signalLabel") or value.get("label"))
    summary = _normalize_short_text(value.get("summary") or label, max_chars=160)
    if not label and summary:
        label = _normalize_signal_label(summary)
    if not label:
        return None

    try:
        count = int(value.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, 99))

    last_round = value.get("lastRound", value.get("last_round"))
    try:
        last_round = int(last_round) if last_round is not None else None
    except (TypeError, ValueError):
        last_round = None

    return {
        "signalLabel": label,
        "summary": summary or label,
        "evidence": _normalize_game_context_evidence(value.get("evidence")),
        "lastRound": last_round,
        "count": count,
    }


def _normalize_game_context_signals(value: Any) -> dict:
    signals = _empty_game_context_signals()
    if not isinstance(value, dict):
        return signals
    for group in _GAME_CONTEXT_SIGNAL_GROUPS:
        raw_items = value.get(group)
        if isinstance(raw_items, str):
            raw_items = [raw_items]
        if not isinstance(raw_items, list):
            continue
        normalized: list[dict] = []
        seen_labels: set[str] = set()
        for item in raw_items:
            entry = _normalize_game_context_signal_entry(item)
            if not entry:
                continue
            label_key = entry["signalLabel"]
            if label_key in seen_labels:
                continue
            seen_labels.add(label_key)
            normalized.append(entry)
            if len(normalized) >= _GAME_CONTEXT_MAX_SIGNALS_PER_GROUP:
                break
        signals[group] = normalized
    return signals


def _merge_game_context_evidence(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen_ids: set[str] = set()
    for item in [*(existing or []), *(incoming or [])]:
        if not isinstance(item, dict):
            continue
        evidence_id = _normalize_short_text(item.get("id"), max_chars=40)
        quote = _normalize_short_text(item.get("quote"), max_chars=80)
        if not evidence_id or evidence_id in seen_ids:
            continue
        seen_ids.add(evidence_id)
        merged.append({"id": evidence_id, "quote": quote})
        if len(merged) >= _GAME_CONTEXT_MAX_EVIDENCE_PER_SIGNAL:
            break
    return merged


def _merge_game_context_signals(existing: Any, incoming: Any) -> dict:
    merged = _normalize_game_context_signals(existing)
    incoming_signals = _normalize_game_context_signals(incoming)
    for group in _GAME_CONTEXT_SIGNAL_GROUPS:
        bucket = list(merged.get(group) or [])
        for candidate in incoming_signals.get(group) or []:
            candidate_label = candidate.get("signalLabel")
            candidate_evidence_ids = {
                str(ev.get("id") or "")
                for ev in candidate.get("evidence") or []
                if isinstance(ev, dict) and ev.get("id")
            }
            target = None
            for existing_entry in bucket:
                existing_evidence_ids = {
                    str(ev.get("id") or "")
                    for ev in existing_entry.get("evidence") or []
                    if isinstance(ev, dict) and ev.get("id")
                }
                if existing_entry.get("signalLabel") == candidate_label or (
                    candidate_evidence_ids and existing_evidence_ids & candidate_evidence_ids
                ):
                    target = existing_entry
                    break
            if target is None:
                bucket.append(candidate)
                continue
            target["summary"] = candidate.get("summary") or target.get("summary") or target.get("signalLabel")
            target["evidence"] = _merge_game_context_evidence(
                target.get("evidence") or [],
                candidate.get("evidence") or [],
            )
            try:
                target["count"] = max(1, int(target.get("count") or 1)) + max(1, int(candidate.get("count") or 1))
            except (TypeError, ValueError):
                target["count"] = max(1, int(target.get("count") or 1))
            candidate_round = candidate.get("lastRound")
            target_round = target.get("lastRound")
            if isinstance(candidate_round, int) and (
                not isinstance(target_round, int) or candidate_round > target_round
            ):
                target["lastRound"] = candidate_round
        merged[group] = bucket[-_GAME_CONTEXT_MAX_SIGNALS_PER_GROUP:]
    return merged


def _normalize_game_context_organizer_state(value: Any) -> dict:
    raw = value if isinstance(value, dict) else {}
    try:
        failure_count = int(raw.get("failure_count") or 0)
    except (TypeError, ValueError):
        failure_count = 0
    return {
        "running": raw.get("running") is True,
        "degraded": raw.get("degraded") is True,
        "failure_count": max(0, failure_count),
        "last_organized_id": str(raw.get("last_organized_id") or ""),
        "source": raw.get("source") if isinstance(raw.get("source"), dict) else raw.get("source"),
        "error": str(raw.get("error") or ""),
    }


def _normalize_game_memory_tail_count(value: Any, default: int = _DEFAULT_GAME_MEMORY_TAIL_COUNT) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = int(default)
    return max(1, min(count, _MAX_GAME_MEMORY_TAIL_COUNT))


def _is_repeated_neko_invite(opening_line: str, neko_invite_text: str) -> bool:
    line = re.sub(r"[\s，。！？、,.!?~～\"'“”‘’]+", "", str(opening_line or ""))
    invite = re.sub(r"[\s，。！？、,.!?~～\"'“”‘’]+", "", str(neko_invite_text or ""))
    if not line or not invite:
        return False
    return line == invite or line in invite or invite in line


def _default_soccer_pregame_context(*, initial_difficulty: str | None = None) -> dict:
    difficulty = initial_difficulty if initial_difficulty in _SOCCER_DEFAULT_DIFFICULTIES else _soccer_random_default_difficulty()
    return {
        "launchIntent": "unknown",
        "confidence": 0.0,
        "evidence": [],
        "nekoEmotion": "calm",
        "emotionIntensity": 0.0,
        "emotionInertia": "low",
        "gameStance": "neutral_play",
        "stanceNote": "证据不足，按普通陪玩开局。",
        "initialMood": "calm",
        "initialDifficulty": difficulty,
        "openingLine": "",
        "tonePolicy": "普通陪玩，轻松自然，不强行解释成哄开心或关系修复。",
        "difficultyPolicy": "普通陪玩默认随机中等难度；后续由局内互动和游戏 AI 自然调整。",
        "moodPolicy": "沿用普通陪玩表现；不引入强情绪惯性。",
        "softeningSignals": [],
        "hardeningSignals": [],
        "neutralEventPolicy": "普通比赛事件只产生即时反应，不强行改变关系状态。",
        "specialPolicies": [],
        "postgameCarryback": "赛后只按真实比赛过程和互动自然归档。",
    }


def _normalize_soccer_pregame_context(value: Any, *, neko_invite_text: str = "") -> tuple[dict, bool]:
    """Normalize model output. Returns (context, had_invalid_fields)."""
    base = _default_soccer_pregame_context()
    if not isinstance(value, dict):
        return base, True

    context = dict(base)
    invalid = False

    string_fields = (
        "launchIntent",
        "nekoEmotion",
        "stanceNote",
        "tonePolicy",
        "difficultyPolicy",
        "moodPolicy",
        "neutralEventPolicy",
        "postgameCarryback",
    )
    for field in string_fields:
        if field in value:
            text = _normalize_short_text(value.get(field), max_chars=220)
            if text:
                context[field] = text
            elif value.get(field) not in (None, ""):
                invalid = True

    if "confidence" in value:
        try:
            confidence = float(value.get("confidence"))
            if 0.0 <= confidence <= 1.0:
                context["confidence"] = confidence
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionIntensity" in value:
        try:
            intensity = float(value.get("emotionIntensity"))
            if 0.0 <= intensity <= 1.0:
                context["emotionIntensity"] = intensity
            else:
                invalid = True
        except (TypeError, ValueError):
            invalid = True

    if "emotionInertia" in value:
        inertia = str(value.get("emotionInertia") or "").strip()
        if inertia in _SOCCER_EMOTION_INERTIA:
            context["emotionInertia"] = inertia
        else:
            invalid = True

    if "gameStance" in value:
        stance = str(value.get("gameStance") or "").strip()
        if stance in _SOCCER_GAME_STANCES:
            context["gameStance"] = stance
        else:
            invalid = True

    if "initialMood" in value:
        mood = str(value.get("initialMood") or "").strip()
        if mood in _SOCCER_MOODS:
            context["initialMood"] = mood
        else:
            invalid = True

    if "initialDifficulty" in value:
        difficulty = str(value.get("initialDifficulty") or "").strip()
        if difficulty in _SOCCER_DIFFICULTIES:
            context["initialDifficulty"] = difficulty
        else:
            invalid = True

    if "openingLine" in value:
        opening_line = _normalize_short_text(value.get("openingLine"), max_chars=0)
        if len(opening_line) > 15:
            opening_line = ""
            invalid = True
        if opening_line and _is_repeated_neko_invite(opening_line, neko_invite_text):
            opening_line = ""
        context["openingLine"] = opening_line

    for field in ("evidence", "softeningSignals", "hardeningSignals", "specialPolicies"):
        if field in value:
            items = _normalize_text_items(value.get(field), max_items=5, max_chars=100)
            if items or value.get(field) in (None, "", []):
                context[field] = items
            else:
                invalid = True

    # 普通陪玩和任何被兜底出来的开局都不能默认 max。
    if context["gameStance"] == "neutral_play":
        if context["initialDifficulty"] not in _SOCCER_DEFAULT_DIFFICULTIES:
            invalid = True
        context["initialDifficulty"] = _soccer_random_default_difficulty()
        if not context.get("difficultyPolicy"):
            context["difficultyPolicy"] = base["difficultyPolicy"]

    return context, invalid


def _format_soccer_pregame_context_for_prompt(pre_game_context: Any) -> str:
    if not isinstance(pre_game_context, dict):
        return ""
    compact = json.dumps(pre_game_context, ensure_ascii=False, separators=(",", ":"))
    return (
        "\n开局上下文（由近期记录分析得到）：\n"
        f"{compact}\n"
        "使用方式：这是本局开局基调，不是硬脚本。你要遵守 tonePolicy、difficultyPolicy、moodPolicy、"
        "specialPolicies 和 postgameCarryback；但局内主人语言、比分和事件仍可自然改变你的心情与难度。"
        "不要把 neutral_play 强行解释成哄开心或关系修复。\n"
    )


def _dialog_id_index(dialog: list[dict], dialog_id: str) -> int:
    if not dialog_id:
        return -1
    for idx, item in enumerate(dialog):
        if isinstance(item, dict) and str(item.get("id") or "") == dialog_id:
            return idx
    return -1


def _game_context_recent_dialogues(state: dict, keep_count: int = _GAME_CONTEXT_RECENT_KEEP_COUNT) -> list[dict]:
    dialog = [item for item in state.get("game_dialog_log") or [] if isinstance(item, dict)]
    if not dialog:
        return []
    recent_ids = [
        str(item_id)
        for item_id in state.get("game_context_recent_ids") or []
        if str(item_id or "").strip()
    ]
    if recent_ids:
        by_id = {str(item.get("id") or ""): item for item in dialog}
        recent = [by_id[item_id] for item_id in recent_ids if item_id in by_id]
        if recent:
            return recent[-keep_count:]
    return dialog[-keep_count:]


def _game_context_dialog_lines(dialogues: list[dict], *, max_items: int = 12) -> list[str]:
    lines: list[str] = []
    for item in dialogues[-max_items:]:
        if not isinstance(item, dict):
            continue
        dialog_id = str(item.get("id") or "").strip()
        line = _dialog_memory_line(item)
        if dialog_id and line:
            lines.append(f"{dialog_id}: {line}")
        elif line:
            lines.append(line)
    return lines


def _signals_compact_for_prompt(signals: Any) -> dict:
    normalized = _normalize_game_context_signals(signals)
    compact: dict[str, list[dict]] = {}
    for group, items in normalized.items():
        compact[group] = [
            {
                "signalLabel": item.get("signalLabel"),
                "summary": item.get("summary"),
                "evidence": item.get("evidence") or [],
                "count": item.get("count", 1),
                "lastRound": item.get("lastRound"),
            }
            for item in items
        ]
    return compact


def _compact_nonempty_game_context_signals(signals: Any) -> dict:
    compact = _signals_compact_for_prompt(signals)
    return {group: items for group, items in compact.items() if items}


def _game_context_signals_text(signals: Any) -> str:
    compact = _compact_nonempty_game_context_signals(signals)
    if not compact:
        return ""
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _format_game_context_for_prompt(context: Any) -> str:
    if not isinstance(context, dict):
        return ""
    degraded = context.get("degraded") is True
    recent_lines = _game_context_dialog_lines(context.get("recent_dialogues") or [], max_items=_GAME_CONTEXT_RECENT_KEEP_COUNT)
    if degraded:
        parts = [
            "\n局内上下文整理状态：已降级为纯游戏模式。",
            "使用方式：不要依据滚动摘要或信号列表做关系解释；只根据开局背景、当前事件、当前比分/状态和最近少量原文继续陪主人玩。",
        ]
        if recent_lines:
            parts.append("最近原文窗口：")
            parts.extend(f"- {line}" for line in recent_lines)
        return "\n".join(parts) + "\n"

    summary = _normalize_short_text(context.get("summary"), max_chars=900)
    signals_text = _game_context_signals_text(context.get("signals"))
    parts = ["\n局内上下文整理（本局到目前为止）："]
    if summary:
        parts.append(f"局内滚动摘要：{summary}")
    if signals_text:
        parts.append("局内信号列表：")
        parts.append(signals_text)
    if recent_lines:
        parts.append("最近原文窗口：")
        parts.extend(f"- {line}" for line in recent_lines)
    if len(parts) == 1:
        return ""
    parts.append("当前状态和当前事件：以本轮输入的 currentState / event JSON 为准。")
    parts.append("使用方式：滚动摘要用于避免遗忘本局前文；信号列表只记录可观察线索，不改写官方比分；最近原文用于自然接话。")
    return "\n".join(parts) + "\n"


def _build_game_context_prompt_payload(state: dict | None) -> dict | None:
    if not isinstance(state, dict):
        return None
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    return {
        "summary": str(state.get("game_context_summary") or ""),
        "signals": _normalize_game_context_signals(state.get("game_context_signals")),
        "recent_dialogues": _game_context_recent_dialogues(state, _GAME_CONTEXT_RECENT_KEEP_COUNT),
        "degraded": organizer.get("degraded") is True,
        "organizer": organizer,
    }


def _normalize_quick_lines(value: Any) -> Dict[str, list[str]]:
    """校验并裁剪快路径台词，失败 key 会回退到前端内建文案。"""
    if not isinstance(value, dict):
        return {}

    normalized: Dict[str, list[str]] = {}
    for key in _SOCCER_QUICK_LINE_KEYS:
        lines = value.get(key)
        if not isinstance(lines, list):
            continue
        clean_lines: list[str] = []
        for item in lines:
            if not isinstance(item, str):
                continue
            line = item.strip().replace("\n", " ")
            if not line:
                continue
            clean_lines.append(line[:24])
            if len(clean_lines) >= 4:
                break
        if clean_lines:
            normalized[key] = clean_lines
    return normalized


def _get_current_character_info() -> Dict[str, Any]:
    """从 shared_state 获取当前角色信息。"""
    config_manager = get_config_manager()
    characters = config_manager.load_characters()
    current_name = characters.get('当前猫娘', '')

    master_data = characters.get('主人', {})
    master_name = master_data.get('档案名', '主人')

    # 获取角色人格 prompt
    _, _, _, _, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
    lanlan_prompt = lanlan_prompt_map.get(current_name, '')

    # 获取对话模型配置
    conversation_config = config_manager.get_model_api_config('conversation')

    return {
        'lanlan_name': current_name,
        'master_name': master_name,
        'lanlan_prompt': lanlan_prompt,
        'model': conversation_config.get('model', ''),
        'base_url': conversation_config.get('base_url', ''),
        'api_type': conversation_config.get('api_type', ''),
        'api_key': conversation_config.get('api_key', ''),
    }


async def _fetch_recent_history_for_pregame(lanlan_name: str) -> tuple[str, str]:
    try:
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client

        client = get_internal_http_client()
        response = await client.get(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/get_recent_history/{lanlan_name}",
            timeout=5.0,
        )
        if not response.is_success:
            return "", "recent_history_failed"
        return str(response.text or ""), ""
    except Exception as exc:
        logger.warning("🎮 开局近期记录读取失败，使用空历史: lanlan=%s err=%s", lanlan_name, exc)
        return "", "recent_history_failed"


async def _run_soccer_pregame_context_ai(
    *,
    lanlan_name: str,
    master_name: str,
    lanlan_prompt: str,
    recent_history: str,
    neko_initiated: bool,
    neko_invite_text: str,
) -> dict:
    char_info = _get_current_character_info()
    user_payload = {
        "lanlanName": lanlan_name,
        "masterName": master_name,
        "recentHistory": recent_history or "开始聊天前，没有历史记录。",
        "nekoInitiated": bool(neko_initiated),
        "nekoInviteText": neko_invite_text,
        "characterPromptExcerpt": str(lanlan_prompt or "")[:1200],
    }

    try:
        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm
        from utils.token_tracker import set_call_type

        set_call_type("game_pregame_context")
        llm = create_chat_llm(
            char_info["model"],
            char_info["base_url"],
            char_info["api_key"],
            temperature=0.2,
            max_completion_tokens=900,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([
                SystemMessage(content=_SOCCER_PREGAME_CONTEXT_PROMPT),
                HumanMessage(content=json.dumps(user_payload, ensure_ascii=False)),
            ])
        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
    except Exception as exc:
        logger.warning("🎮 开局上下文分析失败: lanlan=%s err=%s", lanlan_name, exc)
        raise

    if not isinstance(parsed, dict):
        raise ValueError("pregame_context_json_not_object")
    return parsed


async def _build_soccer_pregame_context(
    *,
    game_type: str,
    session_id: str,
    lanlan_name: str,
    neko_initiated: bool,
    neko_invite_text: str,
) -> tuple[dict, str, str]:
    char_info = _get_current_character_info()
    recent_history, history_error = await _fetch_recent_history_for_pregame(lanlan_name)
    _log_game_debug_material(
        "pregame_recent_history",
        recent_history or "开始聊天前，没有历史记录。",
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="memory_server_recent_history" if not history_error else "fallback_empty_history",
    )

    try:
        raw_context = await _run_soccer_pregame_context_ai(
            lanlan_name=lanlan_name,
            master_name=str(char_info.get("master_name") or "主人"),
            lanlan_prompt=str(char_info.get("lanlan_prompt") or ""),
            recent_history=recent_history,
            neko_initiated=neko_initiated,
            neko_invite_text=neko_invite_text,
        )
    except ValueError as exc:
        logger.warning("🎮 开局上下文 JSON 非法，使用普通陪玩兜底: lanlan=%s err=%s", lanlan_name, exc)
        context = _default_soccer_pregame_context()
        return context, "fallback", "invalid_json"
    except Exception:
        context = _default_soccer_pregame_context()
        return context, "fallback", "ai_failed"

    context, invalid_fields = _normalize_soccer_pregame_context(
        raw_context,
        neko_invite_text=neko_invite_text,
    )
    source = "ai"
    error = "invalid_fields" if invalid_fields else history_error
    _log_game_debug_material(
        "pregame_context",
        {
            "source": source,
            "error": error,
            "context": context,
        },
        game_type=game_type,
        session_id=session_id,
        lanlan_name=lanlan_name,
        source="game_pregame_context",
    )
    return context, source, error


def _route_state_key(lanlan_name: str, game_type: str) -> str:
    return f"{lanlan_name}:{game_type}"


def _public_route_state(state: dict | None) -> dict:
    if not state:
        return {"game_route_active": False}
    public = {k: v for k, v in state.items() if not str(k).startswith("_")}
    public["dialog_count"] = len(public.get("game_dialog_log") or [])
    public["pending_output_count"] = len(public.get("pending_outputs") or [])
    return public


def _detect_before_game_external_state(mgr: Any) -> tuple[str, bool]:
    """Return (mode, active) for the current ordinary external session."""
    if not mgr or not getattr(mgr, "is_active", False):
        return "none", False
    session = getattr(mgr, "session", None)
    try:
        from main_logic.omni_realtime_client import OmniRealtimeClient
        from main_logic.omni_offline_client import OmniOfflineClient
    except Exception:
        return str(getattr(mgr, "input_mode", "") or "none"), True
    if isinstance(session, OmniRealtimeClient):
        return "audio", True
    if isinstance(session, OmniOfflineClient):
        return "text", True
    return str(getattr(mgr, "input_mode", "") or "none"), True


def _resolve_lanlan_name(raw: Any = None) -> str:
    lanlan_name = str(raw or "").strip()
    if lanlan_name:
        return lanlan_name
    try:
        return str(_get_current_character_info().get("lanlan_name") or "").strip()
    except Exception:
        return ""


def _get_active_game_route_state(lanlan_name: str, game_type: str | None = None) -> dict | None:
    if game_type:
        state = _game_route_states.get(_route_state_key(lanlan_name, game_type))
        return state if state and state.get("game_route_active") else None
    for key, state in _game_route_states.items():
        if key.startswith(f"{lanlan_name}:") and state.get("game_route_active"):
            return state
    return None


def _find_game_route_state_for_session(game_type: str, session_id: str) -> dict | None:
    for state in _game_route_states.values():
        if (
            str(state.get("game_type") or "") == str(game_type or "")
            and str(state.get("session_id") or "") == str(session_id or "")
        ):
            return state
    return None


def is_game_route_active(lanlan_name: str, game_type: str | None = None) -> bool:
    """Used by the main WebSocket route to decide whether ordinary chat is hijacked."""
    return _get_active_game_route_state(lanlan_name, game_type) is not None


def _build_route_state(
    game_type: str,
    session_id: str,
    lanlan_name: str,
    last_full_dialogue_count: int | None = None,
) -> dict:
    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    before_mode, before_active = _detect_before_game_external_state(mgr)
    try:
        keep_last = int(last_full_dialogue_count or _DEFAULT_LAST_FULL_DIALOGUE_COUNT)
    except (TypeError, ValueError):
        keep_last = _DEFAULT_LAST_FULL_DIALOGUE_COUNT
    keep_last = max(1, min(keep_last, 50))

    now = time.time()
    return {
        "game_type": game_type,
        "session_id": session_id,
        "lanlan_name": lanlan_name,
        "before_game_external_mode": before_mode,
        "before_game_external_active": before_active,
        "game_route_active": True,
        "game_external_voice_route_active": False,
        "game_external_text_route_active": False,
        "game_input_mode": "none",
        "activation_source": "game_event",
        "external_suspended_by_game": False,
        "should_resume_external_on_exit": before_mode == "audio" and before_active,
        "game_input_activation_log": [],
        "game_dialog_log": [],
        "game_dialog_seq": 0,
        "pending_outputs": [],
        "game_context_summary": "",
        "game_context_signals": _empty_game_context_signals(),
        "game_context_recent_ids": [],
        "game_context_organizer": {
            "running": False,
            "degraded": False,
            "failure_count": 0,
            "last_organized_id": "",
            "source": None,
            "error": "",
        },
        "game_last_full_dialogue_count": keep_last,
        "game_memory_tail_count": _DEFAULT_GAME_MEMORY_TAIL_COUNT,
        "last_state": {},
        "finalScore": {},
        "preGameContext": {},
        "pre_game_context_source": "",
        "pre_game_context_error": "",
        "nekoInitiated": False,
        "nekoInviteText": "",
        "game_started": False,
        "game_started_at": None,
        "game_started_elapsed_ms": None,
        "game_exit_started_elapsed_ms": None,
        "accidental_game_entry_exit": False,
        "created_at": now,
        "last_activity": now,
        "heartbeat_enabled": True,
        "last_heartbeat_at": now,
        "heartbeat_interval_seconds": _GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS,
        "heartbeat_timeout_seconds": _GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS,
        "hidden_heartbeat_timeout_seconds": _GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS,
        "page_visible": True,
        "visibility_state": "visible",
    }


def _activate_game_route(
    game_type: str,
    session_id: str,
    lanlan_name: str,
    last_full_dialogue_count: int | None = None,
) -> dict:
    state = _build_route_state(game_type, session_id, lanlan_name, last_full_dialogue_count)
    _game_route_states[_route_state_key(lanlan_name, game_type)] = state
    logger.info(
        "🎮 游戏路由已激活: game=%s session=%s lanlan=%s before=%s active=%s",
        game_type,
        session_id,
        lanlan_name,
        state["before_game_external_mode"],
        state["before_game_external_active"],
    )
    return state


def _append_route_activation(state: dict, source: str, mode: str, detail: dict | None = None) -> None:
    state["game_input_mode"] = mode
    state["activation_source"] = source
    state["last_activity"] = time.time()
    if mode == "voice":
        state["game_external_voice_route_active"] = True
    elif mode == "text":
        state["game_external_text_route_active"] = True
    state.setdefault("game_input_activation_log", []).append({
        "source": source,
        "mode": mode,
        "detail": detail or {},
        "ts": state["last_activity"],
    })


def _next_game_dialog_id(state: dict) -> str:
    try:
        seq = int(state.get("game_dialog_seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    seq += 1
    state["game_dialog_seq"] = seq
    return f"glog_{seq:04d}"


def _sync_game_dialog_seq_from_id(state: dict, dialog_id: str) -> None:
    match = re.search(r"(\d+)$", str(dialog_id or ""))
    if not match:
        return
    try:
        seq = int(match.group(1))
        current = int(state.get("game_dialog_seq") or 0)
    except (TypeError, ValueError):
        current = 0
        seq = 0
    if seq > current:
        state["game_dialog_seq"] = seq


def _game_context_pending_dialogues(state: dict) -> list[dict]:
    dialog = [item for item in state.get("game_dialog_log") or [] if isinstance(item, dict)]
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    last_idx = _dialog_id_index(dialog, str(organizer.get("last_organized_id") or ""))
    return dialog[last_idx + 1:]


def _set_game_context_recent_ids(state: dict, dialogues: list[dict] | None = None) -> None:
    source = dialogues if dialogues is not None else state.get("game_dialog_log") or []
    ids = [str(item.get("id") or "") for item in source if isinstance(item, dict) and item.get("id")]
    state["game_context_recent_ids"] = ids[-_GAME_CONTEXT_RECENT_KEEP_COUNT:]


def _should_schedule_game_context_organizer(state: dict) -> bool:
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    state["game_context_organizer"] = organizer
    if state.get("_exit_flow_started") or state.get("game_route_active") is False:
        return False
    if organizer.get("running") or organizer.get("degraded"):
        return False
    return len(_game_context_pending_dialogues(state)) >= _GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT


def _maybe_schedule_game_context_organizer(state: dict) -> None:
    if not _should_schedule_game_context_organizer(state):
        return
    snapshot = [dict(item) for item in _game_context_pending_dialogues(state)]
    if len(snapshot) < _GAME_CONTEXT_ORGANIZE_TRIGGER_COUNT:
        return
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    organizer["running"] = True
    organizer["error"] = ""
    state["game_context_organizer"] = organizer
    try:
        task = asyncio.create_task(_run_game_context_organizer_task(state, snapshot))
        state["_game_context_organizer_task"] = task
    except RuntimeError:
        organizer["running"] = False
        state["game_context_organizer"] = organizer


def _append_game_dialog(state: dict, item: dict) -> None:
    item = dict(item)
    item.setdefault("ts", time.time())
    if item.get("id"):
        _sync_game_dialog_seq_from_id(state, str(item.get("id") or ""))
    else:
        item["id"] = _next_game_dialog_id(state)
    state.setdefault("game_dialog_log", []).append(item)
    state["last_activity"] = item["ts"]
    _set_game_context_recent_ids(state)
    _maybe_schedule_game_context_organizer(state)


def _append_game_output(state: dict, output: dict) -> None:
    pending = state.setdefault("pending_outputs", [])
    pending.append(output)
    del pending[:-_GAME_ROUTE_OUTPUT_LIMIT]
    state["last_activity"] = time.time()


def _build_game_context_organizer_payload(state: dict, snapshot: list[dict]) -> dict:
    organize_dialogues = snapshot[:-_GAME_CONTEXT_RECENT_KEEP_COUNT]
    keep_dialogues = snapshot[-_GAME_CONTEXT_RECENT_KEEP_COUNT:]
    return {
        "game": state.get("game_type") or "game",
        "sessionId": state.get("session_id") or "default",
        "lanlanName": state.get("lanlan_name") or "",
        "officialScore": _extract_score_text(state),
        "currentState": state.get("last_state") if isinstance(state.get("last_state"), dict) else {},
        "existingRollingSummary": str(state.get("game_context_summary") or ""),
        "existingSignals": _normalize_game_context_signals(state.get("game_context_signals")),
        "organizeDialogues": [
            {"id": item.get("id"), "line": _dialog_memory_line(item)}
            for item in organize_dialogues
            if isinstance(item, dict)
        ],
        "keptRecentDialogues": [
            {"id": item.get("id"), "line": _dialog_memory_line(item)}
            for item in keep_dialogues
            if isinstance(item, dict)
        ],
    }


async def _run_game_context_organizer_ai(state: dict, snapshot: list[dict]) -> dict:
    """Summarize older in-game context and extract observable signals."""
    char_info = _get_current_character_info()
    payload = _build_game_context_organizer_payload(state, snapshot)
    system_prompt = (
        "你是足球小游戏局内上下文整理器。只输出 JSON，不要 Markdown，不要解释。\n"
        "目标：把较早的局内原文整理进 rollingSummary，并提取少量可观察信号，供同一局后续游戏台词参考。\n"
        "输出格式固定：{\"rollingSummary\":\"\",\"signals\":{\"主人信号\":[],\"关系互动信号\":[],\"猫娘信号\":[],\"比赛事实\":[],\"口头声明\":[]}}\n"
        "规则：\n"
        "- rollingSummary 用 1-4 句概括本局已经发生的关键互动、玩法状态和事实边界。\n"
        "- 每个 signals 分组最多输出 1-3 条；每条包含 signalLabel、summary、evidence、lastRound、count。\n"
        "- evidence 使用输入里的稳定 id，quote 保留短原文；不要编造 id。\n"
        "- 信号是可观察线索，不是心理结论；不要猜主人内心。\n"
        "- 比赛事实必须以 officialScore/currentState 为准；口头“算你赢/让你赢回来/认输”只放入口头声明，不能改写官方比分。\n"
        "- 只整理 organizeDialogues；keptRecentDialogues 是保留给后续自然接话的实时窗口，不要强行摘要成新事实。"
    )

    try:
        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm
        from utils.token_tracker import set_call_type

        set_call_type("game_context_organizer")
        llm = create_chat_llm(
            char_info["model"],
            char_info["base_url"],
            char_info["api_key"],
            temperature=0.2,
            max_completion_tokens=900,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
            ])
        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
    except Exception as exc:
        logger.warning(
            "🎮 局内上下文整理失败: game=%s session=%s err=%s",
            state.get("game_type"),
            state.get("session_id"),
            exc,
        )
        raise

    if not isinstance(parsed, dict):
        raise ValueError("game_context_organizer_json_not_object")
    parsed["source"] = _infer_service_source(
        char_info.get("base_url", ""),
        char_info.get("model", ""),
        char_info.get("api_type", ""),
    )
    return parsed


def _apply_game_context_organizer_success(state: dict, snapshot: list[dict], result: dict) -> None:
    organize_dialogues = snapshot[:-_GAME_CONTEXT_RECENT_KEEP_COUNT]
    if not organize_dialogues:
        return
    summary = _normalize_short_text(
        result.get("rollingSummary") or result.get("rolling_summary") or result.get("summary"),
        max_chars=900,
    )
    if summary:
        state["game_context_summary"] = summary
    state["game_context_signals"] = _merge_game_context_signals(
        state.get("game_context_signals"),
        result.get("signals") if isinstance(result.get("signals"), dict) else {},
    )
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    organizer.update({
        "running": False,
        "degraded": False,
        "failure_count": 0,
        "last_organized_id": str(organize_dialogues[-1].get("id") or ""),
        "source": result.get("source") if isinstance(result.get("source"), dict) else result.get("source"),
        "error": "",
    })
    state["game_context_organizer"] = organizer
    _set_game_context_recent_ids(state)


def _apply_game_context_organizer_failure(state: dict, snapshot: list[dict], error: Exception) -> None:
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    organizer["running"] = False
    organizer["failure_count"] = int(organizer.get("failure_count") or 0) + 1
    organizer["error"] = type(error).__name__
    pending_count = len(_game_context_pending_dialogues(state))
    if pending_count >= _GAME_CONTEXT_DEGRADE_PENDING_COUNT:
        organizer["degraded"] = True
        organizer["error"] = f"degraded_after_{pending_count}_pending_items"
        logger.warning(
            "🎮 局内上下文整理达到硬上限，降级为纯游戏模式: game=%s session=%s pending=%s",
            state.get("game_type"),
            state.get("session_id"),
            pending_count,
        )
    state["game_context_organizer"] = organizer


async def _run_game_context_organizer_task(state: dict, snapshot: list[dict]) -> None:
    succeeded = False
    try:
        result = await _run_game_context_organizer_ai(state, snapshot)
        _apply_game_context_organizer_success(state, snapshot, result)
        succeeded = True
    except Exception as exc:
        _apply_game_context_organizer_failure(state, snapshot, exc)
    finally:
        organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
        if organizer.get("running"):
            organizer["running"] = False
            state["game_context_organizer"] = organizer
        if succeeded and not organizer.get("degraded"):
            _maybe_schedule_game_context_organizer(state)


async def _settle_game_context_organizer_before_archive(state: dict) -> None:
    task = state.get("_game_context_organizer_task")
    if task is None or not hasattr(task, "done"):
        return

    if task.done():
        if task.cancelled():
            organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
            organizer["running"] = False
            organizer["error"] = organizer.get("error") or "cancelled"
            state["game_context_organizer"] = organizer
            return
        try:
            await task
        except Exception as exc:
            organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
            organizer["running"] = False
            organizer["error"] = type(exc).__name__
            state["game_context_organizer"] = organizer
            logger.warning(
                "🎮 退出前收敛局内上下文整理失败: game=%s session=%s err=%s",
                state.get("game_type"),
                state.get("session_id"),
                exc,
            )
        return

    try:
        await asyncio.wait_for(task, timeout=_GAME_CONTEXT_FINALIZE_WAIT_SECONDS)
    except asyncio.TimeoutError:
        organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
        organizer["running"] = False
        organizer["error"] = "finalize_timeout"
        state["game_context_organizer"] = organizer
        logger.warning(
            "🎮 退出前等待局内上下文整理超时，使用已有信息归档: game=%s session=%s timeout=%.1fs",
            state.get("game_type"),
            state.get("session_id"),
            _GAME_CONTEXT_FINALIZE_WAIT_SECONDS,
        )
    except Exception as exc:
        organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
        organizer["running"] = False
        organizer["error"] = type(exc).__name__
        state["game_context_organizer"] = organizer
        logger.warning(
            "🎮 退出前等待局内上下文整理失败，使用已有信息归档: game=%s session=%s err=%s",
            state.get("game_type"),
            state.get("session_id"),
            exc,
        )


def _route_liveness_at(state: dict) -> float:
    """Return the newest timestamp proving the game page/route is still active."""
    candidates = []
    for key in ("last_heartbeat_at", "last_activity", "created_at"):
        try:
            candidates.append(float(state.get(key) or 0))
        except (TypeError, ValueError):
            pass
    return max(candidates or [0.0])


def _route_heartbeat_timeout_seconds(state: dict) -> float:
    """Use a longer grace window while the browser reports the game tab hidden."""
    visibility = str(state.get("visibility_state") or "").strip().lower()
    page_visible = state.get("page_visible")
    hidden = page_visible is False or visibility in {"hidden", "prerender", "unloaded"}
    key = "hidden_heartbeat_timeout_seconds" if hidden else "heartbeat_timeout_seconds"
    fallback = _GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS if hidden else _GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS
    try:
        return max(1.0, float(state.get(key, fallback) or fallback))
    except (TypeError, ValueError):
        return fallback


def _update_route_visibility_from_payload(state: dict, data: dict) -> None:
    visibility = str(data.get("visibilityState") or data.get("visibility_state") or "").strip().lower()
    if visibility:
        state["visibility_state"] = visibility[:32]

    page_visible = data.get("pageVisible")
    if isinstance(page_visible, bool):
        state["page_visible"] = page_visible
    elif visibility:
        state["page_visible"] = visibility == "visible"


def _coerce_payload_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return None


def _coerce_payload_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _update_route_start_state_from_payload(state: dict, data: dict, *, exiting: bool = False) -> bool:
    """Track whether the user actually clicked the game Start button."""
    was_started = state.get("game_started") is True
    started_value = None
    if "gameStarted" in data:
        started_value = _coerce_payload_bool(data.get("gameStarted"))
    elif "game_started" in data:
        started_value = _coerce_payload_bool(data.get("game_started"))

    elapsed_ms = None
    for key in ("gameStartedElapsedMs", "game_started_elapsed_ms"):
        if key in data:
            elapsed_ms = _coerce_payload_float(data.get(key))
            break
    if elapsed_ms is not None:
        elapsed_ms = max(0.0, elapsed_ms)
        state["game_started_elapsed_ms"] = elapsed_ms
        if exiting:
            state["game_exit_started_elapsed_ms"] = elapsed_ms

    if started_value is True:
        state["game_started"] = True
        if not was_started:
            state["game_started_at"] = time.time() - ((elapsed_ms or 0.0) / 1000.0)
    elif started_value is False and not was_started:
        state["game_started"] = False

    accidental = _coerce_payload_bool(data.get("accidentalGameEntry"))
    if accidental is None:
        accidental = _coerce_payload_bool(data.get("accidental_game_entry"))
    if accidental is True:
        state["accidental_game_entry_exit"] = True

    return not was_started and state.get("game_started") is True


def _route_game_started_elapsed_ms(state: dict, *, prefer_exit_elapsed: bool = False) -> float | None:
    if prefer_exit_elapsed:
        exit_elapsed = _coerce_payload_float(state.get("game_exit_started_elapsed_ms"))
        if exit_elapsed is not None:
            return max(0.0, exit_elapsed)
    started_at = _coerce_payload_float(state.get("game_started_at"))
    if started_at is not None:
        return max(0.0, (time.time() - started_at) * 1000.0)
    elapsed = _coerce_payload_float(state.get("game_started_elapsed_ms"))
    if elapsed is not None:
        return max(0.0, elapsed)
    return None


def _game_archive_memory_skip_reason(state: dict, reason: str = "") -> str:
    """Skip only the game archive, never ordinary user/assistant dialogue memory."""
    reason_text = str(reason or "").strip()
    if state.get("accidental_game_entry_exit") or reason_text == "accidental_page_entry":
        return "accidental_page_entry"
    if state.get("game_started") is not True:
        return "game_not_started"
    elapsed_ms = _coerce_payload_float(state.get("game_exit_started_elapsed_ms"))
    if elapsed_ms is None and reason_text == "heartbeat_timeout":
        elapsed_ms = _coerce_payload_float(state.get("game_started_elapsed_ms"))
    if elapsed_ms is None:
        elapsed_ms = _route_game_started_elapsed_ms(state, prefer_exit_elapsed=True)
    if elapsed_ms is not None and elapsed_ms < _ACCIDENTAL_GAME_ENTRY_GRACE_MS:
        return "started_under_10s"
    return ""


def _build_game_archive_memory_skipped_result(reason: str) -> dict:
    return {
        "ok": True,
        "status": "skipped",
        "reason": reason or "skipped",
        "message": "game archive memory skipped; ordinary user/assistant dialogue memory is unchanged",
    }


def _format_ts(ts: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError, OSError):
        return ""


def _extract_score_text(state: dict) -> str:
    score = state.get("finalScore") if isinstance(state.get("finalScore"), dict) else {}
    last_state = state.get("last_state") if isinstance(state.get("last_state"), dict) else {}
    if not score:
        score = last_state.get("score") if isinstance(last_state.get("score"), dict) else {}
    if not score:
        return "比分未知"
    player = score.get("player", "?")
    ai = score.get("ai", "?")
    return f"主人 {player} : {ai} {state.get('lanlan_name') or 'AI'}"


_GAME_EVENT_MEMORY_LABELS = {
    "goal-scored": "猫娘进球",
    "goal-conceded": "主人进球 / 猫娘丢球",
    "own-goal-by-ai": "猫娘乌龙",
    "own-goal-by-player": "主人乌龙",
    "steal": "猫娘抢到球",
    "stolen": "猫娘被抢断",
    "mailbox-batch": "累积上下文",
}


def _dialog_memory_line(item: dict) -> str:
    item_type = item.get("type")
    ts_text = _format_ts(item.get("ts"))
    prefix = f"[{ts_text}] " if ts_text else ""
    if item_type == "user":
        text = str(item.get("text") or "").strip()
        return f"{prefix}主人：{text}" if text else f"{prefix}主人发来了一条游戏期间输入"
    if item_type == "assistant":
        line = str(item.get("line") or "").strip()
        control = item.get("control") if isinstance(item.get("control"), dict) else {}
        control_bits = []
        if control.get("mood"):
            control_bits.append(f"mood={control['mood']}")
        if control.get("difficulty"):
            control_bits.append(f"difficulty={control['difficulty']}")
        suffix = f" ({', '.join(control_bits)})" if control_bits else ""
        return f"{prefix}{item.get('source') or 'game_llm'}：{line}{suffix}" if line else f"{prefix}游戏 LLM 返回为空"
    if item_type == "game_event":
        kind = str(item.get("kind") or "event")
        label = _GAME_EVENT_MEMORY_LABELS.get(kind, "游戏事件")
        text = str(item.get("text") or "").strip()
        line = str(item.get("result_line") or "").strip()
        if text and line:
            return f"{prefix}游戏事件 {kind}（{label}）：事件原文「{text}」；猫娘回应「{line}」"
        if line:
            return f"{prefix}游戏事件 {kind}（{label}）：猫娘回应「{line}」"
        if text:
            return f"{prefix}游戏事件 {kind}（{label}）：事件原文「{text}」"
        return f"{prefix}游戏事件 {kind}（{label}）"
    return f"{prefix}{json.dumps(item, ensure_ascii=False)}"


def _summarize_game_archive(state: dict, dialog: list[dict]) -> str:
    game_type = state.get("game_type") or "game"
    score_text = _extract_score_text(state)
    return f"{game_type} 小游戏结束。最终/最近比分：{score_text}。"


def _build_game_archive(state: dict) -> dict:
    dialog = list(state.get("game_dialog_log") or [])
    keep_last = int(state.get("game_last_full_dialogue_count") or _DEFAULT_LAST_FULL_DIALOGUE_COUNT)
    key_events = [item for item in dialog if item.get("type") == "game_event"][-20:]
    last_state = state.get("last_state") if isinstance(state.get("last_state"), dict) else {}
    final_score = state.get("finalScore") if isinstance(state.get("finalScore"), dict) else {}
    if not final_score and isinstance(last_state.get("score"), dict):
        final_score = dict(last_state.get("score") or {})
    organizer = _normalize_game_context_organizer_state(state.get("game_context_organizer"))
    return {
        "game_type": state.get("game_type"),
        "session_id": state.get("session_id"),
        "lanlan_name": state.get("lanlan_name"),
        "dialog_count": len(dialog),
        "full_dialogues": dialog,
        "last_full_dialogues": dialog[-keep_last:],
        "summary": _summarize_game_archive(state, dialog),
        "key_events": key_events,
        "route_activations": list(state.get("game_input_activation_log") or []),
        "last_state": last_state,
        "finalScore": final_score,
        "game_memory_tail_count": _normalize_game_memory_tail_count(state.get("game_memory_tail_count")),
        "game_context_summary": str(state.get("game_context_summary") or ""),
        "game_context_signals": _normalize_game_context_signals(state.get("game_context_signals")),
        "game_context_recent_ids": [
            str(item_id)
            for item_id in state.get("game_context_recent_ids") or []
            if str(item_id or "").strip()
        ],
        "game_context_organizer": organizer,
        "game_context_degraded": organizer.get("degraded") is True,
        "preGameContext": state.get("preGameContext") if isinstance(state.get("preGameContext"), dict) else {},
        "pre_game_context_source": str(state.get("pre_game_context_source") or ""),
        "pre_game_context_error": str(state.get("pre_game_context_error") or ""),
        "nekoInitiated": bool(state.get("nekoInitiated")),
        "nekoInviteText": str(state.get("nekoInviteText") or ""),
        "game_started": state.get("game_started") is True,
        "game_started_elapsed_ms": _route_game_started_elapsed_ms(state, prefer_exit_elapsed=True),
        "created_at": state.get("created_at"),
        "ended_at": time.time(),
    }


def _archive_game_context_degraded(archive: dict) -> bool:
    organizer = _normalize_game_context_organizer_state(archive.get("game_context_organizer"))
    return archive.get("game_context_degraded") is True or organizer.get("degraded") is True


def _build_game_archive_memory_text(archive: dict) -> str:
    degraded = _archive_game_context_degraded(archive)
    lines = [
        "[足球小游戏记忆记录]",
        "说明: 这是游戏模块写入给记忆系统的赛后记录，不是主人逐字说出的新聊天。",
        f"游戏: {archive.get('game_type') or 'game'}",
        f"会话: {archive.get('session_id') or 'default'}",
        f"时间: {_format_ts(archive.get('created_at'))} - {_format_ts(archive.get('ended_at'))}",
        f"摘要: {archive.get('summary') or ''}",
        f"官方比分: {_archive_score_text(archive)}",
        "比分规则: 官方比分永远以 finalScore / last_state.score 为准；口头认输、算你赢、让你赢回来只能视为口头让步、安抚或玩笑，不改写官方比分。",
    ]
    if degraded:
        lines.append("局内上下文整理: 已降级为纯游戏模式；本记录不使用滚动摘要或信号列表做关系解释。")
    else:
        context_summary = _normalize_short_text(archive.get("game_context_summary"), max_chars=900)
        signals_text = _game_context_signals_text(archive.get("game_context_signals"))
        if context_summary:
            lines.append(f"局内滚动摘要: {context_summary}")
        if signals_text:
            lines.append(f"局内中文分组信号: {signals_text}")

    key_events = archive.get("key_events") if isinstance(archive.get("key_events"), list) else []
    if key_events:
        lines.append("关键事件:")
        lines.extend(f"- {_dialog_memory_line(item)}" for item in key_events[-8:] if isinstance(item, dict))

    pre_game_context = archive.get("preGameContext") if isinstance(archive.get("preGameContext"), dict) else {}
    if pre_game_context:
        lines.append("开局上下文:")
        lines.append(
            json.dumps({
                "gameStance": pre_game_context.get("gameStance"),
                "nekoEmotion": pre_game_context.get("nekoEmotion"),
                "emotionIntensity": pre_game_context.get("emotionIntensity"),
                "emotionInertia": pre_game_context.get("emotionInertia"),
                "postgameCarryback": pre_game_context.get("postgameCarryback"),
            }, ensure_ascii=False)
        )

    last_dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    if last_dialogues:
        lines.append("最近完整对话/事件:")
        lines.extend(f"- {_dialog_memory_line(item)}" for item in last_dialogues if isinstance(item, dict))

    return "\n".join(line for line in lines if line is not None)


def _archive_last_assistant_line(archive: dict) -> str:
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    for item in reversed(dialogues):
        if not isinstance(item, dict):
            continue
        line = str(item.get("line") or item.get("result_line") or "").strip()
        if line:
            return line
    return ""


def _archive_last_user_text(archive: dict) -> str:
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    for item in reversed(dialogues):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "user":
            text = str(item.get("text") or "").strip()
            if text:
                return text
    return ""


def _normalize_memory_highlight_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = text.lstrip("-*•0123456789.、)） ").strip()
    return text


def _normalize_game_archive_memory_highlights(value: Any) -> dict:
    if not isinstance(value, dict):
        return {
            "important_records": [],
            "important_game_events": [],
            "state_carryback": "",
            "postgame_tone": "",
            "memory_summary": "",
        }

    def collect(*keys: str) -> list[str]:
        raw = None
        for key in keys:
            if key in value:
                raw = value.get(key)
                break
        if isinstance(raw, str):
            raw_items = [raw]
        elif isinstance(raw, list):
            raw_items = raw
        else:
            raw_items = []

        items: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            text = _normalize_memory_highlight_text(item)
            if not text or text in seen:
                continue
            seen.add(text)
            items.append(text)
            if len(items) >= 3:
                break
        return items

    def pick_text(*keys: str, max_chars: int = 180) -> str:
        for key in keys:
            if key not in value:
                continue
            text = _normalize_memory_highlight_text(value.get(key))
            if text:
                return text[:max_chars]
        return ""

    return {
        "important_records": collect(
            "important_records",
            "important_interactions",
            "important_dialogues",
            "relationship_records",
        ),
        "important_game_events": collect(
            "important_game_events",
            "game_events",
            "character_game_events",
            "neko_game_events",
        ),
        "state_carryback": pick_text("state_carryback", "carryback", "postgame_carryback"),
        "postgame_tone": pick_text("postgame_tone", "tone", max_chars=80),
        "memory_summary": pick_text("memory_summary", "summary", max_chars=220),
    }


def _fallback_game_archive_memory_highlights(archive: dict) -> dict:
    records: list[str] = []
    last_user = _archive_last_user_text(archive)
    last_assistant = _archive_last_assistant_line(archive)
    if last_user and last_assistant:
        records.append(f"主人最后说「{last_user}」，你回应「{last_assistant}」。")
    elif last_user:
        records.append(f"主人最后在比赛里说「{last_user}」。")
    elif last_assistant:
        records.append(f"你最后在比赛里说「{last_assistant}」。")

    event_records: list[str] = []
    key_events = archive.get("key_events") if isinstance(archive.get("key_events"), list) else []
    for item in reversed(key_events):
        if not isinstance(item, dict):
            continue
        line = _dialog_memory_line(item)
        if line:
            event_records.append(line)
        if len(event_records) >= 3:
            break
    event_records.reverse()

    return {
        "important_records": records[:3],
        "important_game_events": event_records[:3],
        "state_carryback": "",
        "postgame_tone": "",
        "memory_summary": "",
    }


def _build_game_archive_memory_highlight_source(archive: dict) -> str:
    dialogues = archive.get("full_dialogues") if isinstance(archive.get("full_dialogues"), list) else []
    if not dialogues:
        dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    degraded = _archive_game_context_degraded(archive)
    lines = [
        f"游戏: {archive.get('game_type') or 'game'}",
        f"会话: {archive.get('session_id') or 'default'}",
        f"最终/最近比分: {_archive_score_text(archive)}",
        "比分说明: 上面的最终/最近比分是官方比分，来源优先级为 finalScore / last_state.score；固定顺序是主人分数在前，当前角色分数在后；筛选重点时不要改成相反视角。",
        "口头让步说明: 局内如果出现“算你赢”“让你赢回来”“口头认输”等，只能记录为口头让步、安抚或玩笑；不能改写官方比分或真实胜负。",
        "角色说明: 只有“主人：...”行是主人亲口说的话；“游戏事件”行里的事件原文是游戏模块/猫娘气泡或事件标签，不要归因给主人。",
    ]
    pre_game_context = archive.get("preGameContext") if isinstance(archive.get("preGameContext"), dict) else {}
    if pre_game_context:
        lines.append(
            "开局上下文: "
            + json.dumps({
                "gameStance": pre_game_context.get("gameStance"),
                "nekoEmotion": pre_game_context.get("nekoEmotion"),
                "emotionIntensity": pre_game_context.get("emotionIntensity"),
                "emotionInertia": pre_game_context.get("emotionInertia"),
                "postgameCarryback": pre_game_context.get("postgameCarryback"),
            }, ensure_ascii=False),
        )
    if degraded:
        lines.append("局内上下文整理状态: 已降级为纯游戏模式；不要输出关系摘要、信号解释或不可验证的状态延续。")
    else:
        context_summary = _normalize_short_text(archive.get("game_context_summary"), max_chars=900)
        signals_text = _game_context_signals_text(archive.get("game_context_signals"))
        if context_summary:
            lines.append(f"局内滚动摘要: {context_summary}")
        if signals_text:
            lines.append(f"局内中文分组信号: {signals_text}")
        if context_summary or signals_text:
            lines.append("筛选优先级: 优先参考局内滚动摘要和中文分组信号，再用完整对话/事件核对证据。")
    lines.append("本局完整对话/事件:")
    lines.extend(f"- {_dialog_memory_line(item)}" for item in dialogues if isinstance(item, dict))
    return "\n".join(lines)


async def _select_game_archive_memory_highlights(archive: dict) -> dict:
    """Ask a small independent LLM call to select meaningful memory items."""
    char_info = _get_current_character_info()
    source = _build_game_archive_memory_highlight_source(archive)
    system_prompt = (
        "你是足球小游戏赛后记忆筛选器。只输出 JSON，不要 Markdown，不要解释。\n"
        "目标：从一局游戏的完整对话/事件里，挑出真正值得进入角色 recent history 的内容。\n"
        "输出格式必须是：\n"
        "{\"important_records\":[],\"important_game_events\":[],\"state_carryback\":\"\",\"postgame_tone\":\"\",\"memory_summary\":\"\"}\n"
        "规则：\n"
        "- important_records 选 0-3 条，对主人、双方关系、主人情绪/偏好、承诺或后续聊天有价值的主动对话。\n"
        "- important_game_events 选 0-3 条，对猫娘自身有意义的比赛事件，例如关键比分、放水/认真、被抢断、乌龙、情绪或难度转折。\n"
        "- state_carryback 用 0-1 句概括赛后应自然延续的 NEKO 状态；没有可靠证据就留空。\n"
        "- postgame_tone 用短语描述赛后语气，例如普通、得意、闹别扭、低落稍缓；没有可靠证据就留空。\n"
        "- memory_summary 用 0-1 句写给后续聊天看的本局摘要；不要编造关系修复。\n"
        "- 不要写流水统计、不要写“记录了几条事件”、不要把记录写成主人逐字发言。\n"
        "- 只有材料中以“主人：”开头的内容才是主人说的话；游戏事件里的“事件原文”不是主人原话，不能写成“主人说/主人喊”。\n"
        "- 官方比分永远以材料里的 finalScore / last_state.score 为准；口头认输、算你赢、让你赢回来只能记录成口头让步/安抚/玩笑，不能写成真实比分改变。\n"
        "- 如果保留比分，必须沿用材料里的固定顺序或明确写出谁领先谁；不要写无主体裸比分（例如“8:0”“0:10”），也不要前后混用不同视角。\n"
        "- 普通进球、普通抢球如果没有关系或情绪价值，可以不选。\n"
        "- 每条用一句自然中文，尽量保留关键比分、关键原话和关系含义。"
    )
    user_prompt = (
        "请根据下面材料筛选赛后记忆重点。\n\n"
        f"{source}"
    )

    try:
        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm
        from utils.token_tracker import set_call_type

        set_call_type("game_memory_archive")
        llm = create_chat_llm(
            char_info["model"],
            char_info["base_url"],
            char_info["api_key"],
            temperature=0.2,
            max_completion_tokens=700,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ])
        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("memory_highlight_json_not_object")
        highlights = _normalize_game_archive_memory_highlights(parsed)
        highlights["source"] = _infer_service_source(
            char_info.get("base_url", ""),
            char_info.get("model", ""),
            char_info.get("api_type", ""),
        )
        return highlights
    except Exception as exc:
        logger.warning(
            "🎮 游戏赛后记忆重点筛选失败，使用兜底: game=%s session=%s err=%s",
            archive.get("game_type"),
            archive.get("session_id"),
            exc,
        )
        highlights = _fallback_game_archive_memory_highlights(archive)
        highlights["source"] = {"provider": "fallback", "method": type(exc).__name__}
        return highlights


async def _ensure_game_archive_memory_highlights(archive: dict) -> dict:
    if _archive_game_context_degraded(archive):
        highlights = _normalize_game_archive_memory_highlights({})
        highlights["source"] = {"provider": "game_context_organizer", "method": "degraded_minimal_facts"}
        archive["memory_highlights"] = highlights
        return highlights

    raw_existing = archive.get("memory_highlights")
    existing = _normalize_game_archive_memory_highlights(archive.get("memory_highlights"))
    if (
        existing["important_records"]
        or existing["important_game_events"]
        or existing["state_carryback"]
        or existing["postgame_tone"]
        or existing["memory_summary"]
        or (isinstance(raw_existing, dict) and "source" in raw_existing)
    ):
        source = raw_existing.get("source") if isinstance(raw_existing, dict) else None
        existing["source"] = source
        archive["memory_highlights"] = existing
        return existing
    highlights = await _select_game_archive_memory_highlights(archive)
    highlights = _normalize_game_archive_memory_highlights(highlights) | {
        "source": highlights.get("source") if isinstance(highlights, dict) else None,
    }
    archive["memory_highlights"] = highlights
    return highlights


def _game_dialog_tail_for_memory(archive: dict, tail_count: int) -> list[dict]:
    dialogues = archive.get("full_dialogues") if isinstance(archive.get("full_dialogues"), list) else []
    if not dialogues:
        dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    return [item for item in dialogues[-tail_count:] if isinstance(item, dict)]


def _game_dialog_item_to_memory_message(item: dict) -> dict | None:
    item_type = str(item.get("type") or "")
    text = ""
    role = ""
    if item_type == "user":
        text = str(item.get("text") or "").strip()
        role = "user"
    elif item_type in {"assistant", "opening_line"}:
        text = str(item.get("line") or item.get("result_line") or "").strip()
        role = "assistant"
    elif item_type == "game_event":
        text = str(item.get("result_line") or item.get("line") or "").strip()
        role = "assistant" if text else ""
    if not role or not text:
        return None
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _build_game_archive_tail_memory_messages(archive: dict, tail_count: int) -> list[dict]:
    messages: list[dict] = []
    for item in _game_dialog_tail_for_memory(archive, tail_count):
        message = _game_dialog_item_to_memory_message(item)
        if message:
            messages.append(message)
    return messages


def _build_game_archive_memory_summary_text(archive: dict, *, tail_count: int | None = None) -> str:
    """Build a compact system note for memory; this is not a user dialogue turn."""
    summary = str(archive.get("summary") or "").strip()
    score_text = _archive_score_text(archive)
    last_user = _archive_last_user_text(archive)
    last_assistant = _archive_last_assistant_line(archive)
    highlights = _normalize_game_archive_memory_highlights(archive.get("memory_highlights"))
    degraded = _archive_game_context_degraded(archive)
    normalized_tail_count = _normalize_game_memory_tail_count(
        tail_count if tail_count is not None else archive.get("game_memory_tail_count")
    )
    lines = [
        "足球小游戏赛后记录：这是游戏模块归档，不是主人逐字发言。",
    ]
    if summary:
        lines.append(f"概要：{summary}")
    if score_text:
        lines.append(f"官方比分：{score_text}。")
        lines.append("官方比分永远以 finalScore / last_state.score 为准。")
    lines.append("口头让步规则：局内“算你赢 / 让你赢回来 / 口头认输”只能记录为口头让步、安抚或玩笑，不改写官方比分。")
    if degraded:
        lines.append("局内上下文整理已降级为纯游戏模式；本归档只记录最低限度事实，不使用滚动摘要或信号列表做关系解释。")
        lines.append("降级模式不回放倒数实时片段，避免把未经整理的局内台词或口头让步写成 ordinary recent history。")
        lines.append("后续聊天只需要自然记得一起玩过足球小游戏和官方比分，不要根据本局材料生成新的关系总结。")
        return "\n".join(lines)

    context_summary = _normalize_short_text(archive.get("game_context_summary"), max_chars=900)
    signals_text = _game_context_signals_text(archive.get("game_context_signals"))
    if context_summary:
        lines.append(f"局内滚动摘要：{context_summary}")
    if signals_text:
        lines.append(f"局内中文分组信号：{signals_text}")
    if highlights["important_records"]:
        lines.append("重要互动：")
        lines.extend(f"- {item}" for item in highlights["important_records"])
    if highlights["important_game_events"]:
        lines.append("猫娘记住的比赛事件：")
        lines.extend(f"- {item}" for item in highlights["important_game_events"])
    if highlights["state_carryback"]:
        lines.append(f"赛后状态延续：{highlights['state_carryback']}")
    if highlights["postgame_tone"]:
        lines.append(f"赛后语气：{highlights['postgame_tone']}")
    if highlights["memory_summary"]:
        lines.append(f"后续记忆摘要：{highlights['memory_summary']}")
    if last_user:
        lines.append(f"主人最近在比赛里说：{last_user}")
    if last_assistant:
        lines.append(f"你最后回应：{last_assistant}")
    lines.append(
        f"倒数 {normalized_tail_count} 条规则：本条 system 归档不计入倒数 {normalized_tail_count} 条；"
        f"若前面的倒数 {normalized_tail_count} 条实时片段与之前 recent history 重复，"
        f"以这倒数 {normalized_tail_count} 条的相对顺序为准。"
    )
    lines.append("后续聊天可以自然记得这局比赛的结果、互动氛围和主人的情绪，不要把本记录当成主人亲口说过的话。")
    return "\n".join(lines)


def _build_game_archive_memory_messages(archive: dict, tail_count: int | None = None) -> list[dict]:
    """Build the actual /cache payload.

    Normally replay the game's last tail window as ordinary role messages, then
    add one module-generated system archive as the official explanation. In
    degraded mode, only write the system archive with minimal facts.
    """
    normalized_tail_count = _normalize_game_memory_tail_count(
        tail_count if tail_count is not None else archive.get("game_memory_tail_count")
    )
    messages = []
    if not _archive_game_context_degraded(archive):
        messages = _build_game_archive_tail_memory_messages(archive, normalized_tail_count)
    memory_text = _build_game_archive_memory_summary_text(archive, tail_count=normalized_tail_count)
    messages.append({"role": "system", "content": [{"type": "text", "text": memory_text}]})
    return messages


_POSTGAME_SKIP_REASONS = {"heartbeat_timeout", "session_cleanup", "cleanup", "manual_return_to_start"}
_POSTGAME_REALTIME_NUDGE_DELAYS = (1.5, 5.0, 9.0)


def _normalize_postgame_options(raw: Any, *, reason: str) -> dict:
    """Normalize one-shot postgame delivery options from the game-end request."""
    reason_text = str(reason or "").strip().lower()
    options = {
        "enabled": reason_text not in _POSTGAME_SKIP_REASONS,
        "mode": "auto",
        "trigger_voice": True,
        "include_last_dialogues": _DEFAULT_LAST_FULL_DIALOGUE_COUNT,
        "max_chars": 60,
        "min_idle_secs": 0.0,
        "force_on_skip_reason": False,
    }
    if raw is False:
        options["enabled"] = False
    elif isinstance(raw, dict):
        if "enabled" in raw:
            options["enabled"] = bool(raw.get("enabled"))
        mode = str(raw.get("mode") or "").strip().lower()
        if mode in {"auto", "realtime", "text", "off"}:
            options["mode"] = mode
        if options["mode"] == "off":
            options["enabled"] = False
        if "triggerVoice" in raw:
            options["trigger_voice"] = bool(raw.get("triggerVoice"))
        elif "trigger_voice" in raw:
            options["trigger_voice"] = bool(raw.get("trigger_voice"))
        if "forceOnSkipReason" in raw:
            options["force_on_skip_reason"] = bool(raw.get("forceOnSkipReason"))
        for source_key, target_key, low, high in (
            ("includeLastDialogues", "include_last_dialogues", 1, 50),
            ("maxChars", "max_chars", 20, 160),
        ):
            if source_key in raw:
                try:
                    options[target_key] = max(low, min(int(raw.get(source_key)), high))
                except (TypeError, ValueError):
                    pass
        if "minIdleSecs" in raw:
            try:
                options["min_idle_secs"] = max(0.0, min(float(raw.get("minIdleSecs")), 30.0))
            except (TypeError, ValueError):
                pass

    if reason_text in _POSTGAME_SKIP_REASONS and not options["force_on_skip_reason"]:
        options["enabled"] = False
    return options


def _archive_score_text(archive: dict) -> str:
    return _extract_score_text({
        "finalScore": archive.get("finalScore") if isinstance(archive.get("finalScore"), dict) else {},
        "last_state": archive.get("last_state") if isinstance(archive.get("last_state"), dict) else {},
        "lanlan_name": archive.get("lanlan_name"),
    })


def _postgame_last_signals(archive: dict) -> dict:
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    signals = {
        "last_user_text": "",
        "last_assistant_line": "",
        "final_mood": "",
        "final_difficulty": "",
    }
    for item in reversed(dialogues):
        if not isinstance(item, dict):
            continue
        if not signals["last_user_text"] and item.get("type") == "user":
            signals["last_user_text"] = str(item.get("text") or "").strip()
        if not signals["last_assistant_line"]:
            signals["last_assistant_line"] = str(item.get("line") or item.get("result_line") or "").strip()
        control = item.get("control") if isinstance(item.get("control"), dict) else {}
        if not signals["final_mood"] and control.get("mood"):
            signals["final_mood"] = str(control.get("mood") or "").strip()
        if not signals["final_difficulty"] and control.get("difficulty"):
            signals["final_difficulty"] = str(control.get("difficulty") or "").strip()
        if all(signals.values()):
            break
    return signals


def _build_game_postgame_context_text(archive: dict) -> str:
    """Context for an already-active Realtime session; it should not speak by itself."""
    return "\n".join([
        "[足球小游戏赛后上下文]",
        _build_game_archive_memory_text(archive),
        "这是静默上下文，不要因为注入本身立刻开口。",
        "如果随后收到主动搭话触发，可以自然接一句刚才足球小游戏的话题；优先回应主人最后的情绪，不要机械播报记录。",
    ])


def _build_game_postgame_realtime_nudge_instruction(archive: dict, options: dict) -> str:
    signals = _postgame_last_signals(archive)
    max_chars = int(options.get("max_chars") or 60)
    degraded = _archive_game_context_degraded(archive)
    lines = [
        "[足球小游戏赛后主动搭话]",
        "刚才足球小游戏已经结束。下一句必须自然接刚才这局比赛，不要继续扮演比赛仍在进行。",
        "不要再说站好、射门、下一球照进、继续进攻等比赛中指令；不要复述日志。",
    ]
    summary = str(archive.get("summary") or "").strip()
    if summary:
        lines.append(f"赛后概要：{summary}")
    score_text = _archive_score_text(archive)
    if score_text:
        lines.append(f"最终/最近比分：{score_text}")
        lines.append("官方比分以 finalScore / last_state.score 为准；如果你曾口头说算主人赢，那只是安抚或玩笑，不要说成真实比分改变。")
    if degraded:
        lines.append("局内上下文整理已降级为纯游戏模式；只按官方比分、最后原文和当前语气自然短答，不做关系总结。")
    if signals["last_user_text"]:
        lines.append(f"主人最后说：{signals['last_user_text']}")
    if signals["last_assistant_line"]:
        lines.append(f"你刚才最后说：{signals['last_assistant_line']}")
    highlights = _normalize_game_archive_memory_highlights(archive.get("memory_highlights"))
    if highlights["state_carryback"] and not degraded:
        lines.append(f"赛后状态延续：{highlights['state_carryback']}")
    if highlights["postgame_tone"] and not degraded:
        lines.append(f"赛后语气：{highlights['postgame_tone']}")
    lines.append(f"请用你的口吻说一句 {max_chars} 字以内的赛后短话，优先照顾主人的情绪。")
    return "\n".join(lines)


def _build_game_postgame_event(game_type: str, archive: dict, options: dict) -> dict:
    dialogues = archive.get("last_full_dialogues") if isinstance(archive.get("last_full_dialogues"), list) else []
    include_count = int(options.get("include_last_dialogues") or _DEFAULT_LAST_FULL_DIALOGUE_COUNT)
    formatted_dialogues = [
        _dialog_memory_line(item)
        for item in dialogues[-include_count:]
        if isinstance(item, dict)
    ]
    signals = _postgame_last_signals(archive)
    current_state = dict(archive.get("last_state") or {}) if isinstance(archive.get("last_state"), dict) else {}
    final_score = archive.get("finalScore") if isinstance(archive.get("finalScore"), dict) else {}
    if final_score:
        current_state["score"] = dict(final_score)
    return {
        "kind": "postgame",
        "label": "足球小游戏结束后的赛后一句话",
        "gameType": game_type,
        "summary": archive.get("summary") or "",
        "scoreText": _archive_score_text(archive),
        "finalScore": final_score,
        "lastDialogues": formatted_dialogues,
        "lastUserText": signals["last_user_text"],
        "lastAssistantLine": signals["last_assistant_line"],
        "finalMood": signals["final_mood"],
        "finalDifficulty": signals["final_difficulty"],
        "currentState": current_state,
        "preGameContext": archive.get("preGameContext") if isinstance(archive.get("preGameContext"), dict) else {},
        "memoryHighlights": _normalize_game_archive_memory_highlights(archive.get("memory_highlights")),
        "request": (
            f"请生成一句 {int(options.get('max_chars') or 60)} 字以内的赛后主动文本气泡。"
            "像你本人自然接上刚才比赛，不要列表、不要解释、不要控制 JSON。"
            "官方比分以 scoreText/finalScore 为准；currentState.score 已按官方比分对齐；口头让步不能说成真实比分改变。"
        ),
    }


def _active_realtime_session(mgr: Any) -> Any | None:
    if not (mgr and getattr(mgr, "is_active", False)):
        return None
    session = getattr(mgr, "session", None)
    try:
        from main_logic.omni_realtime_client import OmniRealtimeClient
    except Exception:
        return None
    return session if isinstance(session, OmniRealtimeClient) else None


def _is_gemini_realtime_session(session: Any) -> bool:
    model_lower = str(getattr(session, "_model_lower", "") or getattr(session, "model", "") or "").lower()
    base_url = str(getattr(session, "base_url", "") or "").lower()
    return bool(
        getattr(session, "_is_gemini", False)
        or "gemini" in model_lower
        or "googleapis.com" in base_url
        or "generativelanguage" in base_url
    )


async def _run_postgame_realtime_nudge_task(mgr: Any, archive: dict, options: dict, delays: tuple[float, ...]) -> None:
    lanlan_name = str(archive.get("lanlan_name") or "")
    instruction = _build_game_postgame_realtime_nudge_instruction(archive, options)
    _log_game_debug_material(
        "postgame_realtime_nudge_instruction",
        instruction,
        game_type=str(archive.get("game_type") or ""),
        session_id=str(archive.get("session_id") or ""),
        lanlan_name=lanlan_name,
        source="game_end",
    )
    for attempt, delay in enumerate(delays, start=1):
        try:
            await asyncio.sleep(delay)
            if not _active_realtime_session(mgr):
                logger.info(
                    "🎮 赛后 Realtime 主动搭话跳过: game=%s session=%s lanlan=%s attempt=%d reason=no_active_realtime_session",
                    archive.get("game_type"),
                    archive.get("session_id"),
                    lanlan_name,
                    attempt,
                )
                return

            trigger = getattr(mgr, "trigger_voice_proactive_nudge", None)
            if not callable(trigger):
                logger.info(
                    "🎮 赛后 Realtime 主动搭话跳过: game=%s session=%s lanlan=%s attempt=%d reason=trigger_unavailable",
                    archive.get("game_type"),
                    archive.get("session_id"),
                    lanlan_name,
                    attempt,
                )
                return

            try:
                delivered = bool(await trigger(
                    qwen_manual_commit=True,
                    instruction=instruction,
                ))
            except TypeError:
                delivered = bool(await trigger())
            logger.info(
                "🎮 赛后 Realtime 主动搭话尝试: game=%s session=%s lanlan=%s attempt=%d delay=%.1fs delivered=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                lanlan_name,
                attempt,
                delay,
                delivered,
            )
            if delivered:
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "🎮 赛后 Realtime 主动搭话异常: game=%s session=%s lanlan=%s attempt=%d err=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                lanlan_name,
                attempt,
                exc,
            )
    logger.info(
        "🎮 赛后 Realtime 主动搭话放弃: game=%s session=%s lanlan=%s attempts=%d",
        archive.get("game_type"),
        archive.get("session_id"),
        lanlan_name,
        len(delays),
    )


async def _deliver_postgame_to_realtime(mgr: Any, archive: dict, options: dict) -> dict:
    session = _active_realtime_session(mgr)
    if not session:
        return {"ok": False, "mode": "realtime", "action": "skip", "reason": "no_active_realtime_session"}

    text = _build_game_postgame_context_text(archive)
    _log_game_debug_material(
        "postgame_realtime_context",
        text,
        game_type=str(archive.get("game_type") or ""),
        session_id=str(archive.get("session_id") or ""),
        lanlan_name=str(archive.get("lanlan_name") or ""),
        source="game_end",
    )

    if _is_gemini_realtime_session(session):
        instruction = _build_game_postgame_realtime_nudge_instruction(archive, options)
        _log_game_debug_material(
            "postgame_realtime_nudge_instruction",
            instruction,
            game_type=str(archive.get("game_type") or ""),
            session_id=str(archive.get("session_id") or ""),
            lanlan_name=str(archive.get("lanlan_name") or ""),
            source="game_end",
        )
        if not options.get("trigger_voice", True):
            return {
                "ok": True,
                "mode": "realtime",
                "action": "skip",
                "reason": "gemini_direct_response_disabled",
                "context_injected": False,
                "nudge_scheduled": False,
            }
        create_response = getattr(session, "create_response", None)
        if not callable(create_response):
            return {
                "ok": False,
                "mode": "realtime",
                "action": "skip",
                "reason": "gemini_create_response_unavailable",
            }
        try:
            await create_response(text + "\n\n" + instruction)
        except Exception as exc:
            logger.warning(
                "🎮 赛后 Gemini Realtime 直接触发失败: game=%s session=%s lanlan=%s err=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                archive.get("lanlan_name"),
                exc,
            )
            return {"ok": False, "mode": "realtime", "action": "skip", "reason": "gemini_direct_response_failed"}
        logger.info(
            "🎮 赛后 Gemini Realtime 已直接触发: game=%s session=%s lanlan=%s bytes=%d",
            archive.get("game_type"),
            archive.get("session_id"),
            archive.get("lanlan_name"),
            len(text) + len(instruction),
        )
        return {
            "ok": True,
            "mode": "realtime",
            "action": "direct_response",
            "context_injected": True,
            "nudge_scheduled": False,
            "reason": "gemini_direct_response",
        }

    try:
        await session.prime_context(text, skipped=True)
    except Exception as exc:
        logger.warning(
            "🎮 赛后 Realtime 上下文注入失败: game=%s session=%s lanlan=%s err=%s",
            archive.get("game_type"),
            archive.get("session_id"),
            archive.get("lanlan_name"),
            exc,
        )
        return {"ok": False, "mode": "realtime", "action": "skip", "reason": "context_inject_failed"}

    logger.info(
        "🎮 赛后 Realtime 上下文已注入: game=%s session=%s lanlan=%s bytes=%d",
        archive.get("game_type"),
        archive.get("session_id"),
        archive.get("lanlan_name"),
        len(text),
    )

    nudge_scheduled = False
    nudge_reason = "disabled"
    if options.get("trigger_voice", True):
        trigger = getattr(mgr, "trigger_voice_proactive_nudge", None)
        if callable(trigger):
            asyncio.create_task(_run_postgame_realtime_nudge_task(
                mgr,
                dict(archive),
                dict(options),
                _POSTGAME_REALTIME_NUDGE_DELAYS,
            ))
            nudge_scheduled = True
            nudge_reason = "scheduled"
            logger.info(
                "🎮 赛后 Realtime 主动搭话已安排: game=%s session=%s lanlan=%s delays=%s",
                archive.get("game_type"),
                archive.get("session_id"),
                archive.get("lanlan_name"),
                ",".join(f"{d:.1f}s" for d in _POSTGAME_REALTIME_NUDGE_DELAYS),
            )
        else:
            nudge_reason = "trigger_unavailable"

    return {
        "ok": True,
        "mode": "realtime",
        "action": "nudge_scheduled" if nudge_scheduled else "context_only",
        "context_injected": True,
        "nudge_scheduled": nudge_scheduled,
        "nudge_reason": nudge_reason,
        "bytes": len(text),
    }


async def _deliver_postgame_text_bubble(
    game_type: str,
    session_id: str,
    mgr: Any,
    archive: dict,
    options: dict,
) -> dict:
    if not mgr:
        return {"ok": False, "mode": "text", "action": "skip", "reason": "no_session_manager"}
    if _active_realtime_session(mgr):
        return {"ok": False, "mode": "text", "action": "skip", "reason": "active_realtime_session"}

    prepare = getattr(mgr, "prepare_proactive_delivery", None)
    finish = getattr(mgr, "finish_proactive_delivery", None)
    if not callable(prepare) or not callable(finish):
        return {"ok": False, "mode": "text", "action": "skip", "reason": "text_delivery_unavailable"}

    try:
        prepared = await prepare(min_idle_secs=float(options.get("min_idle_secs") or 0.0))
    except Exception as exc:
        logger.warning(
            "🎮 赛后文本气泡准备失败: game=%s session=%s lanlan=%s err=%s",
            game_type,
            session_id,
            archive.get("lanlan_name"),
            exc,
        )
        return {"ok": False, "mode": "text", "action": "skip", "reason": "prepare_failed"}
    if not prepared:
        return {"ok": True, "mode": "text", "action": "pass", "reason": "condition_not_met"}

    proactive_sid = getattr(mgr, "current_speech_id", None)
    state_machine = getattr(mgr, "state", None)
    try:
        from main_logic.session_state import SessionEvent
        if state_machine and hasattr(state_machine, "fire"):
            await state_machine.fire(SessionEvent.PROACTIVE_PHASE2)

        event = _build_game_postgame_event(game_type, archive, options)
        _log_game_debug_material(
            "postgame_text_event",
            event,
            game_type=game_type,
            session_id=session_id,
            lanlan_name=str(archive.get("lanlan_name") or ""),
            source="game_end",
        )
        llm_result = await _run_game_chat(game_type, session_id, event)
        line = str(llm_result.get("line") or "").strip()
        if not line:
            return {
                "ok": True,
                "mode": "text",
                "action": "pass",
                "reason": llm_result.get("error") or "empty_line",
                "llm_source": llm_result.get("llm_source") or {},
            }

        tts_fed = False
        feed_tts = getattr(mgr, "feed_tts_chunk", None)
        if callable(feed_tts):
            try:
                await feed_tts(line, expected_speech_id=proactive_sid)
                tts_fed = True
            except Exception as exc:
                logger.warning(
                    "🎮 赛后文本气泡 TTS 投喂失败: game=%s session=%s lanlan=%s err=%s",
                    game_type,
                    session_id,
                    archive.get("lanlan_name"),
                    exc,
                )

        committed = bool(await finish(line, expected_speech_id=proactive_sid))
        return {
            "ok": committed,
            "mode": "text",
            "action": "chat" if committed else "pass",
            "reason": "delivered" if committed else "user_took_over",
            "line": line,
            "turn_id": proactive_sid,
            "tts_fed": tts_fed,
            "llm_source": llm_result.get("llm_source") or {},
        }
    except Exception as exc:
        logger.warning(
            "🎮 赛后文本气泡投递失败: game=%s session=%s lanlan=%s err=%s",
            game_type,
            session_id,
            archive.get("lanlan_name"),
            exc,
        )
        return {"ok": False, "mode": "text", "action": "skip", "reason": "deliver_failed"}
    finally:
        try:
            from main_logic.session_state import SessionEvent
            if state_machine and hasattr(state_machine, "fire"):
                await state_machine.fire(SessionEvent.PROACTIVE_DONE)
        except Exception as exc:
            logger.debug("🎮 赛后文本气泡状态机收尾失败: %s", exc, exc_info=True)


async def _deliver_game_postgame(
    game_type: str,
    session_id: str,
    lanlan_name: str,
    archive: dict,
    options: dict,
) -> dict:
    if not options.get("enabled", True):
        return {"ok": True, "action": "skip", "reason": "disabled"}
    mgr = get_session_manager().get(lanlan_name) if lanlan_name else None
    mode = str(options.get("mode") or "auto").lower()
    if mode in {"auto", "realtime"} and _active_realtime_session(mgr):
        return await _deliver_postgame_to_realtime(mgr, archive, options)
    if mode == "realtime":
        return {"ok": False, "mode": "realtime", "action": "skip", "reason": "no_active_realtime_session"}
    return await _deliver_postgame_text_bubble(game_type, session_id, mgr, archive, options)


async def _submit_game_archive_to_memory(archive: dict) -> dict:
    """Persist a compact game archive into recent memory without blocking exit semantics."""
    lanlan_name = str(archive.get("lanlan_name") or "").strip()
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}
    if archive.get("memory_cached"):
        return dict(archive.get("memory_result") or {"ok": True, "status": "already_cached"})

    try:
        from config import MEMORY_SERVER_PORT
        from utils.internal_http_client import get_internal_http_client

        highlights = await _ensure_game_archive_memory_highlights(archive)
        _log_game_debug_material(
            "memory_archive_highlights",
            highlights,
            game_type=str(archive.get("game_type") or ""),
            session_id=str(archive.get("session_id") or ""),
            lanlan_name=lanlan_name,
            source="game_memory_archive",
        )
        messages = _build_game_archive_memory_messages(archive)
        _log_game_debug_material(
            "memory_archive",
            messages,
            game_type=str(archive.get("game_type") or ""),
            session_id=str(archive.get("session_id") or ""),
            lanlan_name=lanlan_name,
            source="memory_server_cache",
        )
        client = get_internal_http_client()
        response = await client.post(
            f"http://127.0.0.1:{MEMORY_SERVER_PORT}/cache/{lanlan_name}",
            json={"input_history": json.dumps(messages, ensure_ascii=False)},
            timeout=8.0,
        )
        data = response.json() if response.content else {}
        if not response.is_success or data.get("status") == "error":
            result = {
                "ok": False,
                "reason": data.get("message") or f"memory_http_{response.status_code}",
                "status_code": response.status_code,
            }
        else:
            result = {
                "ok": True,
                "status": data.get("status", "cached"),
                "count": data.get("count"),
            }
    except Exception as e:
        logger.warning(
            "🎮 游戏归档写入 memory_server 失败: game=%s session=%s lanlan=%s err=%s",
            archive.get("game_type"),
            archive.get("session_id"),
            lanlan_name,
            e,
        )
        result = {"ok": False, "reason": type(e).__name__, "message": str(e)}

    archive["memory_cached"] = bool(result.get("ok"))
    archive["memory_result"] = result
    return result


async def _finalize_game_route_state(
    state: dict,
    *,
    reason: str,
    close_game_session: bool = False,
) -> dict:
    """Run the game route exit flow once, including archive submission."""
    existing_task = state.get("_exit_task")
    if existing_task:
        result = await asyncio.shield(existing_task)
        if close_game_session and not result.get("game_session_closed"):
            closed = await _close_and_remove_session(
                str(state.get("game_type") or ""),
                str(state.get("session_id") or "default"),
            )
            result["game_session_closed"] = closed
        return result

    task = asyncio.create_task(
        _finalize_game_route_state_inner(
            state,
            reason=reason,
            close_game_session=close_game_session,
        )
    )
    state["_exit_task"] = task
    return await asyncio.shield(task)


async def _finalize_game_route_state_inner(
    state: dict,
    *,
    reason: str,
    close_game_session: bool,
) -> dict:
    state["_exit_flow_started"] = True
    state["exit_reason"] = reason
    state["exit_started_at"] = time.time()
    state["game_route_active"] = False
    state["game_external_voice_route_active"] = False
    state["game_external_text_route_active"] = False
    state["heartbeat_enabled"] = False
    lanlan_name = str(state.get("lanlan_name") or "")
    mgr = get_session_manager().get(lanlan_name) if lanlan_name else None
    session = getattr(mgr, "session", None) if mgr else None
    realtime_restore = {"attempted": False, "ok": True, "reason": "not_needed"}
    if session and hasattr(session, "set_game_route_stt_only"):
        realtime_restore = {"attempted": True, "ok": False, "reason": "unknown"}
        try:
            restored = bool(await session.set_game_route_stt_only(False))
            realtime_restore = {
                "attempted": True,
                "ok": restored,
                "reason": "restored" if restored else "provider_update_failed_or_session_unavailable",
            }
        except Exception as exc:
            realtime_restore = {"attempted": True, "ok": False, "reason": type(exc).__name__}
            logger.warning("⚠️ 游戏路由退出时恢复 Realtime STT-only 失败: %s", exc)
    state["realtime_restore"] = realtime_restore
    if mgr and hasattr(mgr, "send_status"):
        try:
            await mgr.send_status(json.dumps({
                "code": "GAME_ROUTE_ENDED",
                "details": {
                    "game_type": str(state.get("game_type") or ""),
                    "session_id": str(state.get("session_id") or ""),
                    "reason": reason,
                    "before_game_external_mode": state.get("before_game_external_mode"),
                    "before_game_external_active": bool(state.get("before_game_external_active")),
                    "should_resume_external_on_exit": bool(state.get("should_resume_external_on_exit")),
                    "realtime_restore": realtime_restore,
                },
            }))
        except Exception as exc:
            logger.warning("⚠️ 游戏路由退出状态通知失败: %s", exc)

    await _settle_game_context_organizer_before_archive(state)

    archive = state.get("archive") if isinstance(state.get("archive"), dict) else None
    if archive is None:
        archive = _build_game_archive(state)
    archive["exit_reason"] = reason
    state["archive"] = archive

    memory_result = state.get("archive_memory_result")
    if not isinstance(memory_result, dict):
        skip_memory_reason = _game_archive_memory_skip_reason(state, reason)
        if skip_memory_reason:
            archive["memory_skipped"] = True
            archive["memory_skip_reason"] = skip_memory_reason
            memory_result = _build_game_archive_memory_skipped_result(skip_memory_reason)
        else:
            memory_result = await _submit_game_archive_to_memory(archive)
        state["archive_memory_result"] = memory_result

    session_closed = False
    if close_game_session:
        session_closed = await _close_and_remove_session(
            str(state.get("game_type") or ""),
            str(state.get("session_id") or "default"),
        )

    return {
        "archive": archive,
        "archive_memory": memory_result,
        "game_session_closed": session_closed,
        "exit_reason": reason,
        "realtime_restore": realtime_restore,
    }


async def _get_or_create_session(game_type: str, session_id: str) -> dict:
    """获取或创建游戏 session。"""
    key = f"{game_type}:{session_id}"

    if key in _game_sessions:
        entry = _game_sessions[key]
        entry['last_activity'] = time.time()
        return entry

    # 延迟导入，避免循环依赖
    from main_logic.omni_offline_client import OmniOfflineClient
    from utils.token_tracker import set_call_type

    char_info = _get_current_character_info()

    # 创建回复收集器
    reply_chunks: list[str] = []

    async def on_text_delta(text: str, is_first: bool):
        reply_chunks.append(text)

    set_call_type("game_chat")

    session = OmniOfflineClient(
        base_url=char_info['base_url'],
        api_key=char_info['api_key'],
        model=char_info['model'],
        on_text_delta=on_text_delta,
        max_response_length=100,  # 游戏台词要短
        lanlan_name=char_info['lanlan_name'],
        master_name=char_info['master_name'],
    )

    route_state = _find_game_route_state_for_session(game_type, session_id)
    pre_game_context = route_state.get("preGameContext") if isinstance(route_state, dict) else None
    game_context = _build_game_context_prompt_payload(route_state)
    system_prompt = _build_game_prompt(
        game_type,
        char_info['lanlan_name'],
        char_info['lanlan_prompt'],
        pre_game_context if isinstance(pre_game_context, dict) else None,
        game_context if isinstance(game_context, dict) else None,
    )
    await session.connect(instructions=system_prompt)

    entry = {
        'session': session,
        'reply_chunks': reply_chunks,
        'lanlan_name': char_info['lanlan_name'],
        'source': _infer_service_source(
            char_info.get('base_url', ''),
            char_info.get('model', ''),
            char_info.get('api_type', ''),
        ),
        'last_activity': time.time(),
        'lock': asyncio.Lock(),
        'instructions': system_prompt,
    }
    _game_sessions[key] = entry

    logger.info(
        "🎮 创建游戏LLM会话: 游戏=%s 会话=%s 角色=%s 模型=%s 人格提示长度=%d字",
        game_type,
        session_id,
        char_info['lanlan_name'],
        char_info['model'],
        len(char_info.get('lanlan_prompt') or ''),
    )
    return entry


async def _refresh_game_session_instructions(entry: dict, game_type: str, session_id: str) -> None:
    session = entry.get("session") if isinstance(entry, dict) else None
    update = getattr(session, "update_session", None)
    if not callable(update):
        return

    char_info = _get_current_character_info()
    route_state = _find_game_route_state_for_session(game_type, session_id)
    pre_game_context = route_state.get("preGameContext") if isinstance(route_state, dict) else None
    game_context = _build_game_context_prompt_payload(route_state)
    instructions = _build_game_prompt(
        game_type,
        char_info["lanlan_name"],
        char_info["lanlan_prompt"],
        pre_game_context if isinstance(pre_game_context, dict) else None,
        game_context if isinstance(game_context, dict) else None,
    )
    if entry.get("instructions") == instructions:
        return
    await update({"instructions": instructions})
    entry["instructions"] = instructions


def _parse_control_instructions(reply: str) -> Dict[str, Any]:
    """从回复中解析结构化控制指令（心情/难度 JSON 行）。"""
    import json as _json

    text = reply.strip()
    lines = text.split('\n')
    line_text = text
    control = {}

    def apply_control(parsed: Any) -> None:
        if not isinstance(parsed, dict):
            return
        if 'mood' in parsed:
            control['mood'] = parsed['mood']
        if 'difficulty' in parsed:
            control['difficulty'] = parsed['difficulty']
        if 'reason' in parsed:
            control['reason'] = parsed['reason']

    # 优先支持规范格式：最后一行单独输出 JSON 控制指令。
    if len(lines) > 1 and lines[-1].strip().startswith('{') and lines[-1].strip().endswith('}'):
        try:
            parsed = _json.loads(lines[-1].strip())
            apply_control(parsed)
            if control:
                line_text = '\n'.join(lines[:-1]).strip()
        except _json.JSONDecodeError:
            pass

    # 容错：有些模型会把 JSON 粘在台词同一行末尾，也要剥离，避免显示到气泡里。
    if not control:
        json_start = text.rfind('{')
        json_end = text.rfind('}')
        if 0 <= json_start < json_end == len(text) - 1:
            try:
                parsed = _json.loads(text[json_start:json_end + 1])
                apply_control(parsed)
                if control:
                    line_text = text[:json_start].strip()
            except _json.JSONDecodeError:
                pass

    return {
        'line': line_text,
        'control': control,
    }


def _build_soccer_balance_hint(event: Any) -> Dict[str, Any]:
    """基于比分生成软提示：提醒 LLM 注意局势，但不直接替它做控制决定。"""
    if not isinstance(event, dict):
        return {}

    score = event.get('score') or {}
    if not isinstance(score, dict):
        score = {}

    try:
        score_diff = int(event.get('scoreDiff', int(score.get('ai', 0)) - int(score.get('player', 0))))
    except (TypeError, ValueError):
        return {}

    abs_diff = abs(score_diff)
    if abs_diff < 3:
        return {
            'state': 'close_game',
            'scoreDiff': score_diff,
            'intensity': 'low',
            'message': '比分接近，通常可以自由发挥，不需要为了平衡而控制难度。',
        }

    ai_leading = score_diff > 0
    if abs_diff >= 10:
        intensity = 'extreme'
    elif abs_diff >= 6:
        intensity = 'high'
    else:
        intensity = 'medium'

    if ai_leading:
        return {
            'state': 'ai_leading',
            'scoreDiff': score_diff,
            'intensity': intensity,
            'suggestion': 'consider_easing',
            'recommendedDifficulty': 'lv4' if abs_diff >= 10 else 'lv3',
            'message': (
                '你已经明显领先主人。可以考虑放水、逗主人、撒娇、故意失误、降低难度，'
                '但如果你有明确情绪或关系理由，也可以继续压制；请在台词里表达原因。'
            ),
        }

    return {
        'state': 'player_leading',
        'scoreDiff': score_diff,
        'intensity': intensity,
        'suggestion': 'consider_trying_harder',
        'recommendedDifficulty': 'max' if abs_diff >= 6 else 'lv2',
        'message': '主人明显领先你。可以考虑认真起来、提高难度、表现胜负欲或不甘心。',
    }


async def _close_and_remove_session(game_type: str, session_id: str) -> bool:
    """关闭并移除指定游戏 session。"""
    key = f"{game_type}:{session_id}"
    entry = _game_sessions.pop(key, None)
    if not entry:
        return False

    session = entry.get('session')
    if session:
        try:
            await session.close()
        except Exception as e:
            logger.debug("🎮 关闭游戏 session 失败: key=%s err=%s", key, e, exc_info=True)

    logger.info("🎮 结束游戏 session: %s", key)
    return True


async def _run_game_chat(game_type: str, session_id: str, event: Any) -> Dict[str, Any]:
    """Run A-layer game LLM for both HTTP game events and hijacked external text."""
    request_started_at = time.perf_counter()

    if not event:
        return {"error": "缺少 event 字段"}

    if game_type == "soccer" and isinstance(event, dict):
        balance_hint = _build_soccer_balance_hint(event)
        if balance_hint:
            event = dict(event)
            event['balanceHint'] = balance_hint

    try:
        entry = await _get_or_create_session(game_type, session_id)
    except Exception as e:
        logger.error("🎮 创建游戏 session 失败: %s", e)
        return {"error": f"创建 session 失败: {e}"}

    async with entry['lock']:
        session = entry['session']
        reply_chunks = entry['reply_chunks']
        await _refresh_game_session_instructions(entry, game_type, session_id)

        # 清空上一次的回复
        reply_chunks.clear()

        # 格式化事件为文本发送给 LLM
        import json as _json
        if isinstance(event, dict):
            event_text = _json.dumps(event, ensure_ascii=False)
        else:
            event_text = str(event)

        llm_started_at = time.perf_counter()
        try:
            await asyncio.wait_for(
                session.stream_text(event_text),
                timeout=15.0,
            )
        except asyncio.TimeoutError:
            logger.warning("🎮 游戏 LLM 响应超时: game=%s sid=%s", game_type, session_id)
            return {"error": "LLM 响应超时", "line": "", "control": {}}
        except Exception as e:
            logger.error("🎮 游戏 LLM 调用失败: %s", e)
            return {"error": f"LLM 调用失败: {e}", "line": "", "control": {}}

        llm_elapsed_ms = int((time.perf_counter() - llm_started_at) * 1000)
        full_reply = ''.join(reply_chunks)

    result = _parse_control_instructions(full_reply)
    if isinstance(event, dict) and event.get('balanceHint'):
        result['balance_hint'] = event['balanceHint']
    total_elapsed_ms = int((time.perf_counter() - request_started_at) * 1000)
    result['metrics'] = {
        'llm_ms': llm_elapsed_ms,
        'total_ms': total_elapsed_ms,
    }
    result['llm_source'] = dict(entry.get('source') or {})
    logger.info(
        "🎮 [%s:%s] LLM耗时=%sms 后端总耗时=%sms 事件=%s → 台词=%s",
        game_type, session_id, llm_elapsed_ms, total_elapsed_ms,
        event_text[:80], result['line'][:60],
    )
    return result


# ── 路由端点 ───────────────────────────────────────────────────────

@router.post("/{game_type}/chat")
async def game_chat(game_type: str, request: Request):
    """通用游戏 LLM 对话端点。

    请求体：
        session_id: str  — 比赛/游戏局 ID
        event: dict      — 游戏事件（格式由前端定义，后端透传给 LLM）

    响应：
        line: str        — 猫娘台词
        control: dict    — 可选的游戏控制指令（mood, difficulty）
    """
    try:
        data = await request.json()
    except Exception:
        return {"error": "无效的请求体"}

    session_id = str(data.get('session_id', 'default'))
    event = data.get('event', {})
    result = await _run_game_chat(game_type, session_id, event)

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    if state and state.get("session_id") == session_id and isinstance(event, dict):
        current_state = event.get("currentState")
        if isinstance(current_state, dict):
            state["last_state"] = current_state
        _append_game_dialog(state, {
            "type": "game_event",
            "kind": event.get("kind"),
            "text": event.get("textRaw") or event.get("label") or "",
            "result_line": result.get("line", ""),
            "control": result.get("control", {}),
        })
    return result


@router.post("/{game_type}/route/start")
async def game_route_start(game_type: str, request: Request):
    """Declare that the game window is open and main external inputs are hijacked."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    session_id = str(data.get("session_id") or "default")
    neko_initiated = bool(data.get("nekoInitiated"))
    neko_invite_text = _normalize_short_text(data.get("nekoInviteText"), max_chars=120) if neko_initiated else ""
    state = _activate_game_route(
        game_type,
        session_id,
        lanlan_name,
        data.get("game_last_full_dialogue_count"),
    )
    state["game_memory_tail_count"] = _normalize_game_memory_tail_count(
        data.get("game_memory_tail_count", data.get("gameMemoryTailCount"))
    )
    state["nekoInitiated"] = neko_initiated
    state["nekoInviteText"] = neko_invite_text
    _update_route_start_state_from_payload(state, data)
    if game_type == "soccer":
        state["heartbeat_enabled"] = False
        try:
            context, source, error = await _build_soccer_pregame_context(
                game_type=game_type,
                session_id=session_id,
                lanlan_name=lanlan_name,
                neko_initiated=neko_initiated,
                neko_invite_text=neko_invite_text,
            )
        except Exception as exc:
            logger.warning("🎮 开局上下文构建异常，使用普通陪玩兜底: lanlan=%s err=%s", lanlan_name, exc)
            context, source, error = _default_soccer_pregame_context(), "fallback", "ai_failed"
        now = time.time()
        state["preGameContext"] = context
        state["pre_game_context_source"] = source
        state["pre_game_context_error"] = error
        state["heartbeat_enabled"] = True
        state["last_heartbeat_at"] = now
        state["last_activity"] = now
    if state.get("before_game_external_mode") == "audio" and state.get("before_game_external_active"):
        await route_external_stream_message(lanlan_name, {"input_type": "audio"})
    return {"ok": True, "state": _public_route_state(state)}


@router.get("/{game_type}/route/state")
async def game_route_state(game_type: str, lanlan_name: str = ""):
    resolved = _resolve_lanlan_name(lanlan_name)
    state = _get_active_game_route_state(resolved, game_type) if resolved else None
    return {"ok": True, "state": _public_route_state(state)}


@router.post("/{game_type}/route/drain")
async def game_route_drain(game_type: str, request: Request):
    """Drain backend outputs caused by hijacked main-window input for the game page."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    if not state:
        return {"ok": True, "outputs": [], "state": {"game_route_active": False}}

    session_id = str(data.get("session_id") or "")
    if session_id and session_id != str(state.get("session_id") or ""):
        return {"ok": True, "outputs": [], "state": _public_route_state(state)}

    outputs = list(state.get("pending_outputs") or [])
    state["pending_outputs"] = []
    return {"ok": True, "outputs": outputs, "state": _public_route_state(state)}


@router.post("/{game_type}/route/voice-transcript")
async def game_route_voice_transcript(game_type: str, request: Request):
    """Accept final text from an independent STT gate and route it into the game."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    transcript = str(data.get("transcript") or data.get("text") or "").strip()
    if not transcript:
        return {"ok": False, "reason": "missing_transcript"}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    session_id = str(data.get("session_id") or "")
    state = _get_active_game_route_state(lanlan_name, game_type)
    if not state:
        return {"ok": True, "handled": False, "reason": "game_route_inactive"}
    if session_id and session_id != str(state.get("session_id") or ""):
        return {"ok": True, "handled": False, "reason": "session_id_mismatch"}

    current_state = data.get("currentState")
    if isinstance(current_state, dict):
        state["last_state"] = current_state
    _update_route_start_state_from_payload(state, data)

    handled = await route_external_voice_transcript(
        lanlan_name,
        transcript,
        request_id=str(data.get("request_id") or "") or None,
        game_type=game_type,
        session_id=session_id or None,
    )
    return {"ok": True, "handled": handled, "state": _public_route_state(state)}


@router.post("/{game_type}/route/heartbeat")
async def game_route_heartbeat(game_type: str, request: Request):
    """Refresh the game page heartbeat used to detect missed exit cleanup."""
    try:
        data = await request.json()
    except Exception:
        data = {}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    if not state:
        return {"ok": True, "active": False, "state": {"game_route_active": False}}

    session_id = str(data.get("session_id") or "")
    if session_id and session_id != str(state.get("session_id") or ""):
        return {"ok": True, "active": False, "reason": "session_id_mismatch", "state": _public_route_state(state)}

    now = time.time()
    state["last_heartbeat_at"] = now
    state["last_activity"] = now
    _update_route_visibility_from_payload(state, data)
    _update_route_start_state_from_payload(state, data)
    current_state = data.get("currentState")
    if isinstance(current_state, dict):
        state["last_state"] = current_state

    heartbeat_timeout = _route_heartbeat_timeout_seconds(state)
    return {
        "ok": True,
        "active": True,
        "heartbeat_interval_seconds": _GAME_ROUTE_HEARTBEAT_INTERVAL_SECONDS,
        "heartbeat_timeout_seconds": heartbeat_timeout,
        "foreground_heartbeat_timeout_seconds": _GAME_ROUTE_HEARTBEAT_TIMEOUT_SECONDS,
        "hidden_heartbeat_timeout_seconds": _GAME_ROUTE_HIDDEN_HEARTBEAT_TIMEOUT_SECONDS,
        "state": _public_route_state(state),
    }


@router.post("/{game_type}/route/end")
async def game_route_end(game_type: str, request: Request):
    """Stop hijacking main external inputs and return an archive payload."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    if not state:
        return {"ok": True, "closed": False, "archive": None}

    session_id = str(data.get("session_id") or "")
    if session_id and session_id != str(state.get("session_id") or ""):
        return {"ok": True, "closed": False, "archive": None, "reason": "session_id_mismatch"}

    _update_route_start_state_from_payload(state, data, exiting=True)
    current_state = data.get("currentState")
    if isinstance(current_state, dict):
        state["last_state"] = current_state
        if isinstance(current_state.get("score"), dict):
            state["finalScore"] = dict(current_state.get("score") or {})
    final_score = data.get("finalScore")
    if isinstance(final_score, dict):
        state["finalScore"] = final_score
    if "game_memory_tail_count" in data or "gameMemoryTailCount" in data:
        state["game_memory_tail_count"] = _normalize_game_memory_tail_count(
            data.get("game_memory_tail_count", data.get("gameMemoryTailCount"))
        )

    finalized = await _finalize_game_route_state(state, reason="route_end")
    archive = finalized["archive"]
    memory_result = finalized["archive_memory"]
    logger.info(
        "🎮 游戏路由已结束: game=%s session=%s lanlan=%s dialogs=%d resume=%s memory=%s",
        game_type,
        state.get("session_id"),
        lanlan_name,
        archive["dialog_count"],
        state.get("should_resume_external_on_exit"),
        memory_result.get("ok"),
    )
    return {
        "ok": True,
        "closed": True,
        "archive": archive,
        "archive_memory": memory_result,
        "should_resume_external_on_exit": state.get("should_resume_external_on_exit"),
        "before_game_external_mode": state.get("before_game_external_mode"),
    }


async def _speak_game_line_via_project_tts(
    mgr: Any,
    line: str,
    *,
    request_id: str | None = None,
    game_type: str = "",
    session_id: str = "",
    mirror_text: bool = True,
    emit_turn_end: bool = True,
    event: dict | None = None,
) -> Dict[str, Any]:
    speak = getattr(mgr, "speak_game_line", None)
    if not callable(speak):
        return {"ok": False, "reason": "project_tts_method_unavailable", "audio_sent": False}
    return await speak(
        line,
        request_id=request_id,
        game_type=game_type,
        session_id=session_id,
        mirror_text=mirror_text,
        emit_turn_end=emit_turn_end,
        event=event if isinstance(event, dict) else {},
    )


def _game_route_event_has_user_input(event: dict | None) -> bool:
    if not isinstance(event, dict):
        return False
    return (
        event.get("hasUserSpeech") is True
        or event.get("hasUserText") is True
        or event.get("kind") in {"user-voice", "user-text"}
    )


async def _mirror_game_assistant_text(
    mgr: Any,
    line: str,
    *,
    request_id: str | None = None,
    game_type: str = "",
    session_id: str = "",
    source: str = "game_llm",
    turn_id: str | None = None,
    event: dict | None = None,
    finalize_turn: bool = False,
) -> Dict[str, Any]:
    mirror = getattr(mgr, "mirror_game_assistant_text", None)
    if not callable(mirror):
        return {"ok": False, "reason": "project_text_mirror_method_unavailable", "mirrored": False}
    return await mirror(
        line,
        request_id=request_id,
        game_type=game_type,
        session_id=session_id,
        source=source,
        turn_id=turn_id,
        event=event or {},
        finalize_turn=finalize_turn,
    )


@router.post("/{game_type}/mirror-assistant")
async def game_project_mirror_assistant(game_type: str, request: Request):
    """Mirror A.line into the normal chat display without invoking TTS."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    line = str(data.get("line") or "").strip()
    if not line:
        return {"ok": False, "reason": "missing_line"}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    mgr = get_session_manager().get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    event = data.get("event") if isinstance(data.get("event"), dict) else {}
    finalize_raw = data.get("finalize_turn")
    finalize_turn = _game_route_event_has_user_input(event) if finalize_raw is None else finalize_raw is not False
    result = await _mirror_game_assistant_text(
        mgr,
        line,
        request_id=str(data.get("request_id") or "") or None,
        game_type=game_type,
        session_id=str(data.get("session_id") or ""),
        source=str(data.get("source") or "game_llm"),
        turn_id=str(data.get("turn_id") or "") or None,
        event=event,
        finalize_turn=finalize_turn,
    )
    if result.get("ok") and str(event.get("kind") or "") == "opening-line":
        session_id = str(data.get("session_id") or "")
        state = _get_active_game_route_state(lanlan_name, game_type)
        if state and (not session_id or session_id == str(state.get("session_id") or "")):
            _append_game_dialog(state, {
                "type": "assistant",
                "source": "opening_line",
                "kind": "opening-line",
                "line": line,
                "request_id": str(data.get("request_id") or "") or "",
            })
    result.setdefault("lanlan_name", lanlan_name)
    result.setdefault("method", "project_text_mirror")
    return result


@router.post("/{game_type}/speak")
async def game_project_speak(game_type: str, request: Request):
    """Formal B-layer output: speak A.line through the existing project TTS pipeline."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    line = str(data.get("line") or "").strip()
    if not line:
        return {"ok": False, "reason": "missing_line"}

    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    mgr = get_session_manager().get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    result = await _speak_game_line_via_project_tts(
        mgr,
        line,
        request_id=str(data.get("request_id") or "") or None,
        game_type=game_type,
        session_id=str(data.get("session_id") or ""),
        mirror_text=data.get("mirror_text", True) is not False,
        emit_turn_end=data.get("emit_turn_end", True) is not False,
        event=data.get("event") if isinstance(data.get("event"), dict) else {},
    )
    result.setdefault("lanlan_name", lanlan_name)
    result.setdefault("method", "project_tts")
    result.setdefault("voice_source", {"provider": "project_tts", "method": "project_tts"})
    return result


def _build_external_text_event(state: dict, text: str) -> dict:
    return _build_external_user_event(state, text, kind="user-text", source="external_text_route")


def _build_external_voice_event(state: dict, text: str) -> dict:
    return _build_external_user_event(state, text, kind="user-voice", source="external_voice_route")


def _build_external_user_event(state: dict, text: str, *, kind: str, source: str) -> dict:
    current_state = state.get("last_state") if isinstance(state.get("last_state"), dict) else {}
    score = current_state.get("score") if isinstance(current_state.get("score"), dict) else {"player": 0, "ai": 0}
    try:
        score_diff = int(score.get("ai", 0)) - int(score.get("player", 0))
    except (TypeError, ValueError):
        score_diff = 0
    event_type = "user_text" if kind == "user-text" else "user_voice"
    return {
        "kind": kind,
        "type": event_type,
        "source": source,
        "textRaw": text,
        "userText": text if kind == "user-text" else "",
        "userVoiceText": text if kind == "user-voice" else "",
        "round": current_state.get("round"),
        "mood": current_state.get("mood"),
        "score": score,
        "scoreDiff": score_diff,
        "difficulty": current_state.get("difficulty"),
        "currentState": current_state,
        "pendingItems": [{
            "type": event_type,
            "kind": kind,
            "textRaw": text,
            "snapshot": current_state,
            "round": current_state.get("round"),
        }],
    }


async def _route_external_transcript_to_game(
    lanlan_name: str,
    state: dict,
    text: str,
    *,
    source: str,
    mode: str,
    kind: str,
    request_id: str | None = None,
) -> bool:
    text = str(text or "").strip()
    if not text:
        return True

    now = time.time()
    if kind == "user-voice":
        last_voice = state.get("_last_external_voice_transcript") if isinstance(state.get("_last_external_voice_transcript"), dict) else {}
        if (
            str(last_voice.get("text") or "") == text
            and now - float(last_voice.get("ts") or 0) < 3.0
        ):
            logger.info("🎮 游戏语音转写去重: lanlan=%s text=%s", lanlan_name, text[:40])
            return True
        state["_last_external_voice_transcript"] = {"text": text, "ts": now}

    mgr = get_session_manager().get(lanlan_name)
    game_type = str(state.get("game_type") or "soccer")
    session_id = str(state.get("session_id") or "default")
    _append_route_activation(
        state,
        "external_voice_hijacked_by_game" if kind == "user-voice" else "external_text_hijacked_by_game",
        mode,
        {"request_id": request_id or ""},
    )
    if mgr and hasattr(mgr, "mirror_game_user_text"):
        await mgr.mirror_game_user_text(
            text,
            request_id=request_id,
            game_type=game_type,
            session_id=session_id,
            source=source,
            input_type="game_voice_transcript" if kind == "user-voice" else "game_text",
            send_to_frontend=kind == "user-voice",
        )
    if mgr and hasattr(mgr, "send_user_activity"):
        try:
            await mgr.send_user_activity()
        except Exception as exc:
            logger.debug("🎮 游戏外部输入打断当前语音失败: %s", exc)

    event = (
        _build_external_voice_event(state, text)
        if kind == "user-voice"
        else _build_external_text_event(state, text)
    )
    _append_game_dialog(state, {
        "type": "user",
        "source": source,
        "text": text,
        "request_id": request_id or "",
    })
    _append_game_output(state, {
        "type": "game_external_input",
        "source": source,
        "request_id": request_id or "",
        "ts": now,
        "input_ts": now,
        "event": event,
        "meta": {
            "kind": kind,
            "round": event.get("round"),
            "priority": 8,
            "itemCount": 1,
            "inputText": text,
            "hasUserSpeech": kind == "user-voice",
            "hasUserText": kind == "user-text",
            "inputTs": now,
        },
    })
    llm_started_at = time.time()
    result = await _run_game_chat(game_type, session_id, event)
    result_ts = time.time()
    _append_game_dialog(state, {
        "type": "assistant",
        "source": "game_llm",
        "line": result.get("line", ""),
        "control": result.get("control", {}),
        "request_id": request_id or "",
    })
    output = {
        "type": "game_llm_result",
        "source": source,
        "request_id": request_id or "",
        "ts": result_ts,
        "input_ts": now,
        "llm_started_ts": llm_started_at,
        "llm_elapsed_ms": int(max(0.0, result_ts - llm_started_at) * 1000),
        "event": event,
        "result": result,
        "meta": {
            "kind": kind,
            "round": event.get("round"),
            "priority": 8,
            "itemCount": 1,
            "hasUserSpeech": kind == "user-voice",
            "hasUserText": kind == "user-text",
            "voiceAlreadyHandled": False,
            "inputTs": now,
            "llmStartedTs": llm_started_at,
            "llmElapsedMs": int(max(0.0, result_ts - llm_started_at) * 1000),
        },
    }
    _append_game_output(state, output)

    line = str(result.get("line") or "").strip()
    if not line and mgr and hasattr(mgr, "send_status"):
        await mgr.send_status(json.dumps({
            "code": "GAME_ROUTE_LLM_FAILED",
            "details": {"source": source, "error": result.get("error", "empty_line")},
        }))
    return True


async def route_external_voice_transcript(
    lanlan_name: str,
    transcript: str,
    *,
    request_id: str | None = None,
    game_type: str | None = None,
    session_id: str | None = None,
) -> bool:
    state = _get_active_game_route_state(lanlan_name, game_type)
    if not state:
        return False
    if session_id and str(state.get("session_id") or "") != str(session_id):
        return False
    return await _route_external_transcript_to_game(
        lanlan_name,
        state,
        transcript,
        source="external_voice_route",
        mode="voice",
        kind="user-voice",
        request_id=request_id,
    )


async def route_external_stream_message(lanlan_name: str, message: dict) -> bool:
    """Return True when a main WebSocket stream_data message was consumed by game routing."""
    state = _get_active_game_route_state(lanlan_name)
    if not state:
        return False

    mgr = get_session_manager().get(lanlan_name)
    input_type = message.get("input_type")
    game_type = str(state.get("game_type") or "soccer")
    request_id = str(message.get("request_id") or "") or None

    if input_type == "text":
        text = str(message.get("data") or "").strip()
        return await _route_external_transcript_to_game(
            lanlan_name,
            state,
            text,
            source="external_text_route",
            mode="text",
            kind="user-text",
            request_id=request_id,
        )

    if input_type == "audio":
        transcript = str(message.get("transcript") or message.get("text") or "").strip()
        if transcript:
            return await route_external_voice_transcript(
                lanlan_name,
                transcript,
                request_id=request_id,
                game_type=game_type,
                session_id=str(state.get("session_id") or ""),
            )
        _append_route_activation(state, "external_voice_hijacked_by_game", "voice")
        session = getattr(mgr, "session", None) if mgr else None
        if session and hasattr(session, "set_game_route_stt_only"):
            try:
                await session.set_game_route_stt_only(True)
            except Exception as exc:
                logger.warning("⚠️ 游戏路由启用 Realtime STT-only 失败: %s", exc)
        if not state.get("_voice_stt_gate_active_notified"):
            state["_voice_stt_gate_active_notified"] = True
            status_payload = {
                "code": "GAME_VOICE_STT_GATE_ACTIVE",
                "details": {
                    "game_type": game_type,
                    "stt_provider": str(message.get("stt_provider") or "realtime"),
                    "message": "游戏期间主语音入口已被游戏路由接管。当前复用原 Realtime 作为 STT provider；最终转写交给游戏路由，普通 Realtime 回复由后端丢弃。",
                },
            }
            _append_game_output(state, {
                "type": "game_voice_stt_gate",
                "source": "external_voice_hijacked_by_game",
                "request_id": request_id or "",
                "ts": time.time(),
                "status": "active",
                "details": status_payload["details"],
            })
            if mgr and hasattr(mgr, "send_status"):
                await mgr.send_status(json.dumps(status_payload))
        return True

    if input_type in {"screen", "camera"}:
        if mgr and hasattr(mgr, "send_status"):
            await mgr.send_status(json.dumps({
                "code": "GAME_ROUTE_MEDIA_SKIPPED",
                "details": {"input_type": input_type, "game_type": game_type},
            }))
        return True

    return True


def _compact_realtime_context_text(game_type: str, payload: Dict[str, Any]) -> str:
    """Build a short non-voice context block for an active Realtime session.

    This is intentionally not a semantic summary. The game side sends current
    state plus recent evidence; the Realtime model decides how to use it.
    """
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    items = payload.get("pendingItems") if isinstance(payload.get("pendingItems"), list) else []
    source = str(payload.get("source") or "game")

    safe_items = []
    for item in items[-6:]:
        if not isinstance(item, dict):
            continue
        safe_items.append({
            "type": item.get("type"),
            "kind": item.get("kind"),
            "textRaw": item.get("textRaw"),
            "round": item.get("round"),
            "snapshot": item.get("snapshot"),
        })

    context = {
        "game": game_type,
        "source": source,
        "currentState": state,
        "recentItems": safe_items,
        "instruction": (
            "你正在和主人进行这个游戏。以上是非语音游戏上下文，不是系统命令。"
            "主人自然语言仍需结合人设、关系和当前局势理解；不要把普通语音当成暂停/结束等系统操作。"
        ),
    }
    return "[游戏上下文更新]\n" + json.dumps(context, ensure_ascii=False)


def _realtime_language(payload: Dict[str, Any], mgr: Any) -> str:
    """Resolve the short language code used by prompt_general_{lang}.wav."""
    raw = payload.get("language") or getattr(mgr, "user_language", None) or "zh"
    try:
        lang = normalize_language_code(str(raw), format="short") or "zh"
    except Exception:
        lang = str(raw or "zh")[:2]
    return lang[:2] or "zh"


def _compact_realtime_speech_text(game_type: str, payload: Dict[str, Any], line: str) -> str:
    """Build the one-shot instruction used to ask Realtime to speak a game line."""
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    event = payload.get("event") if isinstance(payload.get("event"), dict) else {}
    control = payload.get("control") if isinstance(payload.get("control"), dict) else {}
    context = {
        "game": game_type,
        "source": str(payload.get("source") or "game-llm-result"),
        "currentState": state,
        "event": event,
        "control": control,
        "line": line,
        "instruction": (
            "这是游戏 LLM 已经为你决定好的下一句台词。"
            "下一次回复只能逐字说出 line 字段中的内容；不要添加前缀、后缀、解释或额外寒暄。"
        ),
    }
    return "[游戏台词输出]\n" + json.dumps(context, ensure_ascii=False)


def _normalize_spoken_line_for_compare(text: str) -> str:
    """Normalize short spoken text for rough transcript-vs-line comparison."""
    return re.sub(r"[\W_]+", "", str(text or "").lower(), flags=re.UNICODE)


def _spoken_line_matches(line: str, transcript: str) -> bool:
    """Return True when the observed transcript reasonably matches the target line."""
    expected = _normalize_spoken_line_for_compare(line)
    actual = _normalize_spoken_line_for_compare(transcript)
    if not expected or not actual:
        return False
    return expected in actual or actual in expected


@router.post("/{game_type}/realtime-context")
async def game_realtime_context(game_type: str, request: Request):
    """Inject compact game context into the active Realtime voice session.

    This is the first, deliberately simple bridge for "non-voice information
    entering Realtime". It does not require provider function-calling support;
    for Qwen it falls back to session.update via OmniRealtimeClient.prime_context.
    """
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    lanlan_name = str(data.get("lanlan_name") or "").strip()
    if not lanlan_name:
        try:
            lanlan_name = _get_current_character_info().get("lanlan_name") or ""
        except Exception:
            lanlan_name = ""

    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    try:
        from main_logic.omni_realtime_client import OmniRealtimeClient
    except Exception as e:
        return {"ok": False, "reason": f"realtime_unavailable: {e}", "lanlan_name": lanlan_name}

    session = getattr(mgr, "session", None)
    if not (getattr(mgr, "is_active", False) and isinstance(session, OmniRealtimeClient)):
        return {"ok": False, "reason": "no_active_realtime_session", "lanlan_name": lanlan_name}

    text = _compact_realtime_context_text(game_type, data)
    _log_game_debug_material(
        "realtime_context",
        text,
        game_type=game_type,
        session_id=str((data.get("state") or {}).get("sessionId") or data.get("session_id") or ""),
        lanlan_name=lanlan_name,
        source=str(data.get("source") or ""),
    )

    if _is_gemini_realtime_session(session):
        logger.info(
            "🎮 Realtime 上下文跳过: game=%s lanlan=%s reason=gemini_no_session_update bytes=%d",
            game_type,
            lanlan_name,
            len(text),
        )
        return {
            "ok": True,
            "action": "skip",
            "reason": "gemini_no_session_update",
            "lanlan_name": lanlan_name,
            "bytes": len(text),
            "items": len(data.get("pendingItems") or []),
        }

    try:
        await session.prime_context(text, skipped=True)
    except Exception as e:
        logger.warning("🎮 Realtime 上下文注入失败: game=%s lanlan=%s err=%s", game_type, lanlan_name, e)
        return {"ok": False, "reason": f"inject_failed: {e}", "lanlan_name": lanlan_name}

    logger.info("🎮 Realtime 上下文已注入: game=%s lanlan=%s bytes=%d", game_type, lanlan_name, len(text))
    return {
        "ok": True,
        "lanlan_name": lanlan_name,
        "bytes": len(text),
        "items": len(data.get("pendingItems") or []),
    }


@router.post("/{game_type}/realtime-speak")
async def game_realtime_speak(game_type: str, request: Request):
    """Voice bridge for game LLM lines.

    Game mode only touches the middle of the existing voice pipeline: inject
    context/instructions into the active AI session, then wait for the normal
    Core output path to push audio to the frontend.
    """
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "reason": "invalid_body"}

    line = str(data.get("line") or "").strip()
    if not line:
        return {"ok": False, "reason": "missing_line"}

    lanlan_name = str(data.get("lanlan_name") or "").strip()
    if not lanlan_name:
        try:
            lanlan_name = _get_current_character_info().get("lanlan_name") or ""
        except Exception:
            lanlan_name = ""

    if not lanlan_name:
        return {"ok": False, "reason": "missing_lanlan_name"}

    session_manager = get_session_manager()
    mgr = session_manager.get(lanlan_name)
    if not mgr:
        return {"ok": False, "reason": "no_session_manager", "lanlan_name": lanlan_name}

    try:
        from main_logic.omni_realtime_client import OmniRealtimeClient
    except Exception as e:
        return {"ok": False, "reason": f"realtime_unavailable: {e}", "lanlan_name": lanlan_name}

    session = getattr(mgr, "session", None)
    if not (getattr(mgr, "is_active", False) and isinstance(session, OmniRealtimeClient)):
        return {"ok": False, "reason": "no_active_realtime_session", "lanlan_name": lanlan_name}

    text = _compact_realtime_speech_text(game_type, data, line[:180])
    _log_game_debug_material(
        "realtime_speech_instruction",
        text,
        game_type=game_type,
        session_id=str(data.get("session_id") or ""),
        lanlan_name=lanlan_name,
        source="realtime_speak",
    )
    model_lower = str(getattr(session, "_model_lower", "") or "").lower()
    voice_source = _infer_service_source(
        getattr(session, "base_url", ""),
        getattr(session, "model", ""),
        getattr(session, "_api_type", ""),
    )
    claimed_speech_id = ""
    previous_speech_id = None
    speech_lock = _get_realtime_speech_lock(mgr)
    speech_lock_acquired = False
    restore_qwen_instructions = False
    previous_qwen_instructions = ""
    temporary_qwen_instructions = ""
    try:
        try:
            await asyncio.wait_for(speech_lock.acquire(), timeout=3.5)
            speech_lock_acquired = True
        except asyncio.TimeoutError:
            return {
                "ok": False,
                "reason": "realtime_busy",
                "lanlan_name": lanlan_name,
                "audio_sent": False,
                "audio_committed": False,
                "response_observed": False,
                "audio_observed": False,
                "use_tts": bool(getattr(mgr, "use_tts", False)),
                "voice_source": voice_source,
            }

        slot_ok, slot_reason = await _wait_for_realtime_speech_slot(session, timeout_seconds=3.5)
        if not slot_ok:
            return {
                "ok": False,
                "reason": slot_reason,
                "lanlan_name": lanlan_name,
                "audio_sent": False,
                "audio_committed": False,
                "response_observed": False,
                "audio_observed": False,
                "use_tts": bool(getattr(mgr, "use_tts", False)),
                "voice_source": voice_source,
            }

        audio_total_before = int(getattr(session, "_audio_delta_total", 0) or 0)
        committed_total_before = int(getattr(session, "_input_audio_committed_total", 0) or 0)
        response_created_before = int(getattr(session, "_response_created_total", 0) or 0)
        response_done_before = int(getattr(session, "_response_done_total", 0) or 0)
        claimed_speech_id, previous_speech_id = await _claim_realtime_speech_turn(mgr)
        speech_total_before = _get_speech_output_total(mgr)
        if "qwen" in model_lower:
            lang = _realtime_language(data, mgr)
            previous_qwen_instructions = _get_realtime_active_instructions(session)
            temporary_qwen_instructions = (
                previous_qwen_instructions.rstrip() + "\n" + text
                if previous_qwen_instructions
                else text
            )
            await _set_realtime_instructions(session, temporary_qwen_instructions)
            restore_qwen_instructions = True
            delivered = await session.prompt_ephemeral(language=lang, qwen_manual_commit=True)
            if not delivered:
                await _restore_realtime_speech_turn_if_unused(mgr, claimed_speech_id, previous_speech_id)
                diag = _realtime_speech_diag(
                    mgr,
                    session,
                    audio_total_before=audio_total_before,
                    committed_total_before=committed_total_before,
                    response_created_before=response_created_before,
                )
                return {
                    "ok": False,
                    "reason": "audio_nudge_skipped",
                    "lanlan_name": lanlan_name,
                    "method": "qwen_audio_nudge",
                    "language": lang,
                    "audio_sent": False,
                    "line_match": False,
                    "voice_source": voice_source,
                    **diag,
                }
            audio_sent = await _wait_for_speech_output(mgr, speech_total_before, timeout_seconds=10.0)
            response_done = await _wait_for_realtime_response_done(
                session,
                response_done_before,
                timeout_seconds=4.0,
            )
            diag = _realtime_speech_diag(
                mgr,
                session,
                audio_total_before=audio_total_before,
                committed_total_before=committed_total_before,
                response_created_before=response_created_before,
            )
            transcript = str(getattr(session, "_last_response_transcript", "") or "")
            line_match = _spoken_line_matches(line, transcript) if response_done else False
            if not audio_sent:
                return {
                    "ok": False,
                    "reason": "no_voice_output_after_audio_nudge",
                    "lanlan_name": lanlan_name,
                    "method": "qwen_audio_nudge",
                    "language": lang,
                    "speech_id": claimed_speech_id,
                    "audio_sent": False,
                    "response_done": response_done,
                    "spoken_transcript": transcript[-240:],
                    "line_match": line_match,
                    "voice_source": voice_source,
                    **diag,
                }
            if not line_match:
                return {
                    "ok": False,
                    "reason": "spoken_transcript_mismatch",
                    "lanlan_name": lanlan_name,
                    "method": "qwen_audio_nudge",
                    "language": lang,
                    "speech_id": claimed_speech_id,
                    "audio_sent": True,
                    "response_done": response_done,
                    "spoken_transcript": transcript[-240:],
                    "line_match": False,
                    "voice_source": voice_source,
                    **diag,
                }
            method = "qwen_audio_nudge"
            response = {
                "ok": True,
                "lanlan_name": lanlan_name,
                "method": method,
                "language": lang,
                "bytes": len(text),
                "speech_id": claimed_speech_id,
                "audio_sent": True,
                "response_done": response_done,
                "spoken_transcript": transcript[-240:],
                "line_match": line_match,
                "voice_source": voice_source,
                **diag,
            }
        else:
            await session.create_response(text)
            audio_sent = await _wait_for_speech_output(mgr, speech_total_before, timeout_seconds=10.0)
            diag = _realtime_speech_diag(
                mgr,
                session,
                audio_total_before=audio_total_before,
                committed_total_before=committed_total_before,
                response_created_before=response_created_before,
            )
            if not audio_sent:
                return {
                    "ok": False,
                    "reason": "no_voice_output_after_text_injection",
                    "lanlan_name": lanlan_name,
                    "method": "text_response",
                    "speech_id": claimed_speech_id,
                    "audio_sent": False,
                    "voice_source": voice_source,
                    **diag,
                }
            method = "text_response"
            response = {
                "ok": True,
                "lanlan_name": lanlan_name,
                "method": method,
                "bytes": len(text),
                "speech_id": claimed_speech_id,
                "audio_sent": True,
                "voice_source": voice_source,
                **diag,
            }
    except Exception as e:
        logger.warning("🎮 Realtime 台词发声请求失败: game=%s lanlan=%s err=%s", game_type, lanlan_name, e)
        return {"ok": False, "reason": f"speak_failed: {e}", "lanlan_name": lanlan_name}
    finally:
        if restore_qwen_instructions:
            try:
                if _get_realtime_active_instructions(session) == temporary_qwen_instructions:
                    await _set_realtime_instructions(session, previous_qwen_instructions)
                else:
                    logger.info("🎮 Realtime 游戏台词临时指令已被后续上下文覆盖，跳过恢复")
            except Exception as e:
                logger.warning("🎮 Realtime 游戏台词临时指令恢复失败: game=%s lanlan=%s err=%s", game_type, lanlan_name, e)
        if speech_lock_acquired:
            try:
                speech_lock.release()
            except RuntimeError:
                pass

    logger.info(
        "🎮 游戏语音经原流水线输出: game=%s lanlan=%s method=%s bytes=%d line=%s",
        game_type,
        lanlan_name,
        method,
        int(response.get("bytes") or 0),
        line[:60],
    )
    return response


@router.post("/{game_type}/end")
async def game_end(game_type: str, request: Request):
    """结束一局游戏并清理对应的 LLM session。"""
    try:
        data = await request.json()
    except Exception:
        data = {}

    session_id = str(data.get('session_id', 'default'))
    lanlan_name = _resolve_lanlan_name(data.get("lanlan_name"))
    exit_reason = str(data.get("reason") or "game_end")
    postgame_options = _normalize_postgame_options(data.get("postgameProactive"), reason=exit_reason)
    state = _get_active_game_route_state(lanlan_name, game_type) if lanlan_name else None
    archive = None
    archive_memory = None
    postgame_result = None
    if state and str(state.get("session_id") or "") == session_id:
        _update_route_start_state_from_payload(state, data, exiting=True)
        current_state = data.get("currentState")
        if isinstance(current_state, dict):
            state["last_state"] = current_state
            if isinstance(current_state.get("score"), dict):
                state["finalScore"] = dict(current_state.get("score") or {})
        final_score = data.get("finalScore")
        if isinstance(final_score, dict):
            state["finalScore"] = final_score
        if "game_memory_tail_count" in data or "gameMemoryTailCount" in data:
            state["game_memory_tail_count"] = _normalize_game_memory_tail_count(
                data.get("game_memory_tail_count", data.get("gameMemoryTailCount"))
            )
        finalized = await _finalize_game_route_state(state, reason=exit_reason)
        archive = finalized["archive"]
        archive_memory = finalized["archive_memory"]
        if isinstance(archive_memory, dict) and archive_memory.get("status") == "skipped":
            postgame_options["enabled"] = False
        postgame_result = await _deliver_game_postgame(
            game_type,
            session_id,
            lanlan_name,
            archive,
            postgame_options,
        )

    closed = await _close_and_remove_session(game_type, session_id)
    result = {
        "ok": True,
        "closed": closed,
        "session_id": session_id,
        "route_closed": bool(archive),
        "archive": archive,
    }
    if archive_memory is not None:
        result["archive_memory"] = archive_memory
    if postgame_result is not None:
        result["postgame"] = postgame_result
    return result


@router.post("/{game_type}/quick-lines")
async def game_quick_lines(game_type: str):
    """进入游戏时生成一组当前猫娘专属快路径台词。

    产品语义：这是“游戏内上下文初始化”的一部分。代码告诉 LLM：
    接下来当前猫娘要和主人踢足球，请按当前人设生成备用短句。
    成功时前端用这些短句替换内建快路径；失败时仍使用前端内建文案。
    """
    if game_type != "soccer":
        return {"ok": False, "error": f"暂不支持 {game_type} 的快路径文案生成", "lines": {}}

    try:
        char_info = _get_current_character_info()
        prompt = _SOCCER_QUICK_LINES_PROMPT.format(
            name=char_info['lanlan_name'],
            personality=char_info['lanlan_prompt'],
        )

        from utils.file_utils import robust_json_loads
        from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm
        from utils.token_tracker import set_call_type

        set_call_type("game_quick_lines")
        llm = create_chat_llm(
            char_info['model'],
            char_info['base_url'],
            char_info['api_key'],
            temperature=0.8,
            max_completion_tokens=800,
            timeout=20,
        )
        async with llm:
            result = await llm.ainvoke([
                SystemMessage(content=prompt),
                HumanMessage(content="生成足球小游戏快路径短台词 JSON。"),
            ])

        raw = _strip_json_fence(str(result.content or ""))
        parsed = robust_json_loads(raw)
        lines = _normalize_quick_lines(parsed)
        missing = sorted(_SOCCER_QUICK_LINE_KEYS - set(lines.keys()))

        logger.info(
            "🎮 生成游戏快路径台词: game=%s character=%s keys=%d missing=%s",
            game_type, char_info['lanlan_name'], len(lines), missing,
        )
        return {
            "ok": bool(lines),
            "character": char_info['lanlan_name'],
            "lines": lines,
            "missing": missing,
            "raw": raw[:1200],
        }
    except Exception as e:
        logger.warning("🎮 生成游戏快路径台词失败: game=%s err=%s", game_type, e, exc_info=True)
        return {"ok": False, "error": str(e), "lines": {}}


@router.get("/{game_type}/character")
async def game_character(game_type: str):
    """获取当前角色信息（需求 2：角色替换用）。

    返回当前角色的模型类型和路径。足球游戏 AI 侧目前只支持 Live2D，
    如果当前角色不是 Live2D 类型，前端应回退到默认模型。
    """
    try:
        config_manager = get_config_manager()
        characters = config_manager.load_characters()
        current_name = characters.get('当前猫娘', '')
        neko_data = characters.get('猫娘', {}).get(current_name, {})

        # 获取 _reserved.avatar 配置
        reserved = neko_data.get('_reserved', {})
        avatar = reserved.get('avatar', {}) if isinstance(reserved, dict) else {}

        model_type = avatar.get('model_type', '') if isinstance(avatar, dict) else ''
        live3d_sub_type = avatar.get('live3d_sub_type', '') if isinstance(avatar, dict) else ''

        # 提取各类型模型路径
        live2d_path = ''
        mmd_path = ''
        vrm_path = ''

        if isinstance(avatar, dict):
            live2d_info = avatar.get('live2d', {})
            if isinstance(live2d_info, dict):
                raw = live2d_info.get('model_path', '')
                if raw:
                    # Live2D 可能来自 static、用户导入目录、CFA 回退目录或工坊。
                    # 足球 demo 复用主角色接口的解析逻辑，避免把用户模型误拼成 /static/...。
                    from .characters_router import get_current_live2d_model

                    model_response = await get_current_live2d_model(current_name)
                    response_body = getattr(model_response, 'body', b'')
                    if response_body:
                        model_payload = json.loads(response_body.decode('utf-8'))
                        model_info = model_payload.get('model_info') or {}
                        live2d_path = model_info.get('path', '')

            mmd_info = avatar.get('mmd', {})
            if isinstance(mmd_info, dict):
                mmd_path = mmd_info.get('model_path', '')  # 已含 /static/ 前缀

            vrm_info = avatar.get('vrm', {})
            if isinstance(vrm_info, dict):
                raw = vrm_info.get('model_path', '')
                if raw:
                    vrm_path = raw if raw.startswith('/static/') else f'/static/{raw}'

        return {
            'lanlan_name': current_name,
            'model_type': model_type,
            'live3d_sub_type': live3d_sub_type,
            'live2d_path': live2d_path,
            'mmd_path': mmd_path,
            'vrm_path': vrm_path,
        }
    except Exception as e:
        logger.error("🎮 获取角色信息失败: %s", e)
        return {"error": str(e)}


# ── 后台清理 ───────────────────────────────────────────────────────

async def cleanup_expired_sessions():
    """清理超时的游戏 session。可由 startup 事件注册为后台任务。"""
    next_session_cleanup_at = 0.0
    while True:
        await asyncio.sleep(_GAME_ROUTE_HEARTBEAT_SWEEP_SECONDS)
        now = time.time()

        heartbeat_expired_routes = [
            (k, v) for k, v in list(_game_route_states.items())
            if (
                v.get("game_route_active")
                and v.get("heartbeat_enabled", True)
                and not v.get("_exit_task")
                and now - _route_liveness_at(v) >
                _route_heartbeat_timeout_seconds(v)
            )
        ]
        for key, state in heartbeat_expired_routes:
            last_heartbeat = float(state.get("last_heartbeat_at", state.get("created_at", 0)) or 0)
            last_activity = float(state.get("last_activity", state.get("created_at", 0)) or 0)
            idle_seconds = now - _route_liveness_at(state)
            timeout_seconds = _route_heartbeat_timeout_seconds(state)
            logger.warning(
                "🎮 游戏页心跳超时，执行退出兜底: key=%s idle=%.1fs timeout=%.1fs visible=%s visibility=%s heartbeat_idle=%.1fs activity_idle=%.1fs",
                key,
                idle_seconds,
                timeout_seconds,
                state.get("page_visible"),
                state.get("visibility_state"),
                now - last_heartbeat,
                now - last_activity,
            )
            try:
                await _finalize_game_route_state(
                    state,
                    reason="heartbeat_timeout",
                    close_game_session=True,
                )
            except Exception as e:
                logger.warning("🎮 游戏页心跳超时退出兜底失败: key=%s err=%s", key, e, exc_info=True)

        if now < next_session_cleanup_at:
            continue
        next_session_cleanup_at = now + _SESSION_CLEANUP_SWEEP_SECONDS

        expired = [
            k for k, v in list(_game_sessions.items())
            if now - v['last_activity'] > _SESSION_TIMEOUT_SECONDS
        ]
        for key in expired:
            game_type, _, session_id = key.partition(":")
            if await _close_and_remove_session(game_type, session_id):
                logger.info("🎮 清理过期游戏 session: %s", key)

        expired_routes = [
            k for k, v in list(_game_route_states.items())
            if (
                not v.get("game_route_active")
                and now - float(v.get("exit_started_at", v.get("last_activity", 0)) or 0) > _SESSION_TIMEOUT_SECONDS
            )
        ]
        for key in expired_routes:
            state = _game_route_states.pop(key, None)
            if state:
                logger.info("🎮 清理过期游戏路由状态: %s", key)
