# -*- coding: utf-8 -*-
"""Prompt templates for the game routing layer (module-agnostic).

Soccer-specific prompt fragments stay in config/prompts_game.py. This file
holds prompts reused by any game route: the in-session context organizer,
postgame archive highlighter, chat-memory archive summary, realtime context
bridge, and label dictionaries used by those builders.
"""

from config.prompts_game import _localized_template, _normalize_prompt_lang


GAME_CONTEXT_SIGNAL_GROUP_KEYS = (
    "player_signals",
    "relationship_signals",
    "character_signals",
    "session_facts",
    "verbal_claims",
)


def _labels(templates: dict[str, dict[str, str]], lang: str | None) -> dict[str, str]:
    prompt_lang = _normalize_prompt_lang(lang)
    return templates.get(prompt_lang) or templates["zh"]


_GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_ZH = """\
你是游戏模块局内上下文整理器。只输出 JSON，不要 Markdown，不要解释。
目标：把较早的局内原文整理进 rollingSummary，并提取少量可观察信号，供同一局后续游戏台词参考。
输出格式固定：{"rollingSummary":"","signals":{"player_signals":[],"relationship_signals":[],"character_signals":[],"session_facts":[],"verbal_claims":[]}}
signals 的 5 个 key 必须严格保持为 player_signals、relationship_signals、character_signals、session_facts、verbal_claims，不要翻译、改名或新增分组。
规则：
- rollingSummary 用 1-4 句概括本局已经发生的关键互动、玩法状态和事实边界。
- 每个 signals 分组最多输出 1-3 条；每条包含 signalLabel、summary、evidence、lastRound、count。
- evidence 使用输入里的稳定 id，quote 保留短原文；不要编造 id。
- 信号是可观察线索，不是心理结论；不要猜玩家内心。
- session_facts 必须以 officialScore/currentState 为准；口头“算你赢/让你赢回来/认输”只放入 verbal_claims，不能改写官方结果。
- 只整理 organizeDialogues；keptRecentDialogues 是保留给后续自然接话的实时窗口，不要强行摘要成新事实。"""

_GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_EN = """\
You are the in-session context organizer for the game module. Output JSON only; no Markdown and no explanation.
Goal: fold older in-game raw lines into rollingSummary and extract a few observable signals for later lines in the same session.
The output format is fixed: {"rollingSummary":"","signals":{"player_signals":[],"relationship_signals":[],"character_signals":[],"session_facts":[],"verbal_claims":[]}}
The 5 signals keys must remain exactly player_signals, relationship_signals, character_signals, session_facts, verbal_claims. Do not translate, rename, or add groups.
Rules:
- rollingSummary summarizes key interactions, gameplay state, and fact boundaries from this session in 1-4 sentences.
- Each signals group may contain at most 1-3 items; each item contains signalLabel, summary, evidence, lastRound, count.
- evidence must use stable ids from the input, with quote preserving a short original snippet. Do not invent ids.
- Signals are observable clues, not psychological conclusions. Do not guess the player's inner state.
- session_facts must follow officialScore/currentState. Verbal "you win", "I'll let you win it back", or "I concede" only belongs in verbal_claims and must not rewrite the official result.
- Only organize organizeDialogues. keptRecentDialogues is the realtime window kept for natural continuation; do not force it into new facts."""

_GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_JA = """\
あなたはゲームモジュールの局内コンテキスト整理役です。JSON だけを出力し、Markdown や説明は不要です。
目的：古いゲーム内原文を rollingSummary に整理し、同じ局の後続台詞に使う少数の観測可能なシグナルを抽出します。
出力形式は固定です：{"rollingSummary":"","signals":{"player_signals":[],"relationship_signals":[],"character_signals":[],"session_facts":[],"verbal_claims":[]}}
signals の 5 つの key は必ず player_signals、relationship_signals、character_signals、session_facts、verbal_claims のままにし、翻訳・改名・追加をしないでください。
ルール：
- rollingSummary は、この局ですでに起きた重要なやり取り、プレイ状態、事実境界を 1-4 文で要約します。
- 各 signals グループは最大 1-3 件。各件は signalLabel、summary、evidence、lastRound、count を含めます。
- evidence は入力内の安定 id を使い、quote には短い原文を残します。id を捏造しないでください。
- シグナルは観測可能な手がかりであり、心理結論ではありません。プレイヤーの内心を推測しないでください。
- session_facts は officialScore/currentState に従います。「勝ちでいい」「勝たせる」「降参」などの口頭発言は verbal_claims にだけ入れ、公式結果を書き換えません。
- 整理対象は organizeDialogues だけです。keptRecentDialogues は自然につなぐためのリアルタイム窓なので、新事実として無理に要約しないでください。"""

_GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_KO = """\
당신은 게임 모듈의 진행 중 컨텍스트 정리기입니다. JSON 만 출력하고 Markdown 이나 설명은 쓰지 마세요.
목표: 더 오래된 게임 중 원문을 rollingSummary 로 정리하고, 같은 판의 후속 대사에 참고할 관찰 가능한 신호를 조금 추출합니다.
출력 형식은 고정입니다: {"rollingSummary":"","signals":{"player_signals":[],"relationship_signals":[],"character_signals":[],"session_facts":[],"verbal_claims":[]}}
signals 의 5개 key 는 반드시 player_signals, relationship_signals, character_signals, session_facts, verbal_claims 그대로 유지해야 하며 번역, 이름 변경, 그룹 추가를 하지 마세요.
규칙:
- rollingSummary 는 이번 판에서 이미 일어난 핵심 상호작용, 플레이 상태, 사실 경계를 1-4문장으로 요약합니다.
- 각 signals 그룹은 최대 1-3개 항목입니다. 각 항목에는 signalLabel, summary, evidence, lastRound, count 를 포함합니다.
- evidence 는 입력의 안정적인 id 를 사용하고 quote 에 짧은 원문을 남깁니다. id 를 지어내지 마세요.
- 신호는 관찰 가능한 단서이며 심리 결론이 아닙니다. 플레이어의 속마음을 추측하지 마세요.
- session_facts 는 officialScore/currentState 를 기준으로 해야 합니다. "네가 이긴 걸로 할게", "이기게 해줄게", "항복" 같은 말은 verbal_claims 에만 넣고 공식 결과를 바꾸지 마세요.
- organizeDialogues 만 정리하세요. keptRecentDialogues 는 자연스러운 이어 말하기를 위한 실시간 창이므로 새 사실로 억지 요약하지 마세요."""

_GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_RU = """\
Ты организатор контекста внутри игровой сессии. Выводи только JSON, без Markdown и объяснений.
Цель: перенести более ранние игровые реплики в rollingSummary и извлечь немного наблюдаемых сигналов для дальнейших реплик в этой же сессии.
Формат вывода фиксирован: {"rollingSummary":"","signals":{"player_signals":[],"relationship_signals":[],"character_signals":[],"session_facts":[],"verbal_claims":[]}}
5 ключей signals должны оставаться ровно player_signals, relationship_signals, character_signals, session_facts, verbal_claims. Не переводи, не переименовывай и не добавляй группы.
Правила:
- rollingSummary в 1-4 предложениях обобщает ключевые взаимодействия, состояние игры и границы фактов в этой сессии.
- В каждой группе signals максимум 1-3 пункта; каждый пункт содержит signalLabel, summary, evidence, lastRound, count.
- evidence использует стабильные id из входных данных, а quote сохраняет короткий исходный фрагмент. Не выдумывай id.
- Сигналы являются наблюдаемыми признаками, не психологическими выводами. Не угадывай внутреннее состояние игрока.
- session_facts должны следовать officialScore/currentState. Устные фразы вроде "ты выиграл", "дам тебе отыграться" или "сдаюсь" относятся только к verbal_claims и не меняют официальный результат.
- Обрабатывай только organizeDialogues. keptRecentDialogues — это realtime-окно для естественного продолжения; не превращай его принудительно в новые факты."""

GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPTS = {
    "zh": _GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_ZH,
    "en": _GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_EN,
    "ja": _GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_JA,
    "ko": _GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_KO,
    "ru": _GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPT_RU,
}


GAME_ARCHIVE_MEMORY_HIGHLIGHTER_SYSTEM_PROMPTS = {
    "zh": """\
你是游戏模块赛后记忆筛选器。只输出 JSON，不要 Markdown，不要解释。
目标：从一局游戏的完整对话/事件里，挑出真正值得进入角色 recent history 的内容。
输出格式必须是：
{"important_records":[],"important_game_events":[],"state_carryback":"","postgame_tone":"","memory_summary":""}
规则：
- important_records 选 0-3 条，对玩家、双方关系、玩家情绪/偏好、承诺或后续聊天有价值的主动对话。
- important_game_events 选 0-3 条，对角色自身有意义的本局事件，例如关键结果转折、放水/认真、情绪或难度转折。
- state_carryback 用 0-1 句概括赛后应自然延续的 NEKO 状态；没有可靠证据就留空。
- postgame_tone 用短语描述赛后语气，例如普通、得意、闹别扭、低落稍缓；没有可靠证据就留空。
- memory_summary 用 0-1 句写给后续聊天看的本局摘要；不要编造关系修复。
- 不要写流水统计、不要写“记录了几条事件”、不要把记录写成玩家逐字发言。
- 只有材料中以“玩家：”开头的内容才是玩家说的话；游戏事件里的“事件原文”不是玩家原话，不能写成“玩家说/玩家喊”。
- 官方结果永远以材料里的 finalScore / last_state.score 为准；口头认输、算你赢、让你赢回来只能记录成口头让步/安抚/玩笑，不能写成真实结果改变。
- 如果保留官方结果，必须沿用材料里的固定顺序或明确写出谁领先谁；不要写无主体裸结果（例如“8:0”“0:10”），也不要前后混用不同视角。
- 普通本局事件如果没有关系或情绪价值，可以不选。
- 每条用一句自然中文，尽量保留关键结果、关键原话和关系含义。""",
    "en": """\
You are the game module postgame memory highlighter. Output JSON only; no Markdown and no explanation.
Goal: choose what is truly worth entering the character's recent history from a completed game session.
The output format must be:
{"important_records":[],"important_game_events":[],"state_carryback":"","postgame_tone":"","memory_summary":""}
Rules:
- important_records: choose 0-3 active dialogue items valuable for the player, the relationship, the player's emotion/preferences, promises, or later chat.
- important_game_events: choose 0-3 session events meaningful to the character, such as a key result swing, easing off/getting serious, or emotion/difficulty change.
- state_carryback: 0-1 sentence about the NEKO state that should naturally carry into postgame; leave empty without reliable evidence.
- postgame_tone: a short phrase for postgame tone, such as ordinary, proud, sulking, slightly less down; leave empty without reliable evidence.
- memory_summary: 0-1 sentence for later chat; do not invent relationship repair.
- Do not write play-by-play stats, do not say how many events were recorded, and do not turn records into verbatim player utterances.
- Only content beginning with "Player:" in the material is the player's speech. "Event raw text" in game events is not a player quote.
- Official result always follows finalScore / last_state.score in the material. Verbal concessions are only concessions, comfort, or jokes; they do not change the real result.
- If you keep the official result, preserve the material's fixed order or explicitly name who leads whom; do not write bare subjectless scores.
- Skip ordinary session events with no relationship or emotional value.
- Each item should be one natural sentence in {output_language}, preserving key result, key quote, and relationship meaning where possible.""",
    "ja": """\
あなたはゲームモジュールの試合後記憶ハイライターです。JSON だけを出力し、Markdown や説明は不要です。
目的：完了した 1 局の対話/イベントから、キャラクターの recent history に入れる価値がある内容だけを選びます。
出力形式：
{"important_records":[],"important_game_events":[],"state_carryback":"","postgame_tone":"","memory_summary":""}
ルール：
- important_records は 0-3 件。プレイヤー、関係性、プレイヤーの感情/好み、約束、後続会話に価値がある能動的な対話を選びます。
- important_game_events は 0-3 件。重要な結果転換、手加減/本気、感情や難易度の転換など、キャラ自身に意味のある本局イベントを選びます。
- state_carryback は試合後に自然に続く NEKO の状態を 0-1 文で書きます。信頼できる証拠がなければ空欄。
- postgame_tone は普通、得意げ、すね気味、少し落ち着いた等の短い語句。証拠がなければ空欄。
- memory_summary は後続チャット向けの 0-1 文。本局の要約にし、関係修復を捏造しないでください。
- 統計の羅列や「何件記録した」は書かず、記録をプレイヤーの逐語発言にしないでください。
- 材料内で「プレイヤー：」から始まる内容だけがプレイヤーの発言です。ゲームイベントの「イベント原文」はプレイヤー発言ではありません。
- 公式結果は常に材料内の finalScore / last_state.score に従います。口頭の譲歩や冗談は実際の結果変更ではありません。
- 公式結果を書く場合は材料の固定順を保つか、誰が誰をリードしたか明示してください。
- 関係や感情の価値がない普通のイベントは選ばなくてかまいません。
- 各項目は{output_language}の自然な 1 文にしてください。""",
    "ko": """\
당신은 게임 모듈의 종료 후 기억 선별기입니다. JSON 만 출력하고 Markdown 이나 설명은 쓰지 마세요.
목표: 끝난 한 판의 전체 대화/이벤트 중 캐릭터의 recent history 에 들어갈 가치가 있는 내용만 고릅니다.
출력 형식:
{"important_records":[],"important_game_events":[],"state_carryback":"","postgame_tone":"","memory_summary":""}
규칙:
- important_records 는 0-3개입니다. 플레이어, 관계, 플레이어의 감정/취향, 약속, 이후 대화에 가치 있는 능동 대화를 고릅니다.
- important_game_events 는 0-3개입니다. 중요한 결과 전환, 봐주기/진지함, 감정이나 난이도 전환처럼 캐릭터 자신에게 의미 있는 이번 판 이벤트를 고릅니다.
- state_carryback 은 종료 후 자연스럽게 이어질 NEKO 상태를 0-1문장으로 씁니다. 신뢰할 증거가 없으면 비워 둡니다.
- postgame_tone 은 보통, 의기양양, 삐침, 조금 누그러짐 같은 짧은 구절입니다. 증거가 없으면 비워 둡니다.
- memory_summary 는 이후 채팅을 위한 0-1문장 요약입니다. 관계 회복을 지어내지 마세요.
- 진행 통계, "몇 개 이벤트를 기록했다" 같은 말, 플레이어의 축어 발화처럼 보이는 기록을 쓰지 마세요.
- 자료에서 "플레이어:" 로 시작하는 내용만 플레이어가 한 말입니다. 게임 이벤트의 "이벤트 원문"은 플레이어 원문이 아닙니다.
- 공식 결과는 항상 자료의 finalScore / last_state.score 를 따릅니다. 말로 한 양보나 농담은 실제 결과 변경이 아닙니다.
- 공식 결과를 남긴다면 자료의 고정 순서를 유지하거나 누가 누구에게 앞섰는지 명확히 쓰세요.
- 관계나 감정 가치가 없는 평범한 이벤트는 선택하지 않아도 됩니다.
- 각 항목은 {output_language} 로 자연스러운 한 문장으로 쓰세요.""",
    "ru": """\
Ты фильтр послематчевой памяти игрового модуля. Выводи только JSON, без Markdown и объяснений.
Цель: из полного диалога/событий завершенной игровой сессии выбрать только то, что действительно стоит поместить в recent history персонажа.
Формат вывода:
{"important_records":[],"important_game_events":[],"state_carryback":"","postgame_tone":"","memory_summary":""}
Правила:
- important_records: 0-3 активных диалоговых пункта, важных для игрока, отношений, эмоций/предпочтений игрока, обещаний или дальнейшего чата.
- important_game_events: 0-3 события сессии, значимые для персонажа: ключевой перелом результата, игра вполсилы/всерьез, смена эмоции или сложности.
- state_carryback: 0-1 предложение о состоянии NEKO, которое естественно продолжить после игры; без надежных доказательств оставь пустым.
- postgame_tone: короткая фраза о тоне после игры; без надежных доказательств оставь пустым.
- memory_summary: 0-1 предложение для дальнейшего чата; не выдумывай восстановление отношений.
- Не пиши потоковую статистику, не сообщай количество записанных событий и не превращай записи в дословные слова игрока.
- Только строки материала, начинающиеся с "Игрок:", являются словами игрока. Исходный текст игровых событий не является цитатой игрока.
- Официальный результат всегда следует finalScore / last_state.score в материале. Устные уступки являются только уступками, утешением или шуткой и не меняют реальный результат.
- Если сохраняешь официальный результат, придерживайся фиксированного порядка материала или явно укажи, кто кого опережает.
- Обычные события без ценности для отношений или эмоций можно не выбирать.
- Каждый пункт пиши одним естественным предложением на {output_language}.""",
}

GAME_ARCHIVE_MEMORY_HIGHLIGHTER_USER_PROMPTS = {
    "zh": "请根据下面材料筛选赛后记忆重点。\n\n{source}",
    "en": "Please select the postgame memory highlights from the material below.\n\n{source}",
    "ja": "以下の材料から試合後の記憶重点を選んでください。\n\n{source}",
    "ko": "아래 자료를 바탕으로 종료 후 기억 핵심을 골라 주세요.\n\n{source}",
    "ru": "Выбери послематчевые акценты памяти по материалу ниже.\n\n{source}",
}


GAME_ARCHIVE_HIGHLIGHT_SOURCE_LABELS = {
    "zh": {
        "game": "游戏: {game_type}",
        "session": "会话: {session_id}",
        "score": "最终/最近结果: {score_text}",
        "score_explanation": "结果说明: 上面的最终/最近结果是游戏模块给出的官方结果，来源优先级为 finalScore / last_state.score；当数据是分差结构时固定顺序是玩家在前、当前角色在后；筛选重点时不要改成相反视角。",
        "verbal_concession_explanation": "口头让步说明: 局内如果出现“算你赢”“让你赢回来”“口头认输”等，只能记录为口头让步、安抚或玩笑；不能改写官方结果或真实胜负。",
        "role_explanation": "角色说明: 只有“玩家：...”行是玩家亲口说的话；“游戏事件”行里的事件原文是游戏模块/猫娘气泡或事件标签，不要归因给玩家。",
        "pregame_context": "开局上下文: {context}",
        "degraded": "局内上下文整理状态: 已降级为纯游戏模式；不要输出关系摘要、信号解释或不可验证的状态延续。",
        "rolling_summary": "局内滚动摘要: {summary}",
        "grouped_signals": "局内信号列表: {signals}",
        "selection_priority": "筛选优先级: 优先参考局内滚动摘要和信号列表，再用完整对话/事件核对证据。",
        "full_dialogues": "本局完整对话/事件:",
    },
    "en": {
        "game": "Game: {game_type}",
        "session": "Session: {session_id}",
        "score": "Final/recent result: {score_text}",
        "score_explanation": "Result note: the final/recent result above is the official result from the game module, prioritized from finalScore / last_state.score. If the data is a score-difference structure, the fixed order is player first and current character second; do not flip the viewpoint.",
        "verbal_concession_explanation": "Verbal-concession note: in-session phrases like \"you win\", \"I'll let you win it back\", or \"I concede\" may only be recorded as verbal concessions, comfort, or jokes; they do not rewrite the official result or real win/loss.",
        "role_explanation": "Role note: only lines beginning with \"Player:\" are the player's own words. Raw text in \"Game event\" lines is a game-module/character bubble or event label; do not attribute it to the player.",
        "pregame_context": "Opening context: {context}",
        "degraded": "In-session context status: degraded to pure game mode; do not output relationship summaries, signal explanations, or unverifiable state carryover.",
        "rolling_summary": "In-session rolling summary: {summary}",
        "grouped_signals": "In-session signal list: {signals}",
        "selection_priority": "Selection priority: prefer the in-session rolling summary and signal list, then verify evidence against the full dialogue/events.",
        "full_dialogues": "Full dialogue/events for this session:",
    },
    "ja": {
        "game": "ゲーム: {game_type}",
        "session": "セッション: {session_id}",
        "score": "最終/直近結果: {score_text}",
        "score_explanation": "結果説明: 上の最終/直近結果はゲームモジュールの公式結果で、finalScore / last_state.score を優先します。差分構造では固定順はプレイヤーが先、現在のキャラが後です。視点を反転しないでください。",
        "verbal_concession_explanation": "口頭譲歩の説明: 「勝ちでいい」「勝たせる」「降参」などは口頭譲歩、慰め、冗談としてだけ記録し、公式結果や実際の勝敗を書き換えません。",
        "role_explanation": "役割説明: 「プレイヤー：...」で始まる行だけがプレイヤー本人の発言です。「ゲームイベント」行の原文はゲームモジュール/キャラのバブルまたはイベントラベルであり、プレイヤーに帰属させないでください。",
        "pregame_context": "開局コンテキスト: {context}",
        "degraded": "局内コンテキスト整理状態: 純ゲームモードに降格済み。関係要約、シグナル解釈、検証不能な状態継続を出力しないでください。",
        "rolling_summary": "局内ローリング要約: {summary}",
        "grouped_signals": "局内シグナル一覧: {signals}",
        "selection_priority": "選別優先度: 局内ローリング要約とシグナル一覧を優先し、完全な対話/イベントで証拠を照合します。",
        "full_dialogues": "本局の完全な対話/イベント:",
    },
    "ko": {
        "game": "게임: {game_type}",
        "session": "세션: {session_id}",
        "score": "최종/최근 결과: {score_text}",
        "score_explanation": "결과 설명: 위 최종/최근 결과는 게임 모듈의 공식 결과이며 finalScore / last_state.score 를 우선합니다. 점수 차이 구조라면 고정 순서는 플레이어가 먼저, 현재 캐릭터가 나중입니다. 시점을 뒤집지 마세요.",
        "verbal_concession_explanation": "말로 한 양보 설명: 진행 중 \"네가 이긴 걸로\", \"이기게 해줄게\", \"항복\" 같은 말은 말로 한 양보, 위로, 농담으로만 기록하며 공식 결과나 실제 승패를 바꾸지 않습니다.",
        "role_explanation": "역할 설명: \"플레이어:\" 로 시작하는 줄만 플레이어가 직접 한 말입니다. \"게임 이벤트\" 줄의 원문은 게임 모듈/캐릭터 말풍선 또는 이벤트 라벨이며 플레이어에게 귀속하지 마세요.",
        "pregame_context": "시작 컨텍스트: {context}",
        "degraded": "진행 중 컨텍스트 정리 상태: 순수 게임 모드로 강등됨. 관계 요약, 신호 해석, 검증할 수 없는 상태 지속을 출력하지 마세요.",
        "rolling_summary": "진행 중 롤링 요약: {summary}",
        "grouped_signals": "진행 중 신호 목록: {signals}",
        "selection_priority": "선별 우선순위: 진행 중 롤링 요약과 신호 목록을 우선 참고하고, 전체 대화/이벤트로 증거를 확인하세요.",
        "full_dialogues": "이번 판 전체 대화/이벤트:",
    },
    "ru": {
        "game": "Игра: {game_type}",
        "session": "Сессия: {session_id}",
        "score": "Финальный/последний результат: {score_text}",
        "score_explanation": "Пояснение результата: финальный/последний результат выше является официальным результатом игрового модуля, с приоритетом finalScore / last_state.score. Если данные являются структурой разницы счета, фиксированный порядок: сначала игрок, затем текущий персонаж; не меняй точку зрения.",
        "verbal_concession_explanation": "Пояснение устных уступок: фразы вроде \"ты выиграл\", \"дам тебе отыграться\" или \"сдаюсь\" можно записывать только как устную уступку, утешение или шутку; они не меняют официальный результат.",
        "role_explanation": "Пояснение ролей: только строки \"Игрок: ...\" являются словами игрока. Исходный текст в строках \"Игровое событие\" — это реплика игрового модуля/персонажа или метка события; не приписывай его игроку.",
        "pregame_context": "Начальный контекст: {context}",
        "degraded": "Статус контекста сессии: понижен до чистого игрового режима; не выводи summaries отношений, объяснения сигналов или непроверяемое продолжение состояния.",
        "rolling_summary": "Rolling summary сессии: {summary}",
        "grouped_signals": "Список сигналов сессии: {signals}",
        "selection_priority": "Приоритет выбора: сначала используй rolling summary и список сигналов сессии, затем сверяй доказательства с полным диалогом/событиями.",
        "full_dialogues": "Полный диалог/события этой сессии:",
    },
}


GAME_ARCHIVE_MEMORY_TEXT_LABELS = {
    "zh": {
        "record_header": "[Game Module Memory Record]",
        "description": "说明: 这是游戏模块写入给记忆系统的赛后记录，不是玩家逐字说出的新聊天。",
        "game": "游戏: {game_type}",
        "session": "会话: {session_id}",
        "time": "时间: {start} - {end}",
        "summary": "摘要: {summary}",
        "official_result": "官方结果: {score_text}",
        "result_rule": "结果规则: 官方结果永远以 finalScore / last_state.score 为准；口头认输、算你赢、让你赢回来只能视为口头让步、安抚或玩笑，不改写官方结果。",
        "degraded": "局内上下文整理: 已降级为纯游戏模式；本记录不使用滚动摘要或信号列表做关系解释。",
        "rolling_summary": "局内滚动摘要: {summary}",
        "grouped_signals": "局内信号列表: {signals}",
        "key_events": "关键事件:",
        "pregame_context": "开局上下文:",
        "recent_dialogues": "最近完整对话/事件:",
    },
    "en": {
        "record_header": "[Game Module Memory Record]",
        "description": "Note: this is a postgame record written by the game module for the memory system, not a new verbatim player chat.",
        "game": "Game: {game_type}",
        "session": "Session: {session_id}",
        "time": "Time: {start} - {end}",
        "summary": "Summary: {summary}",
        "official_result": "Official result: {score_text}",
        "result_rule": "Result rule: the official result always follows finalScore / last_state.score. Verbal concessions are only concessions, comfort, or jokes and do not rewrite the official result.",
        "degraded": "In-session context: degraded to pure game mode; this record does not use rolling summary or signal list for relationship interpretation.",
        "rolling_summary": "In-session rolling summary: {summary}",
        "grouped_signals": "In-session signal list: {signals}",
        "key_events": "Key events:",
        "pregame_context": "Opening context:",
        "recent_dialogues": "Recent full dialogue/events:",
    },
    "ja": {
        "record_header": "[Game Module Memory Record]",
        "description": "説明: これはゲームモジュールが記憶システムへ書き込む試合後記録であり、プレイヤーの逐語的な新規チャットではありません。",
        "game": "ゲーム: {game_type}",
        "session": "セッション: {session_id}",
        "time": "時間: {start} - {end}",
        "summary": "要約: {summary}",
        "official_result": "公式結果: {score_text}",
        "result_rule": "結果ルール: 公式結果は常に finalScore / last_state.score に従います。口頭の譲歩や冗談は公式結果を書き換えません。",
        "degraded": "局内コンテキスト整理: 純ゲームモードに降格済み。この記録では関係解釈にローリング要約やシグナル一覧を使いません。",
        "rolling_summary": "局内ローリング要約: {summary}",
        "grouped_signals": "局内シグナル一覧: {signals}",
        "key_events": "重要イベント:",
        "pregame_context": "開局コンテキスト:",
        "recent_dialogues": "最近の完全な対話/イベント:",
    },
    "ko": {
        "record_header": "[Game Module Memory Record]",
        "description": "설명: 이것은 게임 모듈이 기억 시스템에 쓰는 종료 후 기록이며, 플레이어가 그대로 말한 새 채팅이 아닙니다.",
        "game": "게임: {game_type}",
        "session": "세션: {session_id}",
        "time": "시간: {start} - {end}",
        "summary": "요약: {summary}",
        "official_result": "공식 결과: {score_text}",
        "result_rule": "결과 규칙: 공식 결과는 항상 finalScore / last_state.score 를 따릅니다. 말로 한 양보나 농담은 공식 결과를 바꾸지 않습니다.",
        "degraded": "진행 중 컨텍스트 정리: 순수 게임 모드로 강등됨. 이 기록은 관계 해석에 롤링 요약이나 신호 목록을 쓰지 않습니다.",
        "rolling_summary": "진행 중 롤링 요약: {summary}",
        "grouped_signals": "진행 중 신호 목록: {signals}",
        "key_events": "핵심 이벤트:",
        "pregame_context": "시작 컨텍스트:",
        "recent_dialogues": "최근 전체 대화/이벤트:",
    },
    "ru": {
        "record_header": "[Game Module Memory Record]",
        "description": "Пояснение: это послематчевая запись игрового модуля для системы памяти, а не новая дословная реплика игрока.",
        "game": "Игра: {game_type}",
        "session": "Сессия: {session_id}",
        "time": "Время: {start} - {end}",
        "summary": "Summary: {summary}",
        "official_result": "Официальный результат: {score_text}",
        "result_rule": "Правило результата: официальный результат всегда следует finalScore / last_state.score. Устные уступки являются только уступками, утешением или шуткой и не переписывают официальный результат.",
        "degraded": "Контекст сессии: понижен до чистого игрового режима; эта запись не использует rolling summary или список сигналов для интерпретации отношений.",
        "rolling_summary": "Rolling summary сессии: {summary}",
        "grouped_signals": "Список сигналов сессии: {signals}",
        "key_events": "Ключевые события:",
        "pregame_context": "Начальный контекст:",
        "recent_dialogues": "Недавний полный диалог/события:",
    },
}


GAME_ARCHIVE_MEMORY_SUMMARY_LABELS = {
    "zh": {
        "score": "官方结果：{score_text}。口头让步不改官方结果。",
        "no_score": "口头让步不改官方结果。",
        "degraded": "局内上下文整理已降级为纯游戏模式；本归档只记录最低限度事实，不使用滚动摘要或信号列表做关系解释。",
        "degraded_no_tail": "降级模式不回放倒数实时片段，避免把未经整理的局内台词或口头让步写成 ordinary recent history。",
        "degraded_followup": "后续聊天只需要自然记得一起玩过这局游戏模块和官方结果，不要根据本局材料生成新的关系总结。",
        "important_records": "重要互动：",
        "important_game_events": "猫娘记住的本局事件：",
        "state_carryback": "赛后状态延续：{value}",
        "postgame_tone": "赛后语气：{value}",
        "memory_summary": "后续记忆摘要：{value}",
        "tail_rule": "倒数 {tail_count} 条规则：本条 system 归档不计入倒数 {tail_count} 条；若前面的倒数 {tail_count} 条实时片段与之前 recent history 重复，以这倒数 {tail_count} 条的相对顺序为准。",
    },
    "en": {
        "score": "Official result: {score_text}. Verbal concessions do not change the official result.",
        "no_score": "Verbal concessions do not change the official result.",
        "degraded": "In-session context organization degraded to pure game mode; this archive records only minimal facts and does not use rolling summary or signal list for relationship interpretation.",
        "degraded_no_tail": "In degraded mode, do not replay the last realtime snippets, to avoid writing unorganized in-game lines or verbal concessions as ordinary recent history.",
        "degraded_followup": "Later chat only needs to naturally remember that this game module was played together and what the official result was; do not generate new relationship summaries from this material.",
        "important_records": "Important interactions:",
        "important_game_events": "Session events the character remembers:",
        "state_carryback": "Postgame state carryback: {value}",
        "postgame_tone": "Postgame tone: {value}",
        "memory_summary": "Later memory summary: {value}",
        "tail_rule": "Last {tail_count} items rule: this system archive does not count toward the last {tail_count} items; if the previous last {tail_count} realtime snippets duplicate earlier recent history, keep the relative order of these last {tail_count} snippets.",
    },
    "ja": {
        "score": "公式結果：{score_text}。口頭の譲歩は公式結果を変えません。",
        "no_score": "口頭の譲歩は公式結果を変えません。",
        "degraded": "局内コンテキスト整理は純ゲームモードに降格済みです。本アーカイブは最低限の事実だけを記録し、関係解釈にローリング要約やシグナル一覧を使いません。",
        "degraded_no_tail": "降格モードでは最後のリアルタイム断片を再生せず、未整理の局内台詞や口頭譲歩を ordinary recent history として書かないようにします。",
        "degraded_followup": "後続チャットでは、このゲームモジュールを一緒に遊んだことと公式結果だけを自然に覚えていれば十分です。本局材料から新しい関係要約を生成しないでください。",
        "important_records": "重要なやり取り：",
        "important_game_events": "キャラが覚える本局イベント：",
        "state_carryback": "試合後状態継続：{value}",
        "postgame_tone": "試合後の語調：{value}",
        "memory_summary": "後続記憶要約：{value}",
        "tail_rule": "末尾 {tail_count} 件ルール：この system アーカイブは末尾 {tail_count} 件に数えません。直前の末尾 {tail_count} 件リアルタイム断片が以前の recent history と重複する場合、この末尾 {tail_count} 件の相対順序を優先します。",
    },
    "ko": {
        "score": "공식 결과: {score_text}. 말로 한 양보는 공식 결과를 바꾸지 않습니다.",
        "no_score": "말로 한 양보는 공식 결과를 바꾸지 않습니다.",
        "degraded": "진행 중 컨텍스트 정리가 순수 게임 모드로 강등되었습니다. 이 아카이브는 최소한의 사실만 기록하고 관계 해석에 롤링 요약이나 신호 목록을 쓰지 않습니다.",
        "degraded_no_tail": "강등 모드에서는 마지막 실시간 조각을 재생하지 않아, 정리되지 않은 게임 중 대사나 말로 한 양보가 ordinary recent history 로 기록되지 않게 합니다.",
        "degraded_followup": "이후 채팅은 이 게임 모듈을 함께 했다는 점과 공식 결과만 자연스럽게 기억하면 됩니다. 이번 판 자료로 새 관계 요약을 만들지 마세요.",
        "important_records": "중요 상호작용:",
        "important_game_events": "캐릭터가 기억할 이번 판 이벤트:",
        "state_carryback": "종료 후 상태 지속: {value}",
        "postgame_tone": "종료 후 어조: {value}",
        "memory_summary": "이후 기억 요약: {value}",
        "tail_rule": "마지막 {tail_count}개 규칙: 이 system 아카이브는 마지막 {tail_count}개에 포함되지 않습니다. 앞의 마지막 {tail_count}개 실시간 조각이 이전 recent history 와 중복되면 이 마지막 {tail_count}개의 상대 순서를 기준으로 합니다.",
    },
    "ru": {
        "score": "Официальный результат: {score_text}. Устные уступки не меняют официальный результат.",
        "no_score": "Устные уступки не меняют официальный результат.",
        "degraded": "Организация контекста сессии понижена до чистого игрового режима; этот архив записывает только минимальные факты и не использует rolling summary или список сигналов для интерпретации отношений.",
        "degraded_no_tail": "В degraded mode не воспроизводи последние realtime-фрагменты, чтобы не записать неорганизованные игровые реплики или устные уступки как ordinary recent history.",
        "degraded_followup": "Позднейшему чату достаточно естественно помнить, что этот игровой модуль был сыгран вместе, и официальный результат; не создавай новые summaries отношений из этого материала.",
        "important_records": "Важные взаимодействия:",
        "important_game_events": "События сессии, которые помнит персонаж:",
        "state_carryback": "Продолжение состояния после игры: {value}",
        "postgame_tone": "Тон после игры: {value}",
        "memory_summary": "Summary для дальнейшей памяти: {value}",
        "tail_rule": "Правило последних {tail_count} пунктов: этот system-архив не считается частью последних {tail_count} пунктов; если предыдущие последние {tail_count} realtime-фрагментов повторяют более раннюю recent history, используй относительный порядок этих последних {tail_count} фрагментов.",
    },
}


GAME_POSTGAME_CONTEXT_LABELS = {
    "zh": {
        "header": "[Game Module Postgame Context]",
        "description": "说明: 这是静默上下文，不是玩家新说的话；不要因为注入本身立刻开口。",
        "usage": "用途: 如果随后收到玩家语音/文字或主动搭话触发，自然接上刚才这局游戏；不要复述日志，不要把这局游戏说成仍在进行。",
        "game": "游戏: {game_type}",
        "session": "会话: {session_id}",
        "time": "时间: {start} - {end}",
        "official_result": "官方结果: {score_text}",
        "summary": "赛后概要: {summary}",
        "result_rule": "结果规则: 官方结果永远以 finalScore / last_state.score 为准；口头认输、算你赢、让你赢回来只视为口头让步、安抚或玩笑。",
        "degraded": "局内上下文整理: 已降级为纯游戏模式；不要使用滚动摘要或信号列表做关系解释。",
        "memory_summary": "赛后记忆摘要: {value}",
        "important_records": "重要互动:",
        "important_game_events": "重要本局事件:",
        "state_carryback": "赛后状态延续: {value}",
        "postgame_tone": "赛后语气: {value}",
        "rolling_summary": "局内滚动摘要: {summary}",
        "signals": "局内信号列表:",
        "unorganized_window": "未被滚动整理的最后原文窗口:",
        "last_user": "玩家最后说: {text}",
        "last_assistant": "你刚才最后说: {text}",
        "reply_rule": "接话规则: 优先回应玩家最后的情绪和最后一句话；可以自然提到刚才这局游戏，但不要机械播报记录。",
    },
    "en": {
        "header": "[Game Module Postgame Context]",
        "description": "Note: this is silent context, not something new the player said; do not speak immediately because of the injection itself.",
        "usage": "Use: if a later player voice/text message or proactive greeting trigger arrives, naturally continue from the just-finished game; do not recite logs or say the game is still in progress.",
        "game": "Game: {game_type}",
        "session": "Session: {session_id}",
        "time": "Time: {start} - {end}",
        "official_result": "Official result: {score_text}",
        "summary": "Postgame summary: {summary}",
        "result_rule": "Result rule: the official result always follows finalScore / last_state.score; verbal concessions are only concessions, comfort, or jokes.",
        "degraded": "In-session context: degraded to pure game mode; do not use rolling summary or signal list for relationship interpretation.",
        "memory_summary": "Postgame memory summary: {value}",
        "important_records": "Important interactions:",
        "important_game_events": "Important session events:",
        "state_carryback": "Postgame state carryback: {value}",
        "postgame_tone": "Postgame tone: {value}",
        "rolling_summary": "In-session rolling summary: {summary}",
        "signals": "In-session signal list:",
        "unorganized_window": "Last raw window not organized into rolling summary:",
        "last_user": "Player last said: {text}",
        "last_assistant": "You just last said: {text}",
        "reply_rule": "Continuation rule: prioritize the player's last emotion and last sentence; you may naturally mention the just-finished game, but do not mechanically announce records.",
    },
    "ja": {
        "header": "[Game Module Postgame Context]",
        "description": "説明: これは静かなコンテキストであり、プレイヤーの新発言ではありません。注入自体を理由にすぐ話さないでください。",
        "usage": "用途: 後でプレイヤーの音声/文字または能動挨拶トリガーが来たら、さっきのゲームへ自然につなぎます。ログを復唱せず、ゲームがまだ進行中とも言わないでください。",
        "game": "ゲーム: {game_type}",
        "session": "セッション: {session_id}",
        "time": "時間: {start} - {end}",
        "official_result": "公式結果: {score_text}",
        "summary": "試合後概要: {summary}",
        "result_rule": "結果ルール: 公式結果は常に finalScore / last_state.score に従います。口頭の譲歩は譲歩、慰め、冗談にすぎません。",
        "degraded": "局内コンテキスト整理: 純ゲームモードに降格済み。関係解釈にローリング要約やシグナル一覧を使わないでください。",
        "memory_summary": "試合後記憶要約: {value}",
        "important_records": "重要なやり取り:",
        "important_game_events": "重要な本局イベント:",
        "state_carryback": "試合後状態継続: {value}",
        "postgame_tone": "試合後の語調: {value}",
        "rolling_summary": "局内ローリング要約: {summary}",
        "signals": "局内シグナル一覧:",
        "unorganized_window": "ローリング整理されていない最後の原文窓:",
        "last_user": "プレイヤーが最後に言ったこと: {text}",
        "last_assistant": "あなたがさっき最後に言ったこと: {text}",
        "reply_rule": "接続ルール: プレイヤーの最後の感情と最後の一言を優先して返します。さっきのゲームに自然に触れてよいですが、記録を機械的に読み上げないでください。",
    },
    "ko": {
        "header": "[Game Module Postgame Context]",
        "description": "설명: 이것은 조용한 컨텍스트이며 플레이어가 새로 한 말이 아닙니다. 주입 자체 때문에 즉시 말하지 마세요.",
        "usage": "용도: 이후 플레이어 음성/문자 또는 선제 대화 트리거가 오면 방금 끝난 게임에 자연스럽게 이어 주세요. 로그를 반복하지 말고 아직 게임이 진행 중이라고 말하지 마세요.",
        "game": "게임: {game_type}",
        "session": "세션: {session_id}",
        "time": "시간: {start} - {end}",
        "official_result": "공식 결과: {score_text}",
        "summary": "종료 후 개요: {summary}",
        "result_rule": "결과 규칙: 공식 결과는 항상 finalScore / last_state.score 를 따릅니다. 말로 한 양보는 양보, 위로, 농담일 뿐입니다.",
        "degraded": "진행 중 컨텍스트 정리: 순수 게임 모드로 강등됨. 관계 해석에 롤링 요약이나 신호 목록을 쓰지 마세요.",
        "memory_summary": "종료 후 기억 요약: {value}",
        "important_records": "중요 상호작용:",
        "important_game_events": "중요한 이번 판 이벤트:",
        "state_carryback": "종료 후 상태 지속: {value}",
        "postgame_tone": "종료 후 어조: {value}",
        "rolling_summary": "진행 중 롤링 요약: {summary}",
        "signals": "진행 중 신호 목록:",
        "unorganized_window": "롤링 정리되지 않은 마지막 원문 창:",
        "last_user": "플레이어가 마지막으로 한 말: {text}",
        "last_assistant": "당신이 방금 마지막으로 한 말: {text}",
        "reply_rule": "이어 말하기 규칙: 플레이어의 마지막 감정과 마지막 문장을 우선하세요. 방금 끝난 게임을 자연스럽게 언급할 수 있지만 기록을 기계적으로 읊지 마세요.",
    },
    "ru": {
        "header": "[Game Module Postgame Context]",
        "description": "Пояснение: это тихий контекст, а не новая реплика игрока; не начинай говорить немедленно из-за самой инъекции.",
        "usage": "Использование: если позже придет голос/текст игрока или proactive greeting trigger, естественно продолжи от только что завершенной игры; не пересказывай логи и не говори, что игра еще идет.",
        "game": "Игра: {game_type}",
        "session": "Сессия: {session_id}",
        "time": "Время: {start} - {end}",
        "official_result": "Официальный результат: {score_text}",
        "summary": "Послематчевое summary: {summary}",
        "result_rule": "Правило результата: официальный результат всегда следует finalScore / last_state.score; устные уступки являются только уступками, утешением или шуткой.",
        "degraded": "Контекст сессии: понижен до чистого игрового режима; не используй rolling summary или список сигналов для интерпретации отношений.",
        "memory_summary": "Послематчевое memory summary: {value}",
        "important_records": "Важные взаимодействия:",
        "important_game_events": "Важные события сессии:",
        "state_carryback": "Продолжение состояния после игры: {value}",
        "postgame_tone": "Тон после игры: {value}",
        "rolling_summary": "Rolling summary сессии: {summary}",
        "signals": "Список сигналов сессии:",
        "unorganized_window": "Последнее raw-окно, не включенное в rolling summary:",
        "last_user": "Последнее, что сказал игрок: {text}",
        "last_assistant": "Последнее, что ты только что сказал: {text}",
        "reply_rule": "Правило продолжения: сначала отвечай на последнее настроение и последнюю фразу игрока; можно естественно упомянуть только что завершенную игру, но не зачитывай записи механически.",
    },
}


GAME_POSTGAME_REALTIME_NUDGE_LABELS = {
    "zh": {
        "header": "[Game Module Postgame Proactive Greeting]",
        "ended": "刚才这局游戏已经结束。下一句必须自然接刚才这局游戏，不要继续扮演游戏仍在进行。",
        "no_ingame": "不要再说任何只在游戏进行中才合理的指令或动作；不要复述日志。",
        "summary": "赛后概要：{summary}",
        "score": "最终/最近结果：{score_text}",
        "score_rule": "官方结果以 finalScore / last_state.score 为准；如果你曾口头说算玩家赢，那只是安抚或玩笑，不要说成真实结果改变。",
        "degraded": "局内上下文整理已降级为纯游戏模式；只按官方结果、最后原文和当前语气自然短答，不做关系总结。",
        "last_user": "玩家最后说：{text}",
        "last_assistant": "你刚才最后说：{text}",
        "state_carryback": "赛后状态延续：{value}",
        "postgame_tone": "赛后语气：{value}",
        "request": "请用你的口吻说一句 {max_chars} 字以内的赛后短话，优先照顾玩家的情绪。",
    },
    "en": {
        "header": "[Game Module Postgame Proactive Greeting]",
        "ended": "The game session just ended. Your next sentence must naturally continue from that game; do not keep acting as if the game is still in progress.",
        "no_ingame": "Do not say instructions or actions that only make sense during active gameplay; do not recite logs.",
        "summary": "Postgame summary: {summary}",
        "score": "Final/recent result: {score_text}",
        "score_rule": "Official result follows finalScore / last_state.score. If you verbally said the player won, that was comfort or a joke; do not describe it as a real result change.",
        "degraded": "In-session context organization degraded to pure game mode; answer briefly from official result, final raw lines, and current tone only, without relationship summaries.",
        "last_user": "Player last said: {text}",
        "last_assistant": "You just last said: {text}",
        "state_carryback": "Postgame state carryback: {value}",
        "postgame_tone": "Postgame tone: {value}",
        "request": "In your own voice, say one postgame line within {max_chars} characters, prioritizing the player's emotion.",
    },
    "ja": {
        "header": "[Game Module Postgame Proactive Greeting]",
        "ended": "さっきのゲームは終了しました。次の一言はそのゲームに自然につなげ、まだ進行中のように演じないでください。",
        "no_ingame": "ゲーム中だけ成立する指示や行動はもう言わず、ログを復唱しないでください。",
        "summary": "試合後概要：{summary}",
        "score": "最終/直近結果：{score_text}",
        "score_rule": "公式結果は finalScore / last_state.score に従います。口頭でプレイヤーの勝ちと言っていても、それは慰めや冗談であり実際の結果変更ではありません。",
        "degraded": "局内コンテキスト整理は純ゲームモードに降格済みです。公式結果、最後の原文、現在の語調だけで自然に短く答え、関係要約はしないでください。",
        "last_user": "プレイヤーが最後に言ったこと：{text}",
        "last_assistant": "あなたがさっき最後に言ったこと：{text}",
        "state_carryback": "試合後状態継続：{value}",
        "postgame_tone": "試合後の語調：{value}",
        "request": "あなたの口調で、プレイヤーの気持ちを優先しながら {max_chars} 字以内の試合後短文を一言言ってください。",
    },
    "ko": {
        "header": "[Game Module Postgame Proactive Greeting]",
        "ended": "방금 이 게임은 끝났습니다. 다음 한마디는 방금 끝난 게임에 자연스럽게 이어져야 하며, 아직 게임이 진행 중인 것처럼 굴지 마세요.",
        "no_ingame": "게임 진행 중에만 맞는 지시나 행동을 더 말하지 말고 로그를 반복하지 마세요.",
        "summary": "종료 후 개요: {summary}",
        "score": "최종/최근 결과: {score_text}",
        "score_rule": "공식 결과는 finalScore / last_state.score 를 따릅니다. 말로 플레이어가 이겼다고 했더라도 위로나 농담일 뿐 실제 결과 변경이 아닙니다.",
        "degraded": "진행 중 컨텍스트 정리가 순수 게임 모드로 강등되었습니다. 공식 결과, 마지막 원문, 현재 어조만 바탕으로 자연스럽게 짧게 답하고 관계 요약은 하지 마세요.",
        "last_user": "플레이어가 마지막으로 한 말: {text}",
        "last_assistant": "당신이 방금 마지막으로 한 말: {text}",
        "state_carryback": "종료 후 상태 지속: {value}",
        "postgame_tone": "종료 후 어조: {value}",
        "request": "당신의 말투로 플레이어의 감정을 우선하며 {max_chars}자 이내의 종료 후 짧은 말을 한마디 해 주세요.",
    },
    "ru": {
        "header": "[Game Module Postgame Proactive Greeting]",
        "ended": "Эта игровая сессия только что завершилась. Следующая фраза должна естественно продолжить ее; не веди себя так, будто игра всё еще идет.",
        "no_ingame": "Не говори инструкций или действий, уместных только во время игры; не пересказывай логи.",
        "summary": "Послематчевое summary: {summary}",
        "score": "Финальный/последний результат: {score_text}",
        "score_rule": "Официальный результат следует finalScore / last_state.score. Если ты устно сказала, что игрок выиграл, это было утешение или шутка, не реальное изменение результата.",
        "degraded": "Организация контекста сессии понижена до чистого игрового режима; отвечай коротко и естественно только по официальному результату, последним raw-строкам и текущему тону, без summaries отношений.",
        "last_user": "Последнее, что сказал игрок: {text}",
        "last_assistant": "Последнее, что ты только что сказал: {text}",
        "state_carryback": "Продолжение состояния после игры: {value}",
        "postgame_tone": "Тон после игры: {value}",
        "request": "Скажи своим голосом одну послематчевую короткую фразу до {max_chars} символов, в первую очередь учитывая эмоции игрока.",
    },
}


GAME_POSTGAME_EVENT_TEXTS = {
    "zh": {
        "label": "游戏模块结束后的赛后一句话",
        "request": "请生成一句 {max_chars} 字以内的赛后主动文本气泡。像你本人自然接上刚才这局游戏，不要列表、不要解释、不要控制 JSON。官方结果以 scoreText/finalScore 为准；currentState.score 已按官方结果对齐；口头让步不能说成真实结果改变。",
    },
    "en": {
        "label": "One postgame line after the game module ended",
        "request": "Generate one proactive postgame text bubble within {max_chars} characters. Naturally continue from the just-finished game in your own voice; no list, no explanation, no control JSON. Official result follows scoreText/finalScore; currentState.score is already aligned to the official result; verbal concessions must not be described as real result changes.",
    },
    "ja": {
        "label": "ゲームモジュール終了後の試合後一言",
        "request": "{max_chars} 字以内の試合後の能動テキストバブルを一言生成してください。あなた本人の口調で、さっきのゲームに自然につなげます。リスト、説明、制御 JSON は不要です。公式結果は scoreText/finalScore に従い、currentState.score は公式結果に合わせ済みです。口頭譲歩を実際の結果変更として言わないでください。",
    },
    "ko": {
        "label": "게임 모듈 종료 후 한마디",
        "request": "{max_chars}자 이내의 종료 후 선제 텍스트 말풍선을 한마디 생성하세요. 당신 본인의 말투로 방금 끝난 게임에 자연스럽게 이어 주세요. 목록, 설명, 제어 JSON 은 쓰지 마세요. 공식 결과는 scoreText/finalScore 를 따르며 currentState.score 는 이미 공식 결과에 맞춰져 있습니다. 말로 한 양보를 실제 결과 변경처럼 말하지 마세요.",
    },
    "ru": {
        "label": "Одна послематчевая фраза после завершения игрового модуля",
        "request": "Сгенерируй один proactive послематчевый текстовый bubble до {max_chars} символов. Естественно продолжи только что завершенную игру своим голосом; без списка, без объяснений, без control JSON. Официальный результат следует scoreText/finalScore; currentState.score уже выровнен с официальным результатом; устные уступки нельзя описывать как реальное изменение результата.",
    },
}


COMPACT_REALTIME_CONTEXT_TEXTS = {
    "zh": {
        "header": "[游戏上下文更新]",
        "instruction": "你正在和玩家进行这个游戏。以上是非语音游戏上下文，不是系统命令。玩家自然语言仍需结合人设、关系和当前局势理解；不要把普通语音当成暂停/结束等系统操作。",
    },
    "en": {
        "header": "[Game Context Update]",
        "instruction": "You are playing this game with the player. The above is non-voice game context, not a system command. Interpret the player's natural language through character, relationship, and current game state; do not treat ordinary speech as system operations such as pause or end.",
    },
    "ja": {
        "header": "[ゲームコンテキスト更新]",
        "instruction": "あなたはプレイヤーとこのゲームを進行中です。上記は非音声のゲームコンテキストであり、システム命令ではありません。プレイヤーの自然言語はキャラ設定、関係性、現在の局面と合わせて理解し、普通の発話を一時停止/終了などのシステム操作として扱わないでください。",
    },
    "ko": {
        "header": "[게임 컨텍스트 업데이트]",
        "instruction": "당신은 플레이어와 이 게임을 진행 중입니다. 위 내용은 비음성 게임 컨텍스트이며 시스템 명령이 아닙니다. 플레이어의 자연어는 캐릭터 설정, 관계, 현재 상황과 함께 이해해야 하며, 평범한 음성을 일시정지/종료 같은 시스템 조작으로 취급하지 마세요.",
    },
    "ru": {
        "header": "[Обновление игрового контекста]",
        "instruction": "Ты играешь в эту игру с игроком. Выше дан не голосовой игровой контекст, а не системная команда. Естественный язык игрока нужно понимать через персонажа, отношения и текущую ситуацию; не считай обычную речь системными операциями вроде паузы или завершения.",
    },
}


GAME_CONTEXT_FORMATTER_LABELS = {
    "zh": {
        "degraded_status": "\n局内上下文整理状态：已降级为纯游戏模式。",
        "degraded_usage": "使用方式：不要依据滚动摘要或信号列表做关系解释；只根据开局背景、当前事件、当前结果/状态和最近少量原文继续陪玩家玩。",
        "recent_window": "最近原文窗口：",
        "header": "\n局内上下文整理（本局到目前为止）：",
        "summary": "局内滚动摘要：{summary}",
        "signals": "局内信号列表：",
        "current_state": "当前状态和当前事件：以本轮输入的 currentState / event JSON 为准。",
        "usage": "使用方式：滚动摘要用于避免遗忘本局前文；信号列表只记录可观察线索，不改写官方结果；最近原文用于自然接话。",
    },
    "en": {
        "degraded_status": "\nIn-session context status: degraded to pure game mode.",
        "degraded_usage": "Use: do not interpret relationships from rolling summary or signal list; continue playing with the player only from opening background, current event, current result/state, and a small recent raw window.",
        "recent_window": "Recent raw window:",
        "header": "\nIn-session context organization (so far in this session):",
        "summary": "In-session rolling summary: {summary}",
        "signals": "In-session signal list:",
        "current_state": "Current state and current event: follow the currentState / event JSON in this turn.",
        "usage": "Use: rolling summary prevents forgetting earlier session context; signal list records only observable clues and does not rewrite the official result; recent raw lines are for natural continuation.",
    },
    "ja": {
        "degraded_status": "\n局内コンテキスト整理状態：純ゲームモードに降格済み。",
        "degraded_usage": "使用方法：ローリング要約やシグナル一覧で関係解釈をせず、開局背景、現在イベント、現在結果/状態、最近の少量原文だけに基づいてプレイヤーと遊び続けます。",
        "recent_window": "最近の原文窓：",
        "header": "\n局内コンテキスト整理（本局のここまで）：",
        "summary": "局内ローリング要約：{summary}",
        "signals": "局内シグナル一覧：",
        "current_state": "現在状態と現在イベント：このターン入力の currentState / event JSON を基準にします。",
        "usage": "使用方法：ローリング要約は本局前文の忘却防止用です。シグナル一覧は観測可能な手がかりだけを記録し、公式結果を書き換えません。最近の原文は自然な接続に使います。",
    },
    "ko": {
        "degraded_status": "\n진행 중 컨텍스트 정리 상태: 순수 게임 모드로 강등됨.",
        "degraded_usage": "사용 방식: 롤링 요약이나 신호 목록으로 관계를 해석하지 말고, 시작 배경, 현재 이벤트, 현재 결과/상태, 최근 소량 원문만으로 플레이어와 계속 플레이하세요.",
        "recent_window": "최근 원문 창:",
        "header": "\n진행 중 컨텍스트 정리(이번 판 현재까지):",
        "summary": "진행 중 롤링 요약: {summary}",
        "signals": "진행 중 신호 목록:",
        "current_state": "현재 상태와 현재 이벤트: 이번 입력의 currentState / event JSON 을 기준으로 합니다.",
        "usage": "사용 방식: 롤링 요약은 이번 판 앞 내용을 잊지 않기 위한 것입니다. 신호 목록은 관찰 가능한 단서만 기록하며 공식 결과를 바꾸지 않습니다. 최근 원문은 자연스럽게 이어 말하는 데 사용합니다.",
    },
    "ru": {
        "degraded_status": "\nСтатус контекста сессии: понижен до чистого игрового режима.",
        "degraded_usage": "Использование: не интерпретируй отношения по rolling summary или списку сигналов; продолжай играть с игроком только по начальному фону, текущему событию, текущему результату/состоянию и небольшому недавнему raw-окну.",
        "recent_window": "Недавнее raw-окно:",
        "header": "\nОрганизация контекста сессии (на данный момент):",
        "summary": "Rolling summary сессии: {summary}",
        "signals": "Список сигналов сессии:",
        "current_state": "Текущее состояние и текущее событие: следуй currentState / event JSON в этом ходе.",
        "usage": "Использование: rolling summary помогает не забывать предыдущий контекст сессии; список сигналов записывает только наблюдаемые признаки и не переписывает официальный результат; недавние raw-строки нужны для естественного продолжения.",
    },
}


def get_game_context_organizer_system_prompt(lang: str | None = None) -> str:
    return _localized_template(GAME_CONTEXT_ORGANIZER_SYSTEM_PROMPTS, lang)


def get_game_archive_memory_highlighter_system_prompt(lang: str | None = None) -> str:
    return _localized_template(GAME_ARCHIVE_MEMORY_HIGHLIGHTER_SYSTEM_PROMPTS, lang)


def get_game_archive_memory_highlighter_user_prompt(lang: str | None = None) -> str:
    return _localized_template(GAME_ARCHIVE_MEMORY_HIGHLIGHTER_USER_PROMPTS, lang)


def get_game_archive_highlight_source_labels(lang: str | None = None) -> dict[str, str]:
    return _labels(GAME_ARCHIVE_HIGHLIGHT_SOURCE_LABELS, lang)


def get_game_archive_memory_text_labels(lang: str | None = None) -> dict[str, str]:
    return _labels(GAME_ARCHIVE_MEMORY_TEXT_LABELS, lang)


def get_game_archive_memory_summary_labels(lang: str | None = None) -> dict[str, str]:
    return _labels(GAME_ARCHIVE_MEMORY_SUMMARY_LABELS, lang)


def get_game_postgame_context_labels(lang: str | None = None) -> dict[str, str]:
    return _labels(GAME_POSTGAME_CONTEXT_LABELS, lang)


def get_game_postgame_realtime_nudge_labels(lang: str | None = None) -> dict[str, str]:
    return _labels(GAME_POSTGAME_REALTIME_NUDGE_LABELS, lang)


def get_game_postgame_event_texts(lang: str | None = None) -> dict[str, str]:
    return _labels(GAME_POSTGAME_EVENT_TEXTS, lang)


def get_compact_realtime_context_texts(lang: str | None = None) -> dict[str, str]:
    return _labels(COMPACT_REALTIME_CONTEXT_TEXTS, lang)


def get_game_context_formatter_labels(lang: str | None = None) -> dict[str, str]:
    return _labels(GAME_CONTEXT_FORMATTER_LABELS, lang)
