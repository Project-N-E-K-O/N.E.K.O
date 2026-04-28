"""Emotion-tier LLM enrichment for ActivitySnapshot.

Two functions, both calling the small ``emotion`` model tier with strict
JSON output formats:

  * ``call_activity_guess`` — given the structured snapshot signals plus
    a few recent conversation turns, returns soft scores across the
    behavioural states (0.0-1.0 each, *independent* not normalised) plus
    a one-sentence narrative description. Lets the proactive prompt see
    "user is mostly focused-work but with some chat happening on the
    side" instead of a single hard label.

  * ``call_open_threads`` — given recent conversation turns, returns up
    to a few short phrases describing topics that were raised but not
    closed (AI promises, abandoned user threads, etc.). Covers cases
    the question-mark heuristic in the rule-based ``unfinished_thread``
    misses.

Both calls are advisory — the rule-based state machine remains
authoritative for propensity / source filtering. The emotion-tier LLM
just enriches the prompt context. Failures (LLM down, parse error,
timeout) silently return None / [] and the snapshot's pre-existing
cache stays in place.

Why a separate module: keeps prompt strings + JSON parsing isolated
from the tracker's orchestration logic. Easier to swap implementations
or add new enrichment passes later without touching tracker.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from utils.file_utils import robust_json_loads

logger = logging.getLogger(__name__)


# Input cap: the emotion tier is small and cheap, but we still don't
# want pathological prompt sizes from a long-running session. 8 turns
# of each side covers the realistic "what's hanging" window without
# ballooning latency.
_MAX_CONV_TURNS_PER_SIDE = 8
_MAX_CONV_CHARS_PER_TURN = 200

# Soft-score keys the LLM is asked to fill. Skipping ``transitioning``,
# ``away``, ``stale_returning`` because those are purely temporal /
# rule-derived — there's nothing for the LLM to add. Everything below
# is something an outside observer could reasonably score from window /
# conversation context.
_SCORED_STATES: tuple[str, ...] = (
    'gaming',
    'focused_work',
    'casual_browsing',
    'chatting',
    'voice_engaged',
    'idle',
)


# ── Prompt templates ────────────────────────────────────────────────

_ACTIVITY_GUESS_PROMPTS: dict[str, str] = {
    'zh': """你是一个用户活动分析助手。基于下方的系统信号和最近对话片段，对用户当前的活动状态做软评分，并写一句简短的活动叙述。

======系统信号======
{signals}
======系统信号结束======

======最近对话（按时间顺序）======
{conversation}
======对话结束======

======规则系统的初判======
{rule_state}
======初判结束======

请输出严格的 JSON（不带 markdown 代码块），字段：
- "scores": 一个对象，键是状态名，值是 0.0-1.0 的浮点数（独立打分，不需要归一化）。允许的状态名：{state_keys}
- "guess": 一句话叙述用户当前在做什么，符合中文表达习惯，不超过 40 字

如果某状态完全不像，给 0.0；如果非常像，给接近 1.0。多个状态可以同时高分（例如同时在写代码和聊天）。

如果你的判断和"规则系统的初判"不同，按你看到的实际信号给分；规则只是参考，不必盲从。

输出示例：
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "主人在 VS Code 里写代码，偶尔切到聊天软件回消息"}}""",

    'en': """You are a user-activity analyst. Given the system signals and recent conversation snippets below, give soft scores for the user's current activity state and write a one-sentence narrative.

======System signals======
{signals}
======End of signals======

======Recent conversation (chronological)======
{conversation}
======End of conversation======

======Rule system's initial classification======
{rule_state}
======End of initial classification======

Output strict JSON (no markdown fences), with fields:
- "scores": object mapping state name to a 0.0-1.0 float (independent scoring, no normalization). Allowed states: {state_keys}
- "guess": one short sentence describing what the user is doing right now, max ~40 words

Give 0.0 for states that don't fit at all; close to 1.0 for very fitting ones. Multiple states can be high simultaneously (e.g. coding while chatting).

If you disagree with the rule classification, score based on the actual signals — the rule is just a reference, not gospel.

Example output:
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "Master is coding in VS Code, occasionally switching to a chat app to reply"}}""",

    'ja': """あなたはユーザー活動の分析助手です。下のシステム信号と最近の会話に基づき、ユーザーの現在の活動状態にソフトスコアを付けて、一文の活動叙述を書いてください。

======システム信号======
{signals}
======信号ここまで======

======最近の会話（時系列）======
{conversation}
======会話ここまで======

======ルール系の初期判定======
{rule_state}
======初期判定ここまで======

厳密なJSON（markdownコードブロックなし）で出力してください：
- "scores": 状態名をキー、0.0〜1.0の浮動小数を値とするオブジェクト（独立スコア、正規化不要）。許可される状態：{state_keys}
- "guess": ユーザーが今何をしているかを表す一文、自然な日本語で40字以内

全く当てはまらない状態は0.0、非常に当てはまる状態は1.0近く。複数の状態が同時に高くてもOK。

ルール初期判定と意見が違う場合は、実際の信号に従ってください。ルールは参考に過ぎません。

出力例：
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "ご主人はVS Codeでコーディング中、時々チャットアプリに切り替えて返信している"}}""",

    'ko': """당신은 사용자 활동 분석 도우미입니다. 아래의 시스템 신호와 최근 대화 스니펫을 바탕으로 사용자의 현재 활동 상태에 소프트 점수를 매기고, 활동 서술 한 문장을 작성하세요.

======시스템 신호======
{signals}
======신호 끝======

======최근 대화 (시간순)======
{conversation}
======대화 끝======

======규칙 시스템의 초기 판정======
{rule_state}
======초기 판정 끝======

엄격한 JSON으로 출력하세요 (markdown 코드 블록 없이). 필드:
- "scores": 상태명을 키로, 0.0-1.0 부동소수를 값으로 하는 객체 (독립 점수, 정규화 불필요). 허용 상태: {state_keys}
- "guess": 사용자가 지금 무엇을 하는지에 대한 한 문장, 자연스러운 한국어로 40자 이내

전혀 해당하지 않으면 0.0, 매우 해당하면 1.0 근처. 여러 상태가 동시에 높아도 됨.

규칙 초기 판정과 다르면 실제 신호에 따라 점수를 매기세요. 규칙은 참고일 뿐.

출력 예:
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "주인님이 VS Code에서 코딩 중, 가끔 채팅 앱으로 전환해 답장 중"}}""",

    'ru': """Вы — аналитик активности пользователя. Опираясь на сигналы системы и недавние реплики ниже, поставьте мягкие оценки текущему состоянию активности пользователя и напишите одно предложение-описание.

======Сигналы системы======
{signals}
======Конец сигналов======

======Недавний разговор (хронология)======
{conversation}
======Конец разговора======

======Первоначальная классификация правил======
{rule_state}
======Конец классификации======

Выведите строгий JSON (без markdown-обрамления), поля:
- "scores": объект «название состояния → число 0.0-1.0» (независимые оценки, нормализация не нужна). Допустимые состояния: {state_keys}
- "guess": одно короткое предложение о том, что пользователь делает прямо сейчас, до ~40 слов

0.0 — состояние совсем не подходит; ближе к 1.0 — очень подходит. Несколько состояний могут быть одновременно высокими.

Если вы не согласны с классификацией правил — оценивайте по реальным сигналам. Правила — лишь ориентир.

Пример вывода:
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "Хозяин кодит в VS Code, иногда переключается в чат для ответа"}}""",
}


_OPEN_THREADS_PROMPTS: dict[str, str] = {
    'zh': """你是对话回顾助手。看下面最近的对话，列出最多 3 条"被提起但还没收尾"的话题——比如 AI 答应过但还没做的事、用户提到一半就被打断的事情、双方约定但没跟进的细节。

======最近对话（按时间顺序）======
{conversation}
======对话结束======

输出严格的 JSON（不带 markdown 代码块）：
{{"open_threads": ["短句 1", "短句 2"]}}

每条用一句话写清是谁挂了这个话题、内容是什么。如果对话已经收尾或没什么悬而未决的，返回空数组：{{"open_threads": []}}

不要包括"明显的问题没回答"——那种由另一个机制处理。这里专注语义上的"挂着"。""",

    'en': """You are a conversation review assistant. Look at the recent conversation below and list up to 3 topics that were "raised but not closed" — things like promises the AI made but hasn't fulfilled, user thoughts cut off mid-sentence, plans agreed but not followed up.

======Recent conversation (chronological)======
{conversation}
======End of conversation======

Output strict JSON (no markdown fences):
{{"open_threads": ["short phrase 1", "short phrase 2"]}}

Each entry: one sentence saying who left this thread hanging and what it was about. If the conversation is fully wrapped up or nothing is hanging, return an empty array: {{"open_threads": []}}

Do NOT include "obvious unanswered questions" — those are handled by a separate mechanism. Focus on semantic "left hanging" cases.""",

    'ja': """あなたは会話レビュー助手です。下の最近の会話を見て、「持ち出されたが収まっていない」話題を最大3件挙げてください。例：AIが約束したがまだ実行していないこと、ユーザーが言いかけて中断したこと、双方で合意したのに追いかけていない詳細など。

======最近の会話（時系列）======
{conversation}
======会話ここまで======

厳密なJSON（markdownコードブロックなし）で出力：
{{"open_threads": ["短い文1", "短い文2"]}}

各項目：誰がこの話題を残したか、内容は何かを一文で。会話が完結している、または特に懸案がなければ空配列を返す：{{"open_threads": []}}

「明らかな未回答の質問」は別の仕組みで扱うため除外してください。意味的に「宙ぶらりん」のものに集中。""",

    'ko': """당신은 대화 검토 도우미입니다. 아래 최근 대화를 보고 "꺼냈지만 마무리되지 않은" 화제를 최대 3개 나열하세요. 예: AI가 약속했지만 아직 안 한 일, 사용자가 말을 꺼내다가 끊긴 것, 양쪽이 합의했지만 후속하지 않은 세부 사항 등.

======최근 대화 (시간순)======
{conversation}
======대화 끝======

엄격한 JSON으로 출력 (markdown 코드 블록 없이):
{{"open_threads": ["짧은 문장 1", "짧은 문장 2"]}}

각 항목: 누가 이 화제를 남겼는지, 내용은 무엇인지 한 문장으로. 대화가 마무리되었거나 특별히 미해결이 없으면 빈 배열 반환: {{"open_threads": []}}

"명백한 미답변 질문"은 다른 메커니즘이 처리하므로 제외. 의미적으로 "걸려있는" 경우에 집중.""",

    'ru': """Вы — помощник по обзору разговора. Просмотрите недавний разговор ниже и перечислите до 3 тем, которые «подняли, но не закрыли»: обещания AI, ещё не выполненные; мысли пользователя, оборвавшиеся на полуслове; согласованные планы без продолжения.

======Недавний разговор (хронология)======
{conversation}
======Конец разговора======

Выведите строгий JSON (без markdown):
{{"open_threads": ["короткая фраза 1", "короткая фраза 2"]}}

Каждая запись — одно предложение: кто оставил тему «висеть» и о чём она. Если разговор завершён или ничего не висит — пустой массив: {{"open_threads": []}}

НЕ включайте «очевидные неотвеченные вопросы» — этим занимается отдельный механизм. Фокус на семантических «зависших» случаях.""",
}


# ── Helpers ─────────────────────────────────────────────────────────

def _normalize_lang(lang: str) -> str:
    if not lang:
        return 'zh'
    low = lang.lower()
    if low.startswith('zh'):
        return 'zh'
    if low.startswith('ja'):
        return 'ja'
    if low.startswith('ko'):
        return 'ko'
    if low.startswith('ru'):
        return 'ru'
    return 'en'


def _format_conversation(
    user_msgs: list[tuple[float, str]],
    ai_msgs: list[tuple[float, str]],
) -> str:
    """Interleave user / AI messages by timestamp, render as plain lines.

    Each side is capped to ``_MAX_CONV_TURNS_PER_SIDE`` (most recent),
    each text truncated to ``_MAX_CONV_CHARS_PER_TURN``. Empty input
    returns a placeholder so the prompt still parses.
    """
    items: list[tuple[float, str, str]] = []
    for ts, text in user_msgs[-_MAX_CONV_TURNS_PER_SIDE:]:
        items.append((ts, 'user', text))
    for ts, text in ai_msgs[-_MAX_CONV_TURNS_PER_SIDE:]:
        items.append((ts, 'ai', text))
    items.sort(key=lambda x: x[0])
    if not items:
        return '(no conversation yet)'

    now = time.time()
    out_lines: list[str] = []
    for ts, who, text in items:
        age = max(0.0, now - ts)
        if age < 90:
            age_str = f'{int(age)}s ago'
        elif age < 3600:
            age_str = f'{int(age / 60)}min ago'
        else:
            age_str = f'{int(age / 3600)}h ago'
        clip = text.strip()
        if len(clip) > _MAX_CONV_CHARS_PER_TURN:
            clip = clip[:_MAX_CONV_CHARS_PER_TURN] + '…'
        out_lines.append(f'[{age_str}] {who}: {clip}')
    return '\n'.join(out_lines)


def _format_signals(snapshot_view: dict[str, Any]) -> str:
    """Render a structured-signals dict as compact ``key: value`` lines."""
    return '\n'.join(f'{k}: {v}' for k, v in snapshot_view.items() if v is not None)


def _strip_json_fences(text: str) -> str:
    """Strip ``\\`\\`\\`json`` / ``\\`\\`\\``` fences if the model emitted them
    despite being asked not to."""
    s = text.strip()
    if s.startswith('```'):
        m = re.match(r'^```[a-zA-Z]*\s*(.+?)\s*```\s*$', s, flags=re.S)
        if m:
            return m.group(1).strip()
    return s


# ── Public API ──────────────────────────────────────────────────────

async def call_activity_guess(
    *,
    snapshot_signals: dict[str, Any],
    rule_state: str,
    user_msgs: list[tuple[float, str]],
    ai_msgs: list[tuple[float, str]],
    lang: str,
    timeout: float = 8.0,
) -> dict | None:
    """Run the emotion-tier model to score states + generate a narrative.

    Returns ``{'scores': dict[str, float], 'guess': str}`` on success, or
    ``None`` on any failure (LLM down, parse error, timeout). The caller
    keeps any prior cached value when ``None`` comes back.

    Parameters
    ----------
    snapshot_signals:
        Dict of structured-signal lines to render in the prompt
        (window title, dwell, CPU, GPU, idle, etc). Caller chooses what
        to include — this function just renders ``key: value``.
    rule_state:
        The rule machine's current pick (e.g. ``"focused_work"``) so the
        LLM can choose to confirm or override.
    """
    lang_key = _normalize_lang(lang)
    template = _ACTIVITY_GUESS_PROMPTS.get(lang_key, _ACTIVITY_GUESS_PROMPTS['en'])

    prompt = template.format(
        signals=_format_signals(snapshot_signals),
        conversation=_format_conversation(user_msgs, ai_msgs),
        rule_state=rule_state,
        state_keys=', '.join(_SCORED_STATES),
    )

    raw = await _invoke_emotion_tier(prompt, timeout=timeout, label='activity_guess')
    if raw is None:
        return None

    parsed = _safe_parse_json(raw)
    if not isinstance(parsed, dict):
        logger.debug('activity_guess: LLM did not return a JSON object: %r', raw[:200])
        return None

    raw_scores = parsed.get('scores')
    guess = parsed.get('guess', '') or ''
    if not isinstance(raw_scores, dict) or not isinstance(guess, str):
        logger.debug('activity_guess: malformed JSON shape: %r', parsed)
        return None

    # Sanitise: keep only allowed state keys and clamp to [0, 1].
    scores: dict[str, float] = {}
    for key, value in raw_scores.items():
        if key not in _SCORED_STATES:
            continue
        try:
            f = float(value)
        except (TypeError, ValueError):
            continue
        scores[key] = max(0.0, min(1.0, f))

    return {'scores': scores, 'guess': guess.strip()}


async def call_open_threads(
    *,
    user_msgs: list[tuple[float, str]],
    ai_msgs: list[tuple[float, str]],
    lang: str,
    timeout: float = 6.0,
) -> list[str] | None:
    """Run the emotion-tier model to detect semantically open threads.

    Returns a list of short phrases on success (possibly empty), or
    ``None`` on failure. Caller distinguishes "LLM said nothing's
    hanging" (``[]``) from "LLM call failed" (``None``).
    """
    lang_key = _normalize_lang(lang)
    template = _OPEN_THREADS_PROMPTS.get(lang_key, _OPEN_THREADS_PROMPTS['en'])

    if not user_msgs and not ai_msgs:
        return []

    prompt = template.format(conversation=_format_conversation(user_msgs, ai_msgs))

    raw = await _invoke_emotion_tier(prompt, timeout=timeout, label='open_threads')
    if raw is None:
        return None

    parsed = _safe_parse_json(raw)
    if not isinstance(parsed, dict):
        logger.debug('open_threads: LLM did not return a JSON object: %r', raw[:200])
        return None

    threads = parsed.get('open_threads')
    if not isinstance(threads, list):
        return None
    cleaned: list[str] = []
    for entry in threads[:5]:
        if isinstance(entry, str) and entry.strip():
            cleaned.append(entry.strip())
    return cleaned


# ── Internal LLM driver ─────────────────────────────────────────────

async def _invoke_emotion_tier(prompt: str, *, timeout: float, label: str) -> str | None:
    """Single-shot emotion-tier call. Returns raw response text or None.

    Imports are deferred so importing this module doesn't pull in the
    full LLM stack — useful for tests that exercise prompt formatting
    without a live model.
    """
    from langchain_core.messages import HumanMessage
    from utils.config_manager import get_config_manager
    from utils.llm_client import create_chat_llm
    from utils.token_tracker import set_call_type

    try:
        cfg_mgr = get_config_manager()
        cfg = cfg_mgr.get_model_api_config('emotion')
    except Exception as e:
        logger.debug('emotion config fetch failed: %s', e)
        return None
    model = cfg.get('model')
    api_key = cfg.get('api_key')
    base_url = cfg.get('base_url')
    if not model or not api_key:
        logger.debug('emotion tier model/api_key missing — enrichment disabled')
        return None

    set_call_type('activity_enrichment')
    try:
        llm = create_chat_llm(
            model, base_url, api_key,
            temperature=0.4,
            max_completion_tokens=512,
        )
    except Exception as e:
        logger.debug('emotion-tier llm init failed: %s', e)
        return None

    try:
        async with llm:
            resp = await asyncio.wait_for(
                llm.ainvoke([HumanMessage(content=prompt)]),
                timeout=timeout,
            )
        return getattr(resp, 'content', '') or ''
    except asyncio.TimeoutError:
        logger.debug('emotion-tier %s call timed out (%ss)', label, timeout)
        return None
    except Exception as e:
        logger.debug('emotion-tier %s call failed: %s', label, e)
        return None


def _safe_parse_json(raw: str) -> Any:
    """Parse JSON, tolerating markdown fences and minor LLM noise."""
    if not raw:
        return None
    cleaned = _strip_json_fences(raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        # Fallback to project-local robust parser (handles trailing
        # commas, single quotes, etc — common LLM output blemishes).
        try:
            return robust_json_loads(cleaned)
        except Exception:
            return None
