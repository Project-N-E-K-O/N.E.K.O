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
- 事件 kind 可能是 mailbox-batch：这表示上一轮 LLM 忙碌期间累积了多条离散信息。currentState 是当前最新状态；pendingItems 是忙碌期间收集到的主人语音/游戏事件，每条里的 snapshot 是那条信息发生时的状态。不要逐条播报旧事件，而要根据“最新状态 + 累积证据”给出一句自然反应。
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


def _build_game_prompt(game_type: str, lanlan_name: str, lanlan_prompt: str) -> str:
    """构建游戏 system prompt。"""
    if game_type == "soccer":
        return _SOCCER_SYSTEM_PROMPT.format(name=lanlan_name, personality=lanlan_prompt)
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

    catgirl_data = characters.get('猫娘', {})
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

    system_prompt = _build_game_prompt(
        game_type, char_info['lanlan_name'], char_info['lanlan_prompt']
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
    request_started_at = time.perf_counter()

    try:
        data = await request.json()
    except Exception:
        return {"error": "无效的请求体"}

    session_id = str(data.get('session_id', 'default'))
    event = data.get('event', {})

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
    closed = await _close_and_remove_session(game_type, session_id)
    return {
        "ok": True,
        "closed": closed,
        "session_id": session_id,
    }


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
        catgirl_data = characters.get('猫娘', {}).get(current_name, {})

        # 获取 _reserved.avatar 配置
        reserved = catgirl_data.get('_reserved', {})
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
    while True:
        await asyncio.sleep(60)  # 每分钟检查一次
        now = time.time()
        expired = [
            k for k, v in _game_sessions.items()
            if now - v['last_activity'] > _SESSION_TIMEOUT_SECONDS
        ]
        for key in expired:
            game_type, _, session_id = key.partition(":")
            if await _close_and_remove_session(game_type, session_id):
                logger.info("🎮 清理过期游戏 session: %s", key)
