"""Multi-language prompts and labels for the activity tracker.

Lives under ``config/prompts_*`` per the project's i18n convention —
**all** multi-language strings must live here, not in regular code, so
that adding a new language is a single-file pass over ``config/`` and
nothing slips through. The prompt-hygiene linter
(``scripts/check_prompt_hygiene.py``) only catches *flat*
``{lang_code: str}`` dicts; nested-dict tables (``{lang: {key: str}}``)
must be moved here by convention even though the linter wouldn't fire.

What ships here:

Flat ``{lang_code: str}`` maps (resolved via ``_loc(MAP, lang)``):

* ``ACTIVITY_GUESS_PROMPTS`` — emotion-tier system prompt that asks
  the model to soft-score the user's current activity state and write
  a one-sentence narrative. Consumed by
  ``main_logic/activity/llm_enrichment.py:call_activity_guess``.

* ``OPEN_THREADS_PROMPTS`` — emotion-tier system prompt that detects
  semantically open threads (promises, abandoned mid-sentences, etc.)
  beyond the question-mark heuristic. Consumed by
  ``main_logic/activity/llm_enrichment.py:call_open_threads``.

* ``OS_DEGRADED_MARKER`` — short bracketed text appended to the
  state-section header when the backend can't read the user's OS
  signals. Consumed by
  ``main_logic/activity/snapshot.py:format_activity_state_section``.

Nested ``{lang_code: {key: str}}`` tables (resolved via
``MAP.get(lang, MAP['en']).get(key, ...)``); used by
``format_activity_state_section`` to render the snapshot:

* ``ACTIVITY_STATE_LABELS`` — human-readable label for each
  ``ActivityState`` (e.g. ``focused_work`` → ``专注工作中``).
* ``ACTIVITY_PROPENSITY_DIRECTIVES`` — short directive sentence for
  each ``Propensity`` (e.g. ``restricted_screen_only`` →
  ``只就屏幕内容轻聊一句``).
* ``ACTIVITY_REASON_TEMPLATES`` — ``str.format``-able templates for
  each structured reason code emitted by the state machine.
* ``ACTIVITY_STATE_SECTION_LABELS`` — header / footer / period names
  / time-relative phrases used to assemble the final state section.
"""

from __future__ import annotations


# ── Activity guess + soft scores (emotion-tier) ─────────────────────

ACTIVITY_GUESS_PROMPTS: dict[str, str] = {
    'zh': """你是一个用户活动分析助手。基于下方的系统信号和最近对话片段，对用户当前的活动状态做软评分，并写一句简短的活动叙述。

======以下为系统信号======
{signals}
======以上为系统信号======

======以下为最近对话（按时间顺序）======
{conversation}
======以上为最近对话（按时间顺序）======

======以下为规则系统的初判======
{rule_state}
======以上为规则系统的初判======

请输出严格的 JSON（不带 markdown 代码块），字段：
- "scores": 一个对象，键是状态名，值是 0.0-1.0 的浮点数（独立打分，不需要归一化）。允许的状态名：{state_keys}
- "guess": 一句话叙述用户当前在做什么，符合中文表达习惯，不超过 40 字

如果某状态完全不像，给 0.0；如果非常像，给接近 1.0。多个状态可以同时高分（例如同时在写代码和聊天）。

如果你的判断和"规则系统的初判"不同，按你看到的实际信号给分；规则只是参考，不必盲从。

输出示例：
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "主人在 VS Code 里写代码，偶尔切到聊天软件回消息"}}""",

    'en': """You are a user-activity analyst. Given the system signals and recent conversation snippets below, give soft scores for the user's current activity state and write a one-sentence narrative.

======Below is System signals======
{signals}
======Above is System signals======

======Below is Recent conversation (chronological)======
{conversation}
======Above is Recent conversation (chronological)======

======Below is Rule system's initial classification======
{rule_state}
======Above is Rule system's initial classification======

Output strict JSON (no markdown fences), with fields:
- "scores": object mapping state name to a 0.0-1.0 float (independent scoring, no normalization). Allowed states: {state_keys}
- "guess": one short sentence describing what the user is doing right now, max ~40 words

Give 0.0 for states that don't fit at all; close to 1.0 for very fitting ones. Multiple states can be high simultaneously (e.g. coding while chatting).

If you disagree with the rule classification, score based on the actual signals — the rule is just a reference, not gospel.

Example output:
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "Master is coding in VS Code, occasionally switching to a chat app to reply"}}""",

    'ja': """あなたはユーザー活動の分析助手です。下のシステム信号と最近の会話に基づき、ユーザーの現在の活動状態にソフトスコアを付けて、一文の活動叙述を書いてください。

======以下はシステム信号======
{signals}
======以上はシステム信号======

======以下は最近の会話（時系列）======
{conversation}
======以上は最近の会話（時系列）======

======以下はルール系の初期判定======
{rule_state}
======以上はルール系の初期判定======

厳密なJSON（markdownコードブロックなし）で出力してください：
- "scores": 状態名をキー、0.0〜1.0の浮動小数を値とするオブジェクト（独立スコア、正規化不要）。許可される状態：{state_keys}
- "guess": ユーザーが今何をしているかを表す一文、自然な日本語で40字以内

全く当てはまらない状態は0.0、非常に当てはまる状態は1.0近く。複数の状態が同時に高くてもOK。

ルール初期判定と意見が違う場合は、実際の信号に従ってください。ルールは参考に過ぎません。

出力例：
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "ご主人はVS Codeでコーディング中、時々チャットアプリに切り替えて返信している"}}""",

    'ko': """당신은 사용자 활동 분석 도우미입니다. 아래의 시스템 신호와 최근 대화 스니펫을 바탕으로 사용자의 현재 활동 상태에 소프트 점수를 매기고, 활동 서술 한 문장을 작성하세요.

======아래는 시스템 신호======
{signals}
======위는 시스템 신호======

======아래는 최근 대화 (시간순)======
{conversation}
======위는 최근 대화 (시간순)======

======아래는 규칙 시스템의 초기 판정======
{rule_state}
======위는 규칙 시스템의 초기 판정======

엄격한 JSON으로 출력하세요 (markdown 코드 블록 없이). 필드:
- "scores": 상태명을 키로, 0.0-1.0 부동소수를 값으로 하는 객체 (독립 점수, 정규화 불필요). 허용 상태: {state_keys}
- "guess": 사용자가 지금 무엇을 하는지에 대한 한 문장, 자연스러운 한국어로 40자 이내

전혀 해당하지 않으면 0.0, 매우 해당하면 1.0 근처. 여러 상태가 동시에 높아도 됨.

규칙 초기 판정과 다르면 실제 신호에 따라 점수를 매기세요. 규칙은 참고일 뿐.

출력 예:
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "주인님이 VS Code에서 코딩 중, 가끔 채팅 앱으로 전환해 답장 중"}}""",

    'ru': """Вы — аналитик активности пользователя. Опираясь на сигналы системы и недавние реплики ниже, поставьте мягкие оценки текущему состоянию активности пользователя и напишите одно предложение-описание.

======Ниже Сигналы системы======
{signals}
======Выше Сигналы системы======

======Ниже Недавний разговор (хронология)======
{conversation}
======Выше Недавний разговор (хронология)======

======Ниже Первоначальная классификация правил======
{rule_state}
======Выше Первоначальная классификация правил======

Выведите строгий JSON (без markdown-обрамления), поля:
- "scores": объект «название состояния → число 0.0-1.0» (независимые оценки, нормализация не нужна). Допустимые состояния: {state_keys}
- "guess": одно короткое предложение о том, что пользователь делает прямо сейчас, до ~40 слов

0.0 — состояние совсем не подходит; ближе к 1.0 — очень подходит. Несколько состояний могут быть одновременно высокими.

Если вы не согласны с классификацией правил — оценивайте по реальным сигналам. Правила — лишь ориентир.

Пример вывода:
{{"scores": {{"focused_work": 0.7, "chatting": 0.2, "idle": 0.1, "gaming": 0.0, "casual_browsing": 0.0, "voice_engaged": 0.0}}, "guess": "Хозяин кодит в VS Code, иногда переключается в чат для ответа"}}""",
}


# ── Open-thread semantic detection (emotion-tier) ───────────────────

OPEN_THREADS_PROMPTS: dict[str, str] = {
    'zh': """你是对话回顾助手。看下面最近的对话，列出最多 3 条"被提起但还没收尾"的话题——比如 AI 答应过但还没做的事、用户提到一半就被打断的事情、双方约定但没跟进的细节。

======以下为最近对话（按时间顺序）======
{conversation}
======以上为最近对话（按时间顺序）======

输出严格的 JSON（不带 markdown 代码块）：
{{"open_threads": ["短句 1", "短句 2"]}}

每条用一句话写清是谁挂了这个话题、内容是什么。如果对话已经收尾或没什么悬而未决的，返回空数组：{{"open_threads": []}}

不要包括"明显的问题没回答"——那种由另一个机制处理。这里专注语义上的"挂着"。""",

    'en': """You are a conversation review assistant. Look at the recent conversation below and list up to 3 topics that were "raised but not closed" — things like promises the AI made but hasn't fulfilled, user thoughts cut off mid-sentence, plans agreed but not followed up.

======Below is Recent conversation (chronological)======
{conversation}
======Above is Recent conversation (chronological)======

Output strict JSON (no markdown fences):
{{"open_threads": ["short phrase 1", "short phrase 2"]}}

Each entry: one sentence saying who left this thread hanging and what it was about. If the conversation is fully wrapped up or nothing is hanging, return an empty array: {{"open_threads": []}}

Do NOT include "obvious unanswered questions" — those are handled by a separate mechanism. Focus on semantic "left hanging" cases.""",

    'ja': """あなたは会話レビュー助手です。下の最近の会話を見て、「持ち出されたが収まっていない」話題を最大3件挙げてください。例：AIが約束したがまだ実行していないこと、ユーザーが言いかけて中断したこと、双方で合意したのに追いかけていない詳細など。

======以下は最近の会話（時系列）======
{conversation}
======以上は最近の会話（時系列）======

厳密なJSON（markdownコードブロックなし）で出力：
{{"open_threads": ["短い文1", "短い文2"]}}

各項目：誰がこの話題を残したか、内容は何かを一文で。会話が完結している、または特に懸案がなければ空配列を返す：{{"open_threads": []}}

「明らかな未回答の質問」は別の仕組みで扱うため除外してください。意味的に「宙ぶらりん」のものに集中。""",

    'ko': """당신은 대화 검토 도우미입니다. 아래 최근 대화를 보고 "꺼냈지만 마무리되지 않은" 화제를 최대 3개 나열하세요. 예: AI가 약속했지만 아직 안 한 일, 사용자가 말을 꺼내다가 끊긴 것, 양쪽이 합의했지만 후속하지 않은 세부 사항 등.

======아래는 최근 대화 (시간순)======
{conversation}
======위는 최근 대화 (시간순)======

엄격한 JSON으로 출력 (markdown 코드 블록 없이):
{{"open_threads": ["짧은 문장 1", "짧은 문장 2"]}}

각 항목: 누가 이 화제를 남겼는지, 내용은 무엇인지 한 문장으로. 대화가 마무리되었거나 특별히 미해결이 없으면 빈 배열 반환: {{"open_threads": []}}

"명백한 미답변 질문"은 다른 메커니즘이 처리하므로 제외. 의미적으로 "걸려있는" 경우에 집중.""",

    'ru': """Вы — помощник по обзору разговора. Просмотрите недавний разговор ниже и перечислите до 3 тем, которые «подняли, но не закрыли»: обещания AI, ещё не выполненные; мысли пользователя, оборвавшиеся на полуслове; согласованные планы без продолжения.

======Ниже Недавний разговор (хронология)======
{conversation}
======Выше Недавний разговор (хронология)======

Выведите строгий JSON (без markdown):
{{"open_threads": ["короткая фраза 1", "короткая фраза 2"]}}

Каждая запись — одно предложение: кто оставил тему «висеть» и о чём она. Если разговор завершён или ничего не висит — пустой массив: {{"open_threads": []}}

НЕ включайте «очевидные неотвеченные вопросы» — этим занимается отдельный механизм. Фокус на семантических «зависших» случаях.""",
}


# ── Degraded-mode marker (appended to state-section header) ─────────

OS_DEGRADED_MARKER: dict[str, str] = {
    'zh': '（远程模式·无屏幕信号）',
    'en': '(remote / no screen signal)',
    'ja': '（リモートモード・画面信号なし）',
    'ko': '(원격 모드 · 화면 신호 없음)',
    'ru': '(удалённый режим · нет экранных сигналов)',
}


# ── State labels (rendered next to the raw state name) ──────────────
#
# Inner-key invariant: the value-side keys MUST stay in sync with the
# ``ActivityState`` Literal in ``main_logic/activity/snapshot.py``.
# Adding a state there without updating these tables makes the
# formatter fall back to printing the raw enum string.

ACTIVITY_STATE_LABELS: dict[str, dict[str, str]] = {
    'zh': {
        'away':            '离开',
        'stale_returning': '刚回来',
        'gaming':          '游戏中',
        'focused_work':    '专注工作中',
        'casual_browsing': '休闲浏览',
        'chatting':        '聊天中',
        'voice_engaged':   '语音对话中',
        'idle':            '空闲',
        'transitioning':   '切换状态中',
        'private':         '隐私应用前台',
    },
    'en': {
        'away':            'away',
        'stale_returning': 'just returned',
        'gaming':          'gaming',
        'focused_work':    'focused work',
        'casual_browsing': 'casual browsing',
        'chatting':        'chatting',
        'voice_engaged':   'voice conversation',
        'idle':            'idle',
        'transitioning':   'transitioning',
        'private':         'private app foreground',
    },
    'ja': {
        'away':            '離席',
        'stale_returning': '戻ってきたばかり',
        'gaming':          'ゲーム中',
        'focused_work':    '集中作業中',
        'casual_browsing': 'のんびりブラウジング',
        'chatting':        'チャット中',
        'voice_engaged':   'ボイス会話中',
        'idle':            'アイドル',
        'transitioning':   '状態切替中',
        'private':         'プライベートアプリ前面',
    },
    'ko': {
        'away':            '자리 비움',
        'stale_returning': '방금 돌아옴',
        'gaming':          '게임 중',
        'focused_work':    '집중 작업 중',
        'casual_browsing': '캐주얼 브라우징',
        'chatting':        '채팅 중',
        'voice_engaged':   '음성 대화 중',
        'idle':            '유휴',
        'transitioning':   '상태 전환 중',
        'private':         '비공개 앱 전면',
    },
    'ru': {
        'away':            'отсутствует',
        'stale_returning': 'только что вернулся',
        'gaming':          'играет',
        'focused_work':    'сосредоточенная работа',
        'casual_browsing': 'неспешный сёрфинг',
        'chatting':        'переписка',
        'voice_engaged':   'голосовая беседа',
        'idle':            'простой',
        'transitioning':   'смена контекста',
        'private':         'приватное приложение в фокусе',
    },
}


# ── Tone hints (single-line style modifier) ─────────────────────────
#
# Tone is orthogonal to propensity: propensity decides *what kind of
# source* the AI may draw from, tone decides *how to deliver it*. The
# Phase 2 prompt renders tone as one extra line:
#
#     口吻：短句优先，不延展话题，避免动作描写
#
# Tones and when they fire (see ``derive_tone`` in
# ``main_logic/activity/snapshot.py`` for the full table):
#
#   * ``terse``   — competitive games, rhythm games
#   * ``hushed``  — immersive horror games
#   * ``mellow``  — immersive RPG / story-driven games
#   * ``playful`` — casual gaming, casual_browsing
#   * ``warm``    — voice / chatting / stale_returning
#   * ``concise`` — focused_work / idle / default (rendered nothing —
#                   format_activity_state_section skips when concise to
#                   save a line in the common case)
ACTIVITY_TONE_HINTS: dict[str, dict[str, str]] = {
    'zh': {
        'terse':   '短句优先，不延展话题，避免动作描写',
        'hushed':  '轻声细语，配合氛围克制说话',
        'mellow':  '慢节奏放松陪伴，不丢专业术语进来',
        'playful': '闲适带点小俏皮，可以开玩笑',
        'warm':    '自然对话，回应感强',
        'concise': '不啰嗦，专业克制',
    },
    'en': {
        'terse':   'short sentences first; do not extend topics; avoid action narration',
        'hushed':  'soft and quiet, restrained to match the atmosphere',
        'mellow':  'slow-paced, relaxed companionship; no jargon dumps',
        'playful': 'easygoing with a touch of mischief; jokes welcome',
        'warm':    'natural conversation, responsive in tone',
        'concise': 'no fluff, professional and restrained',
    },
    'ja': {
        'terse':   '短文優先・話題を広げない・動作描写を避ける',
        'hushed':  '小声で控えめに、雰囲気に合わせて',
        'mellow':  'ゆったりした寄り添い、専門用語は出さない',
        'playful': 'のんびりしつつ少し茶目っ気、冗談 OK',
        'warm':    '自然な会話、反応性高め',
        'concise': '冗長なし、控えめでプロフェッショナル',
    },
    'ko': {
        'terse':   '짧은 문장 우선, 화제 확장 금지, 동작 묘사 자제',
        'hushed':  '낮은 목소리로, 분위기에 맞게 절제',
        'mellow':  '느긋한 동행, 전문 용어는 자제',
        'playful': '편안하게 약간 장난스럽게, 농담도 OK',
        'warm':    '자연스러운 대화, 반응성 높게',
        'concise': '군더더기 없이, 절제된 전문성',
    },
    'ru': {
        'terse':   'короткие фразы; не расширять тему; без описаний действий',
        'hushed':  'тихо и сдержанно, в тон атмосфере',
        'mellow':  'неспешное сопровождение, без жаргона',
        'playful': 'непринуждённо и слегка игриво, шутки уместны',
        'warm':    'естественный разговор, отзывчивая интонация',
        'concise': 'без воды, сдержанно и профессионально',
    },
}


# ── Propensity directives (positive instructions, not prohibitions) ─
#
# These say *what to do*, not *what to avoid* — the prompt builder
# already filters the corresponding source channels out of the prompt
# upstream, so spelling the prohibitions out again is just noise.
# Inner keys MUST stay in sync with the ``Propensity`` Literal in
# ``main_logic/activity/snapshot.py``.

ACTIVITY_PROPENSITY_DIRECTIVES: dict[str, dict[str, str]] = {
    'zh': {
        'closed':                 '不便打扰',
        'restricted_screen_only': '只就屏幕内容轻聊一句',
        'open':                   '可正常搭话',
        'greeting_window':        '温和问候，可自然带出久远旧话题的回忆',
    },
    'en': {
        'closed':                 'do not disturb',
        'restricted_screen_only': 'a one-liner on what is on screen, nothing more',
        'open':                   'open to chat',
        'greeting_window':        'a soft greeting fits; weaving in an older memory is welcome',
    },
    'ja': {
        'closed':                 '邪魔しない',
        'restricted_screen_only': '画面の内容について一言だけ',
        'open':                   '普通に話しかけてOK',
        'greeting_window':        '柔らかい挨拶が合う；古い話題の自然な回想も歓迎',
    },
    'ko': {
        'closed':                 '방해 금지',
        'restricted_screen_only': '화면 내용에 대해 한마디만',
        'open':                   '평소처럼 말 걸어도 좋음',
        'greeting_window':        '부드러운 인사가 어울림; 오래된 화제 회상도 환영',
    },
    'ru': {
        'closed':                 'не беспокоить',
        'restricted_screen_only': 'короткая реплика по экрану — и всё',
        'open':                   'открыт к общению',
        'greeting_window':        'уместно мягкое приветствие; воспоминание о давнем — приветствуется',
    },
}


# ── Reason templates (rendered via ``str.format(**params)``) ────────
#
# Codes the state machine emits, with the params each accepts:
#   state_away              {idle_seconds: int}
#   state_stale_returning   {}
#   state_voice_engaged     {}
#   state_gaming            {app: str}
#   state_focused_work      {app: str, dwell_seconds: int}
#   state_casual_browsing   {app: str}
#   state_chatting          {app: str}
#   state_transitioning     {}
#   state_idle              {}
#   high_cpu                {cpu_percent: int}
#   high_gpu                {gpu_percent: int}
#   gaming_by_gpu           {}            (fallback when no game-keyword hit)
#
# When adding a new code, add it to *all* languages — the renderer
# falls back to English on a per-code miss, but a code missing in
# English makes the formatter print the raw code string.

ACTIVITY_REASON_TEMPLATES: dict[str, dict[str, str]] = {
    'zh': {
        'state_away':            '系统已 {idle_seconds}s 无输入',
        'state_stale_returning': '用户刚从离开状态回来',
        'state_voice_engaged':   '语音模式 + 最近有发声',
        'state_gaming':          '前台游戏：{app}',
        'state_focused_work':    '专注 {app} 已 {dwell_seconds}s',
        'state_casual_browsing': '浏览娱乐：{app}',
        'state_chatting':        '前台聊天：{app}',
        'state_transitioning':   '近期窗口频繁切换',
        'state_idle':            '在电脑前但无明显任务',
        'state_private':         '前台是隐私应用——不分类、不缓存',
        'high_cpu':              'CPU 30s 均值 {cpu_percent}%',
        'high_gpu':              'GPU 利用率 {gpu_percent}%',
        'gaming_by_gpu':         'GPU 持续高负载（怀疑未识别的游戏）',
    },
    'en': {
        'state_away':            'system idle for {idle_seconds}s',
        'state_stale_returning': 'user just came back from being away',
        'state_voice_engaged':   'voice mode + recent speech activity',
        'state_gaming':          'foreground game: {app}',
        'state_focused_work':    'focused on {app} for {dwell_seconds}s',
        'state_casual_browsing': 'browsing entertainment: {app}',
        'state_chatting':        'foreground chat: {app}',
        'state_transitioning':   'rapid window switching recently',
        'state_idle':            'at the computer but no clear task',
        'state_private':         'private app in foreground — not classifying / caching',
        'high_cpu':              'CPU 30s avg {cpu_percent}%',
        'high_gpu':              'GPU utilization {gpu_percent}%',
        'gaming_by_gpu':         'sustained high GPU (likely unrecognized game)',
    },
    'ja': {
        'state_away':            'システム {idle_seconds}秒入力なし',
        'state_stale_returning': 'ユーザーが離席から戻ってきた',
        'state_voice_engaged':   'ボイスモード + 直近の発話あり',
        'state_gaming':          'フォアグラウンドゲーム：{app}',
        'state_focused_work':    '{app} に {dwell_seconds}秒間集中中',
        'state_casual_browsing': 'エンタメ閲覧：{app}',
        'state_chatting':        'フォアグラウンドチャット：{app}',
        'state_transitioning':   '最近のウィンドウ切替が頻繁',
        'state_idle':            'PC前にいるが明確な作業なし',
        'state_private':         'プライベートアプリ前面——分類もキャッシュもしない',
        'high_cpu':              'CPU 30秒平均 {cpu_percent}%',
        'high_gpu':              'GPU 使用率 {gpu_percent}%',
        'gaming_by_gpu':         'GPU 高負荷継続（未識別のゲームの可能性）',
    },
    'ko': {
        'state_away':            '시스템 입력 없이 {idle_seconds}초 경과',
        'state_stale_returning': '사용자가 자리 비움에서 막 돌아옴',
        'state_voice_engaged':   '음성 모드 + 최근 발화 있음',
        'state_gaming':          '전경 게임: {app}',
        'state_focused_work':    '{app}에 {dwell_seconds}초 집중 중',
        'state_casual_browsing': '엔터테인먼트 둘러보기: {app}',
        'state_chatting':        '전경 채팅: {app}',
        'state_transitioning':   '최근 창 전환 빈번',
        'state_idle':            'PC 앞에 있으나 명확한 작업 없음',
        'state_private':         '비공개 앱 전면 — 분류/캐시하지 않음',
        'high_cpu':              'CPU 30초 평균 {cpu_percent}%',
        'high_gpu':              'GPU 사용률 {gpu_percent}%',
        'gaming_by_gpu':         'GPU 고부하 지속 (미식별 게임 의심)',
    },
    'ru': {
        'state_away':            'нет ввода {idle_seconds}с',
        'state_stale_returning': 'пользователь только что вернулся',
        'state_voice_engaged':   'голосовой режим + недавняя речь',
        'state_gaming':          'игра на переднем плане: {app}',
        'state_focused_work':    'сосредоточен на {app} уже {dwell_seconds}с',
        'state_casual_browsing': 'просмотр развлечений: {app}',
        'state_chatting':        'переписка на переднем плане: {app}',
        'state_transitioning':   'недавно частая смена окон',
        'state_idle':            'за компьютером без явной задачи',
        'state_private':         'приватное приложение в фокусе — не классифицируем / не кэшируем',
        'high_cpu':              'CPU средн. 30с {cpu_percent}%',
        'high_gpu':              'загрузка GPU {gpu_percent}%',
        'gaming_by_gpu':         'устойчиво высокая GPU (вероятно нераспознанная игра)',
    },
}


# ── State-section labels (header / footer / period / time phrases) ──
#
# Used by ``format_activity_state_section`` to render the snapshot
# into a multi-line prompt section. Inner keys are stable lookup
# names, NOT user-facing — the values are what the proactive AI sees.
#
# Required inner keys:
#   header / footer
#   never                        — placeholder when "seconds since X" is None
#   seconds_ago_fmt              — < 90s
#   minutes_ago_fmt              — < 3600s
#   hours_ago_fmt                — >= 3600s
#   time_fmt                     — "{hour:02d}:00 {period}"
#   period_morning / _afternoon / _evening / _night
#   unfinished_thread_fmt        — {tail, age, used, cap}
#   activity_scores_label
#   activity_guess_label
#   open_threads_label
#   time_user_ai_fmt             — both user_str and ai_str present
#   time_user_only_fmt           — only user_str present
#   time_only_fmt                — neither present (rare; AI spoke but no user msg)

ACTIVITY_STATE_SECTION_LABELS: dict[str, dict[str, str]] = {
    'zh': {
        'header': '======以下为活动状态======',
        'footer': '======以上为活动状态======',
        'never': '无',
        'seconds_ago_fmt': '{seconds:.0f}s前',
        'minutes_ago_fmt': '{minutes:.0f}min前',
        'hours_ago_fmt': '{hours:.0f}h前',
        'time_fmt': '{hour:02d}:00 {period}',
        'period_morning': '上午',
        'period_afternoon': '下午',
        'period_evening': '傍晚',
        'period_night': '夜里',
        'unfinished_thread_fmt': '未收尾话题：「…{tail}」({age},已跟进 {used}/{cap})',
        'activity_scores_label': '评估',
        'activity_guess_label': '叙述',
        'open_threads_label': '开放话题',
        'tone_label': '口吻',
        'time_user_ai_fmt': '{time} | 用户 {user} | AI {ai}',
        'time_user_only_fmt': '{time} | 用户 {user}',
        'time_only_fmt': '{time}',
    },
    'en': {
        'header': '======Below is Activity======',
        'footer': '======Above is Activity======',
        'never': '-',
        'seconds_ago_fmt': '{seconds:.0f}s',
        'minutes_ago_fmt': '{minutes:.0f}min',
        'hours_ago_fmt': '{hours:.0f}h',
        'time_fmt': '{hour:02d}:00 {period}',
        'period_morning': 'morning',
        'period_afternoon': 'afternoon',
        'period_evening': 'evening',
        'period_night': 'night',
        'unfinished_thread_fmt': 'unfinished: "…{tail}" ({age} ago, followed up {used}/{cap})',
        'activity_scores_label': 'scores',
        'activity_guess_label': 'narrative',
        'open_threads_label': 'open threads',
        'tone_label': 'tone',
        'time_user_ai_fmt': '{time} | user msg {user} ago | AI {ai} ago',
        'time_user_only_fmt': '{time} | user msg {user} ago',
        'time_only_fmt': '{time}',
    },
    'ja': {
        'header': '======以下は活動状態======',
        'footer': '======以上は活動状態======',
        'never': '無',
        'seconds_ago_fmt': '{seconds:.0f}秒前',
        'minutes_ago_fmt': '{minutes:.0f}分前',
        'hours_ago_fmt': '{hours:.0f}時間前',
        'time_fmt': '{hour:02d}:00 {period}',
        'period_morning': '朝',
        'period_afternoon': '午後',
        'period_evening': '夕方',
        'period_night': '夜',
        'unfinished_thread_fmt': '未完話題:「…{tail}」({age}, フォロー {used}/{cap})',
        'activity_scores_label': '評価',
        'activity_guess_label': '叙述',
        'open_threads_label': '保留話題',
        'tone_label': '口調',
        'time_user_ai_fmt': '{time} | ユーザー {user} | AI {ai}',
        'time_user_only_fmt': '{time} | ユーザー {user}',
        'time_only_fmt': '{time}',
    },
    'ko': {
        'header': '======아래는 활동 상태======',
        'footer': '======위는 활동 상태======',
        'never': '없음',
        'seconds_ago_fmt': '{seconds:.0f}초 전',
        'minutes_ago_fmt': '{minutes:.0f}분 전',
        'hours_ago_fmt': '{hours:.0f}시간 전',
        'time_fmt': '{hour:02d}:00 {period}',
        'period_morning': '오전',
        'period_afternoon': '오후',
        'period_evening': '저녁',
        'period_night': '밤',
        'unfinished_thread_fmt': '미완 화제: "…{tail}" ({age}, 후속 {used}/{cap})',
        'activity_scores_label': '평가',
        'activity_guess_label': '서술',
        'open_threads_label': '보류 화제',
        'tone_label': '말투',
        'time_user_ai_fmt': '{time} | 사용자 {user} | AI {ai}',
        'time_user_only_fmt': '{time} | 사용자 {user}',
        'time_only_fmt': '{time}',
    },
    'ru': {
        'header': '======Ниже Активность======',
        'footer': '======Выше Активность======',
        'never': '-',
        'seconds_ago_fmt': '{seconds:.0f}с',
        'minutes_ago_fmt': '{minutes:.0f}мин',
        'hours_ago_fmt': '{hours:.0f}ч',
        'time_fmt': '{hour:02d}:00 {period}',
        'period_morning': 'утро',
        'period_afternoon': 'день',
        'period_evening': 'вечер',
        'period_night': 'ночь',
        'unfinished_thread_fmt': 'незакр. нить: «…{tail}» ({age} назад, {used}/{cap})',
        'activity_scores_label': 'оценки',
        'activity_guess_label': 'описание',
        'open_threads_label': 'открытые нити',
        'tone_label': 'тон',
        'time_user_ai_fmt': '{time} | польз. {user} назад | AI {ai} назад',
        'time_user_only_fmt': '{time} | польз. {user} назад',
        'time_only_fmt': '{time}',
    },
}
