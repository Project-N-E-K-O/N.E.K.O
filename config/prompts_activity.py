



or the prompt-hygiene linter (``scripts/check_prompt_hygiene.py``)
flags them as ``I18N_NOT_IN_CONFIG``.

Contents:

* ``ACTIVITY_GUESS_PROMPTS`` — emotion-tier system prompt that asks
  the model to soft-score the user's current activity state and write










  signals. Consumed by
  ``main_logic/activity/snapshot.py:format_activity_state_section``.

* ``STATE_LABELS`` — human-readable localized label for each
  ``ActivityState`` value. Consumed by
  ``main_logic/activity/snapshot.py:format_activity_state_section``.

* ``PROPENSITY_DIRECTIVES`` — localized one-line directive for each
  ``Propensity`` value shown in the proactive prompt. Consumed by
  ``main_logic/activity/snapshot.py:format_activity_state_section``.

* ``REASON_TEMPLATES`` — localized sentence templates for structured
  propensity reason codes emitted by the state machine. Consumed by
  ``main_logic/activity/snapshot.py:_render_reason``.

* ``STATE_SECTION_LABELS`` — localized UI strings (headers, footers,
  time format strings, etc.) used when rendering the activity-state
  section. Consumed by
  ``main_logic/activity/snapshot.py:format_activity_state_section``.

All are simple ``{lang_code: str | dict}`` mappings and can be
resolved via ``MAP.get(lang, MAP['en'])``.
"""

from __future__ import annotations
















































































































































































































    'ko': '(원격 모드 · 화면 신호 없음)',
    'ru': '(удалённый режим · нет экранных сигналов)',
}


# ── State labels (human-readable, localized) ─────────────────────────
# Keys match ActivityState literal values in main_logic/activity/snapshot.py.
# Consumed by format_activity_state_section().

STATE_LABELS: dict[str, dict[str, str]] = {
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


# ── Propensity directives (one-liner shown in proactive prompt) ───────
# Keys match Propensity literal values in main_logic/activity/snapshot.py.
# Phrased as "what is allowed", not "what is forbidden" — the router
# already strips disallowed sources from the prompt; repeating the
# prohibition is just noise for the model.
# Consumed by format_activity_state_section().

PROPENSITY_DIRECTIVES: dict[str, dict[str, str]] = {
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


# ── Reason templates (state-machine propensity reasons) ──────────────
# Keyed by reason code, then by language. Params substituted via
# str.format(). Reason codes the state machine emits:
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
#   gaming_by_gpu           {}   (fallback when no game-keyword hit)
# Consumed by _render_reason() in main_logic/activity/snapshot.py.

REASON_TEMPLATES: dict[str, dict[str, str]] = {
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


# ── State-section UI labels ───────────────────────────────────────────
# Header/footer strings, time/ago format patterns, and sub-labels used
# when rendering the activity state section in the proactive prompt.
# Consumed by format_activity_state_section() in snapshot.py.

STATE_SECTION_LABELS: dict[str, dict[str, str]] = {
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