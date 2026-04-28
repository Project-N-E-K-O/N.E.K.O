"""Activity snapshot types.

The structured output of ``UserActivityTracker.get_snapshot()`` and the
shared vocabulary used between system-signal collection, the state
machine, and the proactive-chat prompt builder.

Design notes
------------

State and propensity are deliberately separate:
  * ``state`` is the inferred user mode (gaming / focused_work / casual_browsing
    / chatting / voice_engaged / idle / transitioning / stale_returning / away).
  * ``propensity`` collapses the state down to a directive the prompt
    builder can act on (closed / restricted_screen_only / open / greeting_window).

Multiple states map to the same propensity — gaming and focused_work
both produce ``restricted_screen_only`` because the prompt's behaviour
is identical for those, even though the upstream cause differs.

The snapshot is always returned by value; callers must not mutate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ── Type aliases ────────────────────────────────────────────────────

ActivityState = Literal[
    'away',                # No activity for >= AWAY_IDLE_SECONDS
    'stale_returning',     # Just back from away (≤ STALE_RECOVERY_SECONDS)
    'gaming',              # Game window in foreground / known game process
    'focused_work',        # IDE / Office / PDF / etc. + sustained input
    'casual_browsing',     # Entertainment domains/clients dominate
    'chatting',            # IM/email/meeting in foreground + active text
    'voice_engaged',       # Voice mode and recent RMS / VAD activity
    'idle',                # At the computer but no clear activity bucket
    'transitioning',       # Recent rapid window switches / mode change
]


Propensity = Literal[
    'closed',                   # Reserved; not currently emitted (away no longer = PASS)
    'restricted_screen_only',   # Only allow screen-derived chatter; no externals/no reminisce
    'open',                     # Default: any channel allowed
    'greeting_window',          # Stale-returning / first-contact: encourage reminiscence
]


# ── Snapshot dataclass ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class WindowObservation:
    """One observed (process, title) pair at a timestamp.

    Held inside ``ActivitySnapshot`` only as the most recent observation;
    the rolling history lives in ``UserActivityTracker``'s buffer.
    """
    process_name: str | None
    title: str | None
    category: str            # 'gaming' | 'work' | 'entertainment' | 'communication' | 'unknown'
    subcategory: str | None  # e.g. 'ide' / 'video' / 'im' / 'game'
    canonical: str | None    # e.g. 'VS Code'
    is_browser: bool


@dataclass(frozen=True, slots=True)
class UnfinishedThread:
    """An open conversation thread the AI may follow up on.

    Set when the AI's last reply contained a question marker (``?`` /
    ``？`` or a sentence-final CN particle like ``吗`` / ``呢`` / ``么``)
    and the user hasn't responded yet. Cleared on user message arrival
    or when the 5-minute window expires.

    Surfaces in ``ActivitySnapshot.unfinished_thread`` so the proactive
    chat prompt can grant a special "thread continuation" allowance —
    even in ``restricted_screen_only`` states (gaming / focused_work)
    where external sources and reminiscence are otherwise forbidden.
    A capped follow-up count (default 2) prevents the AI from harassing
    the user about the same hanging question.
    """
    text: str                 # Short tail of the AI message that opened the thread
    age_seconds: float        # How long the thread has been hanging
    follow_up_count: int      # Times the AI has been allowed to follow up so far
    max_follow_ups: int       # Hard cap (default UNFINISHED_THREAD_MAX_FOLLOWUPS)


@dataclass(frozen=True, slots=True)
class ActivitySnapshot:
    """Inferred user activity at one point in time.

    All fields are presented to the prompt builder. Reasons are intended
    for both human debugging and LLM context — they explain why the
    state machine landed where it did.
    """

    # --- Inferred state ---
    state: ActivityState
    state_age_seconds: float                 # How long the state has held (current run)
    previous_state: ActivityState | None
    transitioned_recently: bool              # True for ~30s after any state flip
    stale_returning: bool                    # True for STALE_RECOVERY_SECONDS after away→active

    # --- Propensity (prompt directive) ---
    propensity: Propensity
    # Structured reasons: each entry is ``(code, params)`` where ``code``
    # is a reason key looked up in ``_REASON_TEMPLATES`` and ``params`` is
    # a dict substituted into that template at format time. Keeps the
    # snapshot language-agnostic — localization happens in
    # ``format_activity_state_section``.
    propensity_reasons: list[tuple[str, dict]] = field(default_factory=list)

    # --- System signals (the raw evidence) ---
    system_idle_seconds: float = 0.0         # GetLastInputInfo, system-wide
    cpu_avg_30s: float = 0.0                 # psutil rolling avg
    cpu_instant: float = 0.0                 # last poll
    active_window: WindowObservation | None = None
    window_switch_rate_5min: int = 0         # unique titles seen in last 5min
    os_signals_available: bool = True
    """False in degraded / remote-deployment mode — see ``SystemSnapshot``.

    When False, ``system_idle_seconds``, ``cpu_*``, and ``active_window``
    don't reflect the user's actual machine, so the prompt formatter
    skips/marks them and the proactive AI knows not to over-trust
    OS-derived states.
    """

    # --- Per-session signals ---
    seconds_since_user_msg: float | None = None
    seconds_since_ai_msg: float | None = None
    voice_recent_rms_active: bool = False    # Is voice RMS / VAD high in last 8s
    voice_mode_active: bool = False

    # --- Time context ---
    hour: int = 0                             # 0-23 local
    weekday: int = 0                          # 0=Mon
    period: str = 'day'                       # 'morning' | 'afternoon' | 'evening' | 'night'

    # --- Unfinished thread (5-min window, max 2 follow-ups) ---
    # Set when the AI's last reply contained a question and the user
    # hasn't responded. Phase 2 prompt is allowed to follow up on this
    # thread regardless of state — including gaming / focused_work where
    # external sources and reminiscence are otherwise forbidden.
    unfinished_thread: UnfinishedThread | None = None

    # --- Emotion-tier LLM enrichment (cached, advisory) ---
    # Soft scores across behavioural states (0.0-1.0 each, not
    # normalised — independent probabilities). Populated by the
    # ``activity_guess`` 20s background loop on the tracker. Empty when
    # the loop hasn't run yet or the LLM call failed.
    #
    # The HARD ``state`` field (rule-derived, above) remains the
    # authority for propensity / source filtering. ``activity_scores``
    # is advisory context for the proactive AI to reconcile against —
    # if the LLM disagrees with the rules, the prompt sees both and
    # picks the angle.
    activity_scores: dict[str, float] = field(default_factory=dict)
    # One-sentence narrative description from the same LLM pass —
    # gives the proactive AI a richer picture than the structured
    # signals alone. Empty until first computed.
    activity_guess: str = ''

    # --- Semantic open-thread detection (LLM-based, lazy) ---
    # Populated by ``kickoff_open_threads_compute`` (typically run in
    # parallel with proactive Phase 1). Each entry is a short phrase
    # describing a topic that was raised but not closed — covers cases
    # the question-mark heuristic misses (AI promises, abandoned user
    # threads, etc.). Cache invalidates on the next user message.
    open_threads: list[str] = field(default_factory=list)


# ── State → propensity mapping ──────────────────────────────────────

_STATE_TO_PROPENSITY: dict[ActivityState, Propensity] = {
    'away':             'open',                    # User said: away does not auto-PASS;
                                                   # frontend backoff naturally throttles
    'stale_returning':  'greeting_window',
    'gaming':           'restricted_screen_only',
    'focused_work':     'restricted_screen_only',
    'casual_browsing':  'open',
    'chatting':         'open',
    'voice_engaged':    'open',
    'idle':             'open',
    'transitioning':    'open',                    # User said: transitioning still allows screen;
                                                   # external sources just get a small weight cut
                                                   # (handled in source-weight layer, not propensity)
}


def state_to_propensity(state: ActivityState) -> Propensity:
    """Map an ``ActivityState`` to its prompt-level propensity directive."""
    return _STATE_TO_PROPENSITY.get(state, 'open')


# ── Localized strings for prompt injection ─────────────────────────
#
# Why these live here rather than in ``config/prompts_proactive.py``:
# the prompt module would have to import ``ActivitySnapshot`` to format
# it, and ``config/`` should not depend on ``main_logic/``. Keeping the
# formatter (and its locale strings) on the snapshot side keeps the
# layering clean — system_router calls ``format_activity_state_section``
# and passes the resulting string into the prompt template.

_STATE_LABELS: dict[str, dict[ActivityState, str]] = {
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
    },
}


_PROPENSITY_DIRECTIVES: dict[str, dict[Propensity, str]] = {
    # 直接说"该做什么"，不写"禁止什么"——后者在 system_router 已经把对应素材
    # 从 prompt 里 filter 掉，再写出来只是噪声。模型看不到的不需要禁。
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


# Reason templates — keyed by code, then by language. Params substituted
# via ``str.format`` on the template. Codes the state machine emits:
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
_REASON_TEMPLATES: dict[str, dict[str, str]] = {
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
        'high_cpu':              'CPU средн. 30с {cpu_percent}%',
        'high_gpu':              'загрузка GPU {gpu_percent}%',
        'gaming_by_gpu':         'устойчиво высокая GPU (вероятно нераспознанная игра)',
    },
}


def _render_reason(reason: tuple[str, dict], lang_key: str) -> str:
    """Render one structured reason via the per-language template table.

    Falls back to English template if the locale entry is missing,
    and to the raw reason code if even English is missing (defensive —
    keeps ``state_section`` printable when a new code is added but the
    table hasn't been updated yet).
    """
    code, params = reason
    table = _REASON_TEMPLATES.get(lang_key, _REASON_TEMPLATES['en'])
    template = table.get(code) or _REASON_TEMPLATES['en'].get(code) or code
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return code


_STATE_SECTION_LABELS: dict[str, dict[str, str]] = {
    'zh': {
        'header': '===活动状态===',
        'footer': '===状态结束===',
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
        'time_user_ai_fmt': '{time} | 用户 {user} | AI {ai}',
        'time_user_only_fmt': '{time} | 用户 {user}',
        'time_only_fmt': '{time}',
    },
    'en': {
        'header': '===Activity===',
        'footer': '===End===',
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
        'time_user_ai_fmt': '{time} | user msg {user} ago | AI {ai} ago',
        'time_user_only_fmt': '{time} | user msg {user} ago',
        'time_only_fmt': '{time}',
    },
    'ja': {
        'header': '===活動状態===',
        'footer': '===終わり===',
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
        'time_user_ai_fmt': '{time} | ユーザー {user} | AI {ai}',
        'time_user_only_fmt': '{time} | ユーザー {user}',
        'time_only_fmt': '{time}',
    },
    'ko': {
        'header': '===활동 상태===',
        'footer': '===끝===',
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
        'time_user_ai_fmt': '{time} | 사용자 {user} | AI {ai}',
        'time_user_only_fmt': '{time} | 사용자 {user}',
        'time_only_fmt': '{time}',
    },
    'ru': {
        'header': '===Активность===',
        'footer': '===Конец===',
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
        'time_user_ai_fmt': '{time} | польз. {user} назад | AI {ai} назад',
        'time_user_only_fmt': '{time} | польз. {user} назад',
        'time_only_fmt': '{time}',
    },
}


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


def _format_seconds_ago(seconds: float | None, labels: dict[str, str]) -> str:
    if seconds is None:
        return labels['never']
    if seconds < 90:
        return labels['seconds_ago_fmt'].format(seconds=seconds)
    if seconds < 3600:
        return labels['minutes_ago_fmt'].format(minutes=seconds / 60)
    return labels['hours_ago_fmt'].format(hours=seconds / 3600)


def format_activity_state_section(snap: 'ActivitySnapshot', lang: str = 'zh') -> str:
    """Render an ``ActivitySnapshot`` into a localized prompt section.

    The result is a multi-line string ready to be substituted into the
    Phase 2 generate prompt's ``{state_section}`` placeholder. Falls
    back to English if ``lang`` isn't in the supported set.

    Layout (zh example, compact):

        ===活动状态===
        focused_work（专注工作中）→ 只就屏幕内容轻聊一句
        专注 VS Code 已 200s; CPU 30s 75%
        18:00 傍晚 | 用户 30s前 | AI 2min前
        未收尾话题:「…你今天准备几点出发?」(60s前,已跟进 0/2)
        评估: focused_work 0.7 · chatting 0.2 · idle 0.1
        叙述: 主人在 VS Code 里调试，刚发了求助
        开放话题:
        - AI 答应等会帮看测试还没看
        - 主人提到 phase 1 跳过逻辑没说完
        ===状态结束===

    Conditional rendering — empty / default fields are omitted entirely:
      * "user/AI msg" line: only includes sides that have a value;
        when both are None, line dropped.
      * Activity scores: only entries with score >= 0.05, top 3.
      * Active-window line dropped — its info already appears in the
        rule-reason line ("专注 VS Code 已 200s" carries the canonical
        name), so re-stating wastes tokens.
    """
    if snap is None:
        return ''
    L = _normalize_lang(lang)
    labels = _STATE_SECTION_LABELS.get(L, _STATE_SECTION_LABELS['en'])
    state_label = _STATE_LABELS.get(L, _STATE_LABELS['en']).get(snap.state, snap.state)
    propensity_directive = _PROPENSITY_DIRECTIVES.get(L, _PROPENSITY_DIRECTIVES['en']).get(
        snap.propensity, snap.propensity,
    )

    period_key = f'period_{snap.period}'
    period_label = labels.get(period_key, snap.period)

    # Header: append a degraded marker when OS signals aren't available.
    # Tells the proactive AI not to over-trust window/idle-derived state.
    # Marker text lives in config/prompts_activity.py (multi-lang dict
    # convention — kept out of regular code per the prompt-hygiene linter).
    header = labels['header']
    if not snap.os_signals_available:
        from config.prompts_activity import OS_DEGRADED_MARKER
        header = header + ' ' + OS_DEGRADED_MARKER.get(L, OS_DEGRADED_MARKER['en'])
    lines: list[str] = [header]

    # Line 1: state + propensity directive on a single line.
    lines.append(f"{snap.state}（{state_label}）→ {propensity_directive}")

    # Line 2: rule reasons (skip if empty — happens for unknown states).
    if snap.propensity_reasons:
        rendered_reasons = [_render_reason(r, L) for r in snap.propensity_reasons]
        lines.append('; '.join(rendered_reasons))

    # Line 3: time + msg recency. Compact form, side(s) omitted when no data.
    time_str = labels['time_fmt'].format(hour=snap.hour, period=period_label)
    user_str = (
        _format_seconds_ago(snap.seconds_since_user_msg, labels)
        if snap.seconds_since_user_msg is not None else None
    )
    ai_str = (
        _format_seconds_ago(snap.seconds_since_ai_msg, labels)
        if snap.seconds_since_ai_msg is not None else None
    )
    if user_str and ai_str:
        lines.append(labels['time_user_ai_fmt'].format(time=time_str, user=user_str, ai=ai_str))
    elif user_str:
        lines.append(labels['time_user_only_fmt'].format(time=time_str, user=user_str))
    elif ai_str:
        # Rare (AI spoke but no user msg yet) — fall back on time only.
        lines.append(labels['time_only_fmt'].format(time=time_str))
    else:
        lines.append(labels['time_only_fmt'].format(time=time_str))

    # Unfinished thread: single compact line.
    if snap.unfinished_thread is not None:
        thread = snap.unfinished_thread
        age_str = _format_seconds_ago(thread.age_seconds, labels)
        tail = thread.text.strip().replace('\n', ' ')
        if len(tail) > 40:
            tail = tail[-40:]
        lines.append(labels['unfinished_thread_fmt'].format(
            tail=tail, age=age_str,
            used=thread.follow_up_count, cap=thread.max_follow_ups,
        ))

    # LLM enrichment — populated only when the emotion-tier loop has run
    # and returned successfully; otherwise quietly omitted.
    if snap.activity_scores:
        # Drop near-zero entries (< 0.05) and keep top 3 — anything more
        # is noise the proactive prompt won't usefully act on.
        ordered = sorted(
            (kv for kv in snap.activity_scores.items() if kv[1] >= 0.05),
            key=lambda kv: -kv[1],
        )[:3]
        if ordered:
            score_str = ' · '.join(f'{name} {score:.1f}' for name, score in ordered)
            lines.append(f"{labels['activity_scores_label']}: {score_str}")
    if snap.activity_guess:
        lines.append(f"{labels['activity_guess_label']}: {snap.activity_guess}")
    if snap.open_threads:
        lines.append(f"{labels['open_threads_label']}:")
        for thread_text in snap.open_threads[:3]:
            lines.append(f'- {thread_text}')

    lines.append(labels['footer'])
    return '\n'.join(lines)
